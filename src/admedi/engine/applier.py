"""Applier for executing diff reports against live mediation configs.

The ``Applier`` takes a ``DiffReport`` produced by the Differ and executes
the required create/update operations via a ``MediationAdapter``, with
safety guards including dry-run default, pre-write snapshots, A/B test
detection, per-app error isolation, and post-write verification.

Examples:
    >>> from admedi.engine.applier import Applier
    >>> applier = Applier(adapter=adapter, storage=storage)
    >>> result = await applier.apply(diff_report, dry_run=True)
    >>> result.was_dry_run
    True
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone

from admedi.adapters.mediation import MediationAdapter
from admedi.adapters.storage import StorageAdapter
from admedi.models.apply_result import AppApplyResult, ApplyResult, ApplyStatus
from admedi.models.config_snapshot import ConfigSnapshot
from admedi.models.diff import AppDiffReport, DiffAction, GroupDiff
from admedi.models.sync_log import SyncLog

logger = logging.getLogger(__name__)


class Applier:
    """Executes a DiffReport by calling adapter write methods.

    Provides safety guards: dry-run default, pre-write snapshots,
    A/B test detection (both from diff-time and fresh re-check),
    per-app error isolation, and post-write verification.

    Attributes:
        adapter: The mediation adapter for API calls.
        storage: The storage adapter for snapshots and sync logs.
    """

    def __init__(
        self, adapter: MediationAdapter, storage: StorageAdapter
    ) -> None:
        """Initialize the Applier with adapter and storage dependencies.

        Args:
            adapter: Mediation adapter for reading/writing groups.
            storage: Storage adapter for persisting snapshots and sync logs.
        """
        self._adapter = adapter
        self._storage = storage

    async def apply(
        self, diff_report: DiffReport, *, dry_run: bool = True
    ) -> ApplyResult:
        """Apply a diff report, executing create/update operations.

        Args:
            diff_report: The diff report to execute.
            dry_run: If True (default), return results without making
                any API calls. If False, execute write operations.

        Returns:
            ApplyResult with per-app outcomes and aggregate totals.
        """
        if dry_run:
            return self._build_dry_run_result(diff_report)

        app_results: list[AppApplyResult] = []
        for app_report in diff_report.app_reports:
            result = await self._process_app(app_report)
            app_results.append(result)

        return ApplyResult(app_results=app_results, was_dry_run=False)

    def _build_dry_run_result(self, diff_report: DiffReport) -> ApplyResult:
        """Build an ApplyResult with DRY_RUN status for all apps.

        No API calls are made. Each app gets zero counts.

        Args:
            diff_report: The diff report to produce dry-run results for.

        Returns:
            ApplyResult with all apps having status=DRY_RUN.
        """
        app_results = [
            AppApplyResult(
                app_key=report.app_key,
                app_name=report.app_name,
                status=ApplyStatus.DRY_RUN,
                groups_created=0,
                groups_updated=0,
                error=None,
            )
            for report in diff_report.app_reports
        ]
        return ApplyResult(app_results=app_results, was_dry_run=True)

    async def _process_app(
        self, app_report: AppDiffReport
    ) -> AppApplyResult:
        """Process a single app's diff report with error isolation.

        Wraps all per-app logic in try/except so failure on one app
        does not affect others.

        Args:
            app_report: The per-app diff report to process.

        Returns:
            AppApplyResult with the outcome for this app.
        """
        try:
            return await self._process_app_inner(app_report)
        except Exception as exc:
            logger.error(
                "Failed to apply changes for app %s: %s",
                app_report.app_key,
                exc,
            )
            return AppApplyResult(
                app_key=app_report.app_key,
                app_name=app_report.app_name,
                status=ApplyStatus.FAILED,
                groups_created=0,
                groups_updated=0,
                error=str(exc),
            )

    async def _process_app_inner(
        self, app_report: AppDiffReport
    ) -> AppApplyResult:
        """Inner per-app processing logic without error isolation.

        Steps:
            0. Idempotency guard: skip if all diffs are UNCHANGED or EXTRA.
            1. A/B test check from diff-time.
            2. Pre-write snapshot with fresh A/B test re-check.
            3a. Execute DELETEs first (frees positions).
            3b. Execute CREATEs in ascending position order per ad_format.
            4. Execute UPDATEs after all CREATEs.
            5. Post-write verification.
            6. Save sync log.

        Args:
            app_report: The per-app diff report to process.

        Returns:
            AppApplyResult with the outcome for this app.
        """
        app_key = app_report.app_key
        app_name = app_report.app_name

        # Step 0: Idempotency guard -- skip if all diffs are UNCHANGED or EXTRA
        if all(
            diff.action in (DiffAction.UNCHANGED, DiffAction.EXTRA)
            for diff in app_report.group_diffs
        ):
            return AppApplyResult(
                app_key=app_key,
                app_name=app_name,
                status=ApplyStatus.SUCCESS,
                groups_created=0,
                groups_updated=0,
                error=None,
            )

        # Step 1: A/B test check from diff-time
        if app_report.has_ab_test:
            return AppApplyResult(
                app_key=app_key,
                app_name=app_name,
                status=ApplyStatus.SKIPPED,
                groups_created=0,
                groups_updated=0,
                error=app_report.ab_test_warning
                or "Active A/B test detected at diff-time",
            )

        # Step 2: Pre-write snapshot + fresh A/B test re-check
        fresh_groups = await self._adapter.get_groups(app_key)
        snapshot = ConfigSnapshot(
            app_key=app_key,
            timestamp=datetime.now(tz=timezone.utc),
            raw_config={
                "groups": [
                    g.model_dump(by_alias=True) for g in fresh_groups
                ]
            },
        )
        await self._storage.save_snapshot(snapshot)

        # A/B test re-check on fresh groups
        for group in fresh_groups:
            if group.ab_test is not None and group.ab_test != "N/A":
                return AppApplyResult(
                    app_key=app_key,
                    app_name=app_name,
                    status=ApplyStatus.SKIPPED,
                    groups_created=0,
                    groups_updated=0,
                    error=f"A/B test detected on fresh read: "
                    f"group '{group.group_name}' has ab_test='{group.ab_test}'",
                )

        # Partition diffs by action
        deletes = [
            d for d in app_report.group_diffs if d.action == DiffAction.DELETE
        ]
        creates = [
            d for d in app_report.group_diffs if d.action == DiffAction.CREATE
        ]
        updates = [
            d for d in app_report.group_diffs if d.action == DiffAction.UPDATE
        ]

        # Step 3a: Execute DELETEs first (frees positions)
        groups_deleted = await self._execute_deletes(app_key, deletes)

        # Step 3b: Execute CREATEs in ascending position order per ad_format
        groups_created = await self._execute_creates(app_key, creates)

        # Step 4: Execute UPDATEs after all CREATEs
        groups_updated = await self._execute_updates(app_key, updates)

        # Step 5: Post-write verification
        warnings = await self._verify_writes(
            app_key, creates, updates, deletes
        )

        # Step 6: Save sync log
        sync_log = SyncLog(
            app_key=app_key,
            timestamp=datetime.now(tz=timezone.utc),
            action="sync",
            groups_created=groups_created,
            groups_updated=groups_updated,
            groups_deleted=groups_deleted,
            status=ApplyStatus.SUCCESS,
        )
        await self._storage.save_sync_log(sync_log)

        return AppApplyResult(
            app_key=app_key,
            app_name=app_name,
            status=ApplyStatus.SUCCESS,
            groups_created=groups_created,
            groups_updated=groups_updated,
            groups_deleted=groups_deleted,
            error=None,
            warnings=warnings,
        )

    async def _execute_deletes(
        self, app_key: str, deletes: list[GroupDiff]
    ) -> int:
        """Execute DELETE operations before creates and updates.

        Deleting groups first frees positions so subsequent creates
        land at the correct slots.

        Args:
            app_key: The app to delete groups from.
            deletes: List of GroupDiff with action=DELETE.

        Returns:
            Number of groups successfully deleted.
        """
        if not deletes:
            return 0

        count = 0
        for diff in deletes:
            if diff.group_id is not None:
                await self._adapter.delete_group(app_key, diff.group_id)
                count += 1

        return count

    async def _execute_creates(
        self, app_key: str, creates: list[GroupDiff]
    ) -> int:
        """Execute CREATE operations in ascending position order per ad_format.

        Insert-and-shift means ascending order ensures each insert lands
        at the correct slot.

        Args:
            app_key: The app to create groups in.
            creates: List of GroupDiff with action=CREATE.

        Returns:
            Number of groups successfully created.
        """
        if not creates:
            return 0

        # Group by ad_format, then sort by position within each format
        by_format: dict[str, list[GroupDiff]] = defaultdict(list)
        for diff in creates:
            by_format[diff.ad_format.value].append(diff)

        count = 0
        for _format_key in sorted(by_format.keys()):
            format_creates = by_format[_format_key]
            format_creates.sort(
                key=lambda d: d.desired_group.position  # type: ignore[union-attr]
                if d.desired_group
                else 0
            )
            for diff in format_creates:
                if diff.desired_group is not None:
                    await self._adapter.create_group(
                        app_key, diff.desired_group
                    )
                    count += 1

        return count

    async def _execute_updates(
        self, app_key: str, updates: list[GroupDiff]
    ) -> int:
        """Execute UPDATE operations after all CREATEs.

        Args:
            app_key: The app to update groups in.
            updates: List of GroupDiff with action=UPDATE.

        Returns:
            Number of groups successfully updated.
        """
        count = 0
        for diff in updates:
            if diff.desired_group is not None and diff.group_id is not None:
                await self._adapter.update_group(
                    app_key, diff.group_id, diff.desired_group
                )
                count += 1
        return count

    async def _verify_writes(
        self,
        app_key: str,
        creates: list[GroupDiff],
        updates: list[GroupDiff],
        deletes: list[GroupDiff] | None = None,
    ) -> list[str]:
        """Post-write verification: check that writes took effect.

        Calls ``adapter.get_groups()`` and verifies:
        - CREATE groups exist (match by group_name AND ad_format)
        - UPDATE groups reflect updated countries
        - DELETE groups do NOT exist (match by group_id)

        Mismatches are returned as warnings and logged via
        ``logging.warning()``. Verification failure does NOT change
        the app status from SUCCESS.

        Args:
            app_key: The app to verify.
            creates: CREATE diffs to verify existence.
            updates: UPDATE diffs to verify country changes.
            deletes: DELETE diffs to verify absence. Defaults to ``None``
                for backward compatibility.

        Returns:
            List of warning strings for any mismatches found.
        """
        if deletes is None:
            deletes = []

        if not creates and not updates and not deletes:
            return []

        post_groups = await self._adapter.get_groups(app_key)
        warnings: list[str] = []

        # Build lookup: (group_name, ad_format) -> Group
        group_lookup: dict[tuple[str, str], list[str]] = {}
        for g in post_groups:
            key = (g.group_name, g.ad_format.value)
            group_lookup[key] = g.countries

        # Verify CREATEs exist
        for diff in creates:
            if diff.desired_group is not None:
                key = (
                    diff.desired_group.group_name,
                    diff.desired_group.ad_format.value,
                )
                if key not in group_lookup:
                    msg = (
                        f"Expected group '{diff.desired_group.group_name}' "
                        f"({diff.desired_group.ad_format.value}) after CREATE "
                        f"but not found in post-write GET"
                    )
                    warnings.append(msg)
                    logger.warning(msg)

        # Verify UPDATEs reflect updated countries
        for diff in updates:
            if diff.desired_group is not None:
                key = (
                    diff.desired_group.group_name,
                    diff.desired_group.ad_format.value,
                )
                if key in group_lookup:
                    actual_countries = set(group_lookup[key])
                    expected_countries = set(diff.desired_group.countries)
                    if actual_countries != expected_countries:
                        msg = (
                            f"Group '{diff.desired_group.group_name}' "
                            f"({diff.desired_group.ad_format.value}) countries "
                            f"mismatch after UPDATE: expected "
                            f"{sorted(expected_countries)}, "
                            f"got {sorted(actual_countries)}"
                        )
                        warnings.append(msg)
                        logger.warning(msg)
                else:
                    msg = (
                        f"Group '{diff.desired_group.group_name}' "
                        f"({diff.desired_group.ad_format.value}) not found "
                        f"in post-write GET after UPDATE"
                    )
                    warnings.append(msg)
                    logger.warning(msg)

        # Verify DELETEs are absent
        post_group_ids = {g.group_id for g in post_groups if g.group_id is not None}
        for diff in deletes:
            if diff.group_id is not None and diff.group_id in post_group_ids:
                msg = (
                    f"Group '{diff.group_name}' (id={diff.group_id}) "
                    f"still exists after DELETE"
                )
                warnings.append(msg)
                logger.warning(msg)

        return warnings

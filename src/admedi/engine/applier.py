"""Applier for executing diff reports against live mediation configs.

The ``Applier`` takes a ``DiffReport`` produced by the Differ and executes
the required create/update operations via a ``MediationAdapter``, with
safety guards including dry-run default, pre-write snapshots, A/B test
detection, per-app error isolation, and post-write verification.

The module also provides ``build_waterfall_payload()``, a standalone helper
that resolves preset entries against live instances to produce an
``adSourcePriority`` payload for the Groups v4 PUT.

Examples:
    >>> from admedi.engine.applier import Applier, build_waterfall_payload
    >>> applier = Applier(adapter=adapter, storage=storage)
    >>> result = await applier.apply(diff_report, dry_run=True)
    >>> result.was_dry_run
    True
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from admedi.adapters.mediation import MediationAdapter
from admedi.adapters.storage import StorageAdapter
from admedi.models.apply_result import AppApplyResult, ApplyResult, ApplyStatus
from admedi.models.config_snapshot import ConfigSnapshot
from admedi.models.diff import AppDiffReport, DiffAction, DiffReport, GroupDiff
from admedi.models.group import Group
from admedi.models.instance import Instance
from admedi.models.portfolio import SyncScope
from admedi.models.sync_log import SyncLog

logger = logging.getLogger(__name__)


def build_waterfall_payload(
    preset_entries: list[dict[str, Any]],
    live_instances: list[Instance],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Resolve preset entries against live instances to build a waterfall payload.

    Matches each preset entry to a live instance by ``(network_name, is_bidder)``
    and optionally ``instance_name``. Returns a payload dict suitable for the
    ``adSourcePriority`` field on the Groups v4 PUT, or ``None`` if the payload
    cannot be safely constructed.

    Resolution rules:
        1. **Exact match** (network + bidder + name): use instance_id.
        2. **Unique match** (network + bidder, no name in preset): use instance_id.
        3. **Ambiguous** (network + bidder, no name, multiple candidates): abort.
        4. **No match** (network not in app): skip with warning.
        5. **Name changed** (preset has name, no match): abort.

    Args:
        preset_entries: List of preset entry dicts, each with ``network`` (str),
            ``bidder`` (bool), and optional ``name`` (str) and ``rate`` (float).
        live_instances: Flat list of live ``Instance`` objects from the group.

    Returns:
        A tuple of ``(payload, warnings)``:
        - ``payload``: A dict with ``"bidding"`` and/or tier keys, or ``None``
          if the preset is empty or resolution was aborted due to ambiguity
          or name-change failure.
        - ``warnings``: List of warning messages. Empty for clean resolution
          or empty preset; non-empty for skipped or aborted entries.

    Examples:
        >>> payload, warnings = build_waterfall_payload(
        ...     [{"network": "Meta", "bidder": True}],
        ...     [Instance(instance_id=1, instance_name="Meta", network_name="Meta", is_bidder=True)],
        ... )
        >>> payload["bidding"]["instances"][0]["instanceId"]
        1
    """
    if not preset_entries:
        return None, []

    warnings: list[str] = []
    bidding_instances: list[dict[str, Any]] = []
    manual_instances: list[dict[str, Any]] = []
    abort = False

    for entry in preset_entries:
        network = entry["network"]
        is_bidder = entry["bidder"]
        preset_name = entry.get("name")
        preset_rate = entry.get("rate")

        # Find candidates matching network + bidder
        candidates = [
            inst for inst in live_instances
            if inst.network_name == network and inst.is_bidder == is_bidder
        ]

        if not candidates:
            # Rule 4: No match -- skip with warning
            warnings.append(
                f"Network '{network}' (bidder={is_bidder}) not found in live "
                f"instances; skipping"
            )
            continue

        if preset_name is not None:
            # Narrow by instance_name
            named_candidates = [
                inst for inst in candidates
                if inst.instance_name == preset_name
            ]
            if not named_candidates:
                # Rule 5: Name changed -- abort
                abort = True
                warnings.append(
                    f"Network '{network}' (bidder={is_bidder}) has no instance "
                    f"named '{preset_name}'; aborting waterfall update"
                )
                continue
            matched = named_candidates[0]
        elif len(candidates) == 1:
            # Rule 2: Unique match
            matched = candidates[0]
        else:
            # Rule 3: Ambiguous -- abort
            abort = True
            candidate_names = [c.instance_name for c in candidates]
            warnings.append(
                f"Network '{network}' (bidder={is_bidder}) has {len(candidates)} "
                f"instances {candidate_names}; cannot resolve without 'name' "
                f"in preset; aborting waterfall update"
            )
            continue

        # Build instance entry
        instance_entry: dict[str, Any] = {
            "providerName": matched.network_name,
            "instanceId": matched.instance_id,
        }
        if preset_rate is not None and not is_bidder:
            instance_entry["rate"] = preset_rate

        if is_bidder:
            bidding_instances.append(instance_entry)
        else:
            manual_instances.append(instance_entry)

    if abort:
        return None, warnings

    # Build payload structure
    payload: dict[str, Any] = {}
    if bidding_instances:
        payload["bidding"] = {
            "tierType": "bidding",
            "instances": bidding_instances,
        }
    if manual_instances:
        payload["tier1"] = {
            "tierType": "sortByCpm",
            "instances": manual_instances,
        }

    if not payload:
        # All entries were skipped (rule 4) but no abort
        return None, warnings

    return payload, warnings


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
        self,
        diff_report: DiffReport,
        *,
        dry_run: bool = True,
        scope: SyncScope | None = None,
        network_presets: dict[str, list[dict[str, Any]]] | None = None,
        tiers: list | None = None,
    ) -> ApplyResult:
        """Apply a diff report, executing create/update operations.

        Args:
            diff_report: The diff report to execute.
            dry_run: If True (default), return results without making
                any API calls. If False, execute write operations.
            scope: Optional sync scope controlling which fields are
                included in PUT payloads. When ``None``, defaults to
                full sync (tiers + networks).
            network_presets: Optional network preset mapping for
                waterfall payload construction. Required when
                ``scope.networks is True``.
            tiers: Optional list of PortfolioTier objects for looking up
                which network preset each group references.

        Returns:
            ApplyResult with per-app outcomes and aggregate totals.
        """
        if dry_run:
            return self._build_dry_run_result(diff_report)

        app_results: list[AppApplyResult] = []
        for app_report in diff_report.app_reports:
            result = await self._process_app(
                app_report, scope=scope,
                network_presets=network_presets, tiers=tiers,
            )
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
        self,
        app_report: AppDiffReport,
        *,
        scope: SyncScope | None = None,
        network_presets: dict[str, list[dict[str, Any]]] | None = None,
        tiers: list | None = None,
    ) -> AppApplyResult:
        """Process a single app's diff report with error isolation.

        Wraps all per-app logic in try/except so failure on one app
        does not affect others.

        Args:
            app_report: The per-app diff report to process.
            scope: Optional sync scope for scoped PUT payloads.
            network_presets: Optional network presets for waterfall payloads.
            tiers: Optional tier list for preset name lookup.

        Returns:
            AppApplyResult with the outcome for this app.
        """
        try:
            return await self._process_app_inner(
                app_report, scope=scope,
                network_presets=network_presets, tiers=tiers,
            )
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
        self,
        app_report: AppDiffReport,
        *,
        scope: SyncScope | None = None,
        network_presets: dict[str, list[dict[str, Any]]] | None = None,
        tiers: list | None = None,
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
            scope: Optional sync scope for scoped PUT payloads.
            network_presets: Optional network presets for waterfall payloads.
            tiers: Optional tier list for preset name lookup.

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
        groups_updated = await self._execute_updates(
            app_key, updates,
            scope=scope,
            network_presets=network_presets,
            fresh_groups=fresh_groups,
            tiers=tiers,
        )

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
        self,
        app_key: str,
        updates: list[GroupDiff],
        *,
        scope: SyncScope | None = None,
        network_presets: dict[str, list[dict[str, Any]]] | None = None,
        fresh_groups: list[Group] | None = None,
        tiers: list | None = None,
    ) -> int:
        """Execute UPDATE operations after all CREATEs.

        When ``scope`` is provided, controls which fields are included
        in PUT payloads:

        - ``scope.tiers and scope.networks``: full update with waterfall.
        - ``scope.tiers only``: tier fields only (current behavior).
        - ``scope.networks only``: waterfall only via ``include_tier_fields=False``.

        Args:
            app_key: The app to update groups in.
            updates: List of GroupDiff with action=UPDATE.
            scope: Optional sync scope for field control.
            network_presets: Optional network presets for waterfall payload.
            fresh_groups: Fresh groups from pre-write snapshot, used for
                instance resolution when building waterfall payloads.
            tiers: Optional list of PortfolioTier objects for looking up
                which network preset each group references.

        Returns:
            Number of groups successfully updated.
        """
        # Determine scope flags
        include_tiers = scope is None or scope.tiers
        include_networks = scope is not None and scope.networks

        # Build group_id -> instances lookup from fresh groups for waterfall resolution
        group_instances: dict[int, list[Instance]] = {}
        if include_networks and fresh_groups:
            for g in fresh_groups:
                if g.group_id is not None and g.instances is not None:
                    group_instances[g.group_id] = g.instances

        # Build group_name -> preset_name lookup from tiers
        tier_preset_lookup: dict[str, str] = {}
        if include_networks and tiers:
            for tier in tiers:
                if tier.network_preset is not None:
                    tier_preset_lookup[tier.name] = tier.network_preset

        count = 0
        for diff in updates:
            if diff.desired_group is not None and diff.group_id is not None:
                # Build waterfall payload if networks scope is active
                waterfall_payload: dict[str, Any] | None = None
                if include_networks and network_presets is not None:
                    has_waterfall_changes = any(
                        c.field == "waterfall" for c in diff.changes
                    )
                    if has_waterfall_changes:
                        # Look up preset name from tier
                        group_name = diff.desired_group.group_name
                        preset_name = tier_preset_lookup.get(group_name)
                        if preset_name and preset_name in network_presets:
                            preset_entries = network_presets[preset_name]
                            live_instances = group_instances.get(
                                diff.group_id, []
                            )
                            waterfall_payload, warnings = (
                                build_waterfall_payload(
                                    preset_entries, live_instances
                                )
                            )
                            for w in warnings:
                                logger.warning(
                                    "Waterfall warning for %s: %s",
                                    group_name, w,
                                )

                await self._adapter.update_group(
                    app_key, diff.group_id, diff.desired_group,
                    include_tier_fields=include_tiers,
                    waterfall_payload=waterfall_payload,
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

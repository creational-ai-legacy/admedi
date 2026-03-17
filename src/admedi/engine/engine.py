"""ConfigEngine orchestrator for config-as-code mediation management.

Ties together the Loader, Differ, and Applier into a cohesive async API
that the CLI commands delegate to. Each public method corresponds to a
CLI command:

- ``audit()``   -> ``admedi audit``
- ``sync()``    -> ``admedi sync-tiers``
- ``status()``  -> ``admedi status``

Group data caching strategy: within a single ``audit()`` or ``sync()``
call, the initial ``get_groups()`` result per app is cached and passed to
the Differ. The Applier fetches fresh data independently for its own
pre-write snapshot (ensuring the snapshot reflects state immediately
before writing).

Examples:
    >>> from admedi.engine.engine import ConfigEngine
    >>> # engine = ConfigEngine(adapter=adapter, storage=storage)
    >>> # report = await engine.audit("examples/shelf-sort-tiers.yaml")
    >>> # report, result = await engine.sync("config.yaml", dry_run=True)
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from admedi.adapters.mediation import MediationAdapter
from admedi.adapters.storage import StorageAdapter
from admedi.engine.applier import Applier
from admedi.engine.differ import compute_diff
from admedi.engine.loader import load_template
from admedi.models.apply_result import AppStatus, ApplyResult, PortfolioStatus
from admedi.models.diff import DiffReport
from admedi.models.group import Group
from admedi.models.portfolio import PortfolioConfig


class ConfigEngine:
    """Orchestrates config-as-code mediation management operations.

    Provides three async methods corresponding to CLI commands: audit,
    sync, and status. Each method manages the full lifecycle of its
    operation: loading templates, fetching remote state, computing
    diffs, and applying changes.

    Attributes:
        _adapter: Mediation adapter for API calls.
        _storage: Storage adapter for persistence.
        _applier: Applier instance for executing diffs.
    """

    def __init__(
        self, adapter: MediationAdapter, storage: StorageAdapter
    ) -> None:
        """Initialize the ConfigEngine with adapter and storage dependencies.

        Args:
            adapter: Mediation adapter for reading/writing groups.
            storage: Storage adapter for persisting snapshots and sync logs.
        """
        self._adapter = adapter
        self._storage = storage
        self._applier = Applier(adapter=adapter, storage=storage)

    async def audit(
        self,
        template_path: str | Path,
        *,
        app_keys: list[str] | None = None,
    ) -> DiffReport:
        """Audit the portfolio by comparing template against live state.

        Loads the YAML template, fetches remote groups concurrently for
        each portfolio app, and computes diffs. Group data from the
        initial read is cached and passed directly to the Differ (no
        redundant API calls for ``compute_diff()``).

        Args:
            template_path: Path to the YAML tier template.
            app_keys: Optional list of app keys to audit. If ``None``,
                all portfolio apps are audited.

        Returns:
            A ``DiffReport`` with per-app diff results.

        Raises:
            FileNotFoundError: If the template file does not exist.
            ConfigValidationError: If the template is invalid.
        """
        template = load_template(template_path)
        apps = self._filter_apps(template, app_keys)

        # Concurrent fetch of remote groups per app
        groups_by_app = await self._fetch_groups_concurrent(
            [app.app_key for app in apps]
        )

        # Compute diff per app using cached group data
        app_reports = [
            compute_diff(
                template.tiers,
                groups_by_app[app.app_key],
                app.app_key,
                app.name,
            )
            for app in apps
        ]

        return DiffReport(app_reports=app_reports)

    async def sync(
        self,
        template_path: str | Path,
        *,
        app_keys: list[str] | None = None,
        dry_run: bool = True,
    ) -> tuple[DiffReport, ApplyResult]:
        """Sync the portfolio by computing and applying diffs.

        Loads the template, computes diffs (same as audit), then applies
        changes via the Applier. The Applier fetches fresh group data
        for its own pre-write snapshot independently.

        Args:
            template_path: Path to the YAML tier template.
            app_keys: Optional list of app keys to sync. If ``None``,
                all portfolio apps are synced.
            dry_run: If ``True`` (default), compute diffs and return
                results without making any write API calls. If ``False``,
                execute write operations.

        Returns:
            A tuple of ``(DiffReport, ApplyResult)``. When ``dry_run``
            is ``True``, the ``ApplyResult`` has ``was_dry_run=True``
            and all entries have ``status=DRY_RUN``.

        Raises:
            FileNotFoundError: If the template file does not exist.
            ConfigValidationError: If the template is invalid.
        """
        diff_report = await self.audit(template_path, app_keys=app_keys)
        apply_result = await self._applier.apply(
            diff_report, dry_run=dry_run
        )
        return diff_report, apply_result

    async def status(
        self,
        template_path: str | Path,
    ) -> PortfolioStatus:
        """Get the current portfolio status.

        Loads the template, fetches group counts and sync history
        concurrently for each portfolio app.

        Args:
            template_path: Path to the YAML tier template.

        Returns:
            A ``PortfolioStatus`` with one ``AppStatus`` per portfolio app.

        Raises:
            FileNotFoundError: If the template file does not exist.
            ConfigValidationError: If the template is invalid.
        """
        template = load_template(template_path)
        apps = template.portfolio

        # Concurrent fetch of groups and sync history per app
        group_tasks = [
            self._adapter.get_groups(app.app_key) for app in apps
        ]
        history_tasks = [
            self._storage.list_sync_history(app.app_key) for app in apps
        ]

        all_results = await asyncio.gather(
            *group_tasks, *history_tasks
        )

        # Split results: first N are groups, next N are histories
        n_apps = len(apps)
        groups_results: list[list[Group]] = list(all_results[:n_apps])
        history_results = list(all_results[n_apps:])

        app_statuses: list[AppStatus] = []
        for i, app in enumerate(apps):
            groups: list[Group] = groups_results[i]
            history = history_results[i]

            # Extract last_sync from most recent SyncLog
            last_sync = None
            if history:
                last_sync = history[0].timestamp

            app_statuses.append(
                AppStatus(
                    app_key=app.app_key,
                    app_name=app.name,
                    platform=app.platform,
                    group_count=len(groups),
                    last_sync=last_sync,
                )
            )

        return PortfolioStatus(
            mediator=template.mediator,
            apps=app_statuses,
        )

    @staticmethod
    def _filter_apps(
        template: PortfolioConfig,
        app_keys: list[str] | None,
    ) -> list:
        """Filter portfolio apps by the optional app_keys list.

        Args:
            template: The loaded portfolio config.
            app_keys: Optional list of app keys to include. If ``None``,
                all portfolio apps are returned.

        Returns:
            Filtered list of ``PortfolioApp`` entries.
        """
        if app_keys is None:
            return list(template.portfolio)
        key_set = set(app_keys)
        return [app for app in template.portfolio if app.app_key in key_set]

    async def _fetch_groups_concurrent(
        self,
        app_keys: list[str],
    ) -> dict[str, list[Group]]:
        """Fetch groups for multiple apps concurrently via asyncio.gather().

        Args:
            app_keys: List of app keys to fetch groups for.

        Returns:
            Dict mapping app_key to its list of Groups.
        """
        tasks = [self._adapter.get_groups(key) for key in app_keys]
        results = await asyncio.gather(*tasks)
        return dict(zip(app_keys, results))

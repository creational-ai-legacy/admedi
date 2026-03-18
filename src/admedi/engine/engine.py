"""ConfigEngine orchestrator for config-as-code mediation management.

Ties together the Loader, Differ, and Applier into a cohesive async API
that the CLI commands delegate to. Each public method corresponds to a
CLI command:

- ``audit()``   -> ``admedi audit``
- ``sync()``    -> ``admedi sync``
- ``status()``  -> ``admedi status``

All methods use profiles-based app discovery (``load_profiles()``) and
two-layer settings resolution (``resolve_app_tiers()``).

Group data caching strategy: within a single ``audit()`` or ``sync()``
call, the initial ``get_groups()`` result per app is cached and passed to
the Differ. The Applier fetches fresh data independently for its own
pre-write snapshot (ensuring the snapshot reflects state immediately
before writing).

Examples:
    >>> from admedi.engine.engine import ConfigEngine
    >>> # engine = ConfigEngine(adapter=adapter, storage=storage)
    >>> # report = await engine.audit()
    >>> # report, result = await engine.sync(dry_run=True)
"""

from __future__ import annotations

import asyncio
import logging

from typing import Any

from admedi.adapters.mediation import MediationAdapter
from admedi.adapters.storage import StorageAdapter
from admedi.engine.applier import Applier
from admedi.engine.differ import compute_diff
from admedi.engine.loader import (
    Profile,
    load_network_presets,
    load_profiles,
    resolve_app_tiers,
)
from admedi.models.apply_result import AppStatus, ApplyResult, PortfolioStatus
from admedi.models.diff import DiffReport
from admedi.models.enums import Mediator
from admedi.models.group import Group
from admedi.models.portfolio import SyncScope

logger = logging.getLogger(__name__)


class ConfigEngine:
    """Orchestrates config-as-code mediation management operations.

    Provides three async methods corresponding to CLI commands: audit,
    sync, and status. Each method manages the full lifecycle of its
    operation: loading profiles, resolving per-app settings through
    the two-layer chain, fetching remote state, computing diffs,
    and applying changes.

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
        *,
        aliases: list[str] | None = None,
        scope: SyncScope | None = None,
    ) -> DiffReport:
        """Audit the portfolio by comparing per-app settings against live state.

        Loads profiles from ``profiles.yaml``, resolves per-app settings
        through the two-layer chain (per-app file -> ``countries.yaml``),
        fetches remote groups concurrently, and computes diffs.

        When iterating all profiles, aliases without a
        ``settings/{alias}.yaml`` file are skipped with a warning. When
        a specific alias is requested via the ``aliases`` parameter, a
        missing settings file raises ``FileNotFoundError``.

        Args:
            aliases: Optional list of profile aliases to audit. If ``None``,
                all profiles in ``profiles.yaml`` are audited.
            scope: Optional sync scope to control which comparisons are
                performed. When provided and ``scope.networks is True``,
                network presets are loaded and passed to ``compute_diff()``.
                When ``scope.tiers is False``, tier comparisons are skipped.

        Returns:
            A ``DiffReport`` with per-app diff results.

        Raises:
            FileNotFoundError: If a specifically-requested alias has no
                settings file, or if ``profiles.yaml`` does not exist.
            ConfigValidationError: If settings are invalid.
        """
        profiles = load_profiles()
        explicit_filter = aliases is not None

        if explicit_filter:
            target_aliases = aliases
        else:
            target_aliases = list(profiles.keys())

        # Resolve per-app tiers and collect profiles to audit
        resolved: list[tuple[Profile, list]] = []
        for alias in target_aliases:
            profile = profiles.get(alias)
            if profile is None:
                from admedi.exceptions import ConfigValidationError

                available = ", ".join(sorted(profiles.keys()))
                raise ConfigValidationError(
                    f"Unknown profile alias '{alias}'. "
                    f"Available profiles: {available}"
                )

            try:
                tiers = resolve_app_tiers(alias)
            except FileNotFoundError:
                if explicit_filter:
                    raise
                logger.warning(
                    "Skipping %s: no settings file found. "
                    "Run `admedi pull --app %s` first.",
                    alias, alias,
                )
                continue

            resolved.append((profile, tiers))

        if not resolved:
            return DiffReport(app_reports=[])

        # Load network presets if scope includes networks
        network_presets: dict[str, list[dict[str, Any]]] | None = None
        if scope is not None and scope.networks:
            try:
                network_presets = load_network_presets()
            except FileNotFoundError:
                logger.warning(
                    "networks.yaml not found; skipping network comparison"
                )

        # Concurrent fetch of remote groups per app
        groups_by_key = await self._fetch_groups_concurrent(
            [p.app_key for p, _ in resolved]
        )

        # Compute diff per app using cached group data
        app_reports = [
            compute_diff(
                tiers,
                groups_by_key[profile.app_key],
                profile.app_key,
                profile.app_name,
                network_presets=network_presets,
                scope=scope,
            )
            for profile, tiers in resolved
        ]

        return DiffReport(app_reports=app_reports)

    async def sync(
        self,
        *,
        aliases: list[str] | None = None,
        dry_run: bool = True,
        scope: SyncScope | None = None,
    ) -> tuple[DiffReport, ApplyResult]:
        """Sync the portfolio by computing and applying diffs.

        Loads profiles, resolves settings (same as audit), then applies
        changes via the Applier. The Applier fetches fresh group data
        for its own pre-write snapshot independently.

        Args:
            aliases: Optional list of profile aliases to sync. If ``None``,
                all profiles are synced.
            dry_run: If ``True`` (default), compute diffs and return
                results without making any write API calls. If ``False``,
                execute write operations.
            scope: Optional sync scope to control which comparisons and
                writes are performed.

        Returns:
            A tuple of ``(DiffReport, ApplyResult)``. When ``dry_run``
            is ``True``, the ``ApplyResult`` has ``was_dry_run=True``
            and all entries have ``status=DRY_RUN``.

        Raises:
            FileNotFoundError: If profiles or settings files do not exist.
            ConfigValidationError: If settings are invalid.
        """
        diff_report = await self.audit(aliases=aliases, scope=scope)
        apply_result = await self._applier.apply(
            diff_report, dry_run=dry_run, scope=scope,
        )
        return diff_report, apply_result

    async def status(self) -> PortfolioStatus:
        """Get the current portfolio status.

        Loads profiles from ``profiles.yaml``, fetches group counts and
        sync history concurrently for each profile app. The mediator is
        hardcoded to ``Mediator.LEVELPLAY`` (the only supported mediator).

        Returns:
            A ``PortfolioStatus`` with one ``AppStatus`` per profile app.

        Raises:
            FileNotFoundError: If ``profiles.yaml`` does not exist.
            ConfigValidationError: If profiles are invalid.
        """
        profiles = load_profiles()
        profile_list = list(profiles.values())

        # Concurrent fetch of groups and sync history per app
        group_tasks = [
            self._adapter.get_groups(p.app_key) for p in profile_list
        ]
        history_tasks = [
            self._storage.list_sync_history(p.app_key) for p in profile_list
        ]

        all_results = await asyncio.gather(
            *group_tasks, *history_tasks
        )

        # Split results: first N are groups, next N are histories
        n_apps = len(profile_list)
        groups_results: list[list[Group]] = list(all_results[:n_apps])
        history_results = list(all_results[n_apps:])

        app_statuses: list[AppStatus] = []
        for i, profile in enumerate(profile_list):
            groups: list[Group] = groups_results[i]
            history = history_results[i]

            # Extract last_sync from most recent SyncLog
            last_sync = None
            if history:
                last_sync = history[0].timestamp

            app_statuses.append(
                AppStatus(
                    app_key=profile.app_key,
                    app_name=profile.app_name,
                    platform=profile.platform,
                    group_count=len(groups),
                    last_sync=last_sync,
                )
            )

        return PortfolioStatus(
            mediator=Mediator.LEVELPLAY,
            apps=app_statuses,
        )

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

"""Tests for the ConfigEngine orchestrator.

Covers audit(), sync(), snapshot(), status() methods with mocked adapter
and storage. Verifies template loading, concurrent group fetching, diff
computation, applier delegation, app_keys filtering, group data caching,
and correct model output structure.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from admedi.engine.engine import ConfigEngine
from admedi.models.app import App
from admedi.models.apply_result import ApplyStatus, PortfolioStatus
from admedi.models.diff import DiffAction, DiffReport
from admedi.models.enums import AdFormat, Mediator, Platform
from admedi.models.group import Group
from admedi.models.sync_log import SyncLog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_group(
    name: str,
    ad_format: AdFormat = AdFormat.INTERSTITIAL,
    countries: list[str] | None = None,
    position: int = 1,
    group_id: int | None = None,
) -> Group:
    """Create a Group model for testing."""
    return Group.model_validate(
        {
            "groupName": name,
            "adFormat": ad_format.value,
            "countries": countries or ["US"],
            "position": position,
            "groupId": group_id,
        }
    )


def _make_app(
    app_key: str = "app1",
    app_name: str = "Test App",
    platform: str = "Android",
) -> App:
    """Create an App model for testing."""
    return App.model_validate(
        {
            "appKey": app_key,
            "appName": app_name,
            "platform": platform,
        }
    )


def _make_sync_log(
    app_key: str = "app1",
    status: ApplyStatus = ApplyStatus.SUCCESS,
    timestamp: datetime | None = None,
) -> SyncLog:
    """Create a SyncLog model for testing."""
    return SyncLog(
        app_key=app_key,
        timestamp=timestamp or datetime.now(tz=timezone.utc),
        action="sync",
        groups_created=1,
        groups_updated=0,
        status=status,
    )


def _make_mocks() -> tuple[AsyncMock, AsyncMock]:
    """Create mocked adapter and storage for ConfigEngine tests."""
    adapter = AsyncMock()
    storage = AsyncMock()
    # Default: get_groups returns empty list, list_apps returns empty list
    adapter.get_groups.return_value = []
    adapter.list_apps.return_value = []
    storage.list_sync_history.return_value = []
    return adapter, storage


TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "examples" / "shelf-sort-tiers.yaml"
)
"""Path to the real Shelf Sort template for integration-style tests."""


# ---------------------------------------------------------------------------
# Test Classes: audit()
# ---------------------------------------------------------------------------


class TestAudit:
    """Tests for ConfigEngine.audit()."""

    @pytest.mark.asyncio
    async def test_audit_returns_diff_report(self) -> None:
        """audit() loads template and returns a DiffReport."""
        adapter, storage = _make_mocks()
        engine = ConfigEngine(adapter=adapter, storage=storage)

        report = await engine.audit(TEMPLATE_PATH)

        assert isinstance(report, DiffReport)

    @pytest.mark.asyncio
    async def test_audit_report_has_correct_app_count(self) -> None:
        """audit() returns DiffReport with one AppDiffReport per portfolio app."""
        adapter, storage = _make_mocks()
        engine = ConfigEngine(adapter=adapter, storage=storage)

        report = await engine.audit(TEMPLATE_PATH)

        # The Shelf Sort template has 6 apps
        assert len(report.app_reports) == 6

    @pytest.mark.asyncio
    async def test_audit_calls_get_groups_per_app(self) -> None:
        """audit() calls adapter.get_groups() once per portfolio app."""
        adapter, storage = _make_mocks()
        engine = ConfigEngine(adapter=adapter, storage=storage)

        await engine.audit(TEMPLATE_PATH)

        # 6 apps in the Shelf Sort template
        assert adapter.get_groups.call_count == 6

    @pytest.mark.asyncio
    async def test_audit_passes_cached_groups_to_differ(self) -> None:
        """audit() passes cached group data to compute_diff, not re-fetching."""
        adapter, storage = _make_mocks()

        remote_group = _make_group("Tier 1", group_id=100)
        adapter.get_groups.return_value = [remote_group]

        engine = ConfigEngine(adapter=adapter, storage=storage)

        report = await engine.audit(TEMPLATE_PATH)

        # get_groups called only once per app (6 apps), not twice
        assert adapter.get_groups.call_count == 6
        # Each app report should have computed diffs against the returned groups
        for app_report in report.app_reports:
            assert len(app_report.group_diffs) > 0

    @pytest.mark.asyncio
    async def test_audit_app_keys_filters_apps(self) -> None:
        """audit(app_keys=[...]) only processes specified apps."""
        adapter, storage = _make_mocks()
        engine = ConfigEngine(adapter=adapter, storage=storage)

        # Pick the first app_key from the template
        from admedi.engine.loader import load_template as _load

        template = _load(TEMPLATE_PATH)
        first_key = template.portfolio[0].app_key

        report = await engine.audit(TEMPLATE_PATH, app_keys=[first_key])

        assert len(report.app_reports) == 1
        assert report.app_reports[0].app_key == first_key
        assert adapter.get_groups.call_count == 1

    @pytest.mark.asyncio
    async def test_audit_app_keys_none_processes_all(self) -> None:
        """audit(app_keys=None) processes all portfolio apps."""
        adapter, storage = _make_mocks()
        engine = ConfigEngine(adapter=adapter, storage=storage)

        report = await engine.audit(TEMPLATE_PATH, app_keys=None)

        assert len(report.app_reports) == 6


# ---------------------------------------------------------------------------
# Test Classes: sync()
# ---------------------------------------------------------------------------


class TestSync:
    """Tests for ConfigEngine.sync()."""

    @pytest.mark.asyncio
    async def test_sync_dry_run_returns_tuple(self) -> None:
        """sync(dry_run=True) returns (DiffReport, ApplyResult) tuple."""
        adapter, storage = _make_mocks()
        engine = ConfigEngine(adapter=adapter, storage=storage)

        result = await engine.sync(TEMPLATE_PATH, dry_run=True)

        assert isinstance(result, tuple)
        assert len(result) == 2
        diff_report, apply_result = result
        assert isinstance(diff_report, DiffReport)

    @pytest.mark.asyncio
    async def test_sync_dry_run_has_was_dry_run_true(self) -> None:
        """sync(dry_run=True) returns ApplyResult with was_dry_run=True."""
        adapter, storage = _make_mocks()
        engine = ConfigEngine(adapter=adapter, storage=storage)

        _report, apply_result = await engine.sync(TEMPLATE_PATH, dry_run=True)

        assert apply_result.was_dry_run is True

    @pytest.mark.asyncio
    async def test_sync_dry_run_all_statuses_are_dry_run(self) -> None:
        """sync(dry_run=True) produces DRY_RUN status for all apps."""
        adapter, storage = _make_mocks()
        engine = ConfigEngine(adapter=adapter, storage=storage)

        _report, apply_result = await engine.sync(TEMPLATE_PATH, dry_run=True)

        for app_result in apply_result.app_results:
            assert app_result.status == ApplyStatus.DRY_RUN

    @pytest.mark.asyncio
    async def test_sync_dry_run_no_write_calls(self) -> None:
        """sync(dry_run=True) makes no adapter write calls."""
        adapter, storage = _make_mocks()
        engine = ConfigEngine(adapter=adapter, storage=storage)

        await engine.sync(TEMPLATE_PATH, dry_run=True)

        adapter.create_group.assert_not_called()
        adapter.update_group.assert_not_called()
        adapter.delete_group.assert_not_called()
        storage.save_snapshot.assert_not_called()
        storage.save_sync_log.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_not_dry_run_calls_applier(self) -> None:
        """sync(dry_run=False) delegates to the Applier for execution."""
        adapter, storage = _make_mocks()
        engine = ConfigEngine(adapter=adapter, storage=storage)

        _report, apply_result = await engine.sync(
            TEMPLATE_PATH, dry_run=False
        )

        assert apply_result.was_dry_run is False

    @pytest.mark.asyncio
    async def test_sync_app_keys_filters_apps(self) -> None:
        """sync(app_keys=[...]) only processes specified apps."""
        adapter, storage = _make_mocks()
        engine = ConfigEngine(adapter=adapter, storage=storage)

        from admedi.engine.loader import load_template as _load

        template = _load(TEMPLATE_PATH)
        first_key = template.portfolio[0].app_key

        report, result = await engine.sync(
            TEMPLATE_PATH, app_keys=[first_key], dry_run=True
        )

        assert len(report.app_reports) == 1
        assert len(result.app_results) == 1


# ---------------------------------------------------------------------------
# Test Classes: snapshot()
# ---------------------------------------------------------------------------


class TestSnapshot:
    """Tests for ConfigEngine.snapshot()."""

    @pytest.mark.asyncio
    async def test_snapshot_returns_yaml_string(self) -> None:
        """snapshot() returns a YAML string."""
        adapter, storage = _make_mocks()
        adapter.get_groups.return_value = [
            _make_group("Tier 1", group_id=100)
        ]
        adapter.list_apps.return_value = [
            _make_app(app_key="test-key", app_name="Test App"),
        ]

        engine = ConfigEngine(adapter=adapter, storage=storage)

        yaml_str = await engine.snapshot("test-key")

        assert isinstance(yaml_str, str)
        assert "Tier 1" in yaml_str

    @pytest.mark.asyncio
    async def test_snapshot_contains_group_data_by_format(self) -> None:
        """snapshot() returns YAML with groups organized by ad_format."""
        adapter, storage = _make_mocks()
        adapter.get_groups.return_value = [
            _make_group(
                "Interstitial Group",
                ad_format=AdFormat.INTERSTITIAL,
                group_id=1,
            ),
            _make_group(
                "Banner Group",
                ad_format=AdFormat.BANNER,
                group_id=2,
            ),
        ]
        adapter.list_apps.return_value = [
            _make_app(app_key="test-key", app_name="Test App"),
        ]

        engine = ConfigEngine(adapter=adapter, storage=storage)

        yaml_str = await engine.snapshot("test-key")

        assert "interstitial:" in yaml_str
        assert "banner:" in yaml_str
        assert "Interstitial Group" in yaml_str
        assert "Banner Group" in yaml_str

    @pytest.mark.asyncio
    async def test_snapshot_resolves_app_name_from_list_apps(self) -> None:
        """snapshot() calls list_apps() to resolve the app name."""
        adapter, storage = _make_mocks()
        adapter.get_groups.return_value = []
        adapter.list_apps.return_value = [
            _make_app(app_key="my-key", app_name="My App Name"),
        ]

        engine = ConfigEngine(adapter=adapter, storage=storage)

        yaml_str = await engine.snapshot("my-key")

        assert "My App Name" in yaml_str
        adapter.list_apps.assert_called_once()

    @pytest.mark.asyncio
    async def test_snapshot_fallback_name_when_not_found(self) -> None:
        """snapshot() uses app_key as fallback when app not in list_apps()."""
        adapter, storage = _make_mocks()
        adapter.get_groups.return_value = []
        adapter.list_apps.return_value = [
            _make_app(app_key="other-key", app_name="Other App"),
        ]

        engine = ConfigEngine(adapter=adapter, storage=storage)

        yaml_str = await engine.snapshot("unknown-key")

        assert "unknown-key" in yaml_str

    @pytest.mark.asyncio
    async def test_snapshot_writes_to_file_when_output_path_given(
        self, tmp_path: Path
    ) -> None:
        """snapshot(output_path=...) writes YAML to file."""
        adapter, storage = _make_mocks()
        adapter.get_groups.return_value = [
            _make_group("Tier 1", group_id=100)
        ]
        adapter.list_apps.return_value = [
            _make_app(app_key="test-key", app_name="Test App"),
        ]

        engine = ConfigEngine(adapter=adapter, storage=storage)

        output = tmp_path / "snapshot.yaml"
        yaml_str = await engine.snapshot("test-key", output_path=output)

        assert output.exists()
        file_content = output.read_text(encoding="utf-8")
        assert file_content == yaml_str
        assert "Tier 1" in file_content

    @pytest.mark.asyncio
    async def test_snapshot_no_file_when_output_path_none(self) -> None:
        """snapshot(output_path=None) does not write to file."""
        adapter, storage = _make_mocks()
        adapter.get_groups.return_value = []
        adapter.list_apps.return_value = []

        engine = ConfigEngine(adapter=adapter, storage=storage)

        yaml_str = await engine.snapshot("test-key")

        # Returns a string, no file I/O
        assert isinstance(yaml_str, str)


# ---------------------------------------------------------------------------
# Test Classes: status()
# ---------------------------------------------------------------------------


class TestStatus:
    """Tests for ConfigEngine.status()."""

    @pytest.mark.asyncio
    async def test_status_returns_portfolio_status(self) -> None:
        """status() returns a PortfolioStatus."""
        adapter, storage = _make_mocks()
        engine = ConfigEngine(adapter=adapter, storage=storage)

        result = await engine.status(TEMPLATE_PATH)

        assert isinstance(result, PortfolioStatus)

    @pytest.mark.asyncio
    async def test_status_has_one_app_status_per_portfolio_app(self) -> None:
        """status() returns one AppStatus per portfolio app."""
        adapter, storage = _make_mocks()
        engine = ConfigEngine(adapter=adapter, storage=storage)

        result = await engine.status(TEMPLATE_PATH)

        # Shelf Sort template has 6 apps
        assert len(result.apps) == 6

    @pytest.mark.asyncio
    async def test_status_mediator_matches_template(self) -> None:
        """status() PortfolioStatus.mediator matches the template."""
        adapter, storage = _make_mocks()
        engine = ConfigEngine(adapter=adapter, storage=storage)

        result = await engine.status(TEMPLATE_PATH)

        assert result.mediator == Mediator.LEVELPLAY

    @pytest.mark.asyncio
    async def test_status_group_count_reflects_remote_groups(self) -> None:
        """status() AppStatus.group_count reflects remote get_groups() count."""
        adapter, storage = _make_mocks()

        # Return 3 groups for every app
        adapter.get_groups.return_value = [
            _make_group("Tier 1", group_id=1),
            _make_group("Tier 2", group_id=2, position=2),
            _make_group("Tier 3", group_id=3, position=3),
        ]

        engine = ConfigEngine(adapter=adapter, storage=storage)

        result = await engine.status(TEMPLATE_PATH)

        for app_status in result.apps:
            assert app_status.group_count == 3

    @pytest.mark.asyncio
    async def test_status_last_sync_from_most_recent_log(self) -> None:
        """status() extracts last_sync from most recent SyncLog timestamp."""
        adapter, storage = _make_mocks()

        sync_time = datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc)
        storage.list_sync_history.return_value = [
            _make_sync_log(timestamp=sync_time),
        ]

        engine = ConfigEngine(adapter=adapter, storage=storage)

        result = await engine.status(TEMPLATE_PATH)

        for app_status in result.apps:
            assert app_status.last_sync == sync_time

    @pytest.mark.asyncio
    async def test_status_last_sync_none_when_no_history(self) -> None:
        """status() sets last_sync=None when no sync history exists."""
        adapter, storage = _make_mocks()
        storage.list_sync_history.return_value = []

        engine = ConfigEngine(adapter=adapter, storage=storage)

        result = await engine.status(TEMPLATE_PATH)

        for app_status in result.apps:
            assert app_status.last_sync is None

    @pytest.mark.asyncio
    async def test_status_app_name_from_template(self) -> None:
        """status() uses app name from the template portfolio."""
        adapter, storage = _make_mocks()
        engine = ConfigEngine(adapter=adapter, storage=storage)

        from admedi.engine.loader import load_template as _load

        template = _load(TEMPLATE_PATH)

        result = await engine.status(TEMPLATE_PATH)

        expected_names = {app.name for app in template.portfolio}
        actual_names = {app.app_name for app in result.apps}
        assert actual_names == expected_names

    @pytest.mark.asyncio
    async def test_status_platform_from_template(self) -> None:
        """status() uses platform from the template portfolio."""
        adapter, storage = _make_mocks()
        engine = ConfigEngine(adapter=adapter, storage=storage)

        from admedi.engine.loader import load_template as _load

        template = _load(TEMPLATE_PATH)

        result = await engine.status(TEMPLATE_PATH)

        expected_platforms = {app.platform for app in template.portfolio}
        actual_platforms = {app.platform for app in result.apps}
        assert actual_platforms == expected_platforms

    @pytest.mark.asyncio
    async def test_status_concurrent_group_and_history_fetches(self) -> None:
        """status() calls get_groups() and list_sync_history() per app."""
        adapter, storage = _make_mocks()
        engine = ConfigEngine(adapter=adapter, storage=storage)

        await engine.status(TEMPLATE_PATH)

        # 6 apps: 6 get_groups calls + 6 list_sync_history calls
        assert adapter.get_groups.call_count == 6
        assert storage.list_sync_history.call_count == 6


# ---------------------------------------------------------------------------
# Test Classes: Import/Export
# ---------------------------------------------------------------------------


class TestImportExport:
    """Tests for module import and export."""

    def test_config_engine_importable_from_engine(self) -> None:
        """ConfigEngine is importable from admedi.engine."""
        from admedi.engine import ConfigEngine as ImportedEngine

        assert ImportedEngine is ConfigEngine

    def test_config_engine_in_engine_all(self) -> None:
        """ConfigEngine is in engine __all__."""
        import admedi.engine

        assert "ConfigEngine" in admedi.engine.__all__

    def test_engine_all_has_five_entries(self) -> None:
        """Engine __all__ has 5 entries after adding ConfigEngine."""
        import admedi.engine

        assert len(admedi.engine.__all__) == 5

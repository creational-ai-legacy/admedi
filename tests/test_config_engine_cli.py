"""Tests for CLI display module (Step 11) and CLI commands (Step 12).

Step 11 tests verify output content by capturing Console output to a StringIO buffer.
Step 12 tests verify CLI commands using typer.testing.CliRunner with mocked ConfigEngine.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from admedi.cli.display import (
    display_apply_result,
    display_audit_table,
    display_snapshot_info,
    display_status_table,
    display_sync_preview,
)
from admedi.cli.main import app as cli_app
from admedi.models.apply_result import (
    AppApplyResult,
    ApplyResult,
    ApplyStatus,
    AppStatus,
    PortfolioStatus,
)
from admedi.models.diff import (
    AppDiffReport,
    DiffAction,
    DiffReport,
    FieldChange,
    GroupDiff,
)
from admedi.models.enums import AdFormat, Mediator, Platform


def _make_console() -> tuple[Console, StringIO]:
    """Create a Console with a StringIO buffer for test output capture."""
    buf = StringIO()
    console = Console(file=buf, highlight=False, width=120)
    return console, buf


# ---------------------------------------------------------------------------
# Fixtures: realistic test data
# ---------------------------------------------------------------------------


def _make_group_diff(
    action: DiffAction,
    name: str = "US Tier 1",
    group_id: int | None = 100,
    ad_format: AdFormat = AdFormat.INTERSTITIAL,
    changes: list[FieldChange] | None = None,
) -> GroupDiff:
    """Create a GroupDiff with sensible defaults."""
    return GroupDiff(
        action=action,
        group_name=name,
        group_id=group_id,
        ad_format=ad_format,
        changes=changes or [],
        desired_group=None,
    )


def _make_app_diff_report(
    app_key: str = "abc123",
    app_name: str = "Shelf Sort",
    group_diffs: list[GroupDiff] | None = None,
    has_ab_test: bool = False,
) -> AppDiffReport:
    """Create an AppDiffReport with sensible defaults."""
    return AppDiffReport(
        app_key=app_key,
        app_name=app_name,
        group_diffs=group_diffs or [],
        has_ab_test=has_ab_test,
        ab_test_warning=None,
    )


def _make_diff_report(app_reports: list[AppDiffReport] | None = None) -> DiffReport:
    """Create a DiffReport with sensible defaults."""
    return DiffReport(app_reports=app_reports or [])


def _make_app_apply_result(
    app_key: str = "abc123",
    status: ApplyStatus = ApplyStatus.SUCCESS,
    created: int = 0,
    updated: int = 0,
    error: str | None = None,
    warnings: list[str] | None = None,
) -> AppApplyResult:
    """Create an AppApplyResult with sensible defaults."""
    return AppApplyResult(
        app_key=app_key,
        status=status,
        groups_created=created,
        groups_updated=updated,
        error=error,
        warnings=warnings or [],
    )


# ===========================================================================
# display_audit_table
# ===========================================================================


class TestDisplayAuditTable:
    """Tests for display_audit_table()."""

    def test_audit_table_shows_app_names(self) -> None:
        """App names appear in audit output."""
        console, buf = _make_console()
        report = _make_diff_report([
            _make_app_diff_report(
                app_name="Shelf Sort",
                group_diffs=[_make_group_diff(DiffAction.UNCHANGED)],
            ),
        ])

        display_audit_table(report, console=console)
        output = buf.getvalue()

        assert "Shelf Sort" in output

    def test_audit_table_ok_status_when_no_drift(self) -> None:
        """Status shows OK when all groups are unchanged."""
        console, buf = _make_console()
        report = _make_diff_report([
            _make_app_diff_report(
                app_name="Clean App",
                group_diffs=[_make_group_diff(DiffAction.UNCHANGED)],
            ),
        ])

        display_audit_table(report, console=console)
        output = buf.getvalue()

        assert "OK" in output
        assert "All apps in sync" in output

    def test_audit_table_drift_status_with_creates(self) -> None:
        """Status shows DRIFT when creates are needed."""
        console, buf = _make_console()
        report = _make_diff_report([
            _make_app_diff_report(
                app_name="Drifted App",
                group_diffs=[
                    _make_group_diff(DiffAction.CREATE, name="New Group", group_id=None),
                ],
            ),
        ])

        display_audit_table(report, console=console)
        output = buf.getvalue()

        assert "DRIFT" in output
        assert "1 to create" in output

    def test_audit_table_drift_status_with_updates(self) -> None:
        """Status shows DRIFT when updates are needed."""
        console, buf = _make_console()
        report = _make_diff_report([
            _make_app_diff_report(
                app_name="Updated App",
                group_diffs=[
                    _make_group_diff(
                        DiffAction.UPDATE,
                        name="Existing Group",
                        changes=[
                            FieldChange(
                                field="countries",
                                old_value=["US"],
                                new_value=["US", "GB"],
                                description="Added GB",
                            )
                        ],
                    ),
                ],
            ),
        ])

        display_audit_table(report, console=console)
        output = buf.getvalue()

        assert "DRIFT" in output
        assert "1 to update" in output

    def test_audit_table_extra_groups_visually_distinct(self) -> None:
        """EXTRA groups are listed with distinct treatment in issues column."""
        console, buf = _make_console()
        report = _make_diff_report([
            _make_app_diff_report(
                app_name="Extra App",
                group_diffs=[
                    _make_group_diff(DiffAction.UNCHANGED),
                    _make_group_diff(DiffAction.EXTRA, name="Legacy Group"),
                ],
            ),
        ])

        display_audit_table(report, console=console)
        output = buf.getvalue()

        assert "1 extra" in output

    def test_audit_table_multiple_apps(self) -> None:
        """Multiple apps are shown in the same table."""
        console, buf = _make_console()
        report = _make_diff_report([
            _make_app_diff_report(
                app_name="App One",
                group_diffs=[_make_group_diff(DiffAction.UNCHANGED)],
            ),
            _make_app_diff_report(
                app_key="def456",
                app_name="App Two",
                group_diffs=[
                    _make_group_diff(DiffAction.CREATE, group_id=None),
                ],
            ),
        ])

        display_audit_table(report, console=console)
        output = buf.getvalue()

        assert "App One" in output
        assert "App Two" in output

    def test_audit_table_empty_report(self) -> None:
        """Empty report produces panel message without error."""
        console, buf = _make_console()
        report = _make_diff_report([])

        display_audit_table(report, console=console)
        output = buf.getvalue()

        assert "No apps to audit" in output

    def test_audit_table_change_count_summary(self) -> None:
        """Summary line shows total change count."""
        console, buf = _make_console()
        report = _make_diff_report([
            _make_app_diff_report(
                group_diffs=[
                    _make_group_diff(DiffAction.CREATE, group_id=None),
                    _make_group_diff(DiffAction.UPDATE, name="Tier 2"),
                ],
            ),
        ])

        display_audit_table(report, console=console)
        output = buf.getvalue()

        assert "2 change(s)" in output


# ===========================================================================
# display_sync_preview
# ===========================================================================


class TestDisplaySyncPreview:
    """Tests for display_sync_preview()."""

    def test_sync_preview_shows_create_prominently(self) -> None:
        """CREATE actions are shown with CREATE label."""
        console, buf = _make_console()
        report = _make_diff_report([
            _make_app_diff_report(
                app_name="Test App",
                group_diffs=[
                    _make_group_diff(DiffAction.CREATE, name="New Tier", group_id=None),
                ],
            ),
        ])

        display_sync_preview(report, console=console)
        output = buf.getvalue()

        assert "CREATE" in output
        assert "New Tier" in output

    def test_sync_preview_shows_update_prominently(self) -> None:
        """UPDATE actions are shown with UPDATE label and change description."""
        console, buf = _make_console()
        report = _make_diff_report([
            _make_app_diff_report(
                app_name="Test App",
                group_diffs=[
                    _make_group_diff(
                        DiffAction.UPDATE,
                        name="US Tier 1",
                        changes=[
                            FieldChange(
                                field="countries",
                                old_value=["US"],
                                new_value=["US", "GB"],
                                description="Added GB to country list",
                            ),
                        ],
                    ),
                ],
            ),
        ])

        display_sync_preview(report, console=console)
        output = buf.getvalue()

        assert "UPDATE" in output
        assert "Added GB to country list" in output

    def test_sync_preview_extra_groups_distinct(self) -> None:
        """EXTRA groups are shown with distinct visual treatment."""
        console, buf = _make_console()
        report = _make_diff_report([
            _make_app_diff_report(
                app_name="Test App",
                group_diffs=[
                    _make_group_diff(DiffAction.EXTRA, name="Legacy Group"),
                ],
            ),
        ])

        display_sync_preview(report, console=console)
        output = buf.getvalue()

        assert "EXTRA" in output
        assert "Legacy Group" in output

    def test_sync_preview_unchanged_hidden(self) -> None:
        """UNCHANGED groups are not shown in sync preview."""
        console, buf = _make_console()
        report = _make_diff_report([
            _make_app_diff_report(
                app_name="Test App",
                group_diffs=[
                    _make_group_diff(DiffAction.UNCHANGED, name="Stable Group"),
                ],
            ),
        ])

        display_sync_preview(report, console=console)
        output = buf.getvalue()

        assert "Stable Group" not in output

    def test_sync_preview_empty_report(self) -> None:
        """Empty report produces panel message without error."""
        console, buf = _make_console()
        report = _make_diff_report([])

        display_sync_preview(report, console=console)
        output = buf.getvalue()

        assert "No changes to preview" in output

    def test_sync_preview_change_count_summary(self) -> None:
        """Summary line shows create/update breakdown."""
        console, buf = _make_console()
        report = _make_diff_report([
            _make_app_diff_report(
                group_diffs=[
                    _make_group_diff(DiffAction.CREATE, name="T1", group_id=None),
                    _make_group_diff(DiffAction.UPDATE, name="T2"),
                ],
            ),
        ])

        display_sync_preview(report, console=console)
        output = buf.getvalue()

        assert "2 change(s)" in output
        assert "1 create" in output
        assert "1 update" in output

    def test_sync_preview_no_actionable_changes(self) -> None:
        """Only EXTRA groups results in no-changes message."""
        console, buf = _make_console()
        report = _make_diff_report([
            _make_app_diff_report(
                group_diffs=[
                    _make_group_diff(DiffAction.EXTRA, name="Legacy"),
                ],
            ),
        ])

        display_sync_preview(report, console=console)
        output = buf.getvalue()

        assert "No changes to apply" in output


# ===========================================================================
# display_apply_result
# ===========================================================================


class TestDisplayApplyResult:
    """Tests for display_apply_result()."""

    def test_apply_result_shows_success_count(self) -> None:
        """Success count is shown in summary."""
        console, buf = _make_console()
        result = ApplyResult(
            app_results=[
                _make_app_apply_result(status=ApplyStatus.SUCCESS, created=2, updated=1),
            ],
            was_dry_run=False,
        )

        display_apply_result(result, console=console)
        output = buf.getvalue()

        assert "1 success" in output

    def test_apply_result_shows_failed_count(self) -> None:
        """Failed count is shown in summary."""
        console, buf = _make_console()
        result = ApplyResult(
            app_results=[
                _make_app_apply_result(
                    status=ApplyStatus.FAILED,
                    error="API timeout",
                ),
            ],
            was_dry_run=False,
        )

        display_apply_result(result, console=console)
        output = buf.getvalue()

        assert "1 failed" in output
        assert "FAILED" in output

    def test_apply_result_shows_skipped_count(self) -> None:
        """Skipped count is shown in summary."""
        console, buf = _make_console()
        result = ApplyResult(
            app_results=[
                _make_app_apply_result(status=ApplyStatus.SKIPPED),
            ],
            was_dry_run=False,
        )

        display_apply_result(result, console=console)
        output = buf.getvalue()

        assert "1 skipped" in output

    def test_apply_result_shows_warnings(self) -> None:
        """Warnings from AppApplyResult are surfaced in output."""
        console, buf = _make_console()
        result = ApplyResult(
            app_results=[
                _make_app_apply_result(
                    app_key="abc123",
                    status=ApplyStatus.SUCCESS,
                    created=1,
                    warnings=["Country mismatch after write: expected US,GB got US"],
                ),
            ],
            was_dry_run=False,
        )

        display_apply_result(result, console=console)
        output = buf.getvalue()

        assert "Warnings" in output
        assert "Country mismatch" in output
        assert "abc123" in output

    def test_apply_result_dry_run_title(self) -> None:
        """Dry run results show DRY RUN in the title."""
        console, buf = _make_console()
        result = ApplyResult(
            app_results=[
                _make_app_apply_result(status=ApplyStatus.DRY_RUN),
            ],
            was_dry_run=True,
        )

        display_apply_result(result, console=console)
        output = buf.getvalue()

        assert "DRY RUN" in output

    def test_apply_result_empty(self) -> None:
        """Empty result produces panel message without error."""
        console, buf = _make_console()
        result = ApplyResult(app_results=[], was_dry_run=False)

        display_apply_result(result, console=console)
        output = buf.getvalue()

        assert "No apps processed" in output

    def test_apply_result_mixed_statuses(self) -> None:
        """Multiple apps with different statuses all appear."""
        console, buf = _make_console()
        result = ApplyResult(
            app_results=[
                _make_app_apply_result(
                    app_key="app1", status=ApplyStatus.SUCCESS, created=1,
                ),
                _make_app_apply_result(
                    app_key="app2", status=ApplyStatus.FAILED, error="Timeout",
                ),
                _make_app_apply_result(
                    app_key="app3", status=ApplyStatus.SKIPPED,
                ),
            ],
            was_dry_run=False,
        )

        display_apply_result(result, console=console)
        output = buf.getvalue()

        assert "1 success" in output
        assert "1 skipped" in output
        assert "1 failed" in output

    def test_apply_result_shows_error_message(self) -> None:
        """Failed apps show the error message in the status column."""
        console, buf = _make_console()
        result = ApplyResult(
            app_results=[
                _make_app_apply_result(
                    status=ApplyStatus.FAILED,
                    error="API timeout",
                ),
            ],
            was_dry_run=False,
        )

        display_apply_result(result, console=console)
        output = buf.getvalue()

        assert "API timeout" in output


# ===========================================================================
# display_status_table
# ===========================================================================


class TestDisplayStatusTable:
    """Tests for display_status_table()."""

    def test_status_table_shows_platform_column(self) -> None:
        """Platform column appears with correct value."""
        console, buf = _make_console()
        status = PortfolioStatus(
            mediator=Mediator.LEVELPLAY,
            apps=[
                AppStatus(
                    app_key="abc123",
                    app_name="Shelf Sort",
                    platform=Platform.IOS,
                    group_count=5,
                    last_sync=None,
                ),
            ],
        )

        display_status_table(status, console=console)
        output = buf.getvalue()

        assert "iOS" in output
        assert "Platform" in output

    def test_status_table_shows_group_count(self) -> None:
        """Groups column shows the count."""
        console, buf = _make_console()
        status = PortfolioStatus(
            mediator=Mediator.LEVELPLAY,
            apps=[
                AppStatus(
                    app_key="abc123",
                    app_name="Shelf Sort",
                    platform=Platform.ANDROID,
                    group_count=12,
                    last_sync=None,
                ),
            ],
        )

        display_status_table(status, console=console)
        output = buf.getvalue()

        assert "12" in output
        assert "Groups" in output

    def test_status_table_shows_last_sync(self) -> None:
        """Last Sync column shows formatted datetime."""
        console, buf = _make_console()
        sync_time = datetime(2026, 3, 12, 18, 30, 0, tzinfo=timezone.utc)
        status = PortfolioStatus(
            mediator=Mediator.LEVELPLAY,
            apps=[
                AppStatus(
                    app_key="abc123",
                    app_name="Shelf Sort",
                    platform=Platform.IOS,
                    group_count=5,
                    last_sync=sync_time,
                ),
            ],
        )

        display_status_table(status, console=console)
        output = buf.getvalue()

        assert "2026-03-12 18:30" in output

    def test_status_table_never_synced(self) -> None:
        """Last Sync shows 'Never' when no sync has occurred."""
        console, buf = _make_console()
        status = PortfolioStatus(
            mediator=Mediator.LEVELPLAY,
            apps=[
                AppStatus(
                    app_key="abc123",
                    app_name="Shelf Sort",
                    platform=Platform.IOS,
                    group_count=3,
                    last_sync=None,
                ),
            ],
        )

        display_status_table(status, console=console)
        output = buf.getvalue()

        assert "Never" in output

    def test_status_table_shows_app_name(self) -> None:
        """App names appear in status output."""
        console, buf = _make_console()
        status = PortfolioStatus(
            mediator=Mediator.LEVELPLAY,
            apps=[
                AppStatus(
                    app_key="abc123",
                    app_name="Shelf Sort",
                    platform=Platform.IOS,
                    group_count=5,
                    last_sync=None,
                ),
            ],
        )

        display_status_table(status, console=console)
        output = buf.getvalue()

        assert "Shelf Sort" in output

    def test_status_table_shows_mediator(self) -> None:
        """Title includes the mediator name."""
        console, buf = _make_console()
        status = PortfolioStatus(
            mediator=Mediator.LEVELPLAY,
            apps=[
                AppStatus(
                    app_key="abc123",
                    app_name="Test App",
                    platform=Platform.ANDROID,
                    group_count=1,
                    last_sync=None,
                ),
            ],
        )

        display_status_table(status, console=console)
        output = buf.getvalue()

        assert "levelplay" in output

    def test_status_table_empty(self) -> None:
        """Empty portfolio produces panel message without error."""
        console, buf = _make_console()
        status = PortfolioStatus(
            mediator=Mediator.LEVELPLAY,
            apps=[],
        )

        display_status_table(status, console=console)
        output = buf.getvalue()

        assert "No apps in portfolio" in output

    def test_status_table_multiple_apps(self) -> None:
        """Multiple apps are listed in the table."""
        console, buf = _make_console()
        status = PortfolioStatus(
            mediator=Mediator.LEVELPLAY,
            apps=[
                AppStatus(
                    app_key="abc123",
                    app_name="App One",
                    platform=Platform.IOS,
                    group_count=5,
                    last_sync=None,
                ),
                AppStatus(
                    app_key="def456",
                    app_name="App Two",
                    platform=Platform.ANDROID,
                    group_count=3,
                    last_sync=None,
                ),
            ],
        )

        display_status_table(status, console=console)
        output = buf.getvalue()

        assert "App One" in output
        assert "App Two" in output


# ===========================================================================
# display_snapshot_info
# ===========================================================================


class TestDisplaySnapshotInfo:
    """Tests for display_snapshot_info()."""

    def test_snapshot_info_shows_path(self) -> None:
        """Output contains the file path."""
        console, buf = _make_console()

        display_snapshot_info("/tmp/snapshot.yaml", "Shelf Sort", console=console)
        output = buf.getvalue()

        assert "/tmp/snapshot.yaml" in output

    def test_snapshot_info_shows_app_name(self) -> None:
        """Output contains the app name."""
        console, buf = _make_console()

        display_snapshot_info("/tmp/snapshot.yaml", "Shelf Sort", console=console)
        output = buf.getvalue()

        assert "Shelf Sort" in output

    def test_snapshot_info_shows_title(self) -> None:
        """Output contains the Snapshot Export title."""
        console, buf = _make_console()

        display_snapshot_info("/tmp/snapshot.yaml", "Test App", console=console)
        output = buf.getvalue()

        assert "Snapshot Export" in output


# ===========================================================================
# Step 12: CLI Command Tests
# ===========================================================================

runner = CliRunner()


def _make_no_drift_report() -> DiffReport:
    """Create a DiffReport with no drift (all unchanged)."""
    return DiffReport(
        app_reports=[
            AppDiffReport(
                app_key="abc123",
                app_name="Shelf Sort",
                group_diffs=[
                    GroupDiff(
                        action=DiffAction.UNCHANGED,
                        group_name="US Tier 1",
                        group_id=100,
                        ad_format=AdFormat.INTERSTITIAL,
                        changes=[],
                        desired_group=None,
                    ),
                ],
                has_ab_test=False,
                ab_test_warning=None,
            ),
        ],
    )


def _make_drift_report() -> DiffReport:
    """Create a DiffReport with drift (CREATE action)."""
    return DiffReport(
        app_reports=[
            AppDiffReport(
                app_key="abc123",
                app_name="Shelf Sort",
                group_diffs=[
                    GroupDiff(
                        action=DiffAction.CREATE,
                        group_name="New Tier",
                        group_id=None,
                        ad_format=AdFormat.INTERSTITIAL,
                        changes=[],
                        desired_group=None,
                    ),
                ],
                has_ab_test=False,
                ab_test_warning=None,
            ),
        ],
    )


def _make_apply_result(dry_run: bool = False) -> ApplyResult:
    """Create a successful ApplyResult."""
    return ApplyResult(
        app_results=[
            AppApplyResult(
                app_key="abc123",
                status=ApplyStatus.DRY_RUN if dry_run else ApplyStatus.SUCCESS,
                groups_created=1,
                groups_updated=0,
                error=None,
            ),
        ],
        was_dry_run=dry_run,
    )


def _make_portfolio_status() -> PortfolioStatus:
    """Create a sample PortfolioStatus."""
    return PortfolioStatus(
        mediator=Mediator.LEVELPLAY,
        apps=[
            AppStatus(
                app_key="abc123",
                app_name="Shelf Sort",
                platform=Platform.IOS,
                group_count=5,
                last_sync=None,
            ),
        ],
    )


def _mock_credential() -> MagicMock:
    """Create a mock credential that passes through adapter construction."""
    cred = MagicMock()
    cred.secret_key = "test-key"
    cred.refresh_token = "test-token"
    cred.mediator = Mediator.LEVELPLAY
    return cred


class TestAuditCommand:
    """Tests for the 'admedi audit' CLI command."""

    @patch("admedi.cli.main.ConfigEngine")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_audit_no_drift_exits_0(
        self, mock_load_cred: MagicMock, mock_adapter_cls: MagicMock,
        mock_engine_cls: MagicMock,
    ) -> None:
        """Exit code 0 when no drift detected."""
        mock_load_cred.return_value = _mock_credential()
        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.audit = AsyncMock(return_value=_make_no_drift_report())
        mock_engine_cls.return_value = mock_engine

        result = runner.invoke(cli_app, ["audit", "--config", "fake.yaml"])

        assert result.exit_code == 0

    @patch("admedi.cli.main.ConfigEngine")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_audit_drift_exits_1(
        self, mock_load_cred: MagicMock, mock_adapter_cls: MagicMock,
        mock_engine_cls: MagicMock,
    ) -> None:
        """Exit code 1 when drift detected."""
        mock_load_cred.return_value = _mock_credential()
        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.audit = AsyncMock(return_value=_make_drift_report())
        mock_engine_cls.return_value = mock_engine

        result = runner.invoke(cli_app, ["audit", "--config", "fake.yaml"])

        assert result.exit_code == 1

    @patch("admedi.cli.main.ConfigEngine")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_audit_format_json_produces_valid_json(
        self, mock_load_cred: MagicMock, mock_adapter_cls: MagicMock,
        mock_engine_cls: MagicMock,
    ) -> None:
        """--format json produces valid JSON output."""
        mock_load_cred.return_value = _mock_credential()
        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.audit = AsyncMock(return_value=_make_no_drift_report())
        mock_engine_cls.return_value = mock_engine

        result = runner.invoke(
            cli_app, ["audit", "--config", "fake.yaml", "--format", "json"]
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "app_reports" in data

    @patch("admedi.cli.main.ConfigEngine")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_audit_with_app_filter(
        self, mock_load_cred: MagicMock, mock_adapter_cls: MagicMock,
        mock_engine_cls: MagicMock,
    ) -> None:
        """--app flag passes app_keys to engine.audit()."""
        mock_load_cred.return_value = _mock_credential()
        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.audit = AsyncMock(return_value=_make_no_drift_report())
        mock_engine_cls.return_value = mock_engine

        runner.invoke(
            cli_app, ["audit", "--config", "fake.yaml", "--app", "abc123"]
        )

        mock_engine.audit.assert_awaited_once()
        call_kwargs = mock_engine.audit.call_args
        assert call_kwargs.kwargs["app_keys"] == ["abc123"]

    @patch("admedi.cli.main.load_credential_from_env")
    def test_audit_missing_credentials_exits_2(
        self, mock_load_cred: MagicMock,
    ) -> None:
        """Missing credentials produce exit code 2 with clear error."""
        from admedi.exceptions import AuthError

        mock_load_cred.side_effect = AuthError(
            "Missing required environment variable(s): LEVELPLAY_SECRET_KEY"
        )

        result = runner.invoke(cli_app, ["audit", "--config", "fake.yaml"])

        assert result.exit_code == 2
        assert "LEVELPLAY_SECRET_KEY" in result.output


class TestSyncTiersCommand:
    """Tests for the 'admedi sync-tiers' CLI command."""

    @patch("admedi.cli.main.ConfigEngine")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_sync_dry_run_exits_1_when_drift(
        self, mock_load_cred: MagicMock, mock_adapter_cls: MagicMock,
        mock_engine_cls: MagicMock,
    ) -> None:
        """--dry-run with drift exits code 1 and never calls sync."""
        mock_load_cred.return_value = _mock_credential()
        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.audit = AsyncMock(return_value=_make_drift_report())
        mock_engine.sync = AsyncMock()
        mock_engine_cls.return_value = mock_engine

        result = runner.invoke(
            cli_app, ["sync-tiers", "--config", "fake.yaml", "--dry-run"]
        )

        assert result.exit_code == 1
        mock_engine.sync.assert_not_awaited()

    @patch("admedi.cli.main.ConfigEngine")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_sync_no_drift_exits_0(
        self, mock_load_cred: MagicMock, mock_adapter_cls: MagicMock,
        mock_engine_cls: MagicMock,
    ) -> None:
        """No drift exits code 0 without applying."""
        mock_load_cred.return_value = _mock_credential()
        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.audit = AsyncMock(return_value=_make_no_drift_report())
        mock_engine.sync = AsyncMock()
        mock_engine_cls.return_value = mock_engine

        result = runner.invoke(
            cli_app, ["sync-tiers", "--config", "fake.yaml"]
        )

        assert result.exit_code == 0
        mock_engine.sync.assert_not_awaited()

    @patch("admedi.cli.main.ConfigEngine")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_sync_yes_applies_changes(
        self, mock_load_cred: MagicMock, mock_adapter_cls: MagicMock,
        mock_engine_cls: MagicMock,
    ) -> None:
        """--yes skips confirmation and applies changes; exits 0."""
        mock_load_cred.return_value = _mock_credential()
        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        drift_report = _make_drift_report()
        apply_result = _make_apply_result()

        mock_engine = MagicMock()
        mock_engine.audit = AsyncMock(return_value=drift_report)
        mock_engine.sync = AsyncMock(return_value=(drift_report, apply_result))
        mock_engine_cls.return_value = mock_engine

        result = runner.invoke(
            cli_app, ["sync-tiers", "--config", "fake.yaml", "--yes"]
        )

        assert result.exit_code == 0
        mock_engine.sync.assert_awaited_once()

    @patch("admedi.cli.main.ConfigEngine")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_sync_format_json_produces_valid_json(
        self, mock_load_cred: MagicMock, mock_adapter_cls: MagicMock,
        mock_engine_cls: MagicMock,
    ) -> None:
        """--format json on sync-tiers produces valid JSON with both keys."""
        mock_load_cred.return_value = _mock_credential()
        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        drift_report = _make_drift_report()
        apply_result = _make_apply_result()

        mock_engine = MagicMock()
        mock_engine.audit = AsyncMock(return_value=drift_report)
        mock_engine.sync = AsyncMock(return_value=(drift_report, apply_result))
        mock_engine_cls.return_value = mock_engine

        result = runner.invoke(
            cli_app,
            ["sync-tiers", "--config", "fake.yaml", "--yes", "--format", "json"],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "diff_report" in data
        assert "apply_result" in data

    @patch("admedi.cli.main.ConfigEngine")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_sync_dry_run_format_json_has_null_apply(
        self, mock_load_cred: MagicMock, mock_adapter_cls: MagicMock,
        mock_engine_cls: MagicMock,
    ) -> None:
        """--dry-run --format json produces JSON with null apply_result."""
        mock_load_cred.return_value = _mock_credential()
        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.audit = AsyncMock(return_value=_make_drift_report())
        mock_engine_cls.return_value = mock_engine

        result = runner.invoke(
            cli_app,
            ["sync-tiers", "--config", "fake.yaml", "--dry-run", "--format", "json"],
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "diff_report" in data
        assert data["apply_result"] is None

    @patch("admedi.cli.main.ConfigEngine")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_sync_user_declines_exits_1(
        self, mock_load_cred: MagicMock, mock_adapter_cls: MagicMock,
        mock_engine_cls: MagicMock,
    ) -> None:
        """User declining confirmation exits code 1."""
        mock_load_cred.return_value = _mock_credential()
        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.audit = AsyncMock(return_value=_make_drift_report())
        mock_engine.sync = AsyncMock()
        mock_engine_cls.return_value = mock_engine

        result = runner.invoke(
            cli_app, ["sync-tiers", "--config", "fake.yaml"], input="n\n"
        )

        assert result.exit_code == 1
        mock_engine.sync.assert_not_awaited()


class TestSnapshotCommand:
    """Tests for the 'admedi snapshot' CLI command."""

    @patch("admedi.cli.main.ConfigEngine")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_snapshot_single_app_exits_0(
        self, mock_load_cred: MagicMock, mock_adapter_cls: MagicMock,
        mock_engine_cls: MagicMock,
    ) -> None:
        """Snapshot single app exits 0 and calls engine.snapshot()."""
        mock_load_cred.return_value = _mock_credential()
        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.snapshot = AsyncMock(return_value="yaml: data")
        mock_engine_cls.return_value = mock_engine

        result = runner.invoke(cli_app, ["snapshot", "--app", "test-key"])

        assert result.exit_code == 0
        mock_engine.snapshot.assert_awaited_once()

    def test_snapshot_app_and_all_mutually_exclusive(self) -> None:
        """--app and --all are mutually exclusive, producing exit code 2."""
        # Must patch credential loading to avoid real env lookup
        with patch(
            "admedi.cli.main.load_credential_from_env",
            return_value=_mock_credential(),
        ):
            result = runner.invoke(
                cli_app,
                ["snapshot", "--app", "test-key", "--all", "--config", "fake.yaml"],
            )

        assert result.exit_code == 2
        assert "mutually exclusive" in result.output

    def test_snapshot_neither_app_nor_all_exits_2(self) -> None:
        """Missing both --app and --all exits code 2."""
        with patch(
            "admedi.cli.main.load_credential_from_env",
            return_value=_mock_credential(),
        ):
            result = runner.invoke(cli_app, ["snapshot"])

        assert result.exit_code == 2
        assert "either --app or --all" in result.output

    def test_snapshot_all_requires_config(self) -> None:
        """--all without --config exits code 2."""
        with patch(
            "admedi.cli.main.load_credential_from_env",
            return_value=_mock_credential(),
        ):
            result = runner.invoke(cli_app, ["snapshot", "--all"])

        assert result.exit_code == 2
        assert "--config is required" in result.output

    @patch("admedi.cli.main.ConfigEngine")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    @patch("admedi.cli.main.load_template")
    def test_snapshot_all_produces_multiple_files(
        self, mock_load_tpl: MagicMock, mock_load_cred: MagicMock,
        mock_adapter_cls: MagicMock, mock_engine_cls: MagicMock,
    ) -> None:
        """--all snapshots each portfolio app with per-app output paths."""
        from admedi.models.portfolio import PortfolioApp, PortfolioConfig, PortfolioTier

        mock_load_cred.return_value = _mock_credential()
        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        portfolio_config = PortfolioConfig(
            mediator=Mediator.LEVELPLAY,
            portfolio=[
                PortfolioApp(app_key="app1", name="App One", platform=Platform.IOS),
                PortfolioApp(app_key="app2", name="App Two", platform=Platform.ANDROID),
            ],
            tiers=[
                PortfolioTier(
                    name="Tier 1", countries=["US"], position=1,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
                PortfolioTier(
                    name="Default", countries=["*"], position=2,
                    is_default=True, ad_formats=[AdFormat.INTERSTITIAL],
                ),
            ],
        )
        mock_load_tpl.return_value = portfolio_config

        mock_engine = MagicMock()
        mock_engine.snapshot = AsyncMock(return_value="yaml: data")
        mock_engine_cls.return_value = mock_engine

        result = runner.invoke(
            cli_app, ["snapshot", "--all", "--config", "fake.yaml"]
        )

        assert result.exit_code == 0
        assert mock_engine.snapshot.await_count == 2

    @patch("admedi.cli.main.load_credential_from_env")
    def test_snapshot_missing_credentials_exits_2(
        self, mock_load_cred: MagicMock,
    ) -> None:
        """Missing credentials on snapshot produce exit code 2."""
        from admedi.exceptions import AuthError

        mock_load_cred.side_effect = AuthError(
            "Missing required environment variable(s): LEVELPLAY_SECRET_KEY"
        )

        result = runner.invoke(cli_app, ["snapshot", "--app", "test-key"])

        assert result.exit_code == 2
        assert "LEVELPLAY_SECRET_KEY" in result.output


class TestStatusCommand:
    """Tests for the 'admedi status' CLI command."""

    @patch("admedi.cli.main.ConfigEngine")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_status_exits_0(
        self, mock_load_cred: MagicMock, mock_adapter_cls: MagicMock,
        mock_engine_cls: MagicMock,
    ) -> None:
        """Status exits code 0 on success."""
        mock_load_cred.return_value = _mock_credential()
        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.status = AsyncMock(return_value=_make_portfolio_status())
        mock_engine_cls.return_value = mock_engine

        result = runner.invoke(cli_app, ["status", "--config", "fake.yaml"])

        assert result.exit_code == 0

    @patch("admedi.cli.main.ConfigEngine")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_status_format_json_produces_valid_json(
        self, mock_load_cred: MagicMock, mock_adapter_cls: MagicMock,
        mock_engine_cls: MagicMock,
    ) -> None:
        """--format json on status produces valid JSON output."""
        mock_load_cred.return_value = _mock_credential()
        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.status = AsyncMock(return_value=_make_portfolio_status())
        mock_engine_cls.return_value = mock_engine

        result = runner.invoke(
            cli_app, ["status", "--config", "fake.yaml", "--format", "json"]
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "apps" in data
        assert "mediator" in data

    @patch("admedi.cli.main.load_credential_from_env")
    def test_status_missing_credentials_exits_2(
        self, mock_load_cred: MagicMock,
    ) -> None:
        """Missing credentials on status produce exit code 2."""
        from admedi.exceptions import AuthError

        mock_load_cred.side_effect = AuthError(
            "Missing required environment variable(s): LEVELPLAY_REFRESH_TOKEN"
        )

        result = runner.invoke(cli_app, ["status", "--config", "fake.yaml"])

        assert result.exit_code == 2
        assert "LEVELPLAY_REFRESH_TOKEN" in result.output


class TestCliCredentialErrors:
    """Tests for credential error handling across all commands."""

    @patch("admedi.cli.main.load_credential_from_env")
    def test_all_commands_show_clear_credential_error(
        self, mock_load_cred: MagicMock,
    ) -> None:
        """All commands produce clear error message for missing credentials."""
        from admedi.exceptions import AuthError

        mock_load_cred.side_effect = AuthError(
            "Missing required environment variable(s): "
            "LEVELPLAY_SECRET_KEY, LEVELPLAY_REFRESH_TOKEN"
        )

        for cmd_args in [
            ["audit", "--config", "fake.yaml"],
            ["sync-tiers", "--config", "fake.yaml"],
            ["snapshot", "--app", "key"],
            ["status", "--config", "fake.yaml"],
        ]:
            result = runner.invoke(cli_app, cmd_args)
            assert result.exit_code == 2, f"Failed for: {cmd_args}"
            assert "LEVELPLAY_SECRET_KEY" in result.output, f"Failed for: {cmd_args}"
            assert "Set LEVELPLAY_SECRET_KEY" in result.output, f"Failed for: {cmd_args}"

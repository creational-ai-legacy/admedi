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
    display_pull,
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
    app_name: str = "",
    status: ApplyStatus = ApplyStatus.SUCCESS,
    created: int = 0,
    updated: int = 0,
    deleted: int = 0,
    error: str | None = None,
    warnings: list[str] | None = None,
) -> AppApplyResult:
    """Create an AppApplyResult with sensible defaults."""
    return AppApplyResult(
        app_key=app_key,
        app_name=app_name,
        status=status,
        groups_created=created,
        groups_updated=updated,
        groups_deleted=deleted,
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
        """Summary line shows create/update/delete breakdown."""
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
        assert "0 delete" in output

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

    def test_sync_preview_delete_shown_bold_not_dimmed(self) -> None:
        """DELETE actions are shown with bold red styling, not dimmed."""
        console, buf = _make_console()
        report = _make_diff_report([
            _make_app_diff_report(
                app_name="Test App",
                group_diffs=[
                    _make_group_diff(DiffAction.DELETE, name="Obsolete Group"),
                ],
            ),
        ])

        display_sync_preview(report, console=console)
        output = buf.getvalue()

        assert "DELETE" in output
        assert "Obsolete Group" in output
        # Must not show the old "reserved" text
        assert "reserved" not in output

    def test_sync_preview_delete_sets_has_any_actionable(self) -> None:
        """DELETE actions set has_any_actionable, showing summary not no-changes."""
        console, buf = _make_console()
        report = _make_diff_report([
            _make_app_diff_report(
                group_diffs=[
                    _make_group_diff(DiffAction.DELETE, name="Old Group"),
                ],
            ),
        ])

        display_sync_preview(report, console=console)
        output = buf.getvalue()

        # Should show change count summary, not "No changes to apply"
        assert "No changes to apply" not in output
        assert "change(s)" in output
        assert "1 delete" in output

    def test_sync_preview_summary_includes_delete_count(self) -> None:
        """Summary line includes create, update, and delete counts."""
        console, buf = _make_console()
        report = _make_diff_report([
            _make_app_diff_report(
                group_diffs=[
                    _make_group_diff(DiffAction.CREATE, name="T1", group_id=None),
                    _make_group_diff(DiffAction.DELETE, name="T2"),
                    _make_group_diff(DiffAction.DELETE, name="T3"),
                ],
            ),
        ])

        display_sync_preview(report, console=console)
        output = buf.getvalue()

        assert "3 change(s)" in output
        assert "1 create" in output
        assert "0 update" in output
        assert "2 delete" in output

    def test_sync_preview_delete_only_shows_summary(self) -> None:
        """A report with only DELETE diffs shows summary, not no-changes."""
        console, buf = _make_console()
        report = _make_diff_report([
            _make_app_diff_report(
                group_diffs=[
                    _make_group_diff(DiffAction.DELETE, name="D1"),
                    _make_group_diff(DiffAction.DELETE, name="D2"),
                    _make_group_diff(DiffAction.DELETE, name="D3"),
                ],
            ),
        ])

        display_sync_preview(report, console=console)
        output = buf.getvalue()

        assert "3 change(s)" in output
        assert "0 create" in output
        assert "0 update" in output
        assert "3 delete" in output
        assert "No changes to apply" not in output


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

    def test_apply_result_shows_app_name_when_set(self) -> None:
        """Apply Results table shows app_name instead of app_key when app_name is set."""
        console, buf = _make_console()
        result = ApplyResult(
            app_results=[
                _make_app_apply_result(
                    app_key="abc123",
                    app_name="Shelf Sort",
                    status=ApplyStatus.SUCCESS,
                    created=2,
                    updated=1,
                ),
            ],
            was_dry_run=False,
        )

        display_apply_result(result, console=console)
        output = buf.getvalue()

        assert "Shelf Sort" in output
        assert "abc123" not in output

    def test_apply_result_falls_back_to_app_key_when_name_empty(self) -> None:
        """Apply Results table falls back to app_key when app_name is empty."""
        console, buf = _make_console()
        result = ApplyResult(
            app_results=[
                _make_app_apply_result(
                    app_key="abc123",
                    app_name="",
                    status=ApplyStatus.SUCCESS,
                    created=1,
                ),
            ],
            was_dry_run=False,
        )

        display_apply_result(result, console=console)
        output = buf.getvalue()

        assert "abc123" in output

    def test_apply_result_warnings_show_app_name(self) -> None:
        """Warnings section shows app_name instead of app_key."""
        console, buf = _make_console()
        result = ApplyResult(
            app_results=[
                _make_app_apply_result(
                    app_key="abc123",
                    app_name="Shelf Sort",
                    status=ApplyStatus.SUCCESS,
                    created=1,
                    warnings=["Country mismatch after write"],
                ),
            ],
            was_dry_run=False,
        )

        display_apply_result(result, console=console)
        output = buf.getvalue()

        assert "Shelf Sort" in output
        assert "Country mismatch" in output
        assert "abc123" not in output

    def test_apply_result_has_deleted_column(self) -> None:
        """Apply Results table includes a 'Deleted' column header."""
        console, buf = _make_console()
        result = ApplyResult(
            app_results=[
                _make_app_apply_result(
                    status=ApplyStatus.SUCCESS,
                    created=1,
                    updated=2,
                    deleted=3,
                ),
            ],
            was_dry_run=False,
        )

        display_apply_result(result, console=console)
        output = buf.getvalue()

        assert "Deleted" in output

    def test_apply_result_shows_groups_deleted_value(self) -> None:
        """Apply Results table shows groups_deleted value in the Deleted column."""
        console, buf = _make_console()
        result = ApplyResult(
            app_results=[
                _make_app_apply_result(
                    app_name="Shelf Sort",
                    status=ApplyStatus.SUCCESS,
                    created=0,
                    updated=0,
                    deleted=5,
                ),
            ],
            was_dry_run=False,
        )

        display_apply_result(result, console=console)
        output = buf.getvalue()

        # The table should contain the deleted count
        assert "5" in output
        assert "Deleted" in output


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


# ---------------------------------------------------------------------------
# _resolve_profile() tests
# ---------------------------------------------------------------------------


class TestResolveProfile:
    """Tests for _resolve_profile() in CLI."""

    @patch("admedi.cli.main.load_profiles")
    def test_returns_profile_for_known_alias(self, mock_load: MagicMock) -> None:
        """_resolve_profile() returns a Profile object for a known alias."""
        from admedi.cli.main import _resolve_profile
        from admedi.engine.loader import Profile

        mock_load.return_value = {
            "hexar-ios": Profile(
                alias="hexar-ios", app_key="676996cd",
                app_name="Hexar.io iOS", platform=Platform.IOS,
            ),
        }

        result = _resolve_profile("hexar-ios")
        assert isinstance(result, Profile)
        assert result.app_key == "676996cd"
        assert result.platform == Platform.IOS

    @patch("admedi.cli.main.load_profiles")
    def test_raises_for_unknown_alias(self, mock_load: MagicMock) -> None:
        """_resolve_profile() raises ConfigValidationError for unknown alias."""
        from admedi.cli.main import _resolve_profile
        from admedi.engine.loader import Profile
        from admedi.exceptions import ConfigValidationError

        mock_load.return_value = {
            "hexar-ios": Profile(
                alias="hexar-ios", app_key="676996cd",
                app_name="Hexar.io iOS", platform=Platform.IOS,
            ),
        }

        with pytest.raises(ConfigValidationError, match="Unknown profile alias 'bad-alias'"):
            _resolve_profile("bad-alias")

    @patch("admedi.cli.main.load_profiles")
    def test_error_lists_available_profiles(self, mock_load: MagicMock) -> None:
        """Error message from _resolve_profile() lists available profile aliases."""
        from admedi.cli.main import _resolve_profile
        from admedi.engine.loader import Profile
        from admedi.exceptions import ConfigValidationError

        mock_load.return_value = {
            "hexar-ios": Profile(
                alias="hexar-ios", app_key="676996cd",
                app_name="Hexar.io iOS", platform=Platform.IOS,
            ),
            "ss-ios": Profile(
                alias="ss-ios", app_key="1f93a90ad",
                app_name="Shelf Sort iOS", platform=Platform.IOS,
            ),
        }

        with pytest.raises(ConfigValidationError) as exc_info:
            _resolve_profile("nonexistent")
        assert "hexar-ios" in exc_info.value.message
        assert "ss-ios" in exc_info.value.message

    @patch("admedi.cli.main.load_profiles")
    def test_callers_access_app_key(self, mock_load: MagicMock) -> None:
        """Callers can access profile.app_key for backward compat."""
        from admedi.cli.main import _resolve_profile
        from admedi.engine.loader import Profile

        mock_load.return_value = {
            "hexar-ios": Profile(
                alias="hexar-ios", app_key="676996cd",
                app_name="Hexar.io iOS", platform=Platform.IOS,
            ),
        }

        profile = _resolve_profile("hexar-ios")
        # This is the pattern used by sync/audit after Step 3
        app_key = profile.app_key
        assert app_key == "676996cd"


# ---------------------------------------------------------------------------
# Pull command tests
# ---------------------------------------------------------------------------


class TestPullCommand:
    """Tests for the 'admedi pull' CLI command."""

    @patch("admedi.cli.main.save_raw_snapshot")
    @patch("admedi.cli.main.write_yaml_file")
    @patch("admedi.cli.main.extract_network_presets")
    @patch("admedi.cli.main.generate_app_settings")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main._resolve_profile")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_pull_invokes_correct_flow(
        self, mock_load_cred: MagicMock, mock_resolve_profile: MagicMock,
        mock_adapter_cls: MagicMock, mock_gen_settings: MagicMock,
        mock_extract_presets: MagicMock, mock_write_yaml: MagicMock,
        mock_save_raw: MagicMock,
    ) -> None:
        """pull --app hexar-ios invokes the full pull flow."""
        from admedi.engine.loader import Profile
        from admedi.models.app import App

        mock_load_cred.return_value = _mock_credential()
        mock_resolve_profile.return_value = Profile(
            alias="hexar-ios", app_key="676996cd",
            app_name="Hexar.io iOS", platform=Platform.IOS,
        )

        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_adapter.get_groups.return_value = []
        mock_adapter.list_apps.return_value = [
            App(app_key="676996cd", app_name="Hexar.io iOS", platform=Platform.IOS),
        ]

        mock_gen_settings.return_value = ("settings/hexar-ios.yaml", [])
        mock_extract_presets.return_value = {}
        mock_save_raw.return_value = "snapshots/hexar-ios.yaml"

        result = runner.invoke(cli_app, ["pull", "--app", "hexar-ios"])

        assert result.exit_code == 0
        mock_resolve_profile.assert_called_once_with("hexar-ios")
        mock_adapter.get_groups.assert_awaited_once_with("676996cd")
        mock_gen_settings.assert_called_once()
        mock_save_raw.assert_called_once()

    @patch("admedi.cli.main._resolve_profile")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_pull_unknown_alias_exits_2(
        self, mock_load_cred: MagicMock, mock_resolve_profile: MagicMock,
    ) -> None:
        """pull --app unknown-alias exits 2 with error message."""
        from admedi.exceptions import ConfigValidationError

        mock_load_cred.return_value = _mock_credential()
        mock_resolve_profile.side_effect = ConfigValidationError(
            "Unknown profile alias 'unknown-alias'. Available profiles: hexar-ios, ss-ios"
        )

        result = runner.invoke(cli_app, ["pull", "--app", "unknown-alias"])

        assert result.exit_code == 2
        assert "Unknown profile alias" in result.output

    @patch("admedi.cli.main.save_raw_snapshot")
    @patch("admedi.cli.main.write_yaml_file")
    @patch("admedi.cli.main.extract_network_presets")
    @patch("admedi.cli.main.generate_app_settings")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main._resolve_profile")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_pull_metadata_drift_warning(
        self, mock_load_cred: MagicMock, mock_resolve_profile: MagicMock,
        mock_adapter_cls: MagicMock, mock_gen_settings: MagicMock,
        mock_extract_presets: MagicMock, mock_write_yaml: MagicMock,
        mock_save_raw: MagicMock,
    ) -> None:
        """Profile metadata drift warning appears when API returns different app_name."""
        from admedi.engine.loader import Profile
        from admedi.models.app import App

        mock_load_cred.return_value = _mock_credential()
        mock_resolve_profile.return_value = Profile(
            alias="hexar-ios", app_key="676996cd",
            app_name="Old Name", platform=Platform.IOS,
        )

        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_adapter.get_groups.return_value = []
        mock_adapter.list_apps.return_value = [
            App(app_key="676996cd", app_name="New Name", platform=Platform.IOS),
        ]

        mock_gen_settings.return_value = ("settings/hexar-ios.yaml", [])
        mock_extract_presets.return_value = {}
        mock_save_raw.return_value = "snapshots/hexar-ios.yaml"

        result = runner.invoke(cli_app, ["pull", "--app", "hexar-ios"])

        assert result.exit_code == 0
        assert "Warning" in result.output
        assert "Old Name" in result.output
        assert "New Name" in result.output

    @patch("admedi.cli.main.save_raw_snapshot")
    @patch("admedi.cli.main.write_yaml_file")
    @patch("admedi.cli.main.extract_network_presets")
    @patch("admedi.cli.main.generate_app_settings")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main._resolve_profile")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_pull_writes_networks_file_when_presets_exist(
        self, mock_load_cred: MagicMock, mock_resolve_profile: MagicMock,
        mock_adapter_cls: MagicMock, mock_gen_settings: MagicMock,
        mock_extract_presets: MagicMock, mock_write_yaml: MagicMock,
        mock_save_raw: MagicMock,
    ) -> None:
        """Pull writes networks file when extract_network_presets returns presets."""
        from admedi.engine.loader import Profile
        from admedi.models.app import App

        mock_load_cred.return_value = _mock_credential()
        mock_resolve_profile.return_value = Profile(
            alias="hexar-ios", app_key="676996cd",
            app_name="Hexar.io iOS", platform=Platform.IOS,
        )

        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_adapter.get_groups.return_value = []
        mock_adapter.list_apps.return_value = [
            App(app_key="676996cd", app_name="Hexar.io iOS", platform=Platform.IOS),
        ]

        mock_gen_settings.return_value = ("settings/hexar-ios.yaml", [])
        mock_extract_presets.return_value = {"bidding": [{"network": "Meta", "bidder": True}]}
        mock_save_raw.return_value = "snapshots/hexar-ios.yaml"

        result = runner.invoke(cli_app, ["pull", "--app", "hexar-ios"])

        assert result.exit_code == 0
        mock_write_yaml.assert_called_once()
        call_args = mock_write_yaml.call_args
        assert "hexar-ios-networks.yaml" in str(call_args.args[0])

    @patch("admedi.cli.main.save_raw_snapshot")
    @patch("admedi.cli.main.write_yaml_file")
    @patch("admedi.cli.main.extract_network_presets")
    @patch("admedi.cli.main.generate_app_settings")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main._resolve_profile")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_pull_skips_networks_file_when_no_presets(
        self, mock_load_cred: MagicMock, mock_resolve_profile: MagicMock,
        mock_adapter_cls: MagicMock, mock_gen_settings: MagicMock,
        mock_extract_presets: MagicMock, mock_write_yaml: MagicMock,
        mock_save_raw: MagicMock,
    ) -> None:
        """Pull does not write networks file when extract_network_presets returns empty."""
        from admedi.engine.loader import Profile
        from admedi.models.app import App

        mock_load_cred.return_value = _mock_credential()
        mock_resolve_profile.return_value = Profile(
            alias="hexar-ios", app_key="676996cd",
            app_name="Hexar.io iOS", platform=Platform.IOS,
        )

        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_adapter.get_groups.return_value = []
        mock_adapter.list_apps.return_value = [
            App(app_key="676996cd", app_name="Hexar.io iOS", platform=Platform.IOS),
        ]

        mock_gen_settings.return_value = ("settings/hexar-ios.yaml", [])
        mock_extract_presets.return_value = {}
        mock_save_raw.return_value = "snapshots/hexar-ios.yaml"

        result = runner.invoke(cli_app, ["pull", "--app", "hexar-ios"])

        assert result.exit_code == 0
        mock_write_yaml.assert_not_called()

    @patch("admedi.cli.main.save_raw_snapshot")
    @patch("admedi.cli.main.write_yaml_file")
    @patch("admedi.cli.main.extract_network_presets")
    @patch("admedi.cli.main.generate_app_settings")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main._resolve_profile")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_pull_saves_raw_snapshot(
        self, mock_load_cred: MagicMock, mock_resolve_profile: MagicMock,
        mock_adapter_cls: MagicMock, mock_gen_settings: MagicMock,
        mock_extract_presets: MagicMock, mock_write_yaml: MagicMock,
        mock_save_raw: MagicMock,
    ) -> None:
        """Pull saves raw snapshot to snapshots/{alias}.yaml."""
        from admedi.engine.loader import Profile
        from admedi.models.app import App

        mock_load_cred.return_value = _mock_credential()
        mock_resolve_profile.return_value = Profile(
            alias="hexar-ios", app_key="676996cd",
            app_name="Hexar.io iOS", platform=Platform.IOS,
        )

        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_adapter.get_groups.return_value = []
        mock_adapter.list_apps.return_value = [
            App(app_key="676996cd", app_name="Hexar.io iOS", platform=Platform.IOS),
        ]

        mock_gen_settings.return_value = ("settings/hexar-ios.yaml", [])
        mock_extract_presets.return_value = {}
        mock_save_raw.return_value = "snapshots/hexar-ios.yaml"

        result = runner.invoke(cli_app, ["pull", "--app", "hexar-ios"])

        assert result.exit_code == 0
        mock_save_raw.assert_called_once()
        call_kwargs = mock_save_raw.call_args
        assert call_kwargs.kwargs["alias"] == "hexar-ios"

    @patch("admedi.cli.main.load_credential_from_env")
    def test_pull_missing_credentials_exits_2(
        self, mock_load_cred: MagicMock,
    ) -> None:
        """Missing credentials on pull produce exit code 2."""
        from admedi.exceptions import AuthError

        mock_load_cred.side_effect = AuthError(
            "Missing required environment variable(s): LEVELPLAY_SECRET_KEY"
        )

        result = runner.invoke(cli_app, ["pull", "--app", "hexar-ios"])

        assert result.exit_code == 2
        assert "LEVELPLAY_SECRET_KEY" in result.output


# ---------------------------------------------------------------------------
# CLI registration and dead code tests
# ---------------------------------------------------------------------------


class TestCliRegistration:
    """Tests for CLI command registration and dead code removal."""

    def test_show_command_not_registered(self) -> None:
        """'admedi show' is no longer a valid command."""
        result = runner.invoke(cli_app, ["show", "--app", "hexar-ios"])
        assert result.exit_code == 2  # typer usage error

    def test_pull_command_registered(self) -> None:
        """'admedi pull' is a registered command."""
        result = runner.invoke(cli_app, ["pull", "--help"])
        assert result.exit_code == 0
        assert "pull" in result.output.lower()

    def test_resolve_app_key_removed(self) -> None:
        """_resolve_app_key() no longer exists in cli/main.py."""
        import admedi.cli.main as main_module
        assert not hasattr(main_module, "_resolve_app_key")

    def test_display_tier_warnings_removed(self) -> None:
        """display_tier_warnings() no longer exists in cli/display.py."""
        import admedi.cli.display as display_module
        assert not hasattr(display_module, "display_tier_warnings")

    def test_display_show_removed(self) -> None:
        """display_show() no longer exists in cli/display.py."""
        import admedi.cli.display as display_module
        assert not hasattr(display_module, "display_show")

    def test_display_pull_exists(self) -> None:
        """display_pull() exists in cli/display.py."""
        import admedi.cli.display as display_module
        assert hasattr(display_module, "display_pull")


# ---------------------------------------------------------------------------
# display_pull() direct tests
# ---------------------------------------------------------------------------


class TestDisplayPull:
    """Tests for display_pull() rendering."""

    def test_display_pull_shows_app_metadata(self) -> None:
        """display_pull() shows app name, platform, and key in header."""
        from admedi.models.app import App

        console, buf = _make_console()
        app = App(app_key="abc123", app_name="Test App", platform=Platform.IOS)

        display_pull(app, [], console=console)
        output = buf.getvalue()

        assert "Test App" in output
        assert "iOS" in output
        assert "abc123" in output

    def test_display_pull_shows_networks_path(self) -> None:
        """display_pull() shows networks path in footer when provided."""
        from admedi.models.app import App

        console, buf = _make_console()
        app = App(app_key="abc123", app_name="Test App", platform=Platform.IOS)

        display_pull(
            app, [],
            networks_path="settings/test-networks.yaml",
            console=console,
        )
        output = buf.getvalue()

        assert "Networks saved to:" in output
        assert "test-networks.yaml" in output

    def test_display_pull_shows_info_messages(self) -> None:
        """display_pull() renders info messages from settings generation."""
        from admedi.models.app import App

        console, buf = _make_console()
        app = App(app_key="abc123", app_name="Test App", platform=Platform.IOS)

        display_pull(
            app, [],
            info_messages=["Created tier 'Tier 1' -> country group 'US'"],
            console=console,
        )
        output = buf.getvalue()

        assert "Created tier" in output
        assert "Tier 1" in output

    def test_display_pull_no_info_messages_when_none(self) -> None:
        """display_pull() does not render info section when messages is None."""
        from admedi.models.app import App

        console, buf = _make_console()
        app = App(app_key="abc123", app_name="Test App", platform=Platform.IOS)

        display_pull(app, [], console=console)
        output = buf.getvalue()

        assert "Created tier" not in output


class TestAuditCommand:
    """Tests for the 'admedi audit' CLI command (profiles-based, no --config)."""

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

        result = runner.invoke(cli_app, ["audit"])

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

        result = runner.invoke(cli_app, ["audit"])

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
            cli_app, ["audit", "--format", "json"]
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "app_reports" in data

    @patch("admedi.cli.main._resolve_profile")
    @patch("admedi.cli.main.ConfigEngine")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_audit_with_app_filter(
        self, mock_load_cred: MagicMock, mock_adapter_cls: MagicMock,
        mock_engine_cls: MagicMock, mock_resolve_profile: MagicMock,
    ) -> None:
        """--app flag passes aliases to engine.audit()."""
        from admedi.engine.loader import Profile

        mock_load_cred.return_value = _mock_credential()
        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_resolve_profile.return_value = Profile(
            alias="hexar-ios", app_key="abc123",
            app_name="Hexar iOS", platform=Platform.IOS,
        )

        mock_engine = MagicMock()
        mock_engine.audit = AsyncMock(return_value=_make_no_drift_report())
        mock_engine_cls.return_value = mock_engine

        runner.invoke(
            cli_app, ["audit", "--app", "hexar-ios"]
        )

        mock_engine.audit.assert_awaited_once()
        call_kwargs = mock_engine.audit.call_args
        assert call_kwargs.kwargs["aliases"] == ["hexar-ios"]

    @patch("admedi.cli.main.load_credential_from_env")
    def test_audit_missing_credentials_exits_2(
        self, mock_load_cred: MagicMock,
    ) -> None:
        """Missing credentials produce exit code 2 with clear error."""
        from admedi.exceptions import AuthError

        mock_load_cred.side_effect = AuthError(
            "Missing required environment variable(s): LEVELPLAY_SECRET_KEY"
        )

        result = runner.invoke(cli_app, ["audit"])

        assert result.exit_code == 2
        assert "LEVELPLAY_SECRET_KEY" in result.output


class TestSyncCommand:
    """Tests for the 'admedi sync' CLI command."""

    def _setup_sync_mocks(
        self,
        mock_load_cred: MagicMock,
        mock_resolve: MagicMock,
        mock_adapter_cls: MagicMock,
        mock_load_tiers: MagicMock,
        mock_compute_diff: MagicMock,
        *,
        drift: bool = False,
    ) -> tuple[MagicMock, MagicMock]:
        """Set up common mocks for sync command tests.

        Returns:
            Tuple of (mock_adapter, mock_compute_diff) for further configuration.
        """
        from admedi.engine.loader import Profile
        from admedi.models.app import App
        from admedi.models.portfolio import PortfolioTier

        mock_load_cred.return_value = _mock_credential()

        # _resolve_profile returns a Profile with app_key matching alias
        mock_resolve.side_effect = lambda v: Profile(
            alias=v, app_key=v,
            app_name=f"Mock {v}", platform=Platform.IOS,
        )

        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Mock list_apps to return test apps matching alias-based keys
        mock_adapter.list_apps.return_value = [
            App(
                app_key="hexar-ios",
                app_name="Hexar iOS",
                platform=Platform.IOS,
            ),
            App(
                app_key="hexar-google",
                app_name="Hexar Google",
                platform=Platform.ANDROID,
            ),
        ]
        mock_adapter.get_groups.return_value = []

        # Mock resolve_app_tiers to return test tiers
        mock_load_tiers.return_value = [
            PortfolioTier(
                name="Tier 1",
                countries=["US", "GB"],
                position=1,
                is_default=False,
                ad_formats=[AdFormat.INTERSTITIAL],
            ),
        ]

        # Mock compute_diff to return appropriate report
        if drift:
            mock_compute_diff.return_value = _make_drift_report().app_reports[0]
        else:
            mock_compute_diff.return_value = _make_no_drift_report().app_reports[0]

        return mock_adapter, mock_compute_diff

    @patch("admedi.cli.main.compute_diff")
    @patch("admedi.cli.main.resolve_app_tiers")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main._resolve_profile")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_sync_tiers_self_no_drift_exits_0(
        self, mock_load_cred: MagicMock, mock_resolve: MagicMock,
        mock_adapter_cls: MagicMock,
        mock_load_tiers: MagicMock, mock_compute_diff: MagicMock,
    ) -> None:
        """Self-sync with --tiers and no drift exits code 0."""
        self._setup_sync_mocks(
            mock_load_cred, mock_resolve, mock_adapter_cls, mock_load_tiers,
            mock_compute_diff, drift=False,
        )

        result = runner.invoke(
            cli_app, ["sync", "--tiers", "hexar-ios", "--dry-run"]
        )

        assert result.exit_code == 0

    @patch("admedi.cli.main.compute_diff")
    @patch("admedi.cli.main.resolve_app_tiers")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main._resolve_profile")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_sync_tiers_dry_run_exits_1_when_drift(
        self, mock_load_cred: MagicMock, mock_resolve: MagicMock,
        mock_adapter_cls: MagicMock,
        mock_load_tiers: MagicMock, mock_compute_diff: MagicMock,
    ) -> None:
        """--tiers --dry-run with drift exits code 1 and shows preview."""
        self._setup_sync_mocks(
            mock_load_cred, mock_resolve, mock_adapter_cls, mock_load_tiers,
            mock_compute_diff, drift=True,
        )

        result = runner.invoke(
            cli_app, ["sync", "--tiers", "hexar-ios", "--dry-run"]
        )

        assert result.exit_code == 1

    @patch("admedi.cli.main.Applier")
    @patch("admedi.cli.main.compute_diff")
    @patch("admedi.cli.main.resolve_app_tiers")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main._resolve_profile")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_sync_tiers_applies_changes_without_dry_run(
        self, mock_load_cred: MagicMock, mock_resolve: MagicMock,
        mock_adapter_cls: MagicMock,
        mock_load_tiers: MagicMock, mock_compute_diff: MagicMock,
        mock_applier_cls: MagicMock,
    ) -> None:
        """--tiers without --dry-run applies changes directly and exits 0."""
        self._setup_sync_mocks(
            mock_load_cred, mock_resolve, mock_adapter_cls, mock_load_tiers,
            mock_compute_diff, drift=True,
        )

        mock_applier = MagicMock()
        mock_applier.apply = AsyncMock(return_value=_make_apply_result())
        mock_applier_cls.return_value = mock_applier

        result = runner.invoke(
            cli_app, ["sync", "--tiers", "hexar-ios"]
        )

        assert result.exit_code == 0
        mock_applier.apply.assert_awaited_once()

    @patch("admedi.cli.main.compute_diff")
    @patch("admedi.cli.main.resolve_app_tiers")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main._resolve_profile")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_sync_tiers_cross_app_positional_args(
        self, mock_load_cred: MagicMock, mock_resolve: MagicMock,
        mock_adapter_cls: MagicMock,
        mock_load_tiers: MagicMock, mock_compute_diff: MagicMock,
    ) -> None:
        """sync --tiers {source} {target} uses cross-app positional args."""
        self._setup_sync_mocks(
            mock_load_cred, mock_resolve, mock_adapter_cls, mock_load_tiers,
            mock_compute_diff, drift=False,
        )

        result = runner.invoke(
            cli_app, ["sync", "--tiers", "hexar-ios", "hexar-google", "--dry-run"]
        )

        assert result.exit_code == 0
        # Verify resolve_app_tiers was called with source alias
        mock_load_tiers.assert_called_once_with("hexar-ios")

    @patch("admedi.cli.main.compute_diff")
    @patch("admedi.cli.main.resolve_app_tiers")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main._resolve_profile")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_sync_no_scope_defaults_to_tiers(
        self, mock_load_cred: MagicMock, mock_resolve: MagicMock,
        mock_adapter_cls: MagicMock,
        mock_load_tiers: MagicMock, mock_compute_diff: MagicMock,
    ) -> None:
        """sync {alias} with no scope flag defaults to --tiers."""
        self._setup_sync_mocks(
            mock_load_cred, mock_resolve, mock_adapter_cls, mock_load_tiers,
            mock_compute_diff, drift=False,
        )

        result = runner.invoke(
            cli_app, ["sync", "hexar-ios", "--dry-run"]
        )

        assert result.exit_code == 0
        # resolve_app_tiers should be called since --tiers is defaulted
        mock_load_tiers.assert_called_once()

    @patch("admedi.cli.main.load_credential_from_env")
    def test_sync_networks_not_implemented_exits_2(
        self, mock_load_cred: MagicMock,
    ) -> None:
        """sync --networks prints 'not yet implemented' and exits 2."""
        mock_load_cred.return_value = _mock_credential()

        result = runner.invoke(
            cli_app, ["sync", "--networks", "hexar-ios"]
        )

        assert result.exit_code == 2
        assert "not yet implemented" in result.output.lower()

    @patch("admedi.cli.main.compute_diff")
    @patch("admedi.cli.main.resolve_app_tiers")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main._resolve_profile")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_sync_tiers_format_json_produces_valid_json(
        self, mock_load_cred: MagicMock, mock_resolve: MagicMock,
        mock_adapter_cls: MagicMock,
        mock_load_tiers: MagicMock, mock_compute_diff: MagicMock,
    ) -> None:
        """--format json --dry-run produces valid JSON with diff_report key."""
        self._setup_sync_mocks(
            mock_load_cred, mock_resolve, mock_adapter_cls, mock_load_tiers,
            mock_compute_diff, drift=True,
        )

        result = runner.invoke(
            cli_app,
            ["sync", "--tiers", "hexar-ios", "--format", "json", "--dry-run"],
        )

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "diff_report" in data
        assert data["apply_result"] is None

    def test_sync_yes_flag_produces_error(self) -> None:
        """--yes flag is no longer accepted and produces a usage error (exit 2)."""
        result = runner.invoke(
            cli_app, ["sync", "--tiers", "hexar-ios", "--yes"]
        )

        assert result.exit_code == 2


class TestStatusCommand:
    """Tests for the 'admedi status' CLI command (profiles-based, no --config)."""

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

        result = runner.invoke(cli_app, ["status"])

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
            cli_app, ["status", "--format", "json"]
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

        result = runner.invoke(cli_app, ["status"])

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
            ["pull", "--app", "fake"],
            ["audit"],
            ["sync", "--tiers", "fake"],
            ["status"],
        ]:
            result = runner.invoke(cli_app, cmd_args)
            assert result.exit_code == 2, f"Failed for: {cmd_args}"
            assert "LEVELPLAY_SECRET_KEY" in result.output, f"Failed for: {cmd_args}"
            assert "Set LEVELPLAY_SECRET_KEY" in result.output, f"Failed for: {cmd_args}"


# ===========================================================================
# AppApplyResult app_name field
# ===========================================================================


class TestAppApplyResultAppName:
    """Tests for the app_name field on AppApplyResult (Step 4)."""

    def test_app_apply_result_accepts_app_name(self) -> None:
        """AppApplyResult stores app_name when provided."""
        result = AppApplyResult(
            app_key="abc123",
            app_name="Shelf Sort",
            status=ApplyStatus.SUCCESS,
            groups_created=2,
            groups_updated=1,
            error=None,
        )
        assert result.app_name == "Shelf Sort"

    def test_app_apply_result_defaults_app_name_to_empty(self) -> None:
        """AppApplyResult defaults app_name to empty string for backward compat."""
        result = AppApplyResult(
            app_key="abc123",
            status=ApplyStatus.SUCCESS,
            groups_created=0,
            groups_updated=0,
            error=None,
        )
        assert result.app_name == ""

    def test_app_apply_result_json_includes_app_name(self) -> None:
        """JSON serialization of AppApplyResult includes app_name field."""
        result = AppApplyResult(
            app_key="abc123",
            app_name="My App",
            status=ApplyStatus.SUCCESS,
            groups_created=0,
            groups_updated=0,
            error=None,
        )
        data = result.model_dump()
        assert "app_name" in data
        assert data["app_name"] == "My App"

    def test_app_apply_result_json_includes_empty_app_name(self) -> None:
        """JSON serialization includes app_name even when default empty string."""
        result = AppApplyResult(
            app_key="abc123",
            status=ApplyStatus.DRY_RUN,
            groups_created=0,
            groups_updated=0,
            error=None,
        )
        data = result.model_dump()
        assert "app_name" in data
        assert data["app_name"] == ""

    def test_existing_helper_still_works_without_app_name(self) -> None:
        """The _make_app_apply_result helper works without app_name (backward compat)."""
        result = _make_app_apply_result(app_key="test-key", created=3, updated=1)
        assert result.app_key == "test-key"
        assert result.app_name == ""
        assert result.groups_created == 3
        assert result.groups_updated == 1


# ---------------------------------------------------------------------------
# AppApplyResult groups_deleted Tests (Step 6)
# ---------------------------------------------------------------------------


class TestAppApplyResultGroupsDeleted:
    """Tests for the groups_deleted field on AppApplyResult (Step 6)."""

    def test_app_apply_result_stores_groups_deleted(self) -> None:
        """AppApplyResult stores groups_deleted when provided."""
        result = AppApplyResult(
            app_key="abc123",
            status=ApplyStatus.SUCCESS,
            groups_created=0,
            groups_updated=0,
            groups_deleted=3,
            error=None,
        )
        assert result.groups_deleted == 3

    def test_app_apply_result_defaults_groups_deleted_to_zero(self) -> None:
        """AppApplyResult defaults groups_deleted to 0 for backward compat."""
        result = AppApplyResult(
            app_key="abc123",
            status=ApplyStatus.SUCCESS,
            groups_created=1,
            groups_updated=2,
            error=None,
        )
        assert result.groups_deleted == 0

    def test_app_apply_result_json_includes_groups_deleted(self) -> None:
        """JSON serialization of AppApplyResult includes groups_deleted field."""
        result = AppApplyResult(
            app_key="abc123",
            status=ApplyStatus.SUCCESS,
            groups_created=0,
            groups_updated=0,
            groups_deleted=5,
            error=None,
        )
        data = result.model_dump()
        assert "groups_deleted" in data
        assert data["groups_deleted"] == 5

    def test_app_apply_result_json_includes_default_groups_deleted(self) -> None:
        """JSON serialization includes groups_deleted even when default 0."""
        result = AppApplyResult(
            app_key="abc123",
            status=ApplyStatus.DRY_RUN,
            groups_created=0,
            groups_updated=0,
            error=None,
        )
        data = result.model_dump()
        assert "groups_deleted" in data
        assert data["groups_deleted"] == 0

    def test_existing_helper_still_works_without_groups_deleted(self) -> None:
        """The _make_app_apply_result helper works without groups_deleted (backward compat)."""
        result = _make_app_apply_result(app_key="test-key", created=2, updated=1)
        assert result.groups_deleted == 0


# ---------------------------------------------------------------------------
# SyncLog groups_deleted Tests (Step 6)
# ---------------------------------------------------------------------------


class TestSyncLogGroupsDeleted:
    """Tests for the groups_deleted field on SyncLog (Step 6)."""

    def test_sync_log_stores_groups_deleted(self) -> None:
        """SyncLog stores groups_deleted when provided."""
        from datetime import datetime, timezone

        from admedi.models.sync_log import SyncLog

        log = SyncLog(
            app_key="abc123",
            timestamp=datetime(2026, 3, 17, 12, 0, tzinfo=timezone.utc),
            action="sync",
            groups_created=0,
            groups_updated=0,
            groups_deleted=2,
            status=ApplyStatus.SUCCESS,
        )
        assert log.groups_deleted == 2

    def test_sync_log_defaults_groups_deleted_to_zero(self) -> None:
        """SyncLog defaults groups_deleted to 0 for backward compat."""
        from datetime import datetime, timezone

        from admedi.models.sync_log import SyncLog

        log = SyncLog(
            app_key="abc123",
            timestamp=datetime(2026, 3, 17, 12, 0, tzinfo=timezone.utc),
            action="sync",
            groups_created=1,
            groups_updated=0,
            status=ApplyStatus.SUCCESS,
        )
        assert log.groups_deleted == 0

    def test_sync_log_json_includes_groups_deleted(self) -> None:
        """JSON serialization of SyncLog includes groups_deleted field."""
        from datetime import datetime, timezone

        from admedi.models.sync_log import SyncLog

        log = SyncLog(
            app_key="abc123",
            timestamp=datetime(2026, 3, 17, 12, 0, tzinfo=timezone.utc),
            action="sync",
            groups_created=0,
            groups_updated=0,
            groups_deleted=4,
            status=ApplyStatus.SUCCESS,
        )
        data = log.model_dump()
        assert "groups_deleted" in data
        assert data["groups_deleted"] == 4

    def test_sync_log_json_round_trip_with_groups_deleted(self) -> None:
        """SyncLog round-trips through model_dump_json/model_validate_json with groups_deleted."""
        from datetime import datetime, timezone

        from admedi.models.sync_log import SyncLog

        original = SyncLog(
            app_key="abc123",
            timestamp=datetime(2026, 3, 17, 12, 0, tzinfo=timezone.utc),
            action="sync",
            groups_created=1,
            groups_updated=2,
            groups_deleted=3,
            status=ApplyStatus.SUCCESS,
        )
        json_str = original.model_dump_json()
        restored = SyncLog.model_validate_json(json_str)
        assert restored.groups_deleted == 3
        assert restored.groups_created == 1
        assert restored.groups_updated == 2


# ---------------------------------------------------------------------------
# Sync EXTRA->DELETE Post-Processing + has_drift (Step 8)
# ---------------------------------------------------------------------------


def _make_extra_only_app_diff_report() -> AppDiffReport:
    """Create an AppDiffReport with only EXTRA diffs (no creates/updates).

    Used to test that the sync command converts EXTRA to DELETE.
    """
    return AppDiffReport(
        app_key="hexar-ios",
        app_name="Hexar iOS",
        group_diffs=[
            GroupDiff(
                action=DiffAction.EXTRA,
                group_name="Old Extra Tier",
                group_id=200,
                ad_format=AdFormat.INTERSTITIAL,
                changes=[],
                desired_group=None,
            ),
            GroupDiff(
                action=DiffAction.EXTRA,
                group_name="Another Extra",
                group_id=201,
                ad_format=AdFormat.REWARDED_VIDEO,
                changes=[],
                desired_group=None,
            ),
        ],
        has_ab_test=False,
        ab_test_warning=None,
    )


def _make_mixed_with_extra_app_diff_report() -> AppDiffReport:
    """Create an AppDiffReport with a mix of CREATE and EXTRA diffs.

    Used to test that EXTRA is converted to DELETE while other actions
    are preserved.
    """
    return AppDiffReport(
        app_key="hexar-ios",
        app_name="Hexar iOS",
        group_diffs=[
            GroupDiff(
                action=DiffAction.CREATE,
                group_name="New Tier",
                group_id=None,
                ad_format=AdFormat.INTERSTITIAL,
                changes=[],
                desired_group=None,
            ),
            GroupDiff(
                action=DiffAction.EXTRA,
                group_name="Old Extra Tier",
                group_id=200,
                ad_format=AdFormat.REWARDED_VIDEO,
                changes=[],
                desired_group=None,
            ),
            GroupDiff(
                action=DiffAction.UNCHANGED,
                group_name="Stable Tier",
                group_id=100,
                ad_format=AdFormat.INTERSTITIAL,
                changes=[],
                desired_group=None,
            ),
        ],
        has_ab_test=False,
        ab_test_warning=None,
    )


class TestSyncExtraToDeletePostProcessing:
    """Tests for EXTRA->DELETE post-processing in the sync command (Step 8).

    The differ produces EXTRA actions for groups on remote but not in template.
    The sync CLI converts EXTRA->DELETE before display/apply so that sync means
    'make it match'.
    """

    @patch("admedi.cli.main.compute_diff")
    @patch("admedi.cli.main.resolve_app_tiers")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main._resolve_profile")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_sync_converts_extra_to_delete_in_dry_run(
        self, mock_load_cred: MagicMock, mock_resolve: MagicMock,
        mock_adapter_cls: MagicMock,
        mock_load_tiers: MagicMock, mock_compute_diff: MagicMock,
    ) -> None:
        """EXTRA groups are converted to DELETE before display in dry-run JSON output."""
        from admedi.engine.loader import Profile
        from admedi.models.portfolio import PortfolioTier
        from admedi.models.app import App

        mock_load_cred.return_value = _mock_credential()
        mock_resolve.side_effect = lambda v: Profile(
            alias=v, app_key=v, app_name=f"Mock {v}", platform=Platform.IOS,
        )

        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_adapter.list_apps.return_value = [
            App(app_key="hexar-ios", app_name="Hexar iOS", platform=Platform.IOS),
        ]
        mock_adapter.get_groups.return_value = []

        mock_load_tiers.return_value = [
            PortfolioTier(
                name="Tier 1", countries=["US"], position=1,
                is_default=False, ad_formats=[AdFormat.INTERSTITIAL],
            ),
        ]

        # compute_diff returns a report with EXTRA diffs
        mock_compute_diff.return_value = _make_mixed_with_extra_app_diff_report()

        result = runner.invoke(
            cli_app,
            ["sync", "--tiers", "hexar-ios", "--format", "json", "--dry-run"],
        )

        assert result.exit_code == 1  # drift detected in dry-run
        data = json.loads(result.output)
        diff_report = data["diff_report"]

        # Verify no EXTRA actions remain -- they should all be DELETE
        for app_report in diff_report["app_reports"]:
            for group_diff in app_report["group_diffs"]:
                assert group_diff["action"] != "extra", (
                    f"EXTRA should have been converted to DELETE: {group_diff['group_name']}"
                )

        # Verify DELETE actions are present
        actions = [
            gd["action"]
            for ar in diff_report["app_reports"]
            for gd in ar["group_diffs"]
        ]
        assert "delete" in actions

    @patch("admedi.cli.main.compute_diff")
    @patch("admedi.cli.main.resolve_app_tiers")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main._resolve_profile")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_sync_only_deletes_triggers_drift(
        self, mock_load_cred: MagicMock, mock_resolve: MagicMock,
        mock_adapter_cls: MagicMock,
        mock_load_tiers: MagicMock, mock_compute_diff: MagicMock,
    ) -> None:
        """Sync with only EXTRA diffs (converted to DELETE) triggers drift and exits 1 on dry-run."""
        from admedi.engine.loader import Profile
        from admedi.models.portfolio import PortfolioTier
        from admedi.models.app import App

        mock_load_cred.return_value = _mock_credential()
        mock_resolve.side_effect = lambda v: Profile(
            alias=v, app_key=v, app_name=f"Mock {v}", platform=Platform.IOS,
        )

        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_adapter.list_apps.return_value = [
            App(app_key="hexar-ios", app_name="Hexar iOS", platform=Platform.IOS),
        ]
        mock_adapter.get_groups.return_value = []

        mock_load_tiers.return_value = [
            PortfolioTier(
                name="Tier 1", countries=["US"], position=1,
                is_default=False, ad_formats=[AdFormat.INTERSTITIAL],
            ),
        ]

        # compute_diff returns EXTRA-only diffs (no creates or updates)
        mock_compute_diff.return_value = _make_extra_only_app_diff_report()

        result = runner.invoke(
            cli_app,
            ["sync", "--tiers", "hexar-ios", "--dry-run"],
        )

        # Should exit 1 (drift detected) NOT exit 0 (no drift)
        assert result.exit_code == 1

    @patch("admedi.cli.main.Applier")
    @patch("admedi.cli.main.compute_diff")
    @patch("admedi.cli.main.resolve_app_tiers")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main._resolve_profile")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_sync_only_deletes_applies_when_not_dry_run(
        self, mock_load_cred: MagicMock, mock_resolve: MagicMock,
        mock_adapter_cls: MagicMock,
        mock_load_tiers: MagicMock, mock_compute_diff: MagicMock,
        mock_applier_cls: MagicMock,
    ) -> None:
        """Sync with only DELETE diffs (from EXTRA conversion) applies changes when not dry-run."""
        from admedi.engine.loader import Profile
        from admedi.models.portfolio import PortfolioTier
        from admedi.models.app import App

        mock_load_cred.return_value = _mock_credential()
        mock_resolve.side_effect = lambda v: Profile(
            alias=v, app_key=v, app_name=f"Mock {v}", platform=Platform.IOS,
        )

        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_adapter.list_apps.return_value = [
            App(app_key="hexar-ios", app_name="Hexar iOS", platform=Platform.IOS),
        ]
        mock_adapter.get_groups.return_value = []

        mock_load_tiers.return_value = [
            PortfolioTier(
                name="Tier 1", countries=["US"], position=1,
                is_default=False, ad_formats=[AdFormat.INTERSTITIAL],
            ),
        ]

        # compute_diff returns EXTRA-only diffs
        mock_compute_diff.return_value = _make_extra_only_app_diff_report()

        mock_applier = MagicMock()
        mock_applier.apply = AsyncMock(return_value=_make_apply_result())
        mock_applier_cls.return_value = mock_applier

        result = runner.invoke(
            cli_app,
            ["sync", "--tiers", "hexar-ios"],
        )

        # Should apply (not skip as "no drift") and exit 0
        assert result.exit_code == 0
        mock_applier.apply.assert_awaited_once()

    def test_has_drift_true_with_only_deletes(self) -> None:
        """DiffReport with only DELETE diffs has total_deletes > 0 (triggering drift)."""
        report = DiffReport(
            app_reports=[
                AppDiffReport(
                    app_key="hexar-ios",
                    app_name="Hexar iOS",
                    group_diffs=[
                        GroupDiff(
                            action=DiffAction.DELETE,
                            group_name="Extra Tier",
                            group_id=200,
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

        assert report.total_creates == 0
        assert report.total_updates == 0
        assert report.total_deletes == 1
        # The has_drift check in main.py uses total_creates > 0 or total_updates > 0 or total_deletes > 0
        has_drift = report.total_creates > 0 or report.total_updates > 0 or report.total_deletes > 0
        assert has_drift is True

    @patch("admedi.cli.main.compute_diff")
    @patch("admedi.cli.main.resolve_app_tiers")
    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main._resolve_profile")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_sync_dry_run_with_delete_shows_preview_exits_1(
        self, mock_load_cred: MagicMock, mock_resolve: MagicMock,
        mock_adapter_cls: MagicMock,
        mock_load_tiers: MagicMock, mock_compute_diff: MagicMock,
    ) -> None:
        """Dry-run with DELETE diffs shows preview and exits 1."""
        from admedi.engine.loader import Profile
        from admedi.models.portfolio import PortfolioTier
        from admedi.models.app import App

        mock_load_cred.return_value = _mock_credential()
        mock_resolve.side_effect = lambda v: Profile(
            alias=v, app_key=v, app_name=f"Mock {v}", platform=Platform.IOS,
        )

        mock_adapter = AsyncMock()
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(return_value=mock_adapter)
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_adapter.list_apps.return_value = [
            App(app_key="hexar-ios", app_name="Hexar iOS", platform=Platform.IOS),
        ]
        mock_adapter.get_groups.return_value = []

        mock_load_tiers.return_value = [
            PortfolioTier(
                name="Tier 1", countries=["US"], position=1,
                is_default=False, ad_formats=[AdFormat.INTERSTITIAL],
            ),
        ]

        # compute_diff returns EXTRA-only diffs
        mock_compute_diff.return_value = _make_extra_only_app_diff_report()

        result = runner.invoke(
            cli_app,
            ["sync", "--tiers", "hexar-ios", "--dry-run"],
        )

        assert result.exit_code == 1
        # Verify the output contains the group name (preview was shown)
        assert "Old Extra Tier" in result.output or "Another Extra" in result.output

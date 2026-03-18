"""Tests for the Applier that executes DiffReports.

Covers dry-run behavior, idempotency guard, A/B test skipping,
CREATE/UPDATE execution, DELETE/EXTRA skipping, pre-write snapshots,
post-write verification, sync log recording, per-app error isolation,
pre-write A/B test re-check, and build_waterfall_payload() resolution.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from admedi.engine.applier import Applier, build_waterfall_payload
from admedi.models.apply_result import ApplyStatus
from admedi.models.diff import (
    AppDiffReport,
    DiffAction,
    DiffReport,
    FieldChange,
    GroupDiff,
)
from admedi.models.enums import AdFormat
from admedi.models.group import Group
from admedi.models.instance import Instance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_group(
    name: str,
    ad_format: AdFormat = AdFormat.INTERSTITIAL,
    countries: list[str] | None = None,
    position: int = 1,
    group_id: int | None = None,
    ab_test: str | None = None,
) -> Group:
    """Create a Group model for testing."""
    return Group.model_validate(
        {
            "groupName": name,
            "adFormat": ad_format.value,
            "countries": countries or ["US"],
            "position": position,
            "groupId": group_id,
            "abTest": ab_test,
        }
    )


def _make_group_diff(
    action: DiffAction,
    name: str = "Tier 1",
    group_id: int | None = None,
    ad_format: AdFormat = AdFormat.INTERSTITIAL,
    desired_group: Group | None = None,
    changes: list[FieldChange] | None = None,
) -> GroupDiff:
    """Create a GroupDiff for testing."""
    return GroupDiff(
        action=action,
        group_name=name,
        group_id=group_id,
        ad_format=ad_format,
        changes=changes or [],
        desired_group=desired_group,
    )


def _make_app_report(
    app_key: str = "app1",
    app_name: str = "Test App",
    group_diffs: list[GroupDiff] | None = None,
    has_ab_test: bool = False,
    ab_test_warning: str | None = None,
) -> AppDiffReport:
    """Create an AppDiffReport for testing."""
    return AppDiffReport(
        app_key=app_key,
        app_name=app_name,
        group_diffs=group_diffs or [],
        has_ab_test=has_ab_test,
        ab_test_warning=ab_test_warning,
    )


def _make_diff_report(
    app_reports: list[AppDiffReport] | None = None,
) -> DiffReport:
    """Create a DiffReport for testing."""
    return DiffReport(app_reports=app_reports or [])


def _make_mocks() -> tuple[AsyncMock, AsyncMock]:
    """Create mocked adapter and storage for Applier tests."""
    adapter = AsyncMock()
    storage = AsyncMock()
    # Default: get_groups returns empty list (overridden per test)
    adapter.get_groups.return_value = []
    return adapter, storage


# ---------------------------------------------------------------------------
# Test Classes
# ---------------------------------------------------------------------------


class TestDryRun:
    """Tests for dry-run mode (no API calls)."""

    @pytest.mark.asyncio
    async def test_dry_run_returns_dry_run_status_for_all_apps(self) -> None:
        """Dry-run mode produces DRY_RUN status for every app."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        report = _make_diff_report(
            app_reports=[
                _make_app_report(app_key="app1"),
                _make_app_report(app_key="app2"),
            ]
        )

        result = await applier.apply(report, dry_run=True)

        assert result.was_dry_run is True
        assert len(result.app_results) == 2
        for r in result.app_results:
            assert r.status == ApplyStatus.DRY_RUN
            assert r.groups_created == 0
            assert r.groups_updated == 0

    @pytest.mark.asyncio
    async def test_dry_run_makes_no_adapter_calls(self) -> None:
        """Dry-run mode makes zero adapter write calls."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        create_diff = _make_group_diff(
            action=DiffAction.CREATE,
            desired_group=_make_group("Tier 1"),
        )
        report = _make_diff_report(
            app_reports=[
                _make_app_report(group_diffs=[create_diff]),
            ]
        )

        await applier.apply(report, dry_run=True)

        adapter.create_group.assert_not_called()
        adapter.update_group.assert_not_called()
        adapter.delete_group.assert_not_called()
        adapter.get_groups.assert_not_called()
        storage.save_snapshot.assert_not_called()
        storage.save_sync_log.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_zero_counts(self) -> None:
        """Dry-run results have zero create/update counts."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        report = _make_diff_report(
            app_reports=[_make_app_report(app_key="app1")]
        )

        result = await applier.apply(report, dry_run=True)

        assert result.app_results[0].groups_created == 0
        assert result.app_results[0].groups_updated == 0


class TestIdempotencyGuard:
    """Tests for the idempotency guard (all-UNCHANGED apps)."""

    @pytest.mark.asyncio
    async def test_all_unchanged_returns_success_with_zero_calls(
        self,
    ) -> None:
        """All-UNCHANGED app produces SUCCESS with zero API calls."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        unchanged = _make_group_diff(
            action=DiffAction.UNCHANGED, name="Existing"
        )
        report = _make_diff_report(
            app_reports=[
                _make_app_report(group_diffs=[unchanged, unchanged]),
            ]
        )

        result = await applier.apply(report, dry_run=False)

        assert result.app_results[0].status == ApplyStatus.SUCCESS
        assert result.app_results[0].groups_created == 0
        assert result.app_results[0].groups_updated == 0
        adapter.get_groups.assert_not_called()
        storage.save_snapshot.assert_not_called()
        storage.save_sync_log.assert_not_called()


class TestAbTestSkip:
    """Tests for A/B test skipping."""

    @pytest.mark.asyncio
    async def test_has_ab_test_returns_skipped(self) -> None:
        """App with has_ab_test=True is skipped."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    has_ab_test=True,
                    ab_test_warning="Group X has active A/B test",
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=_make_group("Tier 1"),
                        )
                    ],
                ),
            ]
        )

        result = await applier.apply(report, dry_run=False)

        assert result.app_results[0].status == ApplyStatus.SKIPPED
        assert "A/B test" in (result.app_results[0].error or "")
        adapter.create_group.assert_not_called()

    @pytest.mark.asyncio
    async def test_fresh_ab_test_recheck_skips(self) -> None:
        """A/B test appearing on fresh get_groups() causes skip."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        # Fresh groups reveal an A/B test
        adapter.get_groups.return_value = [
            _make_group(
                "Tier 1",
                group_id=100,
                ab_test="experiment_123",
            )
        ]

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    has_ab_test=False,
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=_make_group("Tier 2"),
                        )
                    ],
                ),
            ]
        )

        result = await applier.apply(report, dry_run=False)

        assert result.app_results[0].status == ApplyStatus.SKIPPED
        assert "A/B test detected on fresh read" in (
            result.app_results[0].error or ""
        )
        adapter.create_group.assert_not_called()
        # Snapshot should still be saved before the skip
        storage.save_snapshot.assert_called_once()

    @pytest.mark.asyncio
    async def test_fresh_ab_test_na_is_not_active(self) -> None:
        """A/B test value of 'N/A' is not considered active."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        adapter.get_groups.return_value = [
            _make_group("Tier 1", group_id=100, ab_test="N/A")
        ]

        create_group = _make_group("Tier 2")
        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    has_ab_test=False,
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=create_group,
                        )
                    ],
                ),
            ]
        )

        result = await applier.apply(report, dry_run=False)

        # Should NOT be skipped - N/A is not an active A/B test
        assert result.app_results[0].status == ApplyStatus.SUCCESS
        adapter.create_group.assert_called_once()


class TestCreateExecution:
    """Tests for CREATE action execution."""

    @pytest.mark.asyncio
    async def test_create_calls_adapter(self) -> None:
        """CREATE action calls adapter.create_group()."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        desired = _make_group("Tier 1", position=1)
        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    app_key="app1",
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=desired,
                        )
                    ],
                ),
            ]
        )

        await applier.apply(report, dry_run=False)

        adapter.create_group.assert_called_once_with("app1", desired)

    @pytest.mark.asyncio
    async def test_creates_ascending_position_order_per_format(
        self,
    ) -> None:
        """CREATEs execute in ascending position order within each ad_format."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        tier3 = _make_group("Tier 3", position=3)
        tier1 = _make_group("Tier 1", position=1)
        tier2 = _make_group("Tier 2", position=2)

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    app_key="app1",
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=tier3,
                        ),
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=tier1,
                        ),
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=tier2,
                        ),
                    ],
                ),
            ]
        )

        await applier.apply(report, dry_run=False)

        calls = adapter.create_group.call_args_list
        assert len(calls) == 3
        assert calls[0] == call("app1", tier1)
        assert calls[1] == call("app1", tier2)
        assert calls[2] == call("app1", tier3)

    @pytest.mark.asyncio
    async def test_creates_grouped_by_format(self) -> None:
        """CREATEs are grouped by ad_format, then ascending position."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        banner = _make_group(
            "Banner 1", ad_format=AdFormat.BANNER, position=1
        )
        inter2 = _make_group(
            "Inter 2", ad_format=AdFormat.INTERSTITIAL, position=2
        )
        inter1 = _make_group(
            "Inter 1", ad_format=AdFormat.INTERSTITIAL, position=1
        )

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    app_key="app1",
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=inter2,
                            ad_format=AdFormat.INTERSTITIAL,
                        ),
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=banner,
                            ad_format=AdFormat.BANNER,
                        ),
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=inter1,
                            ad_format=AdFormat.INTERSTITIAL,
                        ),
                    ],
                ),
            ]
        )

        await applier.apply(report, dry_run=False)

        calls = adapter.create_group.call_args_list
        assert len(calls) == 3
        # Banner first (alphabetically), then interstitial sorted by position
        assert calls[0] == call("app1", banner)
        assert calls[1] == call("app1", inter1)
        assert calls[2] == call("app1", inter2)

    @pytest.mark.asyncio
    async def test_create_count_in_result(self) -> None:
        """groups_created count reflects the number of CREATEs executed."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=_make_group("T1", position=1),
                        ),
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=_make_group("T2", position=2),
                        ),
                    ],
                ),
            ]
        )

        result = await applier.apply(report, dry_run=False)

        assert result.app_results[0].groups_created == 2


class TestUpdateExecution:
    """Tests for UPDATE action execution."""

    @pytest.mark.asyncio
    async def test_update_calls_adapter_with_group_id(self) -> None:
        """UPDATE action calls adapter.update_group() with correct group_id."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        desired = _make_group("Tier 1", countries=["US", "GB"], group_id=42)
        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    app_key="app1",
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.UPDATE,
                            group_id=42,
                            desired_group=desired,
                        )
                    ],
                ),
            ]
        )

        await applier.apply(report, dry_run=False)

        adapter.update_group.assert_called_once_with(
            "app1", 42, desired,
            include_tier_fields=True, waterfall_payload=None,
        )

    @pytest.mark.asyncio
    async def test_updates_execute_after_creates(self) -> None:
        """UPDATEs are executed after all CREATEs."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        call_order: list[str] = []

        async def mock_create(app_key: str, group: Group) -> Group:
            call_order.append(f"create:{group.group_name}")
            return group

        async def mock_update(
            app_key: str, group_id: int, group: Group, **kwargs: Any
        ) -> Group:
            call_order.append(f"update:{group.group_name}")
            return group

        adapter.create_group.side_effect = mock_create
        adapter.update_group.side_effect = mock_update

        create_group = _make_group("New Tier", position=1)
        update_group = _make_group(
            "Existing Tier", countries=["US", "GB"], group_id=42
        )

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.UPDATE,
                            group_id=42,
                            desired_group=update_group,
                        ),
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=create_group,
                        ),
                    ],
                ),
            ]
        )

        await applier.apply(report, dry_run=False)

        assert call_order == ["create:New Tier", "update:Existing Tier"]

    @pytest.mark.asyncio
    async def test_update_count_in_result(self) -> None:
        """groups_updated count reflects the number of UPDATEs executed."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.UPDATE,
                            group_id=1,
                            desired_group=_make_group(
                                "T1", countries=["US"], group_id=1
                            ),
                        ),
                        _make_group_diff(
                            action=DiffAction.UPDATE,
                            group_id=2,
                            desired_group=_make_group(
                                "T2", countries=["GB"], group_id=2
                            ),
                        ),
                    ],
                ),
            ]
        )

        result = await applier.apply(report, dry_run=False)

        assert result.app_results[0].groups_updated == 2


class TestDeleteExtraSkip:
    """Tests that DELETE calls delete_group and EXTRA is skipped."""

    @pytest.mark.asyncio
    async def test_delete_calls_delete_group(self) -> None:
        """DELETE action calls adapter.delete_group() but not create or update."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.DELETE,
                            group_id=99,
                        ),
                    ],
                ),
            ]
        )

        await applier.apply(report, dry_run=False)

        adapter.create_group.assert_not_called()
        adapter.update_group.assert_not_called()
        adapter.delete_group.assert_called_once_with("app1", 99)

    @pytest.mark.asyncio
    async def test_extra_not_processed(self) -> None:
        """EXTRA action does not call any adapter write methods."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.EXTRA,
                            group_id=88,
                        ),
                    ],
                ),
            ]
        )

        await applier.apply(report, dry_run=False)

        adapter.create_group.assert_not_called()
        adapter.update_group.assert_not_called()
        adapter.delete_group.assert_not_called()


class TestPreWriteSnapshot:
    """Tests for pre-write snapshot behavior."""

    @pytest.mark.asyncio
    async def test_snapshot_saved_before_writes(self) -> None:
        """save_snapshot() is called before any write operations."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        call_order: list[str] = []

        async def mock_save_snapshot(snapshot: object) -> None:
            call_order.append("save_snapshot")

        async def mock_create(app_key: str, group: Group) -> Group:
            call_order.append("create_group")
            return group

        storage.save_snapshot.side_effect = mock_save_snapshot
        adapter.create_group.side_effect = mock_create

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=_make_group("Tier 1"),
                        )
                    ],
                ),
            ]
        )

        await applier.apply(report, dry_run=False)

        assert call_order.index("save_snapshot") < call_order.index(
            "create_group"
        )

    @pytest.mark.asyncio
    async def test_snapshot_contains_fresh_groups(self) -> None:
        """Pre-write snapshot contains groups from fresh get_groups() call."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        fresh_group = _make_group("Existing", group_id=10)
        adapter.get_groups.return_value = [fresh_group]

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    app_key="app1",
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=_make_group("Tier 1"),
                        )
                    ],
                ),
            ]
        )

        await applier.apply(report, dry_run=False)

        snapshot_call = storage.save_snapshot.call_args[0][0]
        assert snapshot_call.app_key == "app1"
        assert len(snapshot_call.raw_config["groups"]) == 1
        assert snapshot_call.raw_config["groups"][0]["groupName"] == "Existing"


class TestSyncLog:
    """Tests for sync log recording."""

    @pytest.mark.asyncio
    async def test_sync_log_saved_after_writes(self) -> None:
        """save_sync_log() is called after write operations."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        call_order: list[str] = []

        async def mock_create(app_key: str, group: Group) -> Group:
            call_order.append("create_group")
            return group

        async def mock_save_log(log: object) -> None:
            call_order.append("save_sync_log")

        adapter.create_group.side_effect = mock_create
        storage.save_sync_log.side_effect = mock_save_log

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=_make_group("Tier 1"),
                        )
                    ],
                ),
            ]
        )

        await applier.apply(report, dry_run=False)

        assert call_order.index("create_group") < call_order.index(
            "save_sync_log"
        )

    @pytest.mark.asyncio
    async def test_sync_log_has_correct_counts(self) -> None:
        """Sync log records correct groups_created and groups_updated."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    app_key="app1",
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=_make_group("T1", position=1),
                        ),
                        _make_group_diff(
                            action=DiffAction.UPDATE,
                            group_id=42,
                            desired_group=_make_group(
                                "T2", countries=["GB"], group_id=42
                            ),
                        ),
                    ],
                ),
            ]
        )

        await applier.apply(report, dry_run=False)

        log_call = storage.save_sync_log.call_args[0][0]
        assert log_call.app_key == "app1"
        assert log_call.groups_created == 1
        assert log_call.groups_updated == 1
        assert log_call.status == ApplyStatus.SUCCESS
        assert log_call.action == "sync"


class TestErrorIsolation:
    """Tests for per-app error isolation."""

    @pytest.mark.asyncio
    async def test_one_app_failure_does_not_affect_others(self) -> None:
        """Failure on one app does not prevent other apps from processing."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        call_count = 0

        async def mock_get_groups(app_key: str) -> list[Group]:
            nonlocal call_count
            call_count += 1
            if app_key == "app1":
                raise RuntimeError("API error for app1")
            return []

        adapter.get_groups.side_effect = mock_get_groups

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    app_key="app1",
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=_make_group("Tier 1"),
                        )
                    ],
                ),
                _make_app_report(
                    app_key="app2",
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=_make_group("Tier 1"),
                        )
                    ],
                ),
            ]
        )

        result = await applier.apply(report, dry_run=False)

        app1_result = result.app_results[0]
        app2_result = result.app_results[1]

        assert app1_result.status == ApplyStatus.FAILED
        assert "API error for app1" in (app1_result.error or "")
        assert app2_result.status == ApplyStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_failed_app_has_zero_counts(self) -> None:
        """Failed app result has zero create/update counts."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        adapter.get_groups.side_effect = RuntimeError("boom")

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=_make_group("Tier 1"),
                        )
                    ],
                ),
            ]
        )

        result = await applier.apply(report, dry_run=False)

        assert result.app_results[0].groups_created == 0
        assert result.app_results[0].groups_updated == 0


class TestPostWriteVerification:
    """Tests for post-write verification."""

    @pytest.mark.asyncio
    async def test_get_groups_called_after_writes(self) -> None:
        """adapter.get_groups() is called after write operations."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    app_key="app1",
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=_make_group("Tier 1"),
                        )
                    ],
                ),
            ]
        )

        await applier.apply(report, dry_run=False)

        # get_groups is called twice: once for snapshot, once for verification
        assert adapter.get_groups.call_count == 2

    @pytest.mark.asyncio
    async def test_create_mismatch_populates_warnings(self) -> None:
        """Missing group after CREATE populates warnings."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        # Post-write get_groups returns empty (group not found)
        adapter.get_groups.return_value = []

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=_make_group("Tier 1"),
                        )
                    ],
                ),
            ]
        )

        result = await applier.apply(report, dry_run=False)

        assert result.app_results[0].status == ApplyStatus.SUCCESS
        assert len(result.app_results[0].warnings) >= 1
        assert "Tier 1" in result.app_results[0].warnings[0]
        assert "not found" in result.app_results[0].warnings[0]

    @pytest.mark.asyncio
    async def test_update_country_mismatch_populates_warnings(self) -> None:
        """Country mismatch after UPDATE populates warnings."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        # Post-write: group exists but with wrong countries
        adapter.get_groups.return_value = [
            _make_group(
                "Tier 1", countries=["US"], group_id=42
            )  # Expected: ["US", "GB"]
        ]

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.UPDATE,
                            group_id=42,
                            desired_group=_make_group(
                                "Tier 1",
                                countries=["US", "GB"],
                                group_id=42,
                            ),
                        )
                    ],
                ),
            ]
        )

        result = await applier.apply(report, dry_run=False)

        assert result.app_results[0].status == ApplyStatus.SUCCESS
        assert len(result.app_results[0].warnings) >= 1
        assert "mismatch" in result.app_results[0].warnings[0]

    @pytest.mark.asyncio
    async def test_verification_success_no_warnings(self) -> None:
        """Successful verification produces no warnings."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        created_group = _make_group("Tier 1", group_id=100)
        adapter.get_groups.return_value = [created_group]

        report = _make_diff_report(
            app_reports=[
                _make_app_report(
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=_make_group("Tier 1"),
                        )
                    ],
                ),
            ]
        )

        result = await applier.apply(report, dry_run=False)

        assert result.app_results[0].warnings == []


class TestApplyResultTotals:
    """Tests for ApplyResult computed totals."""

    @pytest.mark.asyncio
    async def test_totals_correct_mixed_statuses(self) -> None:
        """total_success, total_failed, total_skipped are correct."""
        adapter, storage = _make_mocks()
        applier = Applier(adapter=adapter, storage=storage)

        async def mock_get_groups(app_key: str) -> list[Group]:
            if app_key == "fail_app":
                raise RuntimeError("boom")
            return []

        adapter.get_groups.side_effect = mock_get_groups

        report = _make_diff_report(
            app_reports=[
                # App 1: success (all unchanged = idempotency)
                _make_app_report(
                    app_key="ok_app",
                    group_diffs=[
                        _make_group_diff(action=DiffAction.UNCHANGED),
                    ],
                ),
                # App 2: skipped (A/B test)
                _make_app_report(
                    app_key="ab_app",
                    has_ab_test=True,
                    ab_test_warning="active test",
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=_make_group("Tier 1"),
                        )
                    ],
                ),
                # App 3: fail
                _make_app_report(
                    app_key="fail_app",
                    group_diffs=[
                        _make_group_diff(
                            action=DiffAction.CREATE,
                            desired_group=_make_group("Tier 1"),
                        )
                    ],
                ),
            ]
        )

        result = await applier.apply(report, dry_run=False)

        assert result.total_success == 1
        assert result.total_skipped == 1
        assert result.total_failed == 1
        assert result.was_dry_run is False


class TestImportExport:
    """Tests for module import and export."""

    def test_applier_importable_from_engine(self) -> None:
        """Applier is importable from admedi.engine."""
        from admedi.engine import Applier as ImportedApplier

        assert ImportedApplier is Applier

    def test_applier_in_engine_all(self) -> None:
        """Applier is in engine __all__."""
        import admedi.engine

        assert "Applier" in admedi.engine.__all__


# ---------------------------------------------------------------------------
# Helpers for build_waterfall_payload tests
# ---------------------------------------------------------------------------


def _make_instance(
    instance_id: int,
    name: str,
    network: str,
    is_bidder: bool = False,
    group_rate: float | None = None,
) -> Instance:
    """Create an Instance model for testing."""
    data = {
        "id": instance_id,
        "name": name,
        "networkName": network,
        "isBidder": is_bidder,
    }
    if group_rate is not None:
        data["groupRate"] = group_rate
    return Instance.model_validate(data)


# ---------------------------------------------------------------------------
# Tests: build_waterfall_payload()
# ---------------------------------------------------------------------------


class TestBuildWaterfallPayloadResolved:
    """Tests for build_waterfall_payload() when all entries resolve successfully."""

    def test_all_entries_resolved_with_bidder_and_manual_separation(self) -> None:
        """Returns valid payload with bidders in 'bidding' tier and manuals in 'tier1'."""
        preset = [
            {"network": "Meta", "bidder": True},
            {"network": "AppLovin", "bidder": False, "rate": 10.0},
        ]
        live = [
            _make_instance(101, "Meta Default", "Meta", is_bidder=True),
            _make_instance(201, "AppLovin Default", "AppLovin", is_bidder=False),
        ]

        payload, warnings = build_waterfall_payload(preset, live)

        assert payload is not None
        assert warnings == []
        # Check bidding tier
        assert "bidding" in payload
        assert payload["bidding"]["tierType"] == "bidding"
        assert len(payload["bidding"]["instances"]) == 1
        assert payload["bidding"]["instances"][0]["providerName"] == "Meta"
        assert payload["bidding"]["instances"][0]["instanceId"] == 101
        # Check manual tier
        assert "tier1" in payload
        assert payload["tier1"]["tierType"] == "sortByCpm"
        assert len(payload["tier1"]["instances"]) == 1
        assert payload["tier1"]["instances"][0]["providerName"] == "AppLovin"
        assert payload["tier1"]["instances"][0]["instanceId"] == 201
        assert payload["tier1"]["instances"][0]["rate"] == 10.0

    def test_rate_included_only_for_manual_instances(self) -> None:
        """Rate is included for manual instances with rate, but not for bidders."""
        preset = [
            {"network": "Meta", "bidder": True, "rate": 5.0},
            {"network": "AppLovin", "bidder": False, "rate": 10.0},
        ]
        live = [
            _make_instance(101, "Meta Default", "Meta", is_bidder=True),
            _make_instance(201, "AppLovin Default", "AppLovin", is_bidder=False),
        ]

        payload, warnings = build_waterfall_payload(preset, live)

        assert payload is not None
        # Bidder should NOT have rate (even if preset specifies one)
        assert "rate" not in payload["bidding"]["instances"][0]
        # Manual should have rate
        assert payload["tier1"]["instances"][0]["rate"] == 10.0

    def test_bidders_only_payload(self) -> None:
        """Payload with only bidders has 'bidding' key but no 'tier1'."""
        preset = [
            {"network": "Meta", "bidder": True},
            {"network": "Pangle", "bidder": True},
        ]
        live = [
            _make_instance(101, "Meta Default", "Meta", is_bidder=True),
            _make_instance(102, "Pangle Default", "Pangle", is_bidder=True),
        ]

        payload, warnings = build_waterfall_payload(preset, live)

        assert payload is not None
        assert "bidding" in payload
        assert "tier1" not in payload
        assert len(payload["bidding"]["instances"]) == 2


class TestBuildWaterfallPayloadMissing:
    """Tests for build_waterfall_payload() with missing networks (rule 4)."""

    def test_missing_network_skipped_with_warning(self) -> None:
        """Missing network produces warning but payload is still valid."""
        preset = [
            {"network": "Meta", "bidder": True},
            {"network": "NonExistent", "bidder": True},
        ]
        live = [
            _make_instance(101, "Meta Default", "Meta", is_bidder=True),
        ]

        payload, warnings = build_waterfall_payload(preset, live)

        assert payload is not None
        assert len(warnings) == 1
        assert "NonExistent" in warnings[0]
        assert "not found" in warnings[0]
        # Payload contains only the resolved entry
        assert len(payload["bidding"]["instances"]) == 1
        assert payload["bidding"]["instances"][0]["instanceId"] == 101

    def test_all_missing_returns_none_payload(self) -> None:
        """When all entries are missing (skipped), returns None payload."""
        preset = [
            {"network": "NonExistent1", "bidder": True},
            {"network": "NonExistent2", "bidder": False},
        ]
        live = [
            _make_instance(101, "Meta Default", "Meta", is_bidder=True),
        ]

        payload, warnings = build_waterfall_payload(preset, live)

        assert payload is None
        assert len(warnings) == 2


class TestBuildWaterfallPayloadAmbiguous:
    """Tests for build_waterfall_payload() with ambiguous matches (rule 3)."""

    def test_ambiguous_match_returns_none_payload(self) -> None:
        """Multiple instances for same network+bidder without name aborts."""
        preset = [
            {"network": "AppLovin", "bidder": False},
        ]
        live = [
            _make_instance(201, "AppLovin High", "AppLovin", is_bidder=False),
            _make_instance(202, "AppLovin Low", "AppLovin", is_bidder=False),
        ]

        payload, warnings = build_waterfall_payload(preset, live)

        assert payload is None
        assert len(warnings) == 1
        assert "cannot resolve" in warnings[0].lower()
        assert "AppLovin" in warnings[0]


class TestBuildWaterfallPayloadNameMatch:
    """Tests for build_waterfall_payload() with name-based matching (rules 1/5)."""

    def test_name_match_resolves_correctly(self) -> None:
        """Preset with name resolves to the matching instance."""
        preset = [
            {"network": "AppLovin", "bidder": False, "name": "AppLovin High", "rate": 15.0},
        ]
        live = [
            _make_instance(201, "AppLovin High", "AppLovin", is_bidder=False),
            _make_instance(202, "AppLovin Low", "AppLovin", is_bidder=False),
        ]

        payload, warnings = build_waterfall_payload(preset, live)

        assert payload is not None
        assert warnings == []
        assert payload["tier1"]["instances"][0]["instanceId"] == 201

    def test_name_not_found_aborts(self) -> None:
        """Preset with name that matches no instance aborts (rule 5)."""
        preset = [
            {"network": "AppLovin", "bidder": False, "name": "AppLovin Premium"},
        ]
        live = [
            _make_instance(201, "AppLovin High", "AppLovin", is_bidder=False),
            _make_instance(202, "AppLovin Low", "AppLovin", is_bidder=False),
        ]

        payload, warnings = build_waterfall_payload(preset, live)

        assert payload is None
        assert len(warnings) == 1
        assert "AppLovin Premium" in warnings[0]
        assert "aborting" in warnings[0].lower()


class TestBuildWaterfallPayloadEmpty:
    """Tests for build_waterfall_payload() with empty preset."""

    def test_empty_preset_returns_none_and_no_warnings(self) -> None:
        """Empty preset returns None payload and no warnings."""
        payload, warnings = build_waterfall_payload([], [])

        assert payload is None
        assert warnings == []

    def test_empty_preset_with_live_instances_returns_none(self) -> None:
        """Empty preset with live instances still returns None."""
        live = [
            _make_instance(101, "Meta Default", "Meta", is_bidder=True),
        ]

        payload, warnings = build_waterfall_payload([], live)

        assert payload is None
        assert warnings == []


class TestBuildWaterfallPayloadImport:
    """Tests for build_waterfall_payload import."""

    def test_importable_from_applier_module(self) -> None:
        """build_waterfall_payload is importable from admedi.engine.applier."""
        from admedi.engine.applier import build_waterfall_payload as imported

        assert imported is build_waterfall_payload


# ---------------------------------------------------------------------------
# Test Classes: Cross-app sync preset resolution (Step 8)
# ---------------------------------------------------------------------------


class TestCrossAppSyncPresetResolution:
    """Tests for cross-app sync where preset refs are resolved against destination app instances."""

    def test_preset_resolves_against_destination_instances(self) -> None:
        """build_waterfall_payload matches by network name, not by source app instance IDs."""
        # Source app has Meta with instance_id=100
        # Destination app has Meta with instance_id=200
        # The preset entries don't carry instance_id, so resolution works
        # against the destination app's live instances.
        preset_entries = [
            {"network": "Meta", "bidder": True},
            {"network": "AppLovin", "bidder": False, "rate": 5.0},
        ]

        # Destination app's live instances (different IDs from source)
        dest_instances = [
            _make_instance(200, "Meta Default", "Meta", is_bidder=True),
            _make_instance(201, "AppLovin Default", "AppLovin", is_bidder=False),
        ]

        payload, warnings = build_waterfall_payload(preset_entries, dest_instances)

        assert payload is not None
        assert warnings == []
        # Verify resolution used destination instance IDs (200, 201), not source
        bidding_ids = [
            i["instanceId"] for i in payload["bidding"]["instances"]
        ]
        assert 200 in bidding_ids
        manual_ids = [
            i["instanceId"] for i in payload["tier1"]["instances"]
        ]
        assert 201 in manual_ids

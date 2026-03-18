"""Tests for the config engine Differ (compute_diff).

Covers all acceptance criteria from the plan Step 6 specification:
- Perfect match produces UNCHANGED
- Country mismatch produces UPDATE with FieldChange
- Missing group produces CREATE
- Extra remote group produces EXTRA
- Position mismatch produces UPDATE
- Single-group format skips position comparison
- A/B test detection
- All Countries handling
- desired_group correctness for CREATE and UPDATE
- Empty remote group list
- Position-matched name mismatch
- Default-only format matching
- Remote-only format produces EXTRA
- Known limitation: simultaneous rename + reposition
"""

from __future__ import annotations

import pytest

from admedi.engine.differ import compute_diff
from admedi.exceptions import ConfigValidationError
from admedi.models.diff import AppDiffReport, DiffAction, FieldChange, GroupDiff
from admedi.models.enums import AdFormat, Mediator, Platform
from admedi.models.group import Group
from admedi.models.instance import Instance
from admedi.models.portfolio import PortfolioApp, PortfolioConfig, PortfolioTier


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

APP_KEY = "test_app_123"
APP_NAME = "Test App"


def _make_template(
    tiers: list[PortfolioTier],
    mediator: Mediator = Mediator.LEVELPLAY,
) -> PortfolioConfig:
    """Create a minimal PortfolioConfig with the given tiers."""
    return PortfolioConfig(
        schema_version=1,
        mediator=mediator,
        portfolio=[
            PortfolioApp(
                app_key=APP_KEY,
                name=APP_NAME,
                platform=Platform.IOS,
            ),
        ],
        tiers=tiers,
    )


def _make_group(
    name: str,
    ad_format: AdFormat,
    countries: list[str],
    position: int,
    group_id: int | None = None,
    ab_test: str | None = None,
) -> Group:
    """Create a Group with minimal fields."""
    return Group(
        group_id=group_id,
        group_name=name,
        ad_format=ad_format,
        countries=countries,
        position=position,
        ab_test=ab_test,
    )


def _make_instance(
    network_name: str,
    is_bidder: bool,
    instance_name: str = "Default",
    instance_id: int = 1,
    group_rate: float | None = None,
) -> Instance:
    """Create an Instance with minimal fields."""
    return Instance(
        instance_id=instance_id,
        instance_name=instance_name,
        network_name=network_name,
        is_bidder=is_bidder,
        group_rate=group_rate,
    )


def _make_group_with_instances(
    name: str,
    ad_format: AdFormat,
    countries: list[str],
    position: int,
    group_id: int,
    instances: list[Instance] | None = None,
) -> Group:
    """Create a Group with instances for waterfall testing."""
    return Group(
        group_id=group_id,
        group_name=name,
        ad_format=ad_format,
        countries=countries,
        position=position,
        instances=instances,
    )


def _shelf_sort_template() -> PortfolioConfig:
    """Standard 4-tier Shelf Sort template for interstitial + rewarded."""
    return _make_template(
        tiers=[
            PortfolioTier(
                name="Tier 1",
                countries=["US"],
                position=1,
                ad_formats=[AdFormat.INTERSTITIAL, AdFormat.REWARDED],
            ),
            PortfolioTier(
                name="Tier 2",
                countries=["AU", "CA", "DE", "GB", "JP", "NZ", "KR", "TW"],
                position=2,
                ad_formats=[AdFormat.INTERSTITIAL, AdFormat.REWARDED],
            ),
            PortfolioTier(
                name="Tier 3",
                countries=["FR", "NL"],
                position=3,
                ad_formats=[AdFormat.INTERSTITIAL, AdFormat.REWARDED],
            ),
            PortfolioTier(
                name="All Countries",
                countries=["*"],
                position=4,
                is_default=True,
                ad_formats=[
                    AdFormat.BANNER,
                    AdFormat.INTERSTITIAL,
                    AdFormat.REWARDED,
                    AdFormat.NATIVE,
                ],
            ),
        ],
    )


def _matching_remote_groups(ad_format: AdFormat) -> list[Group]:
    """Remote groups that perfectly match the Shelf Sort template for one format."""
    return [
        _make_group("Tier 1", ad_format, ["US"], 1, group_id=101),
        _make_group("Tier 2", ad_format, ["AU", "CA", "DE", "GB", "JP", "NZ", "KR", "TW"], 2, group_id=102),
        _make_group("Tier 3", ad_format, ["FR", "NL"], 3, group_id=103),
        _make_group("All Countries", ad_format, ["*"], 4, group_id=104),
    ]


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestPerfectMatch:
    """Perfect match between template and remote produces UNCHANGED."""

    def test_all_unchanged_interstitial(self) -> None:
        """All interstitial groups match -- all UNCHANGED."""
        template = _shelf_sort_template()
        remote = _matching_remote_groups(AdFormat.INTERSTITIAL)
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        interstitial_diffs = [
            d for d in report.group_diffs if d.ad_format == AdFormat.INTERSTITIAL
        ]
        assert len(interstitial_diffs) == 4
        for diff in interstitial_diffs:
            assert diff.action == DiffAction.UNCHANGED
            assert diff.changes == []
            assert diff.desired_group is None

    def test_all_unchanged_full_portfolio(self) -> None:
        """All 4 formats with matching groups produce all UNCHANGED."""
        template = _shelf_sort_template()
        remote = (
            _matching_remote_groups(AdFormat.INTERSTITIAL)
            + _matching_remote_groups(AdFormat.REWARDED)
            + [_make_group("All Countries", AdFormat.BANNER, ["*"], 4, group_id=201)]
            + [_make_group("All Countries", AdFormat.NATIVE, ["*"], 4, group_id=301)]
        )
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        for diff in report.group_diffs:
            assert diff.action == DiffAction.UNCHANGED, (
                f"{diff.group_name} ({diff.ad_format}) should be UNCHANGED, got {diff.action}"
            )

    def test_report_metadata(self) -> None:
        """AppDiffReport has correct app_key and app_name."""
        template = _shelf_sort_template()
        remote = _matching_remote_groups(AdFormat.INTERSTITIAL)
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        assert report.app_key == APP_KEY
        assert report.app_name == APP_NAME
        assert isinstance(report, AppDiffReport)


class TestCountryMismatch:
    """Country differences produce UPDATE with FieldChange."""

    def test_nz_vs_nl_in_tier2(self) -> None:
        """Template has NZ in Tier 2, remote has NL instead."""
        template = _shelf_sort_template()
        # Remote Tier 2 has NL instead of NZ
        remote = [
            _make_group("Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=101),
            _make_group(
                "Tier 2",
                AdFormat.INTERSTITIAL,
                ["AU", "CA", "DE", "GB", "JP", "NL", "KR", "TW"],
                2,
                group_id=102,
            ),
            _make_group("Tier 3", AdFormat.INTERSTITIAL, ["FR", "NL"], 3, group_id=103),
            _make_group("All Countries", AdFormat.INTERSTITIAL, ["*"], 4, group_id=104),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        tier2_diffs = [
            d
            for d in report.group_diffs
            if d.group_name == "Tier 2" and d.ad_format == AdFormat.INTERSTITIAL
        ]
        assert len(tier2_diffs) == 1
        diff = tier2_diffs[0]
        assert diff.action == DiffAction.UPDATE
        assert diff.group_id == 102

        country_changes = [c for c in diff.changes if c.field == "countries"]
        assert len(country_changes) == 1
        change = country_changes[0]
        assert "NZ" in change.new_value
        assert "NL" not in change.new_value or "NL" in change.old_value

    def test_country_change_description(self) -> None:
        """FieldChange description mentions added and removed countries."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="Tier 1",
                    countries=["US", "GB"],
                    position=1,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=2,
                    is_default=True,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
            ],
        )
        remote = [
            _make_group("Tier 1", AdFormat.INTERSTITIAL, ["US", "CA"], 1, group_id=1),
            _make_group("All Countries", AdFormat.INTERSTITIAL, ["*"], 2, group_id=2),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        tier1_diff = next(
            d for d in report.group_diffs if d.group_name == "Tier 1"
        )
        assert tier1_diff.action == DiffAction.UPDATE
        country_change = next(c for c in tier1_diff.changes if c.field == "countries")
        assert "GB" in country_change.description
        assert "CA" in country_change.description


class TestMissingGroup:
    """Template tiers without matching remote groups produce CREATE."""

    def test_four_tiers_vs_three_remote(self) -> None:
        """Template has 4 tiers, remote has 3 groups -- one CREATE."""
        template = _shelf_sort_template()
        # Remote is missing Tier 3
        remote = [
            _make_group("Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=101),
            _make_group(
                "Tier 2",
                AdFormat.INTERSTITIAL,
                ["AU", "CA", "DE", "GB", "JP", "NZ", "KR", "TW"],
                2,
                group_id=102,
            ),
            _make_group("All Countries", AdFormat.INTERSTITIAL, ["*"], 4, group_id=104),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        creates = [
            d
            for d in report.group_diffs
            if d.action == DiffAction.CREATE and d.ad_format == AdFormat.INTERSTITIAL
        ]
        assert len(creates) == 1
        assert creates[0].group_name == "Tier 3"

    def test_empty_remote_all_creates(self) -> None:
        """Empty remote group list with template tiers produces all CREATE."""
        template = _shelf_sort_template()
        report = compute_diff(template.tiers, [], APP_KEY, APP_NAME)

        creates = [d for d in report.group_diffs if d.action == DiffAction.CREATE]
        # 4 tiers x 2 formats (interstitial + rewarded) + 1 (banner All Countries) + 1 (native All Countries) = 10
        assert len(creates) == 10
        # All should have group_id=None
        for c in creates:
            assert c.group_id is None


class TestExtraGroup:
    """Remote groups not in template produce EXTRA."""

    def test_extra_remote_group(self) -> None:
        """Remote has a group not in any template tier."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="Tier 1",
                    countries=["US"],
                    position=1,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=2,
                    is_default=True,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
            ],
        )
        remote = [
            _make_group("Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1),
            _make_group("All Countries", AdFormat.INTERSTITIAL, ["*"], 2, group_id=2),
            _make_group("Mystery Group", AdFormat.INTERSTITIAL, ["JP"], 3, group_id=3),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        extras = [d for d in report.group_diffs if d.action == DiffAction.EXTRA]
        assert len(extras) == 1
        assert extras[0].group_name == "Mystery Group"
        assert extras[0].group_id == 3
        assert extras[0].desired_group is None

    def test_remote_format_not_in_template(self) -> None:
        """Remote groups in a format not covered by any template tier are EXTRA."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="Tier 1",
                    countries=["US"],
                    position=1,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=2,
                    is_default=True,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
            ],
        )
        # Remote has native groups, but template has no native tiers
        remote = [
            _make_group("Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1),
            _make_group("All Countries", AdFormat.INTERSTITIAL, ["*"], 2, group_id=2),
            _make_group("Native Group", AdFormat.NATIVE, ["US"], 1, group_id=10),
            _make_group("Native All", AdFormat.NATIVE, ["*"], 2, group_id=11),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        native_diffs = [
            d for d in report.group_diffs if d.ad_format == AdFormat.NATIVE
        ]
        assert len(native_diffs) == 2
        for nd in native_diffs:
            assert nd.action == DiffAction.EXTRA


class TestPositionMismatch:
    """Position differences produce UPDATE with FieldChange."""

    def test_position_mismatch_update(self) -> None:
        """Template position differs from remote position -- UPDATE."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="Tier 1",
                    countries=["US"],
                    position=1,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=2,
                    is_default=True,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
            ],
        )
        remote = [
            _make_group("Tier 1", AdFormat.INTERSTITIAL, ["US"], 3, group_id=1),
            _make_group("All Countries", AdFormat.INTERSTITIAL, ["*"], 2, group_id=2),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        tier1_diff = next(
            d
            for d in report.group_diffs
            if d.group_name == "Tier 1" and d.ad_format == AdFormat.INTERSTITIAL
        )
        assert tier1_diff.action == DiffAction.UPDATE
        pos_changes = [c for c in tier1_diff.changes if c.field == "position"]
        assert len(pos_changes) == 1
        assert pos_changes[0].old_value == 3
        assert pos_changes[0].new_value == 1


class TestSingleGroupPositionSkip:
    """Banner with single group skips position comparison."""

    def test_banner_single_group_position_skip(self) -> None:
        """Single group in both template and remote -- skip position comparison."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=4,
                    is_default=True,
                    ad_formats=[AdFormat.BANNER],
                ),
            ],
        )
        # Remote has position 1, template says position 4 -- but single group, so skip
        remote = [
            _make_group("All Countries", AdFormat.BANNER, ["*"], 1, group_id=50),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        banner_diffs = [
            d for d in report.group_diffs if d.ad_format == AdFormat.BANNER
        ]
        assert len(banner_diffs) == 1
        assert banner_diffs[0].action == DiffAction.UNCHANGED
        assert banner_diffs[0].changes == []

    def test_multi_group_format_does_not_skip_position(self) -> None:
        """Multiple groups in format -- position IS compared."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="Tier 1",
                    countries=["US"],
                    position=1,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=2,
                    is_default=True,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
            ],
        )
        remote = [
            _make_group("Tier 1", AdFormat.INTERSTITIAL, ["US"], 3, group_id=1),
            _make_group("All Countries", AdFormat.INTERSTITIAL, ["*"], 2, group_id=2),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        tier1_diff = next(
            d
            for d in report.group_diffs
            if d.group_name == "Tier 1" and d.ad_format == AdFormat.INTERSTITIAL
        )
        assert tier1_diff.action == DiffAction.UPDATE
        assert any(c.field == "position" for c in tier1_diff.changes)


class TestAbTestDetection:
    """A/B test detection on remote groups."""

    def test_ab_test_active(self) -> None:
        """Remote group with ab_test set triggers has_ab_test=True."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=1,
                    is_default=True,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
            ],
        )
        remote = [
            _make_group(
                "All Countries",
                AdFormat.INTERSTITIAL,
                ["*"],
                1,
                group_id=1,
                ab_test="experiment_123",
            ),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        assert report.has_ab_test is True
        assert report.ab_test_warning is not None
        assert "experiment_123" in report.ab_test_warning

    def test_ab_test_na_not_active(self) -> None:
        """Remote group with ab_test='N/A' does NOT trigger has_ab_test."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=1,
                    is_default=True,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
            ],
        )
        remote = [
            _make_group(
                "All Countries",
                AdFormat.INTERSTITIAL,
                ["*"],
                1,
                group_id=1,
                ab_test="N/A",
            ),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        assert report.has_ab_test is False
        assert report.ab_test_warning is None

    def test_ab_test_none_not_active(self) -> None:
        """Remote group with ab_test=None does NOT trigger has_ab_test."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=1,
                    is_default=True,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
            ],
        )
        remote = [
            _make_group(
                "All Countries",
                AdFormat.INTERSTITIAL,
                ["*"],
                1,
                group_id=1,
                ab_test=None,
            ),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        assert report.has_ab_test is False
        assert report.ab_test_warning is None

    def test_no_ab_test_when_no_remote_groups(self) -> None:
        """No remote groups means no A/B test."""
        template = _shelf_sort_template()
        report = compute_diff(template.tiers, [], APP_KEY, APP_NAME)

        assert report.has_ab_test is False
        assert report.ab_test_warning is None


class TestAllCountries:
    """All Countries (["*"]) matching."""

    def test_wildcard_matches_correctly(self) -> None:
        """Template ["*"] matches remote ["*"] as UNCHANGED."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=1,
                    is_default=True,
                    ad_formats=[AdFormat.BANNER],
                ),
            ],
        )
        remote = [
            _make_group("All Countries", AdFormat.BANNER, ["*"], 1, group_id=1),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        assert len(report.group_diffs) == 1
        assert report.group_diffs[0].action == DiffAction.UNCHANGED

    def test_wildcard_vs_explicit_countries_is_update(self) -> None:
        """Template ["*"] vs remote explicit countries produces UPDATE."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=1,
                    is_default=True,
                    ad_formats=[AdFormat.BANNER],
                ),
            ],
        )
        remote = [
            _make_group(
                "All Countries", AdFormat.BANNER, ["US", "GB", "DE"], 1, group_id=1
            ),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        diff = report.group_diffs[0]
        assert diff.action == DiffAction.UPDATE
        country_change = next(c for c in diff.changes if c.field == "countries")
        assert country_change.new_value == ["*"]


class TestDesiredGroup:
    """Verify desired_group correctness for CREATE and UPDATE actions."""

    def test_create_desired_group_has_none_id(self) -> None:
        """CREATE desired_group has group_id=None and template fields."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="Tier 1",
                    countries=["US"],
                    position=1,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=2,
                    is_default=True,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
            ],
        )
        # Only All Countries exists in remote -- Tier 1 needs CREATE
        remote = [
            _make_group("All Countries", AdFormat.INTERSTITIAL, ["*"], 2, group_id=2),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        create_diff = next(
            d for d in report.group_diffs if d.action == DiffAction.CREATE
        )
        assert create_diff.desired_group is not None
        dg = create_diff.desired_group
        assert dg.group_id is None
        assert dg.group_name == "Tier 1"
        assert dg.ad_format == AdFormat.INTERSTITIAL
        assert dg.countries == ["US"]
        assert dg.position == 1

    def test_update_desired_group_has_remote_id(self) -> None:
        """UPDATE desired_group has group_id from matched remote group."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="Tier 1",
                    countries=["US", "GB"],
                    position=1,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=2,
                    is_default=True,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
            ],
        )
        remote = [
            _make_group("Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=42),
            _make_group("All Countries", AdFormat.INTERSTITIAL, ["*"], 2, group_id=43),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        update_diff = next(
            d for d in report.group_diffs if d.action == DiffAction.UPDATE
        )
        assert update_diff.desired_group is not None
        dg = update_diff.desired_group
        assert dg.group_id == 42
        assert dg.group_name == "Tier 1"
        assert dg.countries == ["US", "GB"]
        assert dg.position == 1
        assert dg.ad_format == AdFormat.INTERSTITIAL

    def test_unchanged_desired_group_is_none(self) -> None:
        """UNCHANGED diffs have desired_group=None."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=1,
                    is_default=True,
                    ad_formats=[AdFormat.BANNER],
                ),
            ],
        )
        remote = [
            _make_group("All Countries", AdFormat.BANNER, ["*"], 1, group_id=1),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        assert report.group_diffs[0].action == DiffAction.UNCHANGED
        assert report.group_diffs[0].desired_group is None

    def test_extra_desired_group_is_none(self) -> None:
        """EXTRA diffs have desired_group=None."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=1,
                    is_default=True,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
            ],
        )
        remote = [
            _make_group("All Countries", AdFormat.INTERSTITIAL, ["*"], 1, group_id=1),
            _make_group("Rogue Group", AdFormat.INTERSTITIAL, ["JP"], 2, group_id=99),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        extra_diff = next(
            d for d in report.group_diffs if d.action == DiffAction.EXTRA
        )
        assert extra_diff.desired_group is None


class TestPositionMatching:
    """Position-based secondary matching."""

    def test_position_matched_name_mismatch(self) -> None:
        """Template tier matched by position to remote group with different name produces UPDATE with name FieldChange."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="Tier 1",
                    countries=["US"],
                    position=1,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=2,
                    is_default=True,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
            ],
        )
        # Remote group at position 1 has a different name
        remote = [
            _make_group(
                "US Premium", AdFormat.INTERSTITIAL, ["US"], 1, group_id=10
            ),
            _make_group(
                "All Countries", AdFormat.INTERSTITIAL, ["*"], 2, group_id=11
            ),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        # "Tier 1" didn't match by name; "US Premium" didn't match by name.
        # They match by position (both position=1).
        # This should produce an UPDATE with a name change.
        update_diffs = [
            d
            for d in report.group_diffs
            if d.action == DiffAction.UPDATE and d.ad_format == AdFormat.INTERSTITIAL
        ]
        assert len(update_diffs) == 1
        name_changes = [c for c in update_diffs[0].changes if c.field == "name"]
        assert len(name_changes) == 1
        assert name_changes[0].old_value == "US Premium"
        assert name_changes[0].new_value == "Tier 1"


class TestDefaultOnlyFormat:
    """Template with only a default/catch-all tier for a format."""

    def test_default_only_matches_single_remote(self) -> None:
        """Template with only default tier for banner matches remote's single All Countries as UNCHANGED."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="Tier 1",
                    countries=["US"],
                    position=1,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=2,
                    is_default=True,
                    ad_formats=[AdFormat.INTERSTITIAL, AdFormat.BANNER],
                ),
            ],
        )
        remote = [
            _make_group("Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1),
            _make_group("All Countries", AdFormat.INTERSTITIAL, ["*"], 2, group_id=2),
            _make_group("All Countries", AdFormat.BANNER, ["*"], 1, group_id=3),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        banner_diffs = [
            d for d in report.group_diffs if d.ad_format == AdFormat.BANNER
        ]
        assert len(banner_diffs) == 1
        # Name matches, single group on each side => position skipped
        assert banner_diffs[0].action == DiffAction.UNCHANGED


class TestKnownLimitations:
    """Known limitation: simultaneous rename + reposition produces CREATE + EXTRA."""

    def test_simultaneous_rename_and_reposition(self) -> None:
        """Rename + reposition in the same sync produces CREATE + EXTRA.

        This is a known MVP limitation. When a template renames a tier AND
        changes its position, neither name match nor position match succeeds,
        resulting in the template tier being treated as new (CREATE) and the
        remote group as unrecognized (EXTRA).

        Workaround: perform renames and repositions in separate syncs.
        """
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="Premium US",  # renamed from "Tier 1"
                    countries=["US"],
                    position=2,  # repositioned from 1 to 2
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=3,
                    is_default=True,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
            ],
        )
        remote = [
            _make_group("Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1),
            _make_group("All Countries", AdFormat.INTERSTITIAL, ["*"], 3, group_id=2),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        interstitial_diffs = [
            d for d in report.group_diffs if d.ad_format == AdFormat.INTERSTITIAL
        ]
        actions = {d.action for d in interstitial_diffs}
        # "All Countries" matches by name -> UNCHANGED
        # "Premium US" (pos 2) vs "Tier 1" (pos 1) -> no name match, no position match
        # -> "Premium US" CREATE, "Tier 1" EXTRA
        assert DiffAction.CREATE in actions, "Expected CREATE for renamed+repositioned tier"
        assert DiffAction.EXTRA in actions, "Expected EXTRA for orphaned remote group"

        create_diff = next(
            d for d in interstitial_diffs if d.action == DiffAction.CREATE
        )
        assert create_diff.group_name == "Premium US"

        extra_diff = next(
            d for d in interstitial_diffs if d.action == DiffAction.EXTRA
        )
        assert extra_diff.group_name == "Tier 1"


class TestImport:
    """Verify import paths work."""

    def test_import_from_engine(self) -> None:
        """compute_diff is importable from admedi.engine."""
        from admedi.engine import compute_diff as cd

        assert callable(cd)

    def test_import_directly(self) -> None:
        """compute_diff is importable from admedi.engine.differ."""
        from admedi.engine.differ import compute_diff as cd

        assert callable(cd)


class TestMultiFormatConsistency:
    """Verify that the differ handles multi-format tiers correctly."""

    def test_tier_scoped_to_two_formats(self) -> None:
        """A tier scoped to [interstitial, rewarded] generates diffs for both formats."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="Tier 1",
                    countries=["US"],
                    position=1,
                    ad_formats=[AdFormat.INTERSTITIAL, AdFormat.REWARDED],
                ),
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=2,
                    is_default=True,
                    ad_formats=[AdFormat.INTERSTITIAL, AdFormat.REWARDED],
                ),
            ],
        )
        # Only interstitial remote groups, no rewarded
        remote = [
            _make_group("Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1),
            _make_group("All Countries", AdFormat.INTERSTITIAL, ["*"], 2, group_id=2),
        ]
        report = compute_diff(template.tiers, remote, APP_KEY, APP_NAME)

        interstitial_diffs = [
            d for d in report.group_diffs if d.ad_format == AdFormat.INTERSTITIAL
        ]
        rewarded_diffs = [
            d for d in report.group_diffs if d.ad_format == AdFormat.REWARDED
        ]

        # Interstitial: both match
        assert all(d.action == DiffAction.UNCHANGED for d in interstitial_diffs)

        # Rewarded: no remote groups -> all CREATE
        assert all(d.action == DiffAction.CREATE for d in rewarded_diffs)
        assert len(rewarded_diffs) == 2

    def test_create_desired_group_format_is_correct(self) -> None:
        """CREATE desired_group has the correct ad_format for the specific format iteration."""
        template = _make_template(
            tiers=[
                PortfolioTier(
                    name="Tier 1",
                    countries=["US"],
                    position=1,
                    ad_formats=[AdFormat.INTERSTITIAL, AdFormat.REWARDED],
                ),
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=2,
                    is_default=True,
                    ad_formats=[AdFormat.INTERSTITIAL, AdFormat.REWARDED],
                ),
            ],
        )
        report = compute_diff(template.tiers, [], APP_KEY, APP_NAME)

        for diff in report.group_diffs:
            assert diff.desired_group is not None
            assert diff.desired_group.ad_format == diff.ad_format


# ---------------------------------------------------------------------------
# Waterfall comparison tests (Step 6)
# ---------------------------------------------------------------------------


class TestWaterfallIdentical:
    """Identical waterfall produces no waterfall FieldChange."""

    def test_identical_waterfall_no_changes(self) -> None:
        """Preset matches live instances exactly -- no waterfall FieldChange."""
        preset = [
            {"network": "Meta", "bidder": True},
            {"network": "AppLovin", "bidder": False, "rate": 10.0},
        ]
        presets = {"standard": preset}

        tier = PortfolioTier(
            name="Tier 1",
            countries=["US"],
            position=1,
            ad_formats=[AdFormat.INTERSTITIAL],
            network_preset="standard",
        )
        group = _make_group_with_instances(
            "Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1,
            instances=[
                _make_instance("Meta", True, "Meta Default", 10),
                _make_instance("AppLovin", False, "AppLovin Default", 11, group_rate=10.0),
            ],
        )

        report = compute_diff([tier], [group], APP_KEY, APP_NAME, network_presets=presets)
        waterfall_changes = [
            c for d in report.group_diffs for c in d.changes if c.field == "waterfall"
        ]
        assert len(waterfall_changes) == 0

    def test_identical_waterfall_unchanged_action(self) -> None:
        """Tier with matching preset and matching tier fields is UNCHANGED."""
        preset = [{"network": "Meta", "bidder": True}]
        presets = {"standard": preset}

        tier = PortfolioTier(
            name="Tier 1",
            countries=["US"],
            position=1,
            ad_formats=[AdFormat.INTERSTITIAL],
            network_preset="standard",
        )
        group = _make_group_with_instances(
            "Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1,
            instances=[_make_instance("Meta", True, "Meta Default", 10)],
        )

        report = compute_diff([tier], [group], APP_KEY, APP_NAME, network_presets=presets)
        assert report.group_diffs[0].action == DiffAction.UNCHANGED


class TestWaterfallMissingBidder:
    """Missing bidder in live instances produces informational FieldChange."""

    def test_missing_bidder_in_live(self) -> None:
        """Preset has a bidder not present in live -- FieldChange with description."""
        preset = [
            {"network": "Meta", "bidder": True},
            {"network": "Pangle", "bidder": True},
        ]
        presets = {"standard": preset}

        tier = PortfolioTier(
            name="Tier 1",
            countries=["US"],
            position=1,
            ad_formats=[AdFormat.INTERSTITIAL],
            network_preset="standard",
        )
        group = _make_group_with_instances(
            "Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1,
            instances=[_make_instance("Meta", True, "Meta Default", 10)],
        )

        report = compute_diff([tier], [group], APP_KEY, APP_NAME, network_presets=presets)
        waterfall_changes = [
            c for d in report.group_diffs for c in d.changes if c.field == "waterfall"
        ]
        # Pangle is missing but informational
        assert len(waterfall_changes) == 1
        assert "Pangle" in waterfall_changes[0].description
        assert "not found" in waterfall_changes[0].description


class TestWaterfallExtraManual:
    """Extra manual instance in live not in preset is flagged as informational."""

    def test_extra_manual_instance_informational(self) -> None:
        """Live has a manual instance not in preset -- informational FieldChange (cannot remove via API)."""
        preset = [{"network": "Meta", "bidder": True}]
        presets = {"standard": preset}

        tier = PortfolioTier(
            name="Tier 1",
            countries=["US"],
            position=1,
            ad_formats=[AdFormat.INTERSTITIAL],
            network_preset="standard",
        )
        group = _make_group_with_instances(
            "Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1,
            instances=[
                _make_instance("Meta", True, "Meta Default", 10),
                _make_instance("ironSource", False, "ironSource Default", 11, group_rate=5.0),
            ],
        )

        report = compute_diff([tier], [group], APP_KEY, APP_NAME, network_presets=presets)
        waterfall_changes = [
            c for d in report.group_diffs for c in d.changes if c.field == "waterfall"
        ]
        assert len(waterfall_changes) == 1
        assert "ironSource" in waterfall_changes[0].description
        assert "cannot be removed via API" in waterfall_changes[0].description


class TestWaterfallRateChange:
    """Rate changes beyond tolerance produce FieldChange."""

    def test_rate_change_detected(self) -> None:
        """Preset rate 1.0, live rate 14.5 -- FieldChange with rate description."""
        preset = [{"network": "AppLovin", "bidder": False, "rate": 1.0}]
        presets = {"standard": preset}

        tier = PortfolioTier(
            name="Tier 1",
            countries=["US"],
            position=1,
            ad_formats=[AdFormat.INTERSTITIAL],
            network_preset="standard",
        )
        group = _make_group_with_instances(
            "Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1,
            instances=[
                _make_instance("AppLovin", False, "AppLovin Default", 10, group_rate=14.5),
            ],
        )

        report = compute_diff([tier], [group], APP_KEY, APP_NAME, network_presets=presets)
        waterfall_changes = [
            c for d in report.group_diffs for c in d.changes if c.field == "waterfall"
        ]
        assert len(waterfall_changes) == 1
        assert waterfall_changes[0].old_value == 14.5
        assert waterfall_changes[0].new_value == 1.0
        assert "rate" in waterfall_changes[0].description

    def test_rate_within_tolerance_no_change(self) -> None:
        """Preset 1.0, live 1.005 -- within 0.01 tolerance, no FieldChange."""
        preset = [{"network": "AppLovin", "bidder": False, "rate": 1.0}]
        presets = {"standard": preset}

        tier = PortfolioTier(
            name="Tier 1",
            countries=["US"],
            position=1,
            ad_formats=[AdFormat.INTERSTITIAL],
            network_preset="standard",
        )
        group = _make_group_with_instances(
            "Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1,
            instances=[
                _make_instance("AppLovin", False, "AppLovin Default", 10, group_rate=1.005),
            ],
        )

        report = compute_diff([tier], [group], APP_KEY, APP_NAME, network_presets=presets)
        waterfall_changes = [
            c for d in report.group_diffs for c in d.changes if c.field == "waterfall"
        ]
        assert len(waterfall_changes) == 0


class TestWaterfallNetworkPresetNone:
    """When network_preset is None, no waterfall comparison occurs."""

    def test_no_network_preset_no_waterfall_comparison(self) -> None:
        """Tier with network_preset=None skips waterfall comparison entirely."""
        presets = {
            "standard": [{"network": "Meta", "bidder": True}],
        }

        tier = PortfolioTier(
            name="Tier 1",
            countries=["US"],
            position=1,
            ad_formats=[AdFormat.INTERSTITIAL],
            network_preset=None,  # explicitly None
        )
        # Live has instances that would differ from any preset,
        # but comparison should be skipped
        group = _make_group_with_instances(
            "Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1,
            instances=[
                _make_instance("ironSource", False, "ironSource Default", 10, group_rate=5.0),
            ],
        )

        report = compute_diff([tier], [group], APP_KEY, APP_NAME, network_presets=presets)
        waterfall_changes = [
            c for d in report.group_diffs for c in d.changes if c.field == "waterfall"
        ]
        assert len(waterfall_changes) == 0
        assert report.group_diffs[0].action == DiffAction.UNCHANGED


class TestWaterfallNetworkPresetsNone:
    """When network_presets parameter is None, existing behavior is preserved."""

    def test_network_presets_none_no_waterfall_comparison(self) -> None:
        """compute_diff with network_presets=None produces same output as before."""
        tier = PortfolioTier(
            name="Tier 1",
            countries=["US"],
            position=1,
            ad_formats=[AdFormat.INTERSTITIAL],
            network_preset="standard",  # tier HAS a preset reference
        )
        group = _make_group_with_instances(
            "Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1,
            instances=[
                _make_instance("ironSource", False, "ironSource Default", 10, group_rate=5.0),
            ],
        )

        # network_presets=None (default) -- no waterfall comparison
        report = compute_diff([tier], [group], APP_KEY, APP_NAME, network_presets=None)
        waterfall_changes = [
            c for d in report.group_diffs for c in d.changes if c.field == "waterfall"
        ]
        assert len(waterfall_changes) == 0

    def test_network_presets_default_none(self) -> None:
        """compute_diff without network_presets arg uses default None."""
        tier = PortfolioTier(
            name="Tier 1",
            countries=["US"],
            position=1,
            ad_formats=[AdFormat.INTERSTITIAL],
            network_preset="standard",
        )
        group = _make_group_with_instances(
            "Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1,
            instances=[
                _make_instance("ironSource", False, "ironSource Default", 10),
            ],
        )

        # No network_presets keyword -- default None
        report = compute_diff([tier], [group], APP_KEY, APP_NAME)
        waterfall_changes = [
            c for d in report.group_diffs for c in d.changes if c.field == "waterfall"
        ]
        assert len(waterfall_changes) == 0


class TestWaterfallInvalidPresetRef:
    """Invalid preset reference raises ConfigValidationError."""

    def test_invalid_preset_ref_raises(self) -> None:
        """Tier references a preset not in network_presets -- raises ConfigValidationError."""
        presets = {"standard": [{"network": "Meta", "bidder": True}]}

        tier = PortfolioTier(
            name="Tier 1",
            countries=["US"],
            position=1,
            ad_formats=[AdFormat.INTERSTITIAL],
            network_preset="nonexistent_preset",
        )
        group = _make_group_with_instances(
            "Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1,
            instances=[_make_instance("Meta", True, "Meta Default", 10)],
        )

        with pytest.raises(ConfigValidationError, match="nonexistent_preset"):
            compute_diff([tier], [group], APP_KEY, APP_NAME, network_presets=presets)


class TestWaterfallInstancesNone:
    """Group with instances=None is handled gracefully."""

    def test_instances_none_warning(self) -> None:
        """Group with instances=None produces warning FieldChange."""
        presets = {"standard": [{"network": "Meta", "bidder": True}]}

        tier = PortfolioTier(
            name="Tier 1",
            countries=["US"],
            position=1,
            ad_formats=[AdFormat.INTERSTITIAL],
            network_preset="standard",
        )
        group = _make_group_with_instances(
            "Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1,
            instances=None,
        )

        report = compute_diff([tier], [group], APP_KEY, APP_NAME, network_presets=presets)
        waterfall_changes = [
            c for d in report.group_diffs for c in d.changes if c.field == "waterfall"
        ]
        assert len(waterfall_changes) == 1
        assert "instances=None" in waterfall_changes[0].description
        assert "cannot compare" in waterfall_changes[0].description


class TestWaterfallNameBasedMatching:
    """Preset with name field matches by instance_name."""

    def test_name_based_matching(self) -> None:
        """Preset with name='High' matches instance with instance_name='High'."""
        preset = [
            {"network": "AppLovin", "bidder": False, "rate": 10.0, "name": "High"},
            {"network": "AppLovin", "bidder": False, "rate": 5.0, "name": "Low"},
        ]
        presets = {"standard": preset}

        tier = PortfolioTier(
            name="Tier 1",
            countries=["US"],
            position=1,
            ad_formats=[AdFormat.INTERSTITIAL],
            network_preset="standard",
        )
        group = _make_group_with_instances(
            "Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1,
            instances=[
                _make_instance("AppLovin", False, "High", 10, group_rate=10.0),
                _make_instance("AppLovin", False, "Low", 11, group_rate=5.0),
            ],
        )

        report = compute_diff([tier], [group], APP_KEY, APP_NAME, network_presets=presets)
        waterfall_changes = [
            c for d in report.group_diffs for c in d.changes if c.field == "waterfall"
        ]
        assert len(waterfall_changes) == 0

    def test_name_mismatch_produces_missing(self) -> None:
        """Preset name='High' with no matching instance_name produces missing."""
        preset = [
            {"network": "AppLovin", "bidder": False, "rate": 10.0, "name": "High"},
        ]
        presets = {"standard": preset}

        tier = PortfolioTier(
            name="Tier 1",
            countries=["US"],
            position=1,
            ad_formats=[AdFormat.INTERSTITIAL],
            network_preset="standard",
        )
        group = _make_group_with_instances(
            "Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1,
            instances=[
                _make_instance("AppLovin", False, "Low", 10, group_rate=5.0),
            ],
        )

        report = compute_diff([tier], [group], APP_KEY, APP_NAME, network_presets=presets)
        waterfall_changes = [
            c for d in report.group_diffs for c in d.changes if c.field == "waterfall"
        ]
        # Missing (High not found) + Extra (Low not matched)
        assert len(waterfall_changes) == 2
        missing = [c for c in waterfall_changes if "not found" in c.description]
        extra = [c for c in waterfall_changes if "cannot be removed" in c.description]
        assert len(missing) == 1
        assert "High" in missing[0].description
        assert len(extra) == 1
        assert "Low" in extra[0].description


# ---------------------------------------------------------------------------
# Test Classes: SyncScope-based comparison scoping (Step 8)
# ---------------------------------------------------------------------------


class TestScopeNetworksOnlySkipsTierFields:
    """scope=SyncScope(tiers=False, networks=True) produces only waterfall FieldChanges."""

    def test_scope_networks_only_no_country_changes(self) -> None:
        """With tiers=False, country differences are not reported."""
        from admedi.models.portfolio import SyncScope

        preset = [{"network": "Meta", "bidder": True}]
        presets = {"bidding-1": preset}

        tier = PortfolioTier(
            name="Tier 1",
            countries=["US", "GB"],  # Different from remote
            position=1,
            ad_formats=[AdFormat.INTERSTITIAL],
            network_preset="bidding-1",
        )
        group = _make_group_with_instances(
            "Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1,
            instances=[
                _make_instance("Meta", True, "Meta", 1),
            ],
        )

        scope = SyncScope(tiers=False, networks=True)
        report = compute_diff(
            [tier], [group], APP_KEY, APP_NAME,
            network_presets=presets, scope=scope,
        )

        # No country/position/name changes should appear
        tier_changes = [
            c for d in report.group_diffs for c in d.changes
            if c.field in ("countries", "position", "name")
        ]
        assert len(tier_changes) == 0

    def test_scope_networks_only_still_produces_waterfall_changes(self) -> None:
        """With tiers=False, waterfall changes are still reported."""
        from admedi.models.portfolio import SyncScope

        preset = [
            {"network": "Meta", "bidder": True},
            {"network": "AppLovin", "bidder": True},  # Not in live
        ]
        presets = {"bidding-2": preset}

        tier = PortfolioTier(
            name="Tier 1",
            countries=["US"],
            position=1,
            ad_formats=[AdFormat.INTERSTITIAL],
            network_preset="bidding-2",
        )
        group = _make_group_with_instances(
            "Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1,
            instances=[
                _make_instance("Meta", True, "Meta", 1),
            ],
        )

        scope = SyncScope(tiers=False, networks=True)
        report = compute_diff(
            [tier], [group], APP_KEY, APP_NAME,
            network_presets=presets, scope=scope,
        )

        waterfall_changes = [
            c for d in report.group_diffs for c in d.changes
            if c.field == "waterfall"
        ]
        assert len(waterfall_changes) > 0
        # AppLovin missing
        missing = [c for c in waterfall_changes if "not found" in c.description]
        assert len(missing) == 1
        assert "AppLovin" in missing[0].description


class TestScopeTiersOnlySkipsWaterfall:
    """scope=SyncScope(tiers=True, networks=False) produces only tier FieldChanges."""

    def test_scope_tiers_only_no_waterfall_changes(self) -> None:
        """With networks=False (via network_presets=None), no waterfall changes are reported."""
        from admedi.models.portfolio import SyncScope

        tier = PortfolioTier(
            name="Tier 1",
            countries=["US", "GB"],  # Different from remote
            position=1,
            ad_formats=[AdFormat.INTERSTITIAL],
            network_preset="bidding-1",  # Preset ref exists but presets not loaded
        )
        group = _make_group_with_instances(
            "Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1,
            instances=[
                _make_instance("Meta", True, "Meta", 1),
            ],
        )

        scope = SyncScope(tiers=True, networks=False)
        # network_presets=None means waterfall comparison is skipped
        report = compute_diff(
            [tier], [group], APP_KEY, APP_NAME,
            network_presets=None, scope=scope,
        )

        waterfall_changes = [
            c for d in report.group_diffs for c in d.changes
            if c.field == "waterfall"
        ]
        assert len(waterfall_changes) == 0

        # But tier changes should be present
        tier_changes = [
            c for d in report.group_diffs for c in d.changes
            if c.field == "countries"
        ]
        assert len(tier_changes) == 1


class TestScopeNoneFullComparison:
    """scope=None defaults to full comparison (backward compatible)."""

    def test_scope_none_produces_both_tier_and_waterfall_changes(self) -> None:
        """With scope=None, both tier and waterfall changes are reported."""
        preset = [
            {"network": "Meta", "bidder": True},
            {"network": "AppLovin", "bidder": True},  # Not in live
        ]
        presets = {"bidding-2": preset}

        tier = PortfolioTier(
            name="Tier 1",
            countries=["US", "GB"],  # Different from remote
            position=1,
            ad_formats=[AdFormat.INTERSTITIAL],
            network_preset="bidding-2",
        )
        group = _make_group_with_instances(
            "Tier 1", AdFormat.INTERSTITIAL, ["US"], 1, group_id=1,
            instances=[
                _make_instance("Meta", True, "Meta", 1),
            ],
        )

        # scope=None (default) = full comparison
        report = compute_diff(
            [tier], [group], APP_KEY, APP_NAME,
            network_presets=presets, scope=None,
        )

        tier_changes = [
            c for d in report.group_diffs for c in d.changes
            if c.field == "countries"
        ]
        waterfall_changes = [
            c for d in report.group_diffs for c in d.changes
            if c.field == "waterfall"
        ]
        assert len(tier_changes) == 1
        assert len(waterfall_changes) > 0

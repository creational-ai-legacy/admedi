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
from admedi.models.diff import AppDiffReport, DiffAction, FieldChange, GroupDiff
from admedi.models.enums import AdFormat, Mediator, Platform
from admedi.models.group import Group
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

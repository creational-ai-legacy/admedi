"""Tests for config engine models.

Step 1: Portfolio Models -- PortfolioApp, PortfolioTier, PortfolioConfig.
Step 2a: Diff Models -- DiffAction, FieldChange, GroupDiff, AppDiffReport, DiffReport.
Step 2b: Apply and Status Models -- ApplyStatus, AppApplyResult, ApplyResult,
    AppStatus, PortfolioStatus, SyncLog, ConfigSnapshot.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from admedi.models.diff import (
    AppDiffReport,
    DiffAction,
    DiffReport,
    FieldChange,
    GroupDiff,
)
from admedi.models.enums import AdFormat, Mediator, Platform
from admedi.models.group import Group
from admedi.models.portfolio import PortfolioApp, PortfolioConfig, PortfolioTier, SyncScope


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(**overrides) -> dict:
    """Build a valid PortfolioApp dict with optional overrides."""
    defaults = {
        "app_key": "abc123",
        "name": "Test App",
        "platform": Platform.ANDROID,
    }
    defaults.update(overrides)
    return defaults


def _make_tier(**overrides) -> dict:
    """Build a valid PortfolioTier dict with optional overrides."""
    defaults = {
        "name": "Tier 1",
        "countries": ["US", "GB"],
        "position": 1,
        "ad_formats": [AdFormat.INTERSTITIAL],
    }
    defaults.update(overrides)
    return defaults


def _make_default_tier(**overrides) -> dict:
    """Build a valid default PortfolioTier dict with optional overrides."""
    defaults = {
        "name": "All Countries",
        "countries": ["*"],
        "position": 3,
        "is_default": True,
        "ad_formats": [AdFormat.INTERSTITIAL],
    }
    defaults.update(overrides)
    return defaults


def _make_config(**overrides) -> dict:
    """Build a valid PortfolioConfig dict with optional overrides."""
    defaults = {
        "mediator": Mediator.LEVELPLAY,
        "portfolio": [_make_app()],
        "tiers": [
            _make_tier(),
            _make_tier(name="Tier 2", countries=["AU", "CA"], position=2),
            _make_default_tier(),
        ],
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# PortfolioApp Tests
# ---------------------------------------------------------------------------

class TestPortfolioApp:
    """Tests for PortfolioApp model."""

    def test_valid_construction(self):
        """Valid PortfolioApp fields are accepted."""
        app = PortfolioApp(**_make_app())
        assert app.app_key == "abc123"
        assert app.name == "Test App"
        assert app.platform == Platform.ANDROID

    def test_ios_platform(self):
        """iOS platform is accepted."""
        app = PortfolioApp(**_make_app(platform=Platform.IOS))
        assert app.platform == Platform.IOS

    def test_amazon_platform(self):
        """Amazon platform is accepted."""
        app = PortfolioApp(**_make_app(platform=Platform.AMAZON))
        assert app.platform == Platform.AMAZON

    def test_empty_app_key_raises(self):
        """Empty string app_key is rejected."""
        with pytest.raises(ValidationError, match="app_key must be a non-empty string"):
            PortfolioApp(**_make_app(app_key=""))

    def test_whitespace_only_app_key_raises(self):
        """Whitespace-only app_key is rejected."""
        with pytest.raises(ValidationError, match="app_key must be a non-empty string"):
            PortfolioApp(**_make_app(app_key="   "))

    def test_round_trip_serialization(self):
        """PortfolioApp round-trips through model_dump/model_validate."""
        original = PortfolioApp(**_make_app())
        dumped = original.model_dump()
        restored = PortfolioApp.model_validate(dumped)
        assert restored == original

    def test_populate_by_name_config(self):
        """ConfigDict has populate_by_name=True."""
        assert PortfolioApp.model_config.get("populate_by_name") is True


# ---------------------------------------------------------------------------
# PortfolioTier Tests
# ---------------------------------------------------------------------------

class TestPortfolioTier:
    """Tests for PortfolioTier model."""

    def test_valid_construction(self):
        """Valid PortfolioTier fields are accepted."""
        tier = PortfolioTier(**_make_tier())
        assert tier.name == "Tier 1"
        assert tier.countries == ["US", "GB"]
        assert tier.position == 1
        assert tier.is_default is False
        assert tier.ad_formats == [AdFormat.INTERSTITIAL]

    def test_default_tier_construction(self):
        """Default tier with catch-all country is accepted."""
        tier = PortfolioTier(**_make_default_tier())
        assert tier.is_default is True
        assert tier.countries == ["*"]

    def test_multiple_ad_formats(self):
        """Tier with multiple ad formats is accepted."""
        tier = PortfolioTier(
            **_make_tier(ad_formats=[AdFormat.INTERSTITIAL, AdFormat.BANNER])
        )
        assert len(tier.ad_formats) == 2

    def test_position_zero_raises(self):
        """Position 0 is rejected (must be >= 1)."""
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            PortfolioTier(**_make_tier(position=0))

    def test_position_negative_raises(self):
        """Negative position is rejected."""
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            PortfolioTier(**_make_tier(position=-1))

    def test_position_one_accepted(self):
        """Position 1 (minimum valid) is accepted."""
        tier = PortfolioTier(**_make_tier(position=1))
        assert tier.position == 1

    def test_round_trip_serialization(self):
        """PortfolioTier round-trips through model_dump/model_validate."""
        original = PortfolioTier(**_make_tier())
        dumped = original.model_dump()
        restored = PortfolioTier.model_validate(dumped)
        assert restored == original

    def test_populate_by_name_config(self):
        """ConfigDict has populate_by_name=True."""
        assert PortfolioTier.model_config.get("populate_by_name") is True

    def test_network_preset_defaults_to_none(self):
        """network_preset defaults to None when not specified."""
        tier = PortfolioTier(**_make_tier())
        assert tier.network_preset is None

    def test_network_preset_construction_with_value(self):
        """network_preset stores value when explicitly set."""
        tier = PortfolioTier(**_make_tier(network_preset="bidding-7"))
        assert tier.network_preset == "bidding-7"

    def test_network_preset_round_trip_serialization(self):
        """network_preset survives model_dump/model_validate round-trip."""
        original = PortfolioTier(**_make_tier(network_preset="bidding-6"))
        dumped = original.model_dump()
        assert dumped["network_preset"] == "bidding-6"
        restored = PortfolioTier.model_validate(dumped)
        assert restored.network_preset == "bidding-6"
        assert restored == original

    def test_network_preset_none_round_trip(self):
        """network_preset=None survives model_dump/model_validate round-trip."""
        original = PortfolioTier(**_make_tier())
        dumped = original.model_dump()
        assert dumped["network_preset"] is None
        restored = PortfolioTier.model_validate(dumped)
        assert restored.network_preset is None
        assert restored == original

    def test_network_preset_explicit_none(self):
        """Explicitly passing network_preset=None is accepted."""
        tier = PortfolioTier(**_make_tier(network_preset=None))
        assert tier.network_preset is None


# ---------------------------------------------------------------------------
# SyncScope Tests
# ---------------------------------------------------------------------------

class TestSyncScope:
    """Tests for SyncScope model."""

    def test_default_construction(self):
        """SyncScope() defaults to tiers=True, networks=True (full sync)."""
        scope = SyncScope()
        assert scope.tiers is True
        assert scope.networks is True

    def test_tiers_only(self):
        """SyncScope(tiers=True, networks=False) scopes to tiers only."""
        scope = SyncScope(tiers=True, networks=False)
        assert scope.tiers is True
        assert scope.networks is False

    def test_networks_only(self):
        """SyncScope(tiers=False, networks=True) scopes to networks only."""
        scope = SyncScope(tiers=False, networks=True)
        assert scope.tiers is False
        assert scope.networks is True

    def test_both_false(self):
        """SyncScope(tiers=False, networks=False) is valid (no-op sync)."""
        scope = SyncScope(tiers=False, networks=False)
        assert scope.tiers is False
        assert scope.networks is False

    def test_model_dump_output(self):
        """model_dump() returns expected dict structure."""
        scope = SyncScope(tiers=True, networks=False)
        dumped = scope.model_dump()
        assert dumped == {"tiers": True, "networks": False}

    def test_model_dump_defaults(self):
        """model_dump() on default SyncScope returns both True."""
        scope = SyncScope()
        dumped = scope.model_dump()
        assert dumped == {"tiers": True, "networks": True}

    def test_round_trip_serialization(self):
        """SyncScope round-trips through model_dump/model_validate."""
        original = SyncScope(tiers=False, networks=True)
        dumped = original.model_dump()
        restored = SyncScope.model_validate(dumped)
        assert restored == original


# ---------------------------------------------------------------------------
# PortfolioConfig Tests -- Valid Construction
# ---------------------------------------------------------------------------

class TestPortfolioConfigValid:
    """Tests for valid PortfolioConfig construction."""

    def test_valid_shelf_sort_config(self):
        """Full Shelf Sort-style config is accepted.

        Mirrors real Shelf Sort tiers: Tier 1 (US/GB), Tier 2 (AU/CA),
        All Countries (catch-all), with interstitial and banner formats.
        """
        config = PortfolioConfig(
            mediator=Mediator.LEVELPLAY,
            portfolio=[
                PortfolioApp(app_key="app1", name="Shelf Sort", platform=Platform.ANDROID),
                PortfolioApp(app_key="app2", name="Shelf Sort", platform=Platform.IOS),
            ],
            tiers=[
                PortfolioTier(
                    name="Tier 1",
                    countries=["US", "GB"],
                    position=1,
                    ad_formats=[AdFormat.INTERSTITIAL, AdFormat.BANNER],
                ),
                PortfolioTier(
                    name="Tier 2",
                    countries=["AU", "CA", "NZ"],
                    position=2,
                    ad_formats=[AdFormat.INTERSTITIAL, AdFormat.BANNER],
                ),
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=3,
                    is_default=True,
                    ad_formats=[AdFormat.INTERSTITIAL, AdFormat.BANNER],
                ),
            ],
        )
        assert config.schema_version == 1
        assert config.mediator == Mediator.LEVELPLAY
        assert len(config.portfolio) == 2
        assert len(config.tiers) == 3

    def test_default_schema_version(self):
        """schema_version defaults to 1."""
        config = PortfolioConfig(**_make_config())
        assert config.schema_version == 1

    def test_same_country_different_format_scopes_valid(self):
        """Same country in different ad format scopes is valid.

        US in interstitial Tier 1 and US in banner Tier 1 should not
        conflict because they are in different format scopes.
        """
        config = PortfolioConfig(
            mediator=Mediator.LEVELPLAY,
            portfolio=[PortfolioApp(**_make_app())],
            tiers=[
                PortfolioTier(
                    name="Interstitial Tier 1",
                    countries=["US"],
                    position=1,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
                PortfolioTier(
                    name="Banner Tier 1",
                    countries=["US"],
                    position=1,
                    ad_formats=[AdFormat.BANNER],
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
        assert len(config.tiers) == 3

    def test_wildcard_country_in_default_tier(self):
        """'*' is allowed as a country code in the default tier."""
        config = PortfolioConfig(**_make_config())
        default_tier = [t for t in config.tiers if t.is_default][0]
        assert "*" in default_tier.countries

    def test_rewarded_format_with_levelplay(self):
        """'rewarded' format is accepted with levelplay mediator."""
        config = PortfolioConfig(
            mediator=Mediator.LEVELPLAY,
            portfolio=[PortfolioApp(**_make_app())],
            tiers=[
                PortfolioTier(
                    name="Tier 1",
                    countries=["US"],
                    position=1,
                    ad_formats=[AdFormat.REWARDED],
                ),
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=2,
                    is_default=True,
                    ad_formats=[AdFormat.REWARDED],
                ),
            ],
        )
        assert config.mediator == Mediator.LEVELPLAY

    def test_round_trip_serialization(self):
        """PortfolioConfig round-trips through model_dump/model_validate."""
        original = PortfolioConfig(**_make_config())
        dumped = original.model_dump()
        restored = PortfolioConfig.model_validate(dumped)
        assert restored == original

    def test_populate_by_name_config(self):
        """ConfigDict has populate_by_name=True."""
        assert PortfolioConfig.model_config.get("populate_by_name") is True


# ---------------------------------------------------------------------------
# PortfolioConfig Tests -- Schema Version Validation
# ---------------------------------------------------------------------------

class TestPortfolioConfigSchemaVersion:
    """Tests for schema_version validation."""

    def test_schema_version_2_raises(self):
        """Unsupported schema_version=2 is rejected."""
        with pytest.raises(ValidationError, match="Unsupported schema_version=2"):
            PortfolioConfig(**_make_config(schema_version=2))

    def test_schema_version_0_raises(self):
        """Unsupported schema_version=0 is rejected."""
        with pytest.raises(ValidationError, match="Unsupported schema_version=0"):
            PortfolioConfig(**_make_config(schema_version=0))

    def test_schema_version_1_accepted(self):
        """schema_version=1 is explicitly accepted."""
        config = PortfolioConfig(**_make_config(schema_version=1))
        assert config.schema_version == 1


# ---------------------------------------------------------------------------
# PortfolioConfig Tests -- Portfolio Validation
# ---------------------------------------------------------------------------

class TestPortfolioConfigPortfolio:
    """Tests for portfolio (apps) validation."""

    def test_empty_portfolio_raises(self):
        """Empty portfolio list is rejected."""
        with pytest.raises(ValidationError, match="portfolio must contain at least one app"):
            PortfolioConfig(**_make_config(portfolio=[]))


# ---------------------------------------------------------------------------
# PortfolioConfig Tests -- Default Tier Validation
# ---------------------------------------------------------------------------

class TestPortfolioConfigDefaultTier:
    """Tests for default tier validation."""

    def test_no_default_tier_raises(self):
        """Missing default tier (no is_default=True) is rejected."""
        with pytest.raises(
            ValidationError,
            match="requires exactly one default tier.*but none was found",
        ):
            PortfolioConfig(
                **_make_config(
                    tiers=[
                        _make_tier(),
                        _make_tier(name="Tier 2", countries=["AU"], position=2),
                    ]
                )
            )

    def test_multiple_default_tiers_raises(self):
        """Multiple tiers with is_default=True are rejected."""
        with pytest.raises(
            ValidationError,
            match="requires exactly one default tier.*but found 2",
        ):
            PortfolioConfig(
                **_make_config(
                    tiers=[
                        _make_tier(),
                        _make_default_tier(name="Default 1", position=2),
                        _make_default_tier(name="Default 2", position=3),
                    ]
                )
            )


# ---------------------------------------------------------------------------
# PortfolioConfig Tests -- Country Code Validation
# ---------------------------------------------------------------------------

class TestPortfolioConfigCountryCodes:
    """Tests for country code validation."""

    def test_lowercase_country_code_raises(self):
        """Lowercase country code (e.g., 'us') is rejected."""
        with pytest.raises(
            ValidationError,
            match="Invalid country code 'us'.*must be exactly 2 uppercase letters",
        ):
            PortfolioConfig(
                **_make_config(
                    tiers=[
                        _make_tier(countries=["us"]),
                        _make_default_tier(),
                    ]
                )
            )

    def test_three_char_country_code_raises(self):
        """Three-character country code (e.g., 'USA') is rejected."""
        with pytest.raises(
            ValidationError,
            match="Invalid country code 'USA'.*must be exactly 2 uppercase letters",
        ):
            PortfolioConfig(
                **_make_config(
                    tiers=[
                        _make_tier(countries=["USA"]),
                        _make_default_tier(),
                    ]
                )
            )

    def test_single_char_country_code_raises(self):
        """Single character country code is rejected."""
        with pytest.raises(
            ValidationError,
            match="Invalid country code 'U'",
        ):
            PortfolioConfig(
                **_make_config(
                    tiers=[
                        _make_tier(countries=["U"]),
                        _make_default_tier(),
                    ]
                )
            )

    def test_wildcard_accepted(self):
        """'*' is accepted as a country code."""
        # This should not raise -- '*' is the catch-all
        config = PortfolioConfig(
            **_make_config(
                tiers=[
                    _make_tier(),
                    _make_default_tier(countries=["*"]),
                ]
            )
        )
        assert config is not None

    def test_valid_country_codes(self):
        """Standard ISO 3166-1 alpha-2 codes are accepted."""
        config = PortfolioConfig(
            **_make_config(
                tiers=[
                    _make_tier(countries=["US", "GB", "DE", "JP"]),
                    _make_default_tier(),
                ]
            )
        )
        assert config.tiers[0].countries == ["US", "GB", "DE", "JP"]


# ---------------------------------------------------------------------------
# PortfolioConfig Tests -- Mediator Format Compatibility
# ---------------------------------------------------------------------------

class TestPortfolioConfigMediatorCompat:
    """Tests for mediator/ad-format compatibility."""

    def test_rewarded_video_with_levelplay_raises(self):
        """rewardedVideo format with levelplay mediator is rejected.

        LevelPlay Groups v4 uses 'rewarded' only.
        """
        with pytest.raises(
            ValidationError,
            match="Ad format 'rewardedVideo' is incompatible with mediator 'levelplay'",
        ):
            PortfolioConfig(
                mediator=Mediator.LEVELPLAY,
                portfolio=[PortfolioApp(**_make_app())],
                tiers=[
                    PortfolioTier(
                        name="Tier 1",
                        countries=["US"],
                        position=1,
                        ad_formats=[AdFormat.REWARDED_VIDEO],
                    ),
                    PortfolioTier(
                        name="All Countries",
                        countries=["*"],
                        position=2,
                        is_default=True,
                        ad_formats=[AdFormat.REWARDED_VIDEO],
                    ),
                ],
            )

    def test_rewarded_video_in_default_tier_with_levelplay_raises(self):
        """rewardedVideo even in default tier with levelplay is rejected."""
        with pytest.raises(
            ValidationError,
            match="Ad format 'rewardedVideo' is incompatible",
        ):
            PortfolioConfig(
                mediator=Mediator.LEVELPLAY,
                portfolio=[PortfolioApp(**_make_app())],
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
                        ad_formats=[AdFormat.INTERSTITIAL, AdFormat.REWARDED_VIDEO],
                    ),
                ],
            )

    def test_rewarded_video_with_max_mediator_accepted(self):
        """rewardedVideo with non-levelplay mediator is accepted.

        MAX mediator has no incompatible formats defined.
        """
        config = PortfolioConfig(
            mediator=Mediator.MAX,
            portfolio=[PortfolioApp(**_make_app())],
            tiers=[
                PortfolioTier(
                    name="Tier 1",
                    countries=["US"],
                    position=1,
                    ad_formats=[AdFormat.REWARDED_VIDEO],
                ),
                PortfolioTier(
                    name="All Countries",
                    countries=["*"],
                    position=2,
                    is_default=True,
                    ad_formats=[AdFormat.REWARDED_VIDEO],
                ),
            ],
        )
        assert config.mediator == Mediator.MAX


# ---------------------------------------------------------------------------
# PortfolioConfig Tests -- Per-Format Duplicate Country Validation
# ---------------------------------------------------------------------------

class TestPortfolioConfigDuplicateCountries:
    """Tests for per-format duplicate country validation."""

    def test_duplicate_country_same_format_raises(self):
        """Same country in two tiers with same ad format is rejected."""
        with pytest.raises(
            ValidationError,
            match="Duplicate country 'US' in ad format 'interstitial'",
        ):
            PortfolioConfig(
                mediator=Mediator.LEVELPLAY,
                portfolio=[PortfolioApp(**_make_app())],
                tiers=[
                    PortfolioTier(
                        name="Tier 1",
                        countries=["US"],
                        position=1,
                        ad_formats=[AdFormat.INTERSTITIAL],
                    ),
                    PortfolioTier(
                        name="Tier 2",
                        countries=["US"],
                        position=2,
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

    def test_same_country_different_formats_valid(self):
        """Same country in different format scopes is valid."""
        config = PortfolioConfig(
            mediator=Mediator.LEVELPLAY,
            portfolio=[PortfolioApp(**_make_app())],
            tiers=[
                PortfolioTier(
                    name="Interstitial Tier 1",
                    countries=["US"],
                    position=1,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
                PortfolioTier(
                    name="Banner Tier 1",
                    countries=["US"],
                    position=1,
                    ad_formats=[AdFormat.BANNER],
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
        assert len(config.tiers) == 3

    def test_duplicate_country_shared_format_raises(self):
        """Country overlap when tiers share a format raises.

        Tier 1 has [interstitial, banner] with US.
        Tier 2 has [banner] with US.
        US is duplicated in the banner format scope.
        """
        with pytest.raises(
            ValidationError,
            match="Duplicate country 'US' in ad format 'banner'",
        ):
            PortfolioConfig(
                mediator=Mediator.LEVELPLAY,
                portfolio=[PortfolioApp(**_make_app())],
                tiers=[
                    PortfolioTier(
                        name="Tier 1",
                        countries=["US"],
                        position=1,
                        ad_formats=[AdFormat.INTERSTITIAL, AdFormat.BANNER],
                    ),
                    PortfolioTier(
                        name="Tier 2",
                        countries=["US"],
                        position=2,
                        ad_formats=[AdFormat.BANNER],
                    ),
                    PortfolioTier(
                        name="All Countries",
                        countries=["*"],
                        position=3,
                        is_default=True,
                        ad_formats=[AdFormat.INTERSTITIAL, AdFormat.BANNER],
                    ),
                ],
            )


# ---------------------------------------------------------------------------
# PortfolioConfig Tests -- Per-Format Duplicate Position Validation
# ---------------------------------------------------------------------------

class TestPortfolioConfigDuplicatePositions:
    """Tests for per-format duplicate position validation."""

    def test_duplicate_position_same_format_raises(self):
        """Same position in two tiers with same ad format is rejected."""
        with pytest.raises(
            ValidationError,
            match="Duplicate position 1 in ad format 'interstitial'",
        ):
            PortfolioConfig(
                mediator=Mediator.LEVELPLAY,
                portfolio=[PortfolioApp(**_make_app())],
                tiers=[
                    PortfolioTier(
                        name="Tier 1",
                        countries=["US"],
                        position=1,
                        ad_formats=[AdFormat.INTERSTITIAL],
                    ),
                    PortfolioTier(
                        name="Tier 2",
                        countries=["GB"],
                        position=1,
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

    def test_same_position_different_formats_valid(self):
        """Same position in different format scopes is valid."""
        config = PortfolioConfig(
            mediator=Mediator.LEVELPLAY,
            portfolio=[PortfolioApp(**_make_app())],
            tiers=[
                PortfolioTier(
                    name="Interstitial Tier 1",
                    countries=["US"],
                    position=1,
                    ad_formats=[AdFormat.INTERSTITIAL],
                ),
                PortfolioTier(
                    name="Banner Tier 1",
                    countries=["GB"],
                    position=1,
                    ad_formats=[AdFormat.BANNER],
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
        assert len(config.tiers) == 3

    def test_duplicate_position_shared_format_raises(self):
        """Position overlap when tiers share a format raises.

        Tier 1 has [interstitial, banner] at position 1.
        Tier 2 has [banner] at position 1.
        Position 1 is duplicated in the banner format scope.
        """
        with pytest.raises(
            ValidationError,
            match="Duplicate position 1 in ad format 'banner'",
        ):
            PortfolioConfig(
                mediator=Mediator.LEVELPLAY,
                portfolio=[PortfolioApp(**_make_app())],
                tiers=[
                    PortfolioTier(
                        name="Tier 1",
                        countries=["US"],
                        position=1,
                        ad_formats=[AdFormat.INTERSTITIAL, AdFormat.BANNER],
                    ),
                    PortfolioTier(
                        name="Tier 2",
                        countries=["GB"],
                        position=1,
                        ad_formats=[AdFormat.BANNER],
                    ),
                    PortfolioTier(
                        name="All Countries",
                        countries=["*"],
                        position=3,
                        is_default=True,
                        ad_formats=[AdFormat.INTERSTITIAL, AdFormat.BANNER],
                    ),
                ],
            )


# ===========================================================================
# Step 2a: Diff Models
# ===========================================================================

# ---------------------------------------------------------------------------
# Helpers -- Diff Models
# ---------------------------------------------------------------------------

def _make_field_change(**overrides) -> dict:
    """Build a valid FieldChange dict with optional overrides."""
    defaults = {
        "field": "countries",
        "old_value": ["US"],
        "new_value": ["US", "GB"],
        "description": "Added GB to country list",
    }
    defaults.update(overrides)
    return defaults


def _make_group_diff(**overrides) -> dict:
    """Build a valid GroupDiff dict with optional overrides."""
    defaults = {
        "action": DiffAction.UPDATE,
        "group_name": "Tier 1",
        "group_id": 12345,
        "ad_format": AdFormat.INTERSTITIAL,
        "changes": [_make_field_change()],
        "desired_group": None,
    }
    defaults.update(overrides)
    return defaults


def _make_app_diff_report(**overrides) -> dict:
    """Build a valid AppDiffReport dict with optional overrides."""
    defaults = {
        "app_key": "abc123",
        "app_name": "Test App",
        "group_diffs": [_make_group_diff()],
        "has_ab_test": False,
        "ab_test_warning": None,
    }
    defaults.update(overrides)
    return defaults


def _make_group(**overrides) -> Group:
    """Build a valid Group instance with optional overrides."""
    defaults = {
        "groupName": "Tier 1",
        "adFormat": "interstitial",
        "countries": ["US", "GB"],
        "position": 1,
    }
    defaults.update(overrides)
    return Group.model_validate(defaults)


# ---------------------------------------------------------------------------
# DiffAction Tests
# ---------------------------------------------------------------------------

class TestDiffAction:
    """Tests for DiffAction enum."""

    def test_create_value(self):
        """CREATE serializes to 'create'."""
        assert DiffAction.CREATE.value == "create"

    def test_update_value(self):
        """UPDATE serializes to 'update'."""
        assert DiffAction.UPDATE.value == "update"

    def test_delete_value(self):
        """DELETE serializes to 'delete'."""
        assert DiffAction.DELETE.value == "delete"

    def test_unchanged_value(self):
        """UNCHANGED serializes to 'unchanged'."""
        assert DiffAction.UNCHANGED.value == "unchanged"

    def test_extra_value(self):
        """EXTRA serializes to 'extra'."""
        assert DiffAction.EXTRA.value == "extra"

    def test_str_enum_mixin(self):
        """DiffAction uses (str, Enum) pattern -- serializes to string value."""
        # In Python 3.14, str() on (str, Enum) returns "ClassName.MEMBER"
        # but the .value attribute returns the string, and JSON serialization
        # uses the value. Verify the value is a plain string.
        assert isinstance(DiffAction.CREATE.value, str)
        assert DiffAction.CREATE == "create"

    def test_member_count(self):
        """DiffAction has exactly 5 members."""
        assert len(DiffAction) == 5

    def test_from_string_value(self):
        """DiffAction can be constructed from its string value."""
        assert DiffAction("create") == DiffAction.CREATE
        assert DiffAction("extra") == DiffAction.EXTRA


# ---------------------------------------------------------------------------
# FieldChange Tests
# ---------------------------------------------------------------------------

class TestFieldChange:
    """Tests for FieldChange model."""

    def test_valid_construction(self):
        """Valid FieldChange fields are accepted."""
        change = FieldChange(**_make_field_change())
        assert change.field == "countries"
        assert change.old_value == ["US"]
        assert change.new_value == ["US", "GB"]
        assert change.description == "Added GB to country list"

    def test_any_value_types(self):
        """FieldChange accepts Any types for old_value and new_value."""
        change = FieldChange(
            field="position",
            old_value=1,
            new_value=2,
            description="Position changed from 1 to 2",
        )
        assert change.old_value == 1
        assert change.new_value == 2

    def test_none_values(self):
        """FieldChange accepts None for old_value and new_value."""
        change = FieldChange(
            field="floor_price",
            old_value=None,
            new_value=0.5,
            description="Floor price set",
        )
        assert change.old_value is None
        assert change.new_value == 0.5

    def test_round_trip_serialization(self):
        """FieldChange round-trips through model_dump/model_validate."""
        original = FieldChange(**_make_field_change())
        dumped = original.model_dump()
        restored = FieldChange.model_validate(dumped)
        assert restored == original

    def test_populate_by_name_config(self):
        """ConfigDict has populate_by_name=True."""
        assert FieldChange.model_config.get("populate_by_name") is True


# ---------------------------------------------------------------------------
# GroupDiff Tests
# ---------------------------------------------------------------------------

class TestGroupDiff:
    """Tests for GroupDiff model."""

    def test_valid_update_construction(self):
        """Valid UPDATE GroupDiff is accepted."""
        group = _make_group()
        diff = GroupDiff(**_make_group_diff(desired_group=group))
        assert diff.action == DiffAction.UPDATE
        assert diff.group_name == "Tier 1"
        assert diff.group_id == 12345
        assert diff.ad_format == AdFormat.INTERSTITIAL
        assert len(diff.changes) == 1
        assert diff.desired_group is not None

    def test_create_action_with_no_group_id(self):
        """CREATE action with group_id=None and desired_group set."""
        group = _make_group(groupId=None)
        diff = GroupDiff(
            action=DiffAction.CREATE,
            group_name="New Tier",
            group_id=None,
            ad_format=AdFormat.BANNER,
            changes=[],
            desired_group=group,
        )
        assert diff.action == DiffAction.CREATE
        assert diff.group_id is None
        assert diff.desired_group is not None
        assert diff.desired_group.group_id is None

    def test_unchanged_action_desired_group_none(self):
        """UNCHANGED action accepts desired_group=None."""
        diff = GroupDiff(
            action=DiffAction.UNCHANGED,
            group_name="Stable Tier",
            group_id=99,
            ad_format=AdFormat.INTERSTITIAL,
            changes=[],
            desired_group=None,
        )
        assert diff.action == DiffAction.UNCHANGED
        assert diff.desired_group is None

    def test_extra_action_desired_group_none(self):
        """EXTRA action accepts desired_group=None."""
        diff = GroupDiff(
            action=DiffAction.EXTRA,
            group_name="Unknown Remote Group",
            group_id=500,
            ad_format=AdFormat.REWARDED,
            changes=[],
            desired_group=None,
        )
        assert diff.action == DiffAction.EXTRA
        assert diff.desired_group is None

    def test_delete_action(self):
        """DELETE action (reserved) accepts desired_group=None."""
        diff = GroupDiff(
            action=DiffAction.DELETE,
            group_name="Deprecated Tier",
            group_id=42,
            ad_format=AdFormat.NATIVE,
            changes=[],
            desired_group=None,
        )
        assert diff.action == DiffAction.DELETE
        assert diff.desired_group is None

    def test_multiple_changes(self):
        """GroupDiff accepts multiple FieldChange entries."""
        changes = [
            FieldChange(
                field="countries",
                old_value=["US"],
                new_value=["US", "GB"],
                description="Added GB",
            ),
            FieldChange(
                field="position",
                old_value=2,
                new_value=1,
                description="Moved to position 1",
            ),
        ]
        diff = GroupDiff(
            action=DiffAction.UPDATE,
            group_name="Tier 1",
            group_id=10,
            ad_format=AdFormat.INTERSTITIAL,
            changes=changes,
            desired_group=None,
        )
        assert len(diff.changes) == 2

    def test_round_trip_serialization(self):
        """GroupDiff round-trips through model_dump/model_validate."""
        original = GroupDiff(**_make_group_diff())
        dumped = original.model_dump()
        restored = GroupDiff.model_validate(dumped)
        assert restored == original

    def test_round_trip_with_desired_group(self):
        """GroupDiff with desired_group round-trips correctly."""
        group = _make_group()
        original = GroupDiff(**_make_group_diff(desired_group=group))
        dumped = original.model_dump()
        restored = GroupDiff.model_validate(dumped)
        assert restored.desired_group is not None
        assert restored.desired_group.group_name == "Tier 1"

    def test_populate_by_name_config(self):
        """ConfigDict has populate_by_name=True."""
        assert GroupDiff.model_config.get("populate_by_name") is True


# ---------------------------------------------------------------------------
# AppDiffReport Tests
# ---------------------------------------------------------------------------

class TestAppDiffReport:
    """Tests for AppDiffReport model."""

    def test_valid_construction(self):
        """Valid AppDiffReport is accepted."""
        report = AppDiffReport(**_make_app_diff_report())
        assert report.app_key == "abc123"
        assert report.app_name == "Test App"
        assert len(report.group_diffs) == 1
        assert report.has_ab_test is False
        assert report.ab_test_warning is None

    def test_with_ab_test_warning(self):
        """AppDiffReport with active A/B test has warning."""
        report = AppDiffReport(
            **_make_app_diff_report(
                has_ab_test=True,
                ab_test_warning="App has active A/B test -- skipping sync",
            )
        )
        assert report.has_ab_test is True
        assert report.ab_test_warning is not None
        assert "A/B test" in report.ab_test_warning

    def test_empty_group_diffs(self):
        """AppDiffReport with no group diffs (all matched) is valid."""
        report = AppDiffReport(
            **_make_app_diff_report(group_diffs=[])
        )
        assert len(report.group_diffs) == 0

    def test_multiple_group_diffs(self):
        """AppDiffReport with multiple group diffs is valid."""
        diffs = [
            _make_group_diff(action=DiffAction.CREATE, group_name="New Tier"),
            _make_group_diff(action=DiffAction.UPDATE, group_name="Existing Tier"),
            _make_group_diff(action=DiffAction.UNCHANGED, group_name="Stable Tier"),
        ]
        report = AppDiffReport(
            **_make_app_diff_report(group_diffs=diffs)
        )
        assert len(report.group_diffs) == 3

    def test_round_trip_serialization(self):
        """AppDiffReport round-trips through model_dump/model_validate."""
        original = AppDiffReport(**_make_app_diff_report())
        dumped = original.model_dump()
        restored = AppDiffReport.model_validate(dumped)
        assert restored == original

    def test_populate_by_name_config(self):
        """ConfigDict has populate_by_name=True."""
        assert AppDiffReport.model_config.get("populate_by_name") is True


# ---------------------------------------------------------------------------
# DiffReport Tests
# ---------------------------------------------------------------------------

class TestDiffReport:
    """Tests for DiffReport model with computed totals."""

    def test_empty_report(self):
        """DiffReport with no app reports has zero totals."""
        report = DiffReport(app_reports=[])
        assert report.total_creates == 0
        assert report.total_updates == 0
        assert report.total_deletes == 0
        assert report.total_unchanged == 0
        assert report.total_extra == 0

    def test_single_app_report_totals(self):
        """DiffReport with one app report produces correct totals."""
        diffs = [
            _make_group_diff(action=DiffAction.CREATE, group_name="New"),
            _make_group_diff(action=DiffAction.UPDATE, group_name="Changed"),
            _make_group_diff(action=DiffAction.UNCHANGED, group_name="Same"),
        ]
        report = DiffReport(
            app_reports=[
                AppDiffReport(**_make_app_diff_report(group_diffs=diffs))
            ]
        )
        assert report.total_creates == 1
        assert report.total_updates == 1
        assert report.total_deletes == 0
        assert report.total_unchanged == 1
        assert report.total_extra == 0

    def test_two_app_reports_totals(self):
        """DiffReport with 2 app reports containing various actions produces correct totals.

        App 1: 1 CREATE, 2 UPDATE, 1 UNCHANGED
        App 2: 1 EXTRA, 1 DELETE, 1 CREATE
        Totals: 2 creates, 2 updates, 1 delete, 1 unchanged, 1 extra
        """
        app1_diffs = [
            _make_group_diff(action=DiffAction.CREATE, group_name="App1-New"),
            _make_group_diff(action=DiffAction.UPDATE, group_name="App1-Changed1"),
            _make_group_diff(action=DiffAction.UPDATE, group_name="App1-Changed2"),
            _make_group_diff(action=DiffAction.UNCHANGED, group_name="App1-Same"),
        ]
        app2_diffs = [
            _make_group_diff(action=DiffAction.EXTRA, group_name="App2-Extra"),
            _make_group_diff(action=DiffAction.DELETE, group_name="App2-Del"),
            _make_group_diff(action=DiffAction.CREATE, group_name="App2-New"),
        ]
        report = DiffReport(
            app_reports=[
                AppDiffReport(
                    **_make_app_diff_report(
                        app_key="app1", app_name="App 1", group_diffs=app1_diffs
                    )
                ),
                AppDiffReport(
                    **_make_app_diff_report(
                        app_key="app2", app_name="App 2", group_diffs=app2_diffs
                    )
                ),
            ]
        )
        assert report.total_creates == 2
        assert report.total_updates == 2
        assert report.total_deletes == 1
        assert report.total_unchanged == 1
        assert report.total_extra == 1

    def test_computed_fields_in_model_dump(self):
        """Computed fields appear in model_dump() output."""
        report = DiffReport(app_reports=[])
        dumped = report.model_dump()
        assert "total_creates" in dumped
        assert "total_updates" in dumped
        assert "total_deletes" in dumped
        assert "total_unchanged" in dumped
        assert "total_extra" in dumped

    def test_round_trip_serialization(self):
        """DiffReport round-trips through model_dump/model_validate."""
        diffs = [
            _make_group_diff(action=DiffAction.CREATE, group_name="New"),
            _make_group_diff(action=DiffAction.EXTRA, group_name="Unknown"),
        ]
        original = DiffReport(
            app_reports=[
                AppDiffReport(**_make_app_diff_report(group_diffs=diffs))
            ]
        )
        dumped = original.model_dump()
        restored = DiffReport.model_validate(dumped)
        assert restored.total_creates == original.total_creates
        assert restored.total_extra == original.total_extra
        assert len(restored.app_reports) == len(original.app_reports)

    def test_populate_by_name_config(self):
        """ConfigDict has populate_by_name=True."""
        assert DiffReport.model_config.get("populate_by_name") is True


# ===========================================================================
# Step 2b: Apply and Status Models
# ===========================================================================

from datetime import datetime, timezone

from admedi.models.apply_result import (
    AppApplyResult,
    AppStatus,
    ApplyResult,
    ApplyStatus,
    PortfolioStatus,
)
from admedi.models.config_snapshot import ConfigSnapshot
from admedi.models.sync_log import SyncLog


# ---------------------------------------------------------------------------
# Helpers -- Apply and Status Models
# ---------------------------------------------------------------------------

def _make_app_apply_result(**overrides) -> dict:
    """Build a valid AppApplyResult dict with optional overrides."""
    defaults = {
        "app_key": "abc123",
        "status": ApplyStatus.SUCCESS,
        "groups_created": 2,
        "groups_updated": 1,
        "error": None,
    }
    defaults.update(overrides)
    return defaults


def _make_app_status(**overrides) -> dict:
    """Build a valid AppStatus dict with optional overrides."""
    defaults = {
        "app_key": "abc123",
        "app_name": "Test App",
        "platform": Platform.ANDROID,
        "group_count": 5,
        "last_sync": datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# ApplyStatus Tests
# ---------------------------------------------------------------------------

class TestApplyStatus:
    """Tests for ApplyStatus enum."""

    def test_success_value(self):
        """SUCCESS serializes to 'success'."""
        assert ApplyStatus.SUCCESS.value == "success"

    def test_skipped_value(self):
        """SKIPPED serializes to 'skipped'."""
        assert ApplyStatus.SKIPPED.value == "skipped"

    def test_failed_value(self):
        """FAILED serializes to 'failed'."""
        assert ApplyStatus.FAILED.value == "failed"

    def test_dry_run_value(self):
        """DRY_RUN serializes to 'dry_run'."""
        assert ApplyStatus.DRY_RUN.value == "dry_run"

    def test_str_enum_mixin(self):
        """ApplyStatus uses (str, Enum) pattern -- value is a plain string."""
        assert isinstance(ApplyStatus.SUCCESS.value, str)
        assert ApplyStatus.SUCCESS == "success"

    def test_member_count(self):
        """ApplyStatus has exactly 4 members."""
        assert len(ApplyStatus) == 4

    def test_from_string_value(self):
        """ApplyStatus can be constructed from its string value."""
        assert ApplyStatus("success") == ApplyStatus.SUCCESS
        assert ApplyStatus("dry_run") == ApplyStatus.DRY_RUN


# ---------------------------------------------------------------------------
# AppApplyResult Tests
# ---------------------------------------------------------------------------

class TestAppApplyResult:
    """Tests for AppApplyResult model."""

    def test_valid_construction(self):
        """Valid AppApplyResult fields are accepted."""
        result = AppApplyResult(**_make_app_apply_result())
        assert result.app_key == "abc123"
        assert result.status == ApplyStatus.SUCCESS
        assert result.groups_created == 2
        assert result.groups_updated == 1
        assert result.error is None

    def test_warnings_default_empty_list(self):
        """Warnings defaults to empty list when not provided."""
        result = AppApplyResult(**_make_app_apply_result())
        assert result.warnings == []
        assert isinstance(result.warnings, list)

    def test_warnings_accepts_string_entries(self):
        """Warnings field accepts list of string entries."""
        result = AppApplyResult(
            **_make_app_apply_result(
                warnings=[
                    "Expected group 'Tier 1' to exist after CREATE but not found",
                    "Position mismatch after update",
                ]
            )
        )
        assert len(result.warnings) == 2
        assert "Tier 1" in result.warnings[0]

    def test_failed_with_error(self):
        """Failed result includes error description."""
        result = AppApplyResult(
            **_make_app_apply_result(
                status=ApplyStatus.FAILED,
                groups_created=0,
                groups_updated=0,
                error="API returned 500 for group creation",
            )
        )
        assert result.status == ApplyStatus.FAILED
        assert result.error is not None
        assert "500" in result.error

    def test_skipped_result(self):
        """Skipped result (e.g., A/B test active)."""
        result = AppApplyResult(
            **_make_app_apply_result(
                status=ApplyStatus.SKIPPED,
                groups_created=0,
                groups_updated=0,
            )
        )
        assert result.status == ApplyStatus.SKIPPED

    def test_dry_run_result(self):
        """Dry-run result records what would have changed."""
        result = AppApplyResult(
            **_make_app_apply_result(
                status=ApplyStatus.DRY_RUN,
                groups_created=3,
                groups_updated=2,
            )
        )
        assert result.status == ApplyStatus.DRY_RUN
        assert result.groups_created == 3
        assert result.groups_updated == 2

    def test_round_trip_serialization(self):
        """AppApplyResult round-trips through model_dump/model_validate."""
        original = AppApplyResult(
            **_make_app_apply_result(warnings=["warning1"])
        )
        dumped = original.model_dump()
        restored = AppApplyResult.model_validate(dumped)
        assert restored == original
        assert restored.warnings == ["warning1"]

    def test_populate_by_name_config(self):
        """ConfigDict has populate_by_name=True."""
        assert AppApplyResult.model_config.get("populate_by_name") is True


# ---------------------------------------------------------------------------
# ApplyResult Tests
# ---------------------------------------------------------------------------

class TestApplyResult:
    """Tests for ApplyResult model with computed totals."""

    def test_empty_result(self):
        """ApplyResult with no app results has zero totals."""
        result = ApplyResult(app_results=[], was_dry_run=False)
        assert result.total_success == 0
        assert result.total_skipped == 0
        assert result.total_failed == 0

    def test_three_app_results_computed_totals(self):
        """ApplyResult with 3 app results produces correct computed totals.

        App 1: SUCCESS, App 2: SKIPPED, App 3: FAILED
        """
        results = [
            AppApplyResult(
                **_make_app_apply_result(app_key="app1", status=ApplyStatus.SUCCESS)
            ),
            AppApplyResult(
                **_make_app_apply_result(
                    app_key="app2",
                    status=ApplyStatus.SKIPPED,
                    groups_created=0,
                    groups_updated=0,
                )
            ),
            AppApplyResult(
                **_make_app_apply_result(
                    app_key="app3",
                    status=ApplyStatus.FAILED,
                    groups_created=0,
                    groups_updated=0,
                    error="API error",
                )
            ),
        ]
        apply_result = ApplyResult(app_results=results, was_dry_run=False)
        assert apply_result.total_success == 1
        assert apply_result.total_skipped == 1
        assert apply_result.total_failed == 1

    def test_all_dry_run(self):
        """ApplyResult in dry-run mode with all DRY_RUN statuses."""
        results = [
            AppApplyResult(
                **_make_app_apply_result(
                    app_key=f"app{i}", status=ApplyStatus.DRY_RUN
                )
            )
            for i in range(3)
        ]
        apply_result = ApplyResult(app_results=results, was_dry_run=True)
        assert apply_result.was_dry_run is True
        assert apply_result.total_success == 0
        assert apply_result.total_skipped == 0
        assert apply_result.total_failed == 0

    def test_multiple_success(self):
        """ApplyResult with multiple SUCCESS apps counts correctly."""
        results = [
            AppApplyResult(
                **_make_app_apply_result(app_key=f"app{i}", status=ApplyStatus.SUCCESS)
            )
            for i in range(4)
        ]
        apply_result = ApplyResult(app_results=results, was_dry_run=False)
        assert apply_result.total_success == 4
        assert apply_result.total_failed == 0
        assert apply_result.total_skipped == 0

    def test_computed_fields_in_model_dump(self):
        """Computed fields appear in model_dump() output."""
        result = ApplyResult(app_results=[], was_dry_run=False)
        dumped = result.model_dump()
        assert "total_success" in dumped
        assert "total_skipped" in dumped
        assert "total_failed" in dumped

    def test_round_trip_serialization(self):
        """ApplyResult round-trips through model_dump/model_validate."""
        results = [
            AppApplyResult(
                **_make_app_apply_result(status=ApplyStatus.SUCCESS)
            ),
            AppApplyResult(
                **_make_app_apply_result(
                    app_key="app2",
                    status=ApplyStatus.FAILED,
                    error="timeout",
                )
            ),
        ]
        original = ApplyResult(app_results=results, was_dry_run=False)
        dumped = original.model_dump()
        restored = ApplyResult.model_validate(dumped)
        assert restored.total_success == original.total_success
        assert restored.total_failed == original.total_failed
        assert len(restored.app_results) == len(original.app_results)

    def test_populate_by_name_config(self):
        """ConfigDict has populate_by_name=True."""
        assert ApplyResult.model_config.get("populate_by_name") is True


# ---------------------------------------------------------------------------
# AppStatus Tests
# ---------------------------------------------------------------------------

class TestAppStatus:
    """Tests for AppStatus model."""

    def test_valid_construction(self):
        """Valid AppStatus with all fields."""
        status = AppStatus(**_make_app_status())
        assert status.app_key == "abc123"
        assert status.app_name == "Test App"
        assert status.platform == Platform.ANDROID
        assert status.group_count == 5
        assert status.last_sync is not None

    def test_last_sync_none(self):
        """AppStatus with last_sync=None (never synced)."""
        status = AppStatus(**_make_app_status(last_sync=None))
        assert status.last_sync is None

    def test_last_sync_timezone_aware(self):
        """AppStatus last_sync preserves timezone-aware datetime."""
        ts = datetime(2026, 3, 12, 10, 30, 0, tzinfo=timezone.utc)
        status = AppStatus(**_make_app_status(last_sync=ts))
        assert status.last_sync == ts
        assert status.last_sync.tzinfo is not None

    def test_ios_platform(self):
        """AppStatus accepts iOS platform."""
        status = AppStatus(**_make_app_status(platform=Platform.IOS))
        assert status.platform == Platform.IOS

    def test_round_trip_serialization(self):
        """AppStatus round-trips through model_dump/model_validate."""
        original = AppStatus(**_make_app_status())
        dumped = original.model_dump()
        restored = AppStatus.model_validate(dumped)
        assert restored.app_key == original.app_key
        assert restored.platform == original.platform
        assert restored.last_sync == original.last_sync

    def test_populate_by_name_config(self):
        """ConfigDict has populate_by_name=True."""
        assert AppStatus.model_config.get("populate_by_name") is True


# ---------------------------------------------------------------------------
# PortfolioStatus Tests
# ---------------------------------------------------------------------------

class TestPortfolioStatus:
    """Tests for PortfolioStatus model."""

    def test_valid_construction(self):
        """Valid PortfolioStatus with mediator and apps."""
        portfolio = PortfolioStatus(
            mediator=Mediator.LEVELPLAY,
            apps=[AppStatus(**_make_app_status())],
        )
        assert portfolio.mediator == Mediator.LEVELPLAY
        assert len(portfolio.apps) == 1

    def test_stores_typed_platform_and_mediator(self):
        """PortfolioStatus correctly stores typed Platform and Mediator values."""
        apps = [
            AppStatus(**_make_app_status(platform=Platform.ANDROID)),
            AppStatus(**_make_app_status(app_key="def456", platform=Platform.IOS)),
        ]
        portfolio = PortfolioStatus(mediator=Mediator.LEVELPLAY, apps=apps)
        assert isinstance(portfolio.mediator, Mediator)
        assert portfolio.mediator == Mediator.LEVELPLAY
        assert isinstance(portfolio.apps[0].platform, Platform)
        assert portfolio.apps[0].platform == Platform.ANDROID
        assert portfolio.apps[1].platform == Platform.IOS

    def test_empty_apps(self):
        """PortfolioStatus with empty apps list."""
        portfolio = PortfolioStatus(mediator=Mediator.MAX, apps=[])
        assert portfolio.mediator == Mediator.MAX
        assert len(portfolio.apps) == 0

    def test_round_trip_serialization(self):
        """PortfolioStatus round-trips through model_dump/model_validate."""
        original = PortfolioStatus(
            mediator=Mediator.LEVELPLAY,
            apps=[
                AppStatus(**_make_app_status()),
                AppStatus(**_make_app_status(app_key="xyz", platform=Platform.IOS)),
            ],
        )
        dumped = original.model_dump()
        restored = PortfolioStatus.model_validate(dumped)
        assert restored.mediator == original.mediator
        assert len(restored.apps) == len(original.apps)
        assert restored.apps[0].platform == original.apps[0].platform

    def test_populate_by_name_config(self):
        """ConfigDict has populate_by_name=True."""
        assert PortfolioStatus.model_config.get("populate_by_name") is True


# ---------------------------------------------------------------------------
# SyncLog Tests (Step 2b -- engine-specific SyncLog with ApplyStatus)
# ---------------------------------------------------------------------------

class TestSyncLogEngine:
    """Tests for the updated SyncLog model with ApplyStatus integration."""

    def test_valid_construction(self):
        """SyncLog with all required fields."""
        ts = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
        log = SyncLog(
            app_key="abc123",
            timestamp=ts,
            action="sync",
            groups_created=2,
            groups_updated=1,
            status=ApplyStatus.SUCCESS,
        )
        assert log.app_key == "abc123"
        assert log.timestamp == ts
        assert log.action == "sync"
        assert log.groups_created == 2
        assert log.groups_updated == 1
        assert log.status == ApplyStatus.SUCCESS
        assert log.error is None

    def test_with_error(self):
        """SyncLog with error on failure."""
        log = SyncLog(
            app_key="abc123",
            timestamp=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
            action="sync",
            groups_created=0,
            groups_updated=0,
            status=ApplyStatus.FAILED,
            error="API rate limit exceeded",
        )
        assert log.status == ApplyStatus.FAILED
        assert log.error == "API rate limit exceeded"

    def test_json_round_trip(self):
        """SyncLog round-trips through model_dump_json/model_validate_json."""
        ts = datetime(2026, 3, 12, 15, 30, 0, tzinfo=timezone.utc)
        original = SyncLog(
            app_key="abc123",
            timestamp=ts,
            action="sync",
            groups_created=1,
            groups_updated=3,
            status=ApplyStatus.SUCCESS,
        )
        json_str = original.model_dump_json()
        restored = SyncLog.model_validate_json(json_str)
        assert restored.app_key == original.app_key
        assert restored.timestamp == original.timestamp
        assert restored.groups_created == original.groups_created
        assert restored.groups_updated == original.groups_updated
        assert restored.status == original.status
        assert restored.error == original.error

    def test_json_round_trip_preserves_timezone(self):
        """SyncLog JSON round-trip preserves timezone-aware timestamp."""
        ts = datetime(2026, 6, 15, 8, 0, 0, tzinfo=timezone.utc)
        original = SyncLog(
            app_key="key",
            timestamp=ts,
            action="sync",
            groups_created=0,
            groups_updated=0,
            status=ApplyStatus.DRY_RUN,
        )
        json_str = original.model_dump_json()
        restored = SyncLog.model_validate_json(json_str)
        assert restored.timestamp.tzinfo is not None
        # Compare as UTC -- the restored timestamp should equal the original
        assert restored.timestamp == ts

    def test_dict_round_trip(self):
        """SyncLog round-trips through model_dump/model_validate."""
        original = SyncLog(
            app_key="abc123",
            timestamp=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
            action="sync",
            groups_created=2,
            groups_updated=0,
            status=ApplyStatus.SUCCESS,
        )
        dumped = original.model_dump()
        restored = SyncLog.model_validate(dumped)
        assert restored == original

    def test_populate_by_name_config(self):
        """ConfigDict has populate_by_name=True."""
        assert SyncLog.model_config.get("populate_by_name") is True


# ---------------------------------------------------------------------------
# ConfigSnapshot Tests (Step 2b -- round-trip verification)
# ---------------------------------------------------------------------------

class TestConfigSnapshotEngine:
    """Tests for ConfigSnapshot JSON serialization and round-trip."""

    def test_valid_construction(self):
        """ConfigSnapshot with raw_config dict."""
        ts = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
        snap = ConfigSnapshot(
            app_key="abc123",
            timestamp=ts,
            raw_config={"groups": [{"groupName": "Default", "position": 1}]},
        )
        assert snap.app_key == "abc123"
        assert snap.timestamp == ts
        assert snap.raw_config["groups"][0]["groupName"] == "Default"

    def test_json_round_trip_preserves_raw_config(self):
        """ConfigSnapshot round-trips through JSON with raw_config dict preserved."""
        raw = {
            "groups": [
                {"groupName": "Tier 1", "countries": ["US", "GB"], "position": 1},
                {"groupName": "All Countries", "countries": ["*"], "position": 2},
            ]
        }
        original = ConfigSnapshot(
            app_key="abc123",
            timestamp=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
            raw_config=raw,
        )
        json_str = original.model_dump_json()
        restored = ConfigSnapshot.model_validate_json(json_str)
        assert restored.app_key == original.app_key
        assert restored.raw_config == raw
        assert len(restored.raw_config["groups"]) == 2
        assert restored.raw_config["groups"][0]["groupName"] == "Tier 1"

    def test_dict_round_trip(self):
        """ConfigSnapshot round-trips through model_dump/model_validate."""
        original = ConfigSnapshot(
            app_key="abc123",
            timestamp=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
            raw_config={"groups": []},
        )
        dumped = original.model_dump()
        restored = ConfigSnapshot.model_validate(dumped)
        assert restored == original

    def test_nested_raw_config(self):
        """ConfigSnapshot handles deeply nested raw_config."""
        raw = {
            "groups": [
                {
                    "groupName": "Tier 1",
                    "instances": [
                        {"instanceId": 1, "network": "ironSource", "rate": 5.0},
                        {"instanceId": 2, "network": "AdMob", "rate": None},
                    ],
                }
            ]
        }
        snap = ConfigSnapshot(
            app_key="key",
            timestamp=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
            raw_config=raw,
        )
        json_str = snap.model_dump_json()
        restored = ConfigSnapshot.model_validate_json(json_str)
        assert restored.raw_config == raw

    def test_populate_by_name_config(self):
        """ConfigDict has populate_by_name=True."""
        assert ConfigSnapshot.model_config.get("populate_by_name") is True

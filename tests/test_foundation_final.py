"""Step 9: Final validation tests for the admedi foundation.

Verifies all __init__.py exports, public API imports, and the complete
Shelf Sort TierTemplate construction as an integration test.
"""

from __future__ import annotations


class TestModelsPackageExports:
    """Verify admedi.models exports all 19 public names."""

    def test_import_all_models_single_statement(self) -> None:
        """All 14 models importable from admedi.models in one statement."""
        from admedi.models import (
            App,
            Capping,
            ConfigSnapshot,
            CountryRate,
            Credential,
            Group,
            Instance,
            Pacing,
            Placement,
            SyncLog,
            TierDefinition,
            TierTemplate,
            WaterfallConfig,
            WaterfallTier,
        )

        # Verify they are the actual classes (not None or re-exported wrong)
        assert App.__name__ == "App"
        assert Capping.__name__ == "Capping"
        assert ConfigSnapshot.__name__ == "ConfigSnapshot"
        assert CountryRate.__name__ == "CountryRate"
        assert Credential.__name__ == "Credential"
        assert Group.__name__ == "Group"
        assert Instance.__name__ == "Instance"
        assert Pacing.__name__ == "Pacing"
        assert Placement.__name__ == "Placement"
        assert SyncLog.__name__ == "SyncLog"
        assert TierDefinition.__name__ == "TierDefinition"
        assert TierTemplate.__name__ == "TierTemplate"
        assert WaterfallConfig.__name__ == "WaterfallConfig"
        assert WaterfallTier.__name__ == "WaterfallTier"

    def test_import_all_enums_single_statement(self) -> None:
        """All 5 enums importable from admedi.models in one statement."""
        from admedi.models import (
            AdFormat,
            Mediator,
            Networks,
            Platform,
            TierType,
        )

        assert AdFormat.__name__ == "AdFormat"
        assert Mediator.__name__ == "Mediator"
        assert Networks.__name__ == "Networks"
        assert Platform.__name__ == "Platform"
        assert TierType.__name__ == "TierType"

    def test_models_all_has_19_entries(self) -> None:
        """admedi.models.__all__ contains exactly 19 names."""
        import admedi.models

        expected = {
            "AdFormat", "App", "Capping", "ConfigSnapshot", "CountryRate",
            "Credential", "Group", "Instance", "Mediator", "Networks",
            "Pacing", "Placement", "Platform", "SyncLog", "TierDefinition",
            "TierTemplate", "TierType", "WaterfallConfig", "WaterfallTier",
        }
        actual = set(admedi.models.__all__)
        assert actual == expected, f"Missing: {expected - actual}, Extra: {actual - expected}"


class TestAdaptersPackageExports:
    """Verify admedi.adapters exports all 3 public names."""

    def test_import_all_adapters_single_statement(self) -> None:
        """All 3 adapter names importable from admedi.adapters."""
        from admedi.adapters import (
            AdapterCapability,
            MediationAdapter,
            StorageAdapter,
        )

        assert AdapterCapability.__name__ == "AdapterCapability"
        assert MediationAdapter.__name__ == "MediationAdapter"
        assert StorageAdapter.__name__ == "StorageAdapter"

    def test_adapters_all_has_3_entries(self) -> None:
        """admedi.adapters.__all__ contains exactly 3 names."""
        import admedi.adapters

        expected = {"AdapterCapability", "MediationAdapter", "StorageAdapter"}
        actual = set(admedi.adapters.__all__)
        assert actual == expected


class TestExceptionsModuleExports:
    """Verify admedi.exceptions exports all 6 exception classes."""

    def test_import_all_exceptions_from_module(self) -> None:
        """All 6 exceptions importable from admedi.exceptions."""
        from admedi.exceptions import (
            AdapterNotSupportedError,
            AdmediError,
            ApiError,
            AuthError,
            ConfigValidationError,
            RateLimitError,
        )

        assert issubclass(AdmediError, Exception)
        assert issubclass(AuthError, AdmediError)
        assert issubclass(RateLimitError, AdmediError)
        assert issubclass(ApiError, AdmediError)
        assert issubclass(ConfigValidationError, AdmediError)
        assert issubclass(AdapterNotSupportedError, AdmediError)

    def test_import_all_exceptions_from_top_level(self) -> None:
        """All 6 exceptions also importable from admedi top-level."""
        from admedi import (
            AdapterNotSupportedError,
            AdmediError,
            ApiError,
            AuthError,
            ConfigValidationError,
            RateLimitError,
        )

        assert issubclass(AdmediError, Exception)
        assert issubclass(AuthError, AdmediError)
        assert issubclass(RateLimitError, AdmediError)
        assert issubclass(ApiError, AdmediError)
        assert issubclass(ConfigValidationError, AdmediError)
        assert issubclass(AdapterNotSupportedError, AdmediError)

    def test_top_level_all_has_6_entries(self) -> None:
        """admedi.__all__ contains exactly 6 exception names."""
        import admedi

        expected = {
            "AdmediError", "AuthError", "RateLimitError", "ApiError",
            "ConfigValidationError", "AdapterNotSupportedError",
        }
        actual = set(admedi.__all__)
        assert actual == expected


class TestNetworksModuleExports:
    """Verify admedi.networks public API is accessible."""

    def test_import_validate_network_credentials(self) -> None:
        """validate_network_credentials importable from admedi.networks."""
        from admedi.networks import validate_network_credentials

        assert callable(validate_network_credentials)

    def test_import_network_schemas(self) -> None:
        """NETWORK_SCHEMAS importable from admedi.networks."""
        from admedi.networks import NETWORK_SCHEMAS

        assert isinstance(NETWORK_SCHEMAS, dict)
        assert len(NETWORK_SCHEMAS) == 34


class TestTopLevelPackage:
    """Verify admedi top-level package attributes."""

    def test_version_exists_and_is_string(self) -> None:
        """__version__ is a non-empty string."""
        from admedi import __version__

        assert isinstance(__version__, str)
        assert __version__
        assert __version__ == "0.1.0"

    def test_subpackages_importable(self) -> None:
        """All 6 subpackages importable from admedi."""
        from admedi import adapters, cli, engine, mcp, models, storage

        assert adapters is not None
        assert cli is not None
        assert engine is not None
        assert mcp is not None
        assert models is not None
        assert storage is not None


class TestShelfSortIntegration:
    """Integration test: construct the exact Shelf Sort TierTemplate from the plan."""

    def test_shelf_sort_tier_template_construction(self) -> None:
        """Construct the Shelf Sort TierTemplate exactly as specified in the plan's acceptance criteria."""
        from admedi.models import AdFormat, TierDefinition, TierTemplate

        template = TierTemplate(
            name="Shelf Sort",
            ad_formats=[AdFormat.BANNER, AdFormat.INTERSTITIAL, AdFormat.REWARDED_VIDEO],
            tiers=[
                TierDefinition(name="Tier 1", countries=["US"], position=1),
                TierDefinition(name="Tier 2", countries=["AU", "CA", "DE", "GB", "JP", "NZ", "KR", "TW"], position=2),
                TierDefinition(name="Tier 3", countries=["FR", "NL"], position=3),
                TierDefinition(name="All Countries", countries=[], position=4, is_default=True),
            ],
        )

        assert template.name == "Shelf Sort"
        assert len(template.ad_formats) == 3
        assert len(template.tiers) == 4

    def test_shelf_sort_tier_details(self) -> None:
        """Verify each tier has the correct countries and properties."""
        from admedi.models import AdFormat, TierDefinition, TierTemplate

        template = TierTemplate(
            name="Shelf Sort",
            ad_formats=[AdFormat.BANNER, AdFormat.INTERSTITIAL, AdFormat.REWARDED_VIDEO],
            tiers=[
                TierDefinition(name="Tier 1", countries=["US"], position=1),
                TierDefinition(name="Tier 2", countries=["AU", "CA", "DE", "GB", "JP", "NZ", "KR", "TW"], position=2),
                TierDefinition(name="Tier 3", countries=["FR", "NL"], position=3),
                TierDefinition(name="All Countries", countries=[], position=4, is_default=True),
            ],
        )

        # Tier 1: US only
        tier1 = template.tiers[0]
        assert tier1.name == "Tier 1"
        assert tier1.countries == ["US"]
        assert tier1.position == 1
        assert tier1.is_default is False

        # Tier 2: 8 countries
        tier2 = template.tiers[1]
        assert tier2.name == "Tier 2"
        assert tier2.countries == ["AU", "CA", "DE", "GB", "JP", "NZ", "KR", "TW"]
        assert tier2.position == 2
        assert tier2.is_default is False

        # Tier 3: FR, NL
        tier3 = template.tiers[2]
        assert tier3.name == "Tier 3"
        assert tier3.countries == ["FR", "NL"]
        assert tier3.position == 3
        assert tier3.is_default is False

        # Default tier: All Countries
        default = template.tiers[3]
        assert default.name == "All Countries"
        assert default.countries == []
        assert default.position == 4
        assert default.is_default is True

    def test_shelf_sort_round_trip(self) -> None:
        """Shelf Sort template round-trips through model_dump/model_validate."""
        from admedi.models import AdFormat, TierDefinition, TierTemplate

        template = TierTemplate(
            name="Shelf Sort",
            ad_formats=[AdFormat.BANNER, AdFormat.INTERSTITIAL, AdFormat.REWARDED_VIDEO],
            tiers=[
                TierDefinition(name="Tier 1", countries=["US"], position=1),
                TierDefinition(name="Tier 2", countries=["AU", "CA", "DE", "GB", "JP", "NZ", "KR", "TW"], position=2),
                TierDefinition(name="Tier 3", countries=["FR", "NL"], position=3),
                TierDefinition(name="All Countries", countries=[], position=4, is_default=True),
            ],
        )

        data = template.model_dump()
        restored = TierTemplate.model_validate(data)

        assert restored.name == template.name
        assert len(restored.tiers) == len(template.tiers)
        assert restored.ad_formats == template.ad_formats
        for orig, rest in zip(template.tiers, restored.tiers, strict=True):
            assert rest.name == orig.name
            assert rest.countries == orig.countries
            assert rest.position == orig.position
            assert rest.is_default == orig.is_default

    def test_shelf_sort_ad_formats_are_enums(self) -> None:
        """Verify ad_formats stores actual AdFormat enum members."""
        from admedi.models import AdFormat, TierDefinition, TierTemplate

        template = TierTemplate(
            name="Shelf Sort",
            ad_formats=[AdFormat.BANNER, AdFormat.INTERSTITIAL, AdFormat.REWARDED_VIDEO],
            tiers=[
                TierDefinition(name="Tier 1", countries=["US"], position=1),
                TierDefinition(name="All Countries", countries=[], position=2, is_default=True),
            ],
        )

        for fmt in template.ad_formats:
            assert isinstance(fmt, AdFormat)

    def test_shelf_sort_by_alias_serialization(self) -> None:
        """Shelf Sort template serializes correctly with by_alias=True."""
        from admedi.models import AdFormat, TierDefinition, TierTemplate

        template = TierTemplate(
            name="Shelf Sort",
            ad_formats=[AdFormat.BANNER, AdFormat.INTERSTITIAL, AdFormat.REWARDED_VIDEO],
            tiers=[
                TierDefinition(name="Tier 1", countries=["US"], position=1),
                TierDefinition(name="Tier 2", countries=["AU", "CA", "DE", "GB", "JP", "NZ", "KR", "TW"], position=2),
                TierDefinition(name="Tier 3", countries=["FR", "NL"], position=3),
                TierDefinition(name="All Countries", countries=[], position=4, is_default=True),
            ],
        )

        data = template.model_dump(by_alias=True)
        assert "ad_formats" in data or "adFormats" in data
        # Verify it can be re-validated from the alias dump
        restored = TierTemplate.model_validate(data)
        assert restored.name == "Shelf Sort"

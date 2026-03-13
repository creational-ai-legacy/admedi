"""Tests for admedi enums and constants.

Verifies enum values match LevelPlay API expectations, member counts are
correct, no duplicate values exist, all URL constants are well-formed,
and enums serialize to JSON correctly.
"""

import json
from enum import Enum

import pytest

from admedi.constants import (
    APPS_URL,
    AUTH_URL,
    GROUPS_V4_URL,
    INSTANCES_V1_URL,
    INSTANCES_V3_URL,
    LEVELPLAY_BASE_URL,
    MEDIATION_MGMT_V2_URL,
    PLACEMENTS_URL,
    REPORTING_URL,
)
from admedi.models.enums import AdFormat, Mediator, Networks, Platform, TierType


class TestAdFormat:
    """Tests for AdFormat enum."""

    def test_banner_value(self) -> None:
        """BANNER value matches LevelPlay API string."""
        assert AdFormat.BANNER.value == "banner"

    def test_interstitial_value(self) -> None:
        """INTERSTITIAL value matches LevelPlay API string."""
        assert AdFormat.INTERSTITIAL.value == "interstitial"

    def test_rewarded_video_value(self) -> None:
        """REWARDED_VIDEO value matches LevelPlay API string."""
        assert AdFormat.REWARDED_VIDEO.value == "rewardedVideo"

    def test_member_count(self) -> None:
        """AdFormat has exactly 5 members."""
        assert len(AdFormat) == 5

    def test_is_str_subclass(self) -> None:
        """AdFormat members are str instances for JSON serialization."""
        assert isinstance(AdFormat.BANNER, str)

    def test_json_serializable(self) -> None:
        """AdFormat serializes to its value in JSON."""
        result = json.dumps({"format": AdFormat.BANNER})
        assert '"banner"' in result


class TestPlatform:
    """Tests for Platform enum."""

    def test_android_value(self) -> None:
        """ANDROID value matches LevelPlay API string."""
        assert Platform.ANDROID.value == "Android"

    def test_ios_value(self) -> None:
        """IOS value matches LevelPlay API string."""
        assert Platform.IOS.value == "iOS"

    def test_amazon_value(self) -> None:
        """AMAZON value matches LevelPlay API string."""
        assert Platform.AMAZON.value == "Amazon"

    def test_member_count(self) -> None:
        """Platform has exactly 3 members."""
        assert len(Platform) == 3

    def test_is_str_subclass(self) -> None:
        """Platform members are str instances for JSON serialization."""
        assert isinstance(Platform.IOS, str)

    def test_json_serializable(self) -> None:
        """Platform serializes to its value in JSON."""
        result = json.dumps({"platform": Platform.IOS})
        assert '"iOS"' in result


class TestTierType:
    """Tests for TierType enum."""

    def test_manual_value(self) -> None:
        """MANUAL value matches LevelPlay API string."""
        assert TierType.MANUAL.value == "manual"

    def test_sort_by_cpm_value(self) -> None:
        """SORT_BY_CPM value matches LevelPlay API string."""
        assert TierType.SORT_BY_CPM.value == "sortByCpm"

    def test_optimized_value(self) -> None:
        """OPTIMIZED value matches LevelPlay API string."""
        assert TierType.OPTIMIZED.value == "optimized"

    def test_bidding_value(self) -> None:
        """BIDDING value matches LevelPlay API string."""
        assert TierType.BIDDING.value == "bidding"

    def test_member_count(self) -> None:
        """TierType has exactly 4 members."""
        assert len(TierType) == 4

    def test_is_str_subclass(self) -> None:
        """TierType members are str instances for JSON serialization."""
        assert isinstance(TierType.MANUAL, str)


class TestMediator:
    """Tests for Mediator enum."""

    def test_levelplay_value(self) -> None:
        """LEVELPLAY value matches expected string."""
        assert Mediator.LEVELPLAY.value == "levelplay"

    def test_max_value(self) -> None:
        """MAX value matches expected string."""
        assert Mediator.MAX.value == "max"

    def test_admob_value(self) -> None:
        """ADMOB value matches expected string."""
        assert Mediator.ADMOB.value == "admob"

    def test_member_count(self) -> None:
        """Mediator has exactly 3 members."""
        assert len(Mediator) == 3

    def test_is_str_subclass(self) -> None:
        """Mediator members are str instances for JSON serialization."""
        assert isinstance(Mediator.LEVELPLAY, str)


class TestNetworks:
    """Tests for Networks enum."""

    def test_member_count(self) -> None:
        """Networks has exactly 34 members."""
        assert len(Networks) == 34

    def test_iron_source_value(self) -> None:
        """IRON_SOURCE value matches LevelPlay API string."""
        assert Networks.IRON_SOURCE.value == "ironSource"

    def test_iron_source_bidding_value(self) -> None:
        """IRON_SOURCE_BIDDING value matches LevelPlay API string."""
        assert Networks.IRON_SOURCE_BIDDING.value == "ironSourceBidding"

    def test_applovin_value(self) -> None:
        """APPLOVIN value matches LevelPlay API string."""
        assert Networks.APPLOVIN.value == "AppLovin"

    def test_admob_value(self) -> None:
        """ADMOB value matches LevelPlay API string."""
        assert Networks.ADMOB.value == "AdMob"

    def test_unity_ads_value(self) -> None:
        """UNITY_ADS value matches LevelPlay API string."""
        assert Networks.UNITY_ADS.value == "UnityAds"

    def test_vungle_value(self) -> None:
        """VUNGLE value matches LevelPlay API string."""
        assert Networks.VUNGLE.value == "Vungle"

    def test_vungle_bidding_value(self) -> None:
        """VUNGLE_BIDDING is distinct from VUNGLE (API distinguishes them)."""
        assert Networks.VUNGLE_BIDDING.value == "vungleBidding"
        assert Networks.VUNGLE != Networks.VUNGLE_BIDDING

    def test_facebook_value(self) -> None:
        """FACEBOOK value matches LevelPlay API string."""
        assert Networks.FACEBOOK.value == "Facebook"

    def test_facebook_bidding_value(self) -> None:
        """FACEBOOK_BIDDING value matches LevelPlay API string."""
        assert Networks.FACEBOOK_BIDDING.value == "facebookBidding"

    def test_liftoff_value(self) -> None:
        """LIFTOFF value matches LevelPlay API string (includes space)."""
        assert Networks.LIFTOFF.value == "Liftoff Bidding"

    def test_hyprmx_value(self) -> None:
        """HYPRMX value matches LevelPlay API string (note: HyprMX not HyperMX)."""
        assert Networks.HYPRMX.value == "HyprMX"

    def test_is_str_subclass(self) -> None:
        """Networks members are str instances for JSON serialization."""
        assert isinstance(Networks.IRON_SOURCE, str)

    def test_json_serializable(self) -> None:
        """Networks serializes to its value in JSON."""
        result = json.dumps({"network": Networks.ADMOB})
        assert '"AdMob"' in result

    def test_all_expected_networks_present(self) -> None:
        """All 34 expected network API values are present."""
        expected_values = {
            "ironSource",
            "ironSourceBidding",
            "AppLovin",
            "AdColony",
            "adColonyBidding",
            "AdMob",
            "AdManager",
            "Amazon",
            "Chartboost",
            "crossPromotionBidding",
            "CSJ",
            "DirectDeals",
            "Facebook",
            "facebookBidding",
            "Fyber",
            "HyprMX",
            "InMobi",
            "inMobiBidding",
            "Liftoff Bidding",
            "Maio",
            "mediaBrix",
            "myTargetBidding",
            "Pangle",
            "pangleBidding",
            "smaatoBidding",
            "Snap",
            "SuperAwesomeBidding",
            "TapJoy",
            "TapJoyBidding",
            "Tencent",
            "UnityAds",
            "Vungle",
            "vungleBidding",
            "yahooBidding",
        }
        actual_values = {member.value for member in Networks}
        assert actual_values == expected_values


class TestNoDuplicateValues:
    """Tests that no enum has duplicate values."""

    @pytest.mark.parametrize(
        "enum_cls",
        [AdFormat, Platform, TierType, Mediator, Networks],
        ids=["AdFormat", "Platform", "TierType", "Mediator", "Networks"],
    )
    def test_no_duplicate_values(self, enum_cls: type[Enum]) -> None:
        """Each enum member has a unique value."""
        values = [member.value for member in enum_cls]
        assert len(values) == len(set(values)), (
            f"{enum_cls.__name__} has duplicate values"
        )


class TestEnumImportsFromModels:
    """Tests that enums are importable from admedi.models."""

    def test_import_ad_format(self) -> None:
        """AdFormat is importable from admedi.models."""
        from admedi.models import AdFormat as AF
        assert AF.BANNER.value == "banner"

    def test_import_platform(self) -> None:
        """Platform is importable from admedi.models."""
        from admedi.models import Platform as P
        assert P.IOS.value == "iOS"

    def test_import_tier_type(self) -> None:
        """TierType is importable from admedi.models."""
        from admedi.models import TierType as TT
        assert TT.MANUAL.value == "manual"

    def test_import_mediator(self) -> None:
        """Mediator is importable from admedi.models."""
        from admedi.models import Mediator as M
        assert M.LEVELPLAY.value == "levelplay"

    def test_import_networks(self) -> None:
        """Networks is importable from admedi.models."""
        from admedi.models import Networks as N
        assert N.IRON_SOURCE.value == "ironSource"


class TestConstants:
    """Tests for API URL constants."""

    def test_base_url(self) -> None:
        """LEVELPLAY_BASE_URL is the ironSource platform URL."""
        assert LEVELPLAY_BASE_URL == "https://platform.ironsrc.com"

    def test_auth_url(self) -> None:
        """AUTH_URL points to the authentication endpoint."""
        assert AUTH_URL == "https://platform.ironsrc.com/partners/publisher/auth"

    def test_apps_url(self) -> None:
        """APPS_URL points to the applications v6 endpoint."""
        assert APPS_URL == "https://platform.ironsrc.com/partners/publisher/applications/v6"

    def test_groups_v4_url(self) -> None:
        """GROUPS_V4_URL points to the LevelPlay groups v4 endpoint."""
        assert GROUPS_V4_URL == "https://platform.ironsrc.com/levelPlay/groups/v4"

    def test_mediation_mgmt_v2_url(self) -> None:
        """MEDIATION_MGMT_V2_URL points to the mediation management v2 endpoint."""
        assert MEDIATION_MGMT_V2_URL == "https://platform.ironsrc.com/partners/publisher/mediation/management/v2"

    def test_instances_v1_url(self) -> None:
        """INSTANCES_V1_URL points to the instances v1 endpoint."""
        assert INSTANCES_V1_URL == "https://platform.ironsrc.com/partners/publisher/instances/v1"

    def test_instances_v3_url(self) -> None:
        """INSTANCES_V3_URL points to the instances v3 endpoint (reference only)."""
        assert INSTANCES_V3_URL == "https://platform.ironsrc.com/partners/publisher/instances/v3"

    def test_placements_url(self) -> None:
        """PLACEMENTS_URL points to the placements v1 endpoint."""
        assert PLACEMENTS_URL == "https://platform.ironsrc.com/partners/publisher/placements/v1"

    def test_reporting_url(self) -> None:
        """REPORTING_URL points to the LevelPlay reporting v1 endpoint."""
        assert REPORTING_URL == "https://platform.ironsrc.com/levelPlay/reporting/v1"

    @pytest.mark.parametrize(
        "url",
        [
            AUTH_URL,
            APPS_URL,
            GROUPS_V4_URL,
            MEDIATION_MGMT_V2_URL,
            INSTANCES_V1_URL,
            INSTANCES_V3_URL,
            PLACEMENTS_URL,
            REPORTING_URL,
        ],
        ids=[
            "AUTH_URL",
            "APPS_URL",
            "GROUPS_V4_URL",
            "MEDIATION_MGMT_V2_URL",
            "INSTANCES_V1_URL",
            "INSTANCES_V3_URL",
            "PLACEMENTS_URL",
            "REPORTING_URL",
        ],
    )
    def test_all_urls_start_with_base(self, url: str) -> None:
        """All URL constants start with LEVELPLAY_BASE_URL."""
        assert url.startswith(LEVELPLAY_BASE_URL)

    @pytest.mark.parametrize(
        "url",
        [
            LEVELPLAY_BASE_URL,
            AUTH_URL,
            APPS_URL,
            GROUPS_V4_URL,
            MEDIATION_MGMT_V2_URL,
            INSTANCES_V1_URL,
            INSTANCES_V3_URL,
            PLACEMENTS_URL,
            REPORTING_URL,
        ],
    )
    def test_all_urls_are_strings(self, url: str) -> None:
        """All URL constants are strings."""
        assert isinstance(url, str)

"""Enums for ad mediation entities.

All enums use the ``(str, Enum)`` mixin pattern so they serialize directly
to their string values when passed to ``json.dumps``.

Examples:
    >>> from admedi.models.enums import AdFormat, Platform, Networks
    >>> AdFormat.BANNER.value
    'banner'
    >>> Platform.IOS.value
    'iOS'
    >>> Networks.IRON_SOURCE.value
    'ironSource'
"""

from enum import Enum


class AdFormat(str, Enum):
    """Supported ad unit formats.

    Values match the LevelPlay API field strings. The Groups v4 API uses
    ``"rewarded"`` while the legacy Mediation Management v2 API uses
    ``"rewardedVideo"`` — both are included for compatibility.
    """

    BANNER = "banner"
    INTERSTITIAL = "interstitial"
    REWARDED = "rewarded"
    REWARDED_VIDEO = "rewardedVideo"
    NATIVE = "native"


class Platform(str, Enum):
    """Target mobile platforms.

    Values match the LevelPlay API's ``platform`` field strings.
    """

    ANDROID = "Android"
    IOS = "iOS"
    AMAZON = "Amazon"


class TierType(str, Enum):
    """Waterfall tier ordering strategies.

    Values match the LevelPlay Groups API ``type`` field strings.
    """

    MANUAL = "manual"
    SORT_BY_CPM = "sortByCpm"
    OPTIMIZED = "optimized"
    BIDDING = "bidding"


class Mediator(str, Enum):
    """Supported mediation platforms.

    ``LEVELPLAY`` is the MVP target. ``MAX`` and ``ADMOB`` are future
    adapter targets.
    """

    LEVELPLAY = "levelplay"
    MAX = "max"
    ADMOB = "admob"


class Networks(str, Enum):
    """Ad network identifiers recognized by the LevelPlay API.

    Derived from the ironSource Python lib's ``Networks`` enum with exact
    string values that the LevelPlay API expects. Both ``VUNGLE`` and
    ``VUNGLE_BIDDING`` are separate entries since the API distinguishes them.

    34 members total.
    """

    IRON_SOURCE = "ironSource"
    IRON_SOURCE_BIDDING = "ironSourceBidding"
    APPLOVIN = "AppLovin"
    AD_COLONY = "AdColony"
    AD_COLONY_BIDDING = "adColonyBidding"
    ADMOB = "AdMob"
    AD_MANAGER = "AdManager"
    AMAZON = "Amazon"
    CHARTBOOST = "Chartboost"
    CROSS_PROMOTION_BIDDING = "crossPromotionBidding"
    CSJ = "CSJ"
    DIRECT_DEALS = "DirectDeals"
    FACEBOOK = "Facebook"
    FACEBOOK_BIDDING = "facebookBidding"
    FYBER = "Fyber"
    HYPRMX = "HyprMX"
    INMOBI = "InMobi"
    INMOBI_BIDDING = "inMobiBidding"
    LIFTOFF = "Liftoff Bidding"
    MAIO = "Maio"
    MEDIA_BRIX = "mediaBrix"
    MY_TARGET = "myTargetBidding"
    PANGLE = "Pangle"
    PANGLE_BIDDING = "pangleBidding"
    SMAATO = "smaatoBidding"
    SNAP = "Snap"
    SUPER_AWESOME = "SuperAwesomeBidding"
    TAPJOY = "TapJoy"
    TAPJOY_BIDDING = "TapJoyBidding"
    TENCENT = "Tencent"
    UNITY_ADS = "UnityAds"
    VUNGLE = "Vungle"
    VUNGLE_BIDDING = "vungleBidding"
    YAHOO_BIDDING = "yahooBidding"

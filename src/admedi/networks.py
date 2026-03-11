"""Network credential registry for LevelPlay ad networks.

Maps each of the 34 LevelPlay networks to their credential field schemas,
distinguishing app-level and instance-level fields. Provides a validation
function to check that required fields are present in a config dict.

Field names use camelCase as the default convention, matching the majority
pattern across networks in the ironSource Python lib's ``get_object()`` and
``get_app_data_obj()`` output. Several networks deviate from camelCase in
the lib -- these are documented in ``_UNVERIFIED_CASING`` and should be
verified against the live LevelPlay API during integration testing.

Examples:
    >>> from admedi.networks import validate_network_credentials, NETWORK_SCHEMAS
    >>> len(NETWORK_SCHEMAS)
    34
    >>> validate_network_credentials("AppLovin", {"sdkKey": "abc", "zoneId": "123"})
    []
    >>> validate_network_credentials("AppLovin", {"sdkKey": "abc"})
    ['AppLovin: missing required field "zoneId"']
"""

from __future__ import annotations

from typing import Any, TypeAlias

# Each field entry maps field_name -> metadata dict with:
#   "type": always "str" for this foundation
#   "required": bool
#   "level": "app" | "instance"
NetworkFieldSchema: TypeAlias = dict[str, str | bool]
NetworkSchema: TypeAlias = dict[str, NetworkFieldSchema]

# Networks whose field names in the ironSource lib deviate from camelCase.
# These need verification against the live LevelPlay API during integration
# testing. The registry below uses camelCase as the initial assumption.
#
# Snake_case outliers (from get_object()):
#   AppLovin: zone_id -> assumed zoneId
#   CSJ: slot_id -> assumed slotId
#   Facebook/FacebookBidding: placement_id -> assumed placementId
#   CrossPromotionBidding: traffic_id -> assumed trafficId
#   DirectDeals: traffic_id -> assumed trafficId
#
# PascalCase outliers (from get_object()):
#   MyTarget: PlacementID -> assumed placementId
#   Snap: SlotID -> assumed slotId
#   Vungle/VungleBidding: PlacementId -> assumed placementId
#   Pangle/PangleBidding: slotID -> assumed slotId
#
# Mixed-case outlier (from get_object()):
#   Smaato: adspaceID -> assumed adspaceId
#
# App-level outliers (from get_app_data_obj()):
#   AdColony/AdColonyBidding: appID -> assumed appId
#   CSJ: appID -> assumed appId
#   Pangle/PangleBidding: appID -> assumed appId
#   Snap: AppId -> assumed appId
#   Vungle/VungleBidding: AppID -> assumed appId, reportingAPIId -> assumed reportingApiId
#   Yahoo: siteId (used for app_id getter) -> kept as siteId (standard camelCase)
#
# Fyber outlier (from get_object()):
#   adSoptId (likely typo for adSpotId) -> assumed adSpotId
_UNVERIFIED_CASING: dict[str, list[str]] = {
    "AppLovin": ["zoneId"],
    "CSJ": ["appId", "slotId"],
    "Facebook": ["placementId"],
    "facebookBidding": ["placementId"],
    "crossPromotionBidding": ["trafficId"],
    "DirectDeals": ["trafficId"],
    "myTargetBidding": ["placementId"],
    "Snap": ["appId", "slotId"],
    "smaatoBidding": ["adspaceId"],
    "Pangle": ["appId", "slotId"],
    "pangleBidding": ["appId", "slotId"],
    "Vungle": ["appId", "reportingApiId", "placementId"],
    "vungleBidding": ["appId", "reportingApiId", "placementId"],
    "AdColony": ["appId"],
    "adColonyBidding": ["appId"],
    "Fyber": ["adSpotId"],
}

NETWORK_SCHEMAS: dict[str, NetworkSchema] = {
    # --- ironSource (no per-instance config beyond base) ---
    "ironSource": {},
    "ironSourceBidding": {},
    # --- AdColony / AdColony Bidding ---
    # App: appId (lib uses appID), Instance: zoneId
    "AdColony": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "zoneId": {"type": "str", "required": True, "level": "instance"},
    },
    "adColonyBidding": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "zoneId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- AdMob ---
    # App: appId, Instance: adUnitId
    "AdMob": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "adUnitId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- AdManager ---
    # App: appId, Instance: adUnitId
    "AdManager": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "adUnitId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- Amazon ---
    # App: appKey, Instance: ec
    "Amazon": {
        "appKey": {"type": "str", "required": True, "level": "app"},
        "ec": {"type": "str", "required": True, "level": "instance"},
    },
    # --- AppLovin ---
    # App: sdkKey, Instance: zoneId (lib uses zone_id -- unverified)
    "AppLovin": {
        "sdkKey": {"type": "str", "required": True, "level": "app"},
        "zoneId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- Chartboost ---
    # App: appId + appSignature, Instance: adLocation
    "Chartboost": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "appSignature": {"type": "str", "required": True, "level": "app"},
        "adLocation": {"type": "str", "required": True, "level": "instance"},
    },
    # --- CrossPromotionBidding ---
    # No app-level, Instance: trafficId (lib uses traffic_id -- unverified)
    "crossPromotionBidding": {
        "trafficId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- CSJ ---
    # App: appId (lib uses appID -- unverified), Instance: slotId (lib uses slot_id -- unverified)
    "CSJ": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "slotId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- DirectDeals ---
    # No app-level, Instance: trafficId (lib uses traffic_id -- unverified)
    "DirectDeals": {
        "trafficId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- Facebook ---
    # App: appId + userAccessToken, Instance: placementId (lib uses placement_id -- unverified)
    "Facebook": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "userAccessToken": {"type": "str", "required": True, "level": "app"},
        "placementId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- Facebook Bidding ---
    # App: appId + userAccessToken, Instance: placementId (lib uses placement_id -- unverified)
    "facebookBidding": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "userAccessToken": {"type": "str", "required": True, "level": "app"},
        "placementId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- Fyber ---
    # App: appId, Instance: adSpotId + contentId
    # Note: lib has typo "adSoptId" in get_object() -- assumed correct name is adSpotId
    "Fyber": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "adSpotId": {"type": "str", "required": True, "level": "instance"},
        "contentId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- HyprMX ---
    # App: distributorId, Instance: placementId
    "HyprMX": {
        "distributorId": {"type": "str", "required": True, "level": "app"},
        "placementId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- InMobi ---
    # No app-level, Instance: placementId
    "InMobi": {
        "placementId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- InMobi Bidding ---
    # No app-level, Instance: placementId
    "inMobiBidding": {
        "placementId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- Liftoff Bidding ---
    # App: appId, Instance: adUnitId
    "Liftoff Bidding": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "adUnitId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- Maio ---
    # App: appId, Instance: zoneId + mediaId
    "Maio": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "zoneId": {"type": "str", "required": True, "level": "instance"},
        "mediaId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- mediaBrix ---
    # App: appId + reportingProperty, Instance: zone
    "mediaBrix": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "reportingProperty": {"type": "str", "required": True, "level": "app"},
        "zone": {"type": "str", "required": True, "level": "instance"},
    },
    # --- myTargetBidding ---
    # No app-level, Instance: slotId + placementId (lib uses PlacementID -- unverified)
    "myTargetBidding": {
        "slotId": {"type": "str", "required": True, "level": "instance"},
        "placementId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- Pangle ---
    # App: appId (lib uses appID -- unverified), Instance: slotId (lib uses slotID -- unverified)
    "Pangle": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "slotId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- Pangle Bidding ---
    # App: appId (lib uses appID -- unverified), Instance: slotId (lib uses slotID -- unverified)
    "pangleBidding": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "slotId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- smaatoBidding ---
    # App: applicationName, Instance: adspaceId (lib uses adspaceID -- unverified)
    "smaatoBidding": {
        "applicationName": {"type": "str", "required": True, "level": "app"},
        "adspaceId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- Snap ---
    # App: appId (lib uses AppId -- unverified), Instance: slotId (lib uses SlotID -- unverified)
    "Snap": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "slotId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- SuperAwesomeBidding ---
    # App: appId, Instance: placementId
    "SuperAwesomeBidding": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "placementId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- TapJoy ---
    # App: sdkKey + apiKey, Instance: placementName
    "TapJoy": {
        "sdkKey": {"type": "str", "required": True, "level": "app"},
        "apiKey": {"type": "str", "required": True, "level": "app"},
        "placementName": {"type": "str", "required": True, "level": "instance"},
    },
    # --- TapJoy Bidding ---
    # App: sdkKey + apiKey, Instance: placementName
    "TapJoyBidding": {
        "sdkKey": {"type": "str", "required": True, "level": "app"},
        "apiKey": {"type": "str", "required": True, "level": "app"},
        "placementName": {"type": "str", "required": True, "level": "instance"},
    },
    # --- Tencent ---
    # App: appId, Instance: placementId
    "Tencent": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "placementId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- UnityAds ---
    # App: sourceId, Instance: zoneId
    "UnityAds": {
        "sourceId": {"type": "str", "required": True, "level": "app"},
        "zoneId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- Vungle ---
    # App: appId (lib uses AppID) + reportingApiId (lib uses reportingAPIId -- unverified),
    # Instance: placementId (lib uses PlacementId -- unverified)
    "Vungle": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "reportingApiId": {"type": "str", "required": True, "level": "app"},
        "placementId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- Vungle Bidding ---
    # App: appId (lib uses AppID) + reportingApiId (lib uses reportingAPIId -- unverified),
    # Instance: placementId (lib uses PlacementId -- unverified)
    "vungleBidding": {
        "appId": {"type": "str", "required": True, "level": "app"},
        "reportingApiId": {"type": "str", "required": True, "level": "app"},
        "placementId": {"type": "str", "required": True, "level": "instance"},
    },
    # --- Yahoo Bidding ---
    # App: siteId (standard camelCase), Instance: placementId
    "yahooBidding": {
        "siteId": {"type": "str", "required": True, "level": "app"},
        "placementId": {"type": "str", "required": True, "level": "instance"},
    },
}


def validate_network_credentials(
    network: str, config: dict[str, Any]
) -> list[str]:
    """Validate a credential config dict against a network's schema.

    Checks that the network exists in the registry and that all required
    fields are present in the config. Does not validate field values or types
    beyond presence.

    Args:
        network: Network name string matching a ``Networks`` enum value
            (e.g., ``"AppLovin"``, ``"ironSource"``).
        config: Dict of credential field names to values.

    Returns:
        List of validation error message strings. Empty list means valid.

    Examples:
        >>> validate_network_credentials("AppLovin", {"sdkKey": "abc", "zoneId": "123"})
        []
        >>> validate_network_credentials("AppLovin", {"sdkKey": "abc"})
        ['AppLovin: missing required field "zoneId"']
        >>> validate_network_credentials("UnknownNetwork", {})
        ['Unknown network: "UnknownNetwork"']
    """
    errors: list[str] = []

    if network not in NETWORK_SCHEMAS:
        errors.append(f'Unknown network: "{network}"')
        return errors

    schema = NETWORK_SCHEMAS[network]
    for field_name, field_meta in schema.items():
        if field_meta["required"] and field_name not in config:
            errors.append(f'{network}: missing required field "{field_name}"')

    return errors

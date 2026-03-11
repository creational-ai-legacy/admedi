"""Tests for the network credential registry (Step 7).

Covers: registry completeness, schema structure, enum alignment,
validation function behavior (happy path, missing fields, unknown network,
no-config networks).
"""

from __future__ import annotations

import pytest

from admedi.models.enums import Networks
from admedi.networks import NETWORK_SCHEMAS, validate_network_credentials


# ---------------------------------------------------------------------------
# Registry completeness
# ---------------------------------------------------------------------------


class TestRegistryCompleteness:
    """Verify the registry has exactly 34 entries matching Networks enum."""

    def test_schema_count_is_34(self) -> None:
        assert len(NETWORK_SCHEMAS) == 34

    def test_all_network_enum_values_present(self) -> None:
        """Every Networks enum .value must appear as a key in NETWORK_SCHEMAS."""
        enum_values = {member.value for member in Networks}
        schema_keys = set(NETWORK_SCHEMAS.keys())
        missing = enum_values - schema_keys
        assert not missing, f"Networks in enum but not in registry: {missing}"

    def test_no_extra_keys_beyond_enum(self) -> None:
        """Registry should not have keys that are not in the Networks enum."""
        enum_values = {member.value for member in Networks}
        schema_keys = set(NETWORK_SCHEMAS.keys())
        extra = schema_keys - enum_values
        assert not extra, f"Keys in registry but not in enum: {extra}"

    def test_enum_values_exactly_match_schema_keys(self) -> None:
        """Enum values and schema keys are identical sets."""
        enum_values = {member.value for member in Networks}
        schema_keys = set(NETWORK_SCHEMAS.keys())
        assert enum_values == schema_keys


# ---------------------------------------------------------------------------
# Schema structure
# ---------------------------------------------------------------------------


class TestSchemaStructure:
    """Verify each schema entry has the expected field metadata shape."""

    @pytest.mark.parametrize("network", list(NETWORK_SCHEMAS.keys()))
    def test_field_metadata_has_required_keys(self, network: str) -> None:
        """Each field entry must have 'type', 'required', and 'level'."""
        schema = NETWORK_SCHEMAS[network]
        for field_name, meta in schema.items():
            assert "type" in meta, f"{network}.{field_name} missing 'type'"
            assert "required" in meta, f"{network}.{field_name} missing 'required'"
            assert "level" in meta, f"{network}.{field_name} missing 'level'"

    @pytest.mark.parametrize("network", list(NETWORK_SCHEMAS.keys()))
    def test_type_is_always_str(self, network: str) -> None:
        """Foundation task: all field types are 'str'."""
        schema = NETWORK_SCHEMAS[network]
        for field_name, meta in schema.items():
            assert meta["type"] == "str", (
                f"{network}.{field_name} type is {meta['type']!r}, expected 'str'"
            )

    @pytest.mark.parametrize("network", list(NETWORK_SCHEMAS.keys()))
    def test_required_is_bool(self, network: str) -> None:
        schema = NETWORK_SCHEMAS[network]
        for field_name, meta in schema.items():
            assert isinstance(meta["required"], bool), (
                f"{network}.{field_name} 'required' is {type(meta['required'])}, expected bool"
            )

    @pytest.mark.parametrize("network", list(NETWORK_SCHEMAS.keys()))
    def test_level_is_app_or_instance(self, network: str) -> None:
        schema = NETWORK_SCHEMAS[network]
        for field_name, meta in schema.items():
            assert meta["level"] in ("app", "instance"), (
                f"{network}.{field_name} level is {meta['level']!r}, expected 'app' or 'instance'"
            )


# ---------------------------------------------------------------------------
# Specific network schemas (spot-checks for correctness)
# ---------------------------------------------------------------------------


class TestSpecificNetworkSchemas:
    """Spot-check a few networks to verify their fields are correct."""

    def test_ironsource_has_no_fields(self) -> None:
        assert NETWORK_SCHEMAS["ironSource"] == {}

    def test_ironsource_bidding_has_no_fields(self) -> None:
        assert NETWORK_SCHEMAS["ironSourceBidding"] == {}

    def test_applovin_has_sdk_key_and_zone_id(self) -> None:
        schema = NETWORK_SCHEMAS["AppLovin"]
        assert "sdkKey" in schema
        assert schema["sdkKey"]["level"] == "app"
        assert schema["sdkKey"]["required"] is True
        assert "zoneId" in schema
        assert schema["zoneId"]["level"] == "instance"
        assert schema["zoneId"]["required"] is True

    def test_admob_has_app_id_and_ad_unit_id(self) -> None:
        schema = NETWORK_SCHEMAS["AdMob"]
        assert "appId" in schema
        assert schema["appId"]["level"] == "app"
        assert "adUnitId" in schema
        assert schema["adUnitId"]["level"] == "instance"

    def test_facebook_has_three_fields(self) -> None:
        schema = NETWORK_SCHEMAS["Facebook"]
        assert len(schema) == 3
        assert "appId" in schema
        assert "userAccessToken" in schema
        assert "placementId" in schema
        assert schema["appId"]["level"] == "app"
        assert schema["userAccessToken"]["level"] == "app"
        assert schema["placementId"]["level"] == "instance"

    def test_chartboost_has_three_fields(self) -> None:
        schema = NETWORK_SCHEMAS["Chartboost"]
        assert len(schema) == 3
        assert "appId" in schema
        assert "appSignature" in schema
        assert "adLocation" in schema

    def test_vungle_has_three_fields(self) -> None:
        schema = NETWORK_SCHEMAS["Vungle"]
        assert len(schema) == 3
        assert "appId" in schema
        assert "reportingApiId" in schema
        assert "placementId" in schema
        assert schema["appId"]["level"] == "app"
        assert schema["reportingApiId"]["level"] == "app"
        assert schema["placementId"]["level"] == "instance"

    def test_tapjoy_has_three_fields(self) -> None:
        schema = NETWORK_SCHEMAS["TapJoy"]
        assert len(schema) == 3
        assert "sdkKey" in schema
        assert "apiKey" in schema
        assert "placementName" in schema

    def test_unity_ads_has_source_id_and_zone_id(self) -> None:
        schema = NETWORK_SCHEMAS["UnityAds"]
        assert "sourceId" in schema
        assert schema["sourceId"]["level"] == "app"
        assert "zoneId" in schema
        assert schema["zoneId"]["level"] == "instance"

    def test_liftoff_bidding_key_has_space(self) -> None:
        """Liftoff Bidding has a space in its network name."""
        assert "Liftoff Bidding" in NETWORK_SCHEMAS
        schema = NETWORK_SCHEMAS["Liftoff Bidding"]
        assert "appId" in schema
        assert "adUnitId" in schema


# ---------------------------------------------------------------------------
# validate_network_credentials()
# ---------------------------------------------------------------------------


class TestValidateNetworkCredentials:
    """Test the validation function."""

    # -- Happy path --

    def test_applovin_all_required_fields_returns_empty(self) -> None:
        """Acceptance criterion: known network with all fields -> []."""
        errors = validate_network_credentials(
            "AppLovin", {"sdkKey": "abc", "zoneId": "123"}
        )
        assert errors == []

    def test_ironsource_empty_config_returns_empty(self) -> None:
        """Acceptance criterion: ironSource has no config -> [] with empty dict."""
        errors = validate_network_credentials("ironSource", {})
        assert errors == []

    def test_ironsource_bidding_empty_config_returns_empty(self) -> None:
        errors = validate_network_credentials("ironSourceBidding", {})
        assert errors == []

    def test_admob_all_fields_returns_empty(self) -> None:
        errors = validate_network_credentials(
            "AdMob", {"appId": "ca-app-123", "adUnitId": "ca-unit-456"}
        )
        assert errors == []

    def test_facebook_all_fields_returns_empty(self) -> None:
        errors = validate_network_credentials(
            "Facebook",
            {
                "appId": "fb-123",
                "userAccessToken": "token-abc",
                "placementId": "p-789",
            },
        )
        assert errors == []

    def test_extra_fields_are_ignored(self) -> None:
        """Extra keys beyond schema should not cause errors."""
        errors = validate_network_credentials(
            "AppLovin",
            {"sdkKey": "abc", "zoneId": "123", "extraField": "ignored"},
        )
        assert errors == []

    # -- Missing required fields --

    def test_applovin_missing_zone_id_returns_error(self) -> None:
        """Acceptance criterion: missing zoneId -> error list."""
        errors = validate_network_credentials("AppLovin", {"sdkKey": "abc"})
        assert len(errors) == 1
        assert "zoneId" in errors[0]
        assert "AppLovin" in errors[0]

    def test_applovin_missing_all_fields_returns_two_errors(self) -> None:
        errors = validate_network_credentials("AppLovin", {})
        assert len(errors) == 2

    def test_facebook_missing_one_field(self) -> None:
        errors = validate_network_credentials(
            "Facebook", {"appId": "fb-123", "userAccessToken": "token-abc"}
        )
        assert len(errors) == 1
        assert "placementId" in errors[0]

    def test_chartboost_missing_multiple_fields(self) -> None:
        errors = validate_network_credentials("Chartboost", {"appId": "cb-123"})
        assert len(errors) == 2  # missing appSignature and adLocation

    def test_error_message_contains_network_name(self) -> None:
        errors = validate_network_credentials("AdMob", {})
        for error in errors:
            assert "AdMob" in error

    def test_error_message_contains_field_name(self) -> None:
        errors = validate_network_credentials("AdMob", {"appId": "x"})
        assert len(errors) == 1
        assert "adUnitId" in errors[0]

    # -- Unknown network --

    def test_unknown_network_returns_error(self) -> None:
        """Acceptance criterion: unknown network -> error list."""
        errors = validate_network_credentials("UnknownNetwork", {})
        assert len(errors) == 1
        assert "UnknownNetwork" in errors[0]
        assert "Unknown network" in errors[0]

    def test_unknown_network_does_not_check_fields(self) -> None:
        """Unknown network should short-circuit -- no field errors."""
        errors = validate_network_credentials("FakeNetwork", {"a": "1"})
        assert len(errors) == 1

    # -- Edge cases --

    def test_empty_string_value_counts_as_present(self) -> None:
        """Empty string values should be considered present (not missing)."""
        errors = validate_network_credentials(
            "AppLovin", {"sdkKey": "", "zoneId": ""}
        )
        assert errors == []

    def test_none_value_counts_as_present(self) -> None:
        """None values should be considered present (key exists in dict)."""
        errors = validate_network_credentials(
            "AppLovin", {"sdkKey": None, "zoneId": None}
        )
        assert errors == []


# ---------------------------------------------------------------------------
# Import paths
# ---------------------------------------------------------------------------


class TestNetworkImports:
    """Verify public API is importable from expected paths."""

    def test_import_network_schemas(self) -> None:
        from admedi.networks import NETWORK_SCHEMAS as schemas

        assert isinstance(schemas, dict)

    def test_import_validate_function(self) -> None:
        from admedi.networks import validate_network_credentials as fn

        assert callable(fn)

    def test_import_unverified_casing(self) -> None:
        """Internal constant is accessible for documentation purposes."""
        from admedi.networks import _UNVERIFIED_CASING

        assert isinstance(_UNVERIFIED_CASING, dict)

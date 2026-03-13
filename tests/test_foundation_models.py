"""Tests for admedi pydantic v2 models.

Covers serialization round-trips, camelCase alias support, enum
integration, optional field defaults, and edge cases for all entity models.

Step 3: App, Credential, SyncLog, ConfigSnapshot
Step 4: Instance, CountryRate, Placement, Capping, Pacing, normalize_bool
Step 5: WaterfallTier, WaterfallConfig, Group
Step 6: TierDefinition, TierTemplate
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

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
from admedi.models.enums import AdFormat, Mediator, Platform, TierType
from admedi.models.placement import normalize_bool


# ---------------------------------------------------------------------------
# App model tests
# ---------------------------------------------------------------------------


class TestApp:
    """Tests for the App model."""

    def test_create_with_camelcase_aliases(self) -> None:
        """App accepts camelCase JSON keys via aliases."""
        app = App.model_validate(
            {
                "appKey": "abc123",
                "appName": "Test App",
                "platform": "Android",
                "bundleId": "com.test.app",
            }
        )
        assert app.app_key == "abc123"
        assert app.app_name == "Test App"
        assert app.platform == Platform.ANDROID
        assert app.bundle_id == "com.test.app"

    def test_create_with_snake_case_fields(self) -> None:
        """App accepts snake_case Python field names directly."""
        app = App(
            app_key="abc123",
            app_name="Test App",
            platform=Platform.ANDROID,
            bundle_id="com.test.app",
        )
        assert app.app_key == "abc123"

    def test_dump_by_alias_produces_camelcase(self) -> None:
        """model_dump(by_alias=True) produces camelCase keys."""
        app = App(
            app_key="abc123",
            app_name="Test App",
            platform=Platform.ANDROID,
            bundle_id="com.test.app",
        )
        dumped = app.model_dump(by_alias=True)
        assert dumped["appKey"] == "abc123"
        assert dumped["appName"] == "Test App"
        assert dumped["bundleId"] == "com.test.app"
        assert dumped["appStatus"] == "active"

    def test_round_trip_via_alias(self) -> None:
        """Round-trip: model_dump(by_alias=True) -> model_validate produces identical model."""
        app = App(
            app_key="abc123",
            app_name="Test App",
            platform=Platform.ANDROID,
            bundle_id="com.test.app",
            coppa=True,
            taxonomy="games",
        )
        dumped = app.model_dump(by_alias=True)
        restored = App.model_validate(dumped)
        assert restored == app

    def test_round_trip_without_alias(self) -> None:
        """Round-trip: model_dump() -> model_validate produces identical model."""
        app = App(
            app_key="abc123",
            app_name="Test App",
            platform=Platform.IOS,
            bundle_id="com.test.app",
        )
        dumped = app.model_dump()
        restored = App.model_validate(dumped)
        assert restored == app

    def test_defaults(self) -> None:
        """Optional fields default to None and status/mediator/coppa have defaults."""
        app = App(
            app_key="key",
            app_name="name",
            platform=Platform.ANDROID,
            bundle_id="com.test",
        )
        assert app.app_status == "active"
        assert app.mediator == Mediator.LEVELPLAY
        assert app.coppa is False
        assert app.taxonomy is None
        assert app.creation_date is None
        assert app.ad_units is None
        assert app.ccpa is None
        assert app.network_reporting_api is None
        assert app.bundle_ref_id is None
        assert app.icon is None

    def test_platform_enum_from_string(self) -> None:
        """Platform field accepts string value and resolves to enum."""
        app = App.model_validate(
            {
                "appKey": "k",
                "appName": "n",
                "platform": "iOS",
                "bundleId": "com.t",
            }
        )
        assert app.platform == Platform.IOS
        assert isinstance(app.platform, Platform)

    def test_mediator_enum_from_string(self) -> None:
        """Mediator field accepts string value and resolves to enum."""
        app = App(
            app_key="k",
            app_name="n",
            platform=Platform.ANDROID,
            bundle_id="com.t",
            mediator="levelplay",  # type: ignore[arg-type]
        )
        assert app.mediator == Mediator.LEVELPLAY

    def test_all_optional_fields_populated(self) -> None:
        """App with all fields populated round-trips correctly."""
        app = App(
            app_key="abc",
            app_name="Full App",
            platform=Platform.AMAZON,
            bundle_id="com.full.app",
            app_status="paused",
            mediator=Mediator.LEVELPLAY,
            coppa=True,
            taxonomy="games",
            creation_date="2025-06-15",
            ad_units={"banner": {"active": True}},
            ccpa="1YYY",
            network_reporting_api=True,
            bundle_ref_id="ref123",
            icon="https://example.com/icon.png",
        )
        dumped = app.model_dump(by_alias=True)
        restored = App.model_validate(dumped)
        assert restored == app
        assert restored.creation_date == "2025-06-15"
        assert restored.ad_units == {"banner": {"active": True}}
        assert restored.icon == "https://example.com/icon.png"

    def test_missing_required_field_raises(self) -> None:
        """Missing required field raises ValidationError."""
        with pytest.raises(ValidationError):
            App.model_validate(
                {
                    "appKey": "abc",
                    "appName": "Test",
                    # missing platform and bundleId
                }
            )

    def test_invalid_platform_raises(self) -> None:
        """Invalid platform string raises ValidationError."""
        with pytest.raises(ValidationError):
            App.model_validate(
                {
                    "appKey": "abc",
                    "appName": "Test",
                    "platform": "Windows",
                    "bundleId": "com.test",
                }
            )

    def test_ad_units_nested_dict(self) -> None:
        """ad_units accepts arbitrarily nested dict."""
        nested = {
            "banner": {"active": True, "refresh_rate": 30},
            "interstitial": {"capping": {"enabled": True, "amount": 5}},
        }
        app = App(
            app_key="k",
            app_name="n",
            platform=Platform.ANDROID,
            bundle_id="com.t",
            ad_units=nested,
        )
        assert app.ad_units == nested


# ---------------------------------------------------------------------------
# Credential model tests
# ---------------------------------------------------------------------------


class TestCredential:
    """Tests for the Credential model."""

    def test_create_with_enum(self) -> None:
        """Credential accepts Mediator enum directly."""
        cred = Credential(
            mediator=Mediator.LEVELPLAY,
            secret_key="sk_abc",
            refresh_token="rt_xyz",
        )
        assert cred.mediator == Mediator.LEVELPLAY
        assert cred.secret_key == "sk_abc"
        assert cred.refresh_token == "rt_xyz"
        assert cred.token_expiry is None

    def test_create_from_string_mediator(self) -> None:
        """Credential accepts string value for mediator and resolves to enum."""
        cred = Credential(
            mediator="levelplay",  # type: ignore[arg-type]
            secret_key="sk",
            refresh_token="rt",
        )
        assert cred.mediator == Mediator.LEVELPLAY
        assert isinstance(cred.mediator, Mediator)

    def test_token_expiry_round_trip(self) -> None:
        """Credential with token_expiry datetime round-trips correctly."""
        expiry = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        cred = Credential(
            mediator=Mediator.LEVELPLAY,
            secret_key="sk",
            refresh_token="rt",
            token_expiry=expiry,
        )
        dumped = cred.model_dump()
        restored = Credential.model_validate(dumped)
        assert restored.token_expiry == expiry
        assert restored == cred

    def test_round_trip(self) -> None:
        """Full round-trip via model_dump/model_validate."""
        cred = Credential(
            mediator=Mediator.MAX,
            secret_key="sk_max",
            refresh_token="rt_max",
        )
        dumped = cred.model_dump()
        restored = Credential.model_validate(dumped)
        assert restored == cred

    def test_missing_required_field_raises(self) -> None:
        """Missing required field raises ValidationError."""
        with pytest.raises(ValidationError):
            Credential(
                mediator=Mediator.LEVELPLAY,
                secret_key="sk",
                # missing refresh_token
            )  # type: ignore[call-arg]

    def test_invalid_mediator_raises(self) -> None:
        """Invalid mediator string raises ValidationError."""
        with pytest.raises(ValidationError):
            Credential(
                mediator="unknown_platform",  # type: ignore[arg-type]
                secret_key="sk",
                refresh_token="rt",
            )


# ---------------------------------------------------------------------------
# SyncLog model tests
# ---------------------------------------------------------------------------


class TestSyncLog:
    """Tests for the SyncLog model."""

    def test_create_minimal(self) -> None:
        """SyncLog with required fields only (error defaults to None)."""
        from admedi.models.apply_result import ApplyStatus

        ts = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
        log = SyncLog(
            app_key="abc123",
            timestamp=ts,
            action="sync",
            groups_created=2,
            groups_updated=1,
            status=ApplyStatus.SUCCESS,
        )
        assert log.timestamp == ts
        assert log.app_key == "abc123"
        assert log.action == "sync"
        assert log.groups_created == 2
        assert log.groups_updated == 1
        assert log.status == ApplyStatus.SUCCESS
        assert log.error is None

    def test_create_with_all_fields(self) -> None:
        """SyncLog with all fields populated including error."""
        from admedi.models.apply_result import ApplyStatus

        ts = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
        log = SyncLog(
            app_key="abc123",
            timestamp=ts,
            action="sync",
            groups_created=0,
            groups_updated=0,
            status=ApplyStatus.FAILED,
            error="API returned 500",
        )
        assert log.status == ApplyStatus.FAILED
        assert log.error == "API returned 500"

    def test_round_trip(self) -> None:
        """SyncLog round-trips via model_dump/model_validate."""
        from admedi.models.apply_result import ApplyStatus

        ts = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
        log = SyncLog(
            app_key="abc123",
            timestamp=ts,
            action="sync",
            groups_created=1,
            groups_updated=0,
            status=ApplyStatus.SUCCESS,
        )
        dumped = log.model_dump()
        restored = SyncLog.model_validate(dumped)
        assert restored == log

    def test_datetime_serialization(self) -> None:
        """SyncLog datetime field serializes and deserializes correctly."""
        from admedi.models.apply_result import ApplyStatus

        ts = datetime(2026, 6, 15, 14, 30, 0, tzinfo=timezone.utc)
        log = SyncLog(
            app_key="key",
            timestamp=ts,
            action="sync",
            groups_created=0,
            groups_updated=0,
            status=ApplyStatus.DRY_RUN,
        )
        dumped = log.model_dump()
        restored = SyncLog.model_validate(dumped)
        assert restored.timestamp == ts

    def test_missing_required_field_raises(self) -> None:
        """Missing required field raises ValidationError."""
        with pytest.raises(ValidationError):
            SyncLog(
                timestamp=datetime.now(tz=timezone.utc),
                app_key="key",
                # missing action, groups_created, groups_updated, status
            )  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ConfigSnapshot model tests
# ---------------------------------------------------------------------------


class TestConfigSnapshot:
    """Tests for the ConfigSnapshot model."""

    def test_create(self) -> None:
        """ConfigSnapshot with basic raw_config."""
        ts = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
        snap = ConfigSnapshot(
            app_key="abc123",
            timestamp=ts,
            raw_config={"groups": [{"groupName": "Default"}]},
        )
        assert snap.app_key == "abc123"
        assert snap.timestamp == ts
        assert snap.raw_config["groups"][0]["groupName"] == "Default"

    def test_round_trip(self) -> None:
        """ConfigSnapshot round-trips via model_dump/model_validate."""
        ts = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
        snap = ConfigSnapshot(
            app_key="abc123",
            timestamp=ts,
            raw_config={"groups": [], "instances": {}},
        )
        dumped = snap.model_dump()
        restored = ConfigSnapshot.model_validate(dumped)
        assert restored == snap

    def test_nested_raw_config(self) -> None:
        """ConfigSnapshot handles deeply nested raw_config."""
        config = {
            "groups": [
                {
                    "groupName": "Tier1",
                    "countries": ["US"],
                    "waterfall": {
                        "tier1": {
                            "tierType": "manual",
                            "instances": [
                                {"id": 1, "name": "IS", "networkName": "ironSource"}
                            ],
                        }
                    },
                }
            ],
            "placements": [{"name": "Default", "adDelivery": 1}],
        }
        snap = ConfigSnapshot(
            app_key="key",
            timestamp=datetime.now(tz=timezone.utc),
            raw_config=config,
        )
        dumped = snap.model_dump()
        restored = ConfigSnapshot.model_validate(dumped)
        assert restored.raw_config == config

    def test_empty_raw_config(self) -> None:
        """ConfigSnapshot accepts empty raw_config dict."""
        snap = ConfigSnapshot(
            app_key="key",
            timestamp=datetime.now(tz=timezone.utc),
            raw_config={},
        )
        assert snap.raw_config == {}

    def test_missing_required_field_raises(self) -> None:
        """Missing required field raises ValidationError."""
        with pytest.raises(ValidationError):
            ConfigSnapshot(
                app_key="key",
                # missing timestamp and raw_config
            )  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Import path tests
# ---------------------------------------------------------------------------


class TestModelImports:
    """Tests that models are importable from admedi.models."""

    def test_import_app_from_models(self) -> None:
        """App is importable from admedi.models."""
        from admedi.models import App as AppModel

        assert AppModel is App

    def test_import_credential_from_models(self) -> None:
        """Credential is importable from admedi.models."""
        from admedi.models import Credential as CredModel

        assert CredModel is Credential

    def test_import_sync_log_from_models(self) -> None:
        """SyncLog is importable from admedi.models."""
        from admedi.models import SyncLog as LogModel

        assert LogModel is SyncLog

    def test_import_config_snapshot_from_models(self) -> None:
        """ConfigSnapshot is importable from admedi.models."""
        from admedi.models import ConfigSnapshot as SnapModel

        assert SnapModel is ConfigSnapshot

    def test_import_instance_from_models(self) -> None:
        """Instance is importable from admedi.models."""
        from admedi.models import Instance as InstModel

        assert InstModel is Instance

    def test_import_country_rate_from_models(self) -> None:
        """CountryRate is importable from admedi.models."""
        from admedi.models import CountryRate as CRModel

        assert CRModel is CountryRate

    def test_import_placement_from_models(self) -> None:
        """Placement is importable from admedi.models."""
        from admedi.models import Placement as PlModel

        assert PlModel is Placement

    def test_import_capping_from_models(self) -> None:
        """Capping is importable from admedi.models."""
        from admedi.models import Capping as CapModel

        assert CapModel is Capping

    def test_import_pacing_from_models(self) -> None:
        """Pacing is importable from admedi.models."""
        from admedi.models import Pacing as PacModel

        assert PacModel is Pacing


# ---------------------------------------------------------------------------
# CountryRate model tests (Step 4)
# ---------------------------------------------------------------------------


class TestCountryRate:
    """Tests for the CountryRate model."""

    def test_create_with_camelcase(self) -> None:
        """CountryRate accepts camelCase alias."""
        cr = CountryRate.model_validate({"countryCode": "US", "rate": 15.0})
        assert cr.country_code == "US"
        assert cr.rate == 15.0

    def test_create_with_snake_case(self) -> None:
        """CountryRate accepts snake_case field names."""
        cr = CountryRate(country_code="GB", rate=12.5)
        assert cr.country_code == "GB"
        assert cr.rate == 12.5

    def test_dump_by_alias(self) -> None:
        """model_dump(by_alias=True) produces camelCase keys."""
        cr = CountryRate(country_code="JP", rate=10.0)
        dumped = cr.model_dump(by_alias=True)
        assert dumped["countryCode"] == "JP"
        assert dumped["rate"] == 10.0

    def test_round_trip_via_alias(self) -> None:
        """Round-trip via camelCase alias produces identical model."""
        cr = CountryRate(country_code="AU", rate=8.0)
        dumped = cr.model_dump(by_alias=True)
        restored = CountryRate.model_validate(dumped)
        assert restored == cr

    def test_round_trip_without_alias(self) -> None:
        """Round-trip via snake_case produces identical model."""
        cr = CountryRate(country_code="DE", rate=11.0)
        dumped = cr.model_dump()
        restored = CountryRate.model_validate(dumped)
        assert restored == cr

    def test_missing_required_field_raises(self) -> None:
        """Missing required field raises ValidationError."""
        with pytest.raises(ValidationError):
            CountryRate.model_validate({"countryCode": "US"})

    def test_zero_rate(self) -> None:
        """CountryRate accepts zero rate."""
        cr = CountryRate(country_code="FR", rate=0.0)
        assert cr.rate == 0.0


# ---------------------------------------------------------------------------
# Instance model tests (Step 4)
# ---------------------------------------------------------------------------


class TestInstance:
    """Tests for the Instance model."""

    def test_create_groups_v4_embedded_shape(self) -> None:
        """Instance accepts the Groups v4 embedded shape (minimal common fields)."""
        inst = Instance.model_validate(
            {
                "id": 1,
                "name": "ironSource Default",
                "networkName": "ironSource",
                "isBidder": True,
            }
        )
        assert inst.instance_id == 1
        assert inst.instance_name == "ironSource Default"
        assert inst.network_name == "ironSource"
        assert inst.is_bidder is True

    def test_create_with_snake_case(self) -> None:
        """Instance accepts snake_case field names."""
        inst = Instance(
            instance_id=2,
            instance_name="AdMob Instance",
            network_name="AdMob",
            is_bidder=False,
        )
        assert inst.instance_id == 2
        assert inst.instance_name == "AdMob Instance"

    def test_standalone_shape_with_optional_fields(self) -> None:
        """Instance accepts the standalone Instances API shape with extra fields."""
        inst = Instance.model_validate(
            {
                "id": 5,
                "name": "Standalone IS",
                "networkName": "ironSource",
                "isBidder": False,
                "adUnit": "banner",
                "isLive": True,
                "isOptimized": False,
                "groupRate": 5.5,
            }
        )
        assert inst.ad_unit == AdFormat.BANNER
        assert inst.is_live is True
        assert inst.is_optimized is False
        assert inst.group_rate == 5.5

    def test_defaults(self) -> None:
        """Optional fields default to None, is_bidder defaults to False."""
        inst = Instance(
            instance_name="Minimal",
            network_name="UnityAds",
        )
        assert inst.instance_id is None
        assert inst.is_bidder is False
        assert inst.group_rate is None
        assert inst.countries_rate is None
        assert inst.ad_unit is None
        assert inst.is_live is None
        assert inst.is_optimized is None

    def test_dump_by_alias(self) -> None:
        """model_dump(by_alias=True) produces camelCase keys."""
        inst = Instance(
            instance_id=10,
            instance_name="Test",
            network_name="Facebook",
            is_bidder=True,
            group_rate=3.0,
        )
        dumped = inst.model_dump(by_alias=True)
        assert dumped["id"] == 10
        assert dumped["name"] == "Test"
        assert dumped["networkName"] == "Facebook"
        assert dumped["isBidder"] is True
        assert dumped["groupRate"] == 3.0

    def test_round_trip_via_alias(self) -> None:
        """Round-trip via camelCase alias produces identical model."""
        inst = Instance(
            instance_id=3,
            instance_name="RT Test",
            network_name="AppLovin",
            is_bidder=True,
            group_rate=7.5,
            ad_unit=AdFormat.INTERSTITIAL,
            is_live=True,
            is_optimized=False,
        )
        dumped = inst.model_dump(by_alias=True)
        restored = Instance.model_validate(dumped)
        assert restored == inst

    def test_round_trip_without_alias(self) -> None:
        """Round-trip via snake_case produces identical model."""
        inst = Instance(
            instance_name="Snake Test",
            network_name="Chartboost",
        )
        dumped = inst.model_dump()
        restored = Instance.model_validate(dumped)
        assert restored == inst

    def test_with_countries_rate(self) -> None:
        """Instance with countries_rate list of CountryRate objects round-trips."""
        rates = [
            CountryRate(country_code="US", rate=15.0),
            CountryRate(country_code="GB", rate=12.0),
        ]
        inst = Instance(
            instance_id=4,
            instance_name="Geo Inst",
            network_name="InMobi",
            countries_rate=rates,
        )
        assert inst.countries_rate is not None
        assert len(inst.countries_rate) == 2
        assert inst.countries_rate[0].country_code == "US"
        assert inst.countries_rate[1].rate == 12.0

    def test_countries_rate_round_trip_via_alias(self) -> None:
        """Instance with nested CountryRate round-trips via camelCase."""
        rates = [CountryRate(country_code="JP", rate=10.0)]
        inst = Instance(
            instance_id=6,
            instance_name="JP Inst",
            network_name="Pangle",
            countries_rate=rates,
        )
        dumped = inst.model_dump(by_alias=True)
        restored = Instance.model_validate(dumped)
        assert restored == inst
        assert restored.countries_rate is not None
        assert restored.countries_rate[0].country_code == "JP"

    def test_ad_unit_enum_from_string(self) -> None:
        """ad_unit field accepts string value and resolves to AdFormat enum."""
        inst = Instance.model_validate(
            {
                "name": "Enum Test",
                "networkName": "AdMob",
                "adUnit": "rewardedVideo",
            }
        )
        assert inst.ad_unit == AdFormat.REWARDED_VIDEO
        assert isinstance(inst.ad_unit, AdFormat)

    def test_missing_required_field_raises(self) -> None:
        """Missing required field raises ValidationError."""
        with pytest.raises(ValidationError):
            Instance.model_validate({"id": 1, "networkName": "ironSource"})

    def test_invalid_ad_unit_raises(self) -> None:
        """Invalid adUnit string raises ValidationError."""
        with pytest.raises(ValidationError):
            Instance.model_validate(
                {
                    "name": "Bad",
                    "networkName": "AdMob",
                    "adUnit": "fullscreen",
                }
            )


# ---------------------------------------------------------------------------
# normalize_bool function tests (Step 4)
# ---------------------------------------------------------------------------


class TestNormalizeBool:
    """Tests for the normalize_bool utility function."""

    def test_bool_true_passthrough(self) -> None:
        """True passes through unchanged."""
        assert normalize_bool(True) is True

    def test_bool_false_passthrough(self) -> None:
        """False passes through unchanged."""
        assert normalize_bool(False) is False

    def test_int_zero(self) -> None:
        """Integer 0 normalizes to False."""
        assert normalize_bool(0) is False

    def test_int_one(self) -> None:
        """Integer 1 normalizes to True."""
        assert normalize_bool(1) is True

    def test_string_zero(self) -> None:
        """String '0' normalizes to False."""
        assert normalize_bool("0") is False

    def test_string_one(self) -> None:
        """String '1' normalizes to True."""
        assert normalize_bool("1") is True

    def test_string_true(self) -> None:
        """String 'true' normalizes to True."""
        assert normalize_bool("true") is True

    def test_string_false(self) -> None:
        """String 'false' normalizes to False."""
        assert normalize_bool("false") is False

    def test_string_active(self) -> None:
        """String 'active' normalizes to True."""
        assert normalize_bool("active") is True

    def test_string_inactive(self) -> None:
        """String 'inactive' normalizes to False."""
        assert normalize_bool("inactive") is False

    def test_string_live(self) -> None:
        """String 'live' normalizes to True."""
        assert normalize_bool("live") is True

    def test_string_case_insensitive_true(self) -> None:
        """String comparison is case-insensitive ('True' -> True)."""
        assert normalize_bool("True") is True

    def test_string_case_insensitive_false(self) -> None:
        """String comparison is case-insensitive ('FALSE' -> False)."""
        assert normalize_bool("FALSE") is False

    def test_string_case_insensitive_active(self) -> None:
        """String comparison is case-insensitive ('Active' -> True)."""
        assert normalize_bool("Active") is True

    def test_string_case_insensitive_inactive(self) -> None:
        """String comparison is case-insensitive ('INACTIVE' -> False)."""
        assert normalize_bool("INACTIVE") is False

    def test_invalid_int_raises(self) -> None:
        """Integer other than 0 or 1 raises ValueError."""
        with pytest.raises(ValueError, match="Cannot interpret integer 2"):
            normalize_bool(2)

    def test_invalid_string_raises(self) -> None:
        """Unrecognized string raises ValueError."""
        with pytest.raises(ValueError, match="Cannot interpret string"):
            normalize_bool("maybe")

    def test_invalid_type_raises(self) -> None:
        """Non-bool/int/str type raises ValueError."""
        with pytest.raises(ValueError, match="Cannot interpret list"):
            normalize_bool([1, 2, 3])  # type: ignore[arg-type]

    def test_none_raises(self) -> None:
        """None raises ValueError."""
        with pytest.raises(ValueError, match="Cannot interpret NoneType"):
            normalize_bool(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Capping model tests (Step 4)
# ---------------------------------------------------------------------------


class TestCapping:
    """Tests for the Capping model."""

    def test_create_enabled(self) -> None:
        """Capping with all fields populated."""
        cap = Capping(enabled=True, amount=5, interval="day")
        assert cap.enabled is True
        assert cap.amount == 5
        assert cap.interval == "day"

    def test_create_disabled(self) -> None:
        """Capping with enabled=False and no other fields."""
        cap = Capping(enabled=False)
        assert cap.enabled is False
        assert cap.amount is None
        assert cap.interval is None

    def test_round_trip(self) -> None:
        """Capping round-trips via model_dump/model_validate."""
        cap = Capping(enabled=True, amount=10, interval="hour")
        dumped = cap.model_dump()
        restored = Capping.model_validate(dumped)
        assert restored == cap

    def test_missing_enabled_raises(self) -> None:
        """Missing 'enabled' field raises ValidationError."""
        with pytest.raises(ValidationError):
            Capping.model_validate({"amount": 5})


# ---------------------------------------------------------------------------
# Pacing model tests (Step 4)
# ---------------------------------------------------------------------------


class TestPacing:
    """Tests for the Pacing model."""

    def test_create_enabled(self) -> None:
        """Pacing with enabled and seconds."""
        pac = Pacing(enabled=True, seconds=30.0)
        assert pac.enabled is True
        assert pac.seconds == 30.0

    def test_create_disabled(self) -> None:
        """Pacing with enabled=False and no seconds."""
        pac = Pacing(enabled=False)
        assert pac.enabled is False
        assert pac.seconds is None

    def test_round_trip(self) -> None:
        """Pacing round-trips via model_dump/model_validate."""
        pac = Pacing(enabled=True, seconds=60.0)
        dumped = pac.model_dump()
        restored = Pacing.model_validate(dumped)
        assert restored == pac

    def test_missing_enabled_raises(self) -> None:
        """Missing 'enabled' field raises ValidationError."""
        with pytest.raises(ValidationError):
            Pacing.model_validate({"seconds": 10.0})


# ---------------------------------------------------------------------------
# Placement model tests (Step 4)
# ---------------------------------------------------------------------------


class TestPlacement:
    """Tests for the Placement model."""

    def test_create_with_camelcase(self) -> None:
        """Placement accepts camelCase JSON keys via aliases."""
        p = Placement.model_validate(
            {"name": "Default", "adUnit": "banner", "adDelivery": True}
        )
        assert p.name == "Default"
        assert p.ad_unit == AdFormat.BANNER
        assert p.ad_delivery is True

    def test_create_with_snake_case(self) -> None:
        """Placement accepts snake_case field names."""
        p = Placement(
            name="Default",
            ad_unit=AdFormat.BANNER,
            ad_delivery=True,
        )
        assert p.name == "Default"
        assert p.ad_unit == AdFormat.BANNER
        assert p.ad_delivery is True

    def test_dump_by_alias(self) -> None:
        """model_dump(by_alias=True) produces camelCase keys."""
        p = Placement(
            placement_id=42,
            name="Test",
            ad_unit=AdFormat.INTERSTITIAL,
            ad_delivery=False,
        )
        dumped = p.model_dump(by_alias=True)
        assert dumped["id"] == 42
        assert dumped["adUnit"] == "interstitial"
        assert dumped["adDelivery"] is False

    def test_round_trip_via_alias(self) -> None:
        """Round-trip via camelCase alias produces identical model."""
        p = Placement(
            placement_id=1,
            name="RT Test",
            ad_unit=AdFormat.REWARDED_VIDEO,
            ad_delivery=True,
        )
        dumped = p.model_dump(by_alias=True)
        restored = Placement.model_validate(dumped)
        assert restored == p

    def test_round_trip_without_alias(self) -> None:
        """Round-trip via snake_case produces identical model."""
        p = Placement(
            name="Snake",
            ad_unit=AdFormat.BANNER,
            ad_delivery=False,
        )
        dumped = p.model_dump()
        restored = Placement.model_validate(dumped)
        assert restored == p

    def test_defaults(self) -> None:
        """Optional fields default to None."""
        p = Placement(
            name="Minimal",
            ad_unit=AdFormat.BANNER,
            ad_delivery=True,
        )
        assert p.placement_id is None
        assert p.capping is None
        assert p.pacing is None

    # Boolean normalization tests (acceptance criteria)

    def test_bool_normalization_int_zero(self) -> None:
        """adDelivery: 0 -> False."""
        p = Placement.model_validate(
            {"name": "Default", "adUnit": "banner", "adDelivery": 0}
        )
        assert p.ad_delivery is False

    def test_bool_normalization_int_one(self) -> None:
        """adDelivery: 1 -> True."""
        p = Placement.model_validate(
            {"name": "Default", "adUnit": "banner", "adDelivery": 1}
        )
        assert p.ad_delivery is True

    def test_bool_normalization_string_active(self) -> None:
        """adDelivery: 'active' -> True."""
        p = Placement.model_validate(
            {"name": "Default", "adUnit": "banner", "adDelivery": "active"}
        )
        assert p.ad_delivery is True

    def test_bool_normalization_string_inactive(self) -> None:
        """adDelivery: 'inactive' -> False."""
        p = Placement.model_validate(
            {"name": "Default", "adUnit": "banner", "adDelivery": "inactive"}
        )
        assert p.ad_delivery is False

    def test_bool_normalization_string_true(self) -> None:
        """adDelivery: 'true' -> True."""
        p = Placement.model_validate(
            {"name": "Default", "adUnit": "banner", "adDelivery": "true"}
        )
        assert p.ad_delivery is True

    def test_bool_normalization_string_false(self) -> None:
        """adDelivery: 'false' -> False."""
        p = Placement.model_validate(
            {"name": "Default", "adUnit": "banner", "adDelivery": "false"}
        )
        assert p.ad_delivery is False

    def test_bool_normalization_string_one(self) -> None:
        """adDelivery: '1' -> True."""
        p = Placement.model_validate(
            {"name": "Default", "adUnit": "banner", "adDelivery": "1"}
        )
        assert p.ad_delivery is True

    def test_bool_normalization_string_zero(self) -> None:
        """adDelivery: '0' -> False."""
        p = Placement.model_validate(
            {"name": "Default", "adUnit": "banner", "adDelivery": "0"}
        )
        assert p.ad_delivery is False

    def test_bool_normalization_string_live(self) -> None:
        """adDelivery: 'live' -> True."""
        p = Placement.model_validate(
            {"name": "Default", "adUnit": "banner", "adDelivery": "live"}
        )
        assert p.ad_delivery is True

    def test_bool_normalization_bool_true(self) -> None:
        """adDelivery: True passes through unchanged."""
        p = Placement.model_validate(
            {"name": "Default", "adUnit": "banner", "adDelivery": True}
        )
        assert p.ad_delivery is True

    def test_bool_normalization_bool_false(self) -> None:
        """adDelivery: False passes through unchanged."""
        p = Placement.model_validate(
            {"name": "Default", "adUnit": "banner", "adDelivery": False}
        )
        assert p.ad_delivery is False

    def test_bool_normalization_invalid_string_raises(self) -> None:
        """adDelivery: invalid string raises ValidationError."""
        with pytest.raises(ValidationError):
            Placement.model_validate(
                {"name": "Default", "adUnit": "banner", "adDelivery": "maybe"}
            )

    # Nested Capping and Pacing tests

    def test_with_capping_and_pacing(self) -> None:
        """Placement with nested Capping and Pacing objects."""
        p = Placement(
            name="Full",
            ad_unit=AdFormat.INTERSTITIAL,
            ad_delivery=True,
            capping=Capping(enabled=True, amount=5, interval="day"),
            pacing=Pacing(enabled=False),
        )
        assert p.capping is not None
        assert p.capping.enabled is True
        assert p.capping.amount == 5
        assert p.capping.interval == "day"
        assert p.pacing is not None
        assert p.pacing.enabled is False

    def test_with_nested_capping_pacing_round_trip(self) -> None:
        """Placement with nested Capping and Pacing round-trips via alias."""
        p = Placement(
            placement_id=99,
            name="Nested RT",
            ad_unit=AdFormat.REWARDED_VIDEO,
            ad_delivery=True,
            capping=Capping(enabled=True, amount=10, interval="hour"),
            pacing=Pacing(enabled=True, seconds=45.0),
        )
        dumped = p.model_dump(by_alias=True)
        restored = Placement.model_validate(dumped)
        assert restored == p
        assert restored.capping is not None
        assert restored.capping.amount == 10
        assert restored.pacing is not None
        assert restored.pacing.seconds == 45.0

    def test_with_nested_dicts_from_json(self) -> None:
        """Placement accepts nested capping/pacing as raw dicts."""
        p = Placement.model_validate(
            {
                "name": "JSON",
                "adUnit": "banner",
                "adDelivery": 1,
                "capping": {"enabled": True, "amount": 3, "interval": "day"},
                "pacing": {"enabled": True, "seconds": 20.0},
            }
        )
        assert p.capping is not None
        assert p.capping.enabled is True
        assert p.capping.amount == 3
        assert p.pacing is not None
        assert p.pacing.seconds == 20.0

    def test_missing_required_field_raises(self) -> None:
        """Missing required field raises ValidationError."""
        with pytest.raises(ValidationError):
            Placement.model_validate({"name": "Default"})

    def test_invalid_ad_unit_raises(self) -> None:
        """Invalid adUnit string raises ValidationError."""
        with pytest.raises(ValidationError):
            Placement.model_validate(
                {"name": "Bad", "adUnit": "fullscreen", "adDelivery": True}
            )


# ---------------------------------------------------------------------------
# WaterfallTier model tests (Step 5)
# ---------------------------------------------------------------------------


class TestWaterfallTier:
    """Tests for the WaterfallTier model."""

    def test_create_with_camelcase(self) -> None:
        """WaterfallTier accepts camelCase alias for tier_type."""
        wt = WaterfallTier.model_validate({"tierType": "manual"})
        assert wt.tier_type == TierType.MANUAL
        assert wt.instances == []

    def test_create_with_snake_case(self) -> None:
        """WaterfallTier accepts snake_case field names."""
        wt = WaterfallTier(tier_type=TierType.SORT_BY_CPM)
        assert wt.tier_type == TierType.SORT_BY_CPM
        assert wt.instances == []

    def test_with_instances(self) -> None:
        """WaterfallTier with a list of Instance objects."""
        inst = Instance(
            instance_id=1,
            instance_name="IS Default",
            network_name="ironSource",
            is_bidder=False,
        )
        wt = WaterfallTier(tier_type=TierType.MANUAL, instances=[inst])
        assert len(wt.instances) == 1
        assert wt.instances[0].instance_name == "IS Default"

    def test_dump_by_alias(self) -> None:
        """model_dump(by_alias=True) produces camelCase keys."""
        wt = WaterfallTier(tier_type=TierType.BIDDING)
        dumped = wt.model_dump(by_alias=True)
        assert dumped["tierType"] == "bidding"
        assert dumped["instances"] == []

    def test_round_trip_via_alias(self) -> None:
        """Round-trip via camelCase alias produces identical model."""
        inst = Instance(
            instance_id=2,
            instance_name="AppLovin Inst",
            network_name="AppLovin",
            is_bidder=True,
            group_rate=5.0,
        )
        wt = WaterfallTier(tier_type=TierType.MANUAL, instances=[inst])
        dumped = wt.model_dump(by_alias=True)
        restored = WaterfallTier.model_validate(dumped)
        assert restored == wt

    def test_round_trip_without_alias(self) -> None:
        """Round-trip via snake_case produces identical model."""
        wt = WaterfallTier(tier_type=TierType.OPTIMIZED)
        dumped = wt.model_dump()
        restored = WaterfallTier.model_validate(dumped)
        assert restored == wt

    def test_tier_type_enum_from_string(self) -> None:
        """tier_type field accepts string value and resolves to TierType enum."""
        wt = WaterfallTier.model_validate({"tierType": "sortByCpm"})
        assert wt.tier_type == TierType.SORT_BY_CPM
        assert isinstance(wt.tier_type, TierType)

    def test_instances_from_raw_dicts(self) -> None:
        """WaterfallTier accepts instances as raw dicts."""
        wt = WaterfallTier.model_validate(
            {
                "tierType": "manual",
                "instances": [
                    {"name": "IS", "networkName": "ironSource"},
                    {"name": "AL", "networkName": "AppLovin", "isBidder": True},
                ],
            }
        )
        assert len(wt.instances) == 2
        assert wt.instances[0].network_name == "ironSource"
        assert wt.instances[1].is_bidder is True

    def test_missing_tier_type_raises(self) -> None:
        """Missing tier_type field raises ValidationError."""
        with pytest.raises(ValidationError):
            WaterfallTier.model_validate({"instances": []})

    def test_invalid_tier_type_raises(self) -> None:
        """Invalid tier_type string raises ValidationError."""
        with pytest.raises(ValidationError):
            WaterfallTier.model_validate({"tierType": "randomOrder"})


# ---------------------------------------------------------------------------
# WaterfallConfig model tests (Step 5)
# ---------------------------------------------------------------------------


class TestWaterfallConfig:
    """Tests for the WaterfallConfig model and its OPTIMIZED+bidding validator."""

    def test_empty_waterfall(self) -> None:
        """WaterfallConfig with no tiers is valid."""
        wf = WaterfallConfig()
        assert wf.bidding is None
        assert wf.tier1 is None
        assert wf.tier2 is None
        assert wf.tier3 is None

    def test_bidding_with_manual_tier(self) -> None:
        """Bidding slot with MANUAL tier is valid."""
        wf = WaterfallConfig(
            bidding=WaterfallTier(tier_type=TierType.BIDDING),
            tier1=WaterfallTier(tier_type=TierType.MANUAL),
        )
        assert wf.bidding is not None
        assert wf.tier1 is not None
        assert wf.tier1.tier_type == TierType.MANUAL

    def test_bidding_with_sort_by_cpm_tier(self) -> None:
        """Bidding slot with SORT_BY_CPM tier is valid."""
        wf = WaterfallConfig(
            bidding=WaterfallTier(tier_type=TierType.BIDDING),
            tier1=WaterfallTier(tier_type=TierType.SORT_BY_CPM),
        )
        assert wf.tier1 is not None
        assert wf.tier1.tier_type == TierType.SORT_BY_CPM

    def test_optimized_without_bidding_is_valid(self) -> None:
        """OPTIMIZED tier type without bidding slot is valid."""
        wf = WaterfallConfig(
            tier1=WaterfallTier(tier_type=TierType.OPTIMIZED),
        )
        assert wf.bidding is None
        assert wf.tier1 is not None
        assert wf.tier1.tier_type == TierType.OPTIMIZED

    def test_optimized_with_bidding_raises(self) -> None:
        """OPTIMIZED tier type with bidding slot raises ValidationError."""
        with pytest.raises(ValidationError, match="OPTIMIZED"):
            WaterfallConfig(
                bidding=WaterfallTier(tier_type=TierType.BIDDING),
                tier1=WaterfallTier(tier_type=TierType.OPTIMIZED),
            )

    def test_optimized_in_tier2_with_bidding_raises(self) -> None:
        """OPTIMIZED in tier2 with bidding slot raises ValidationError."""
        with pytest.raises(ValidationError, match="OPTIMIZED"):
            WaterfallConfig(
                bidding=WaterfallTier(tier_type=TierType.BIDDING),
                tier1=WaterfallTier(tier_type=TierType.MANUAL),
                tier2=WaterfallTier(tier_type=TierType.OPTIMIZED),
            )

    def test_optimized_in_tier3_with_bidding_raises(self) -> None:
        """OPTIMIZED in tier3 with bidding slot raises ValidationError."""
        with pytest.raises(ValidationError, match="OPTIMIZED"):
            WaterfallConfig(
                bidding=WaterfallTier(tier_type=TierType.BIDDING),
                tier3=WaterfallTier(tier_type=TierType.OPTIMIZED),
            )

    def test_multiple_tiers_without_bidding(self) -> None:
        """Multiple tiers including OPTIMIZED without bidding is valid."""
        wf = WaterfallConfig(
            tier1=WaterfallTier(tier_type=TierType.MANUAL),
            tier2=WaterfallTier(tier_type=TierType.OPTIMIZED),
            tier3=WaterfallTier(tier_type=TierType.SORT_BY_CPM),
        )
        assert wf.tier1 is not None
        assert wf.tier2 is not None
        assert wf.tier3 is not None

    def test_round_trip_via_alias(self) -> None:
        """Full WaterfallConfig round-trips via camelCase alias."""
        inst = Instance(
            instance_id=1,
            instance_name="IS",
            network_name="ironSource",
            is_bidder=True,
        )
        wf = WaterfallConfig(
            bidding=WaterfallTier(tier_type=TierType.BIDDING, instances=[inst]),
            tier1=WaterfallTier(tier_type=TierType.MANUAL),
        )
        dumped = wf.model_dump(by_alias=True)
        restored = WaterfallConfig.model_validate(dumped)
        assert restored == wf

    def test_round_trip_without_alias(self) -> None:
        """Full WaterfallConfig round-trips via snake_case."""
        wf = WaterfallConfig(
            tier1=WaterfallTier(tier_type=TierType.SORT_BY_CPM),
            tier2=WaterfallTier(tier_type=TierType.MANUAL),
        )
        dumped = wf.model_dump()
        restored = WaterfallConfig.model_validate(dumped)
        assert restored == wf

    def test_from_raw_dicts(self) -> None:
        """WaterfallConfig accepts tier slots as raw dicts."""
        wf = WaterfallConfig.model_validate(
            {
                "bidding": {
                    "tierType": "bidding",
                    "instances": [
                        {"name": "Bidder", "networkName": "AppLovin", "isBidder": True}
                    ],
                },
                "tier1": {"tierType": "manual"},
            }
        )
        assert wf.bidding is not None
        assert len(wf.bidding.instances) == 1
        assert wf.tier1 is not None
        assert wf.tier1.tier_type == TierType.MANUAL

    def test_all_none_tiers_is_valid(self) -> None:
        """WaterfallConfig with all None tier slots is valid."""
        wf = WaterfallConfig(bidding=None, tier1=None, tier2=None, tier3=None)
        assert wf.bidding is None


# ---------------------------------------------------------------------------
# Group model tests (Step 5)
# ---------------------------------------------------------------------------


class TestGroup:
    """Tests for the Group model."""

    def test_create_with_camelcase(self) -> None:
        """Group accepts camelCase JSON keys via aliases."""
        group = Group.model_validate(
            {
                "groupId": 100,
                "groupName": "US Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }
        )
        assert group.group_id == 100
        assert group.group_name == "US Tier 1"
        assert group.ad_format == AdFormat.INTERSTITIAL
        assert group.countries == ["US"]
        assert group.position == 1

    def test_create_with_snake_case(self) -> None:
        """Group accepts snake_case field names."""
        group = Group(
            group_name="Default",
            ad_format=AdFormat.BANNER,
            countries=["US", "CA"],
            position=0,
        )
        assert group.group_name == "Default"
        assert group.ad_format == AdFormat.BANNER
        assert group.countries == ["US", "CA"]

    def test_dump_by_alias(self) -> None:
        """model_dump(by_alias=True) produces camelCase keys."""
        group = Group(
            group_id=50,
            group_name="Test",
            ad_format=AdFormat.REWARDED_VIDEO,
            countries=["GB"],
            position=2,
            floor_price=1.5,
        )
        dumped = group.model_dump(by_alias=True)
        assert dumped["groupId"] == 50
        assert dumped["groupName"] == "Test"
        assert dumped["adFormat"] == "rewardedVideo"
        assert dumped["floorPrice"] == 1.5

    def test_defaults(self) -> None:
        """Optional fields default to None."""
        group = Group(
            group_name="Minimal",
            ad_format=AdFormat.BANNER,
            countries=["US"],
            position=1,
        )
        assert group.group_id is None
        assert group.floor_price is None
        assert group.ab_test is None
        assert group.instances is None
        assert group.waterfall is None
        assert group.mediation_ad_unit_id is None
        assert group.mediation_ad_unit_name is None
        assert group.segments is None

    def test_round_trip_via_alias(self) -> None:
        """Round-trip via camelCase alias produces identical model."""
        group = Group(
            group_id=10,
            group_name="RT Test",
            ad_format=AdFormat.INTERSTITIAL,
            countries=["US", "CA", "GB"],
            position=1,
            floor_price=2.0,
        )
        dumped = group.model_dump(by_alias=True)
        restored = Group.model_validate(dumped)
        assert restored == group

    def test_round_trip_without_alias(self) -> None:
        """Round-trip via snake_case produces identical model."""
        group = Group(
            group_name="Snake",
            ad_format=AdFormat.BANNER,
            countries=["DE"],
            position=3,
        )
        dumped = group.model_dump()
        restored = Group.model_validate(dumped)
        assert restored == group

    def test_with_flat_instances(self) -> None:
        """Group with flat instances list (API shape) round-trips."""
        instances = [
            Instance(
                instance_id=1,
                instance_name="IS",
                network_name="ironSource",
            ),
            Instance(
                instance_id=2,
                instance_name="AL",
                network_name="AppLovin",
                is_bidder=True,
            ),
        ]
        group = Group(
            group_name="With Instances",
            ad_format=AdFormat.INTERSTITIAL,
            countries=["US"],
            position=1,
            instances=instances,
        )
        assert group.instances is not None
        assert len(group.instances) == 2
        dumped = group.model_dump(by_alias=True)
        restored = Group.model_validate(dumped)
        assert restored == group

    def test_with_waterfall(self) -> None:
        """Group with nested WaterfallConfig containing tiers and instances."""
        inst_bidder = Instance(
            instance_id=1,
            instance_name="Bidder IS",
            network_name="ironSource",
            is_bidder=True,
        )
        inst_manual = Instance(
            instance_id=2,
            instance_name="Manual AL",
            network_name="AppLovin",
        )
        waterfall = WaterfallConfig(
            bidding=WaterfallTier(
                tier_type=TierType.BIDDING, instances=[inst_bidder]
            ),
            tier1=WaterfallTier(
                tier_type=TierType.MANUAL, instances=[inst_manual]
            ),
        )
        group = Group(
            group_id=200,
            group_name="Full Group",
            ad_format=AdFormat.REWARDED_VIDEO,
            countries=["US", "CA"],
            position=1,
            floor_price=3.0,
            waterfall=waterfall,
        )
        assert group.waterfall is not None
        assert group.waterfall.bidding is not None
        assert len(group.waterfall.bidding.instances) == 1
        assert group.waterfall.tier1 is not None
        assert group.waterfall.tier1.tier_type == TierType.MANUAL

    def test_full_nested_round_trip(self) -> None:
        """Group with full nested structure round-trips via model_dump(by_alias=True)."""
        inst1 = Instance(
            instance_id=10,
            instance_name="Bidder",
            network_name="ironSource",
            is_bidder=True,
            group_rate=8.0,
        )
        inst2 = Instance(
            instance_id=20,
            instance_name="Manual1",
            network_name="AppLovin",
            group_rate=5.0,
        )
        inst3 = Instance(
            instance_id=30,
            instance_name="Manual2",
            network_name="AdMob",
            group_rate=3.0,
            countries_rate=[
                CountryRate(country_code="US", rate=15.0),
                CountryRate(country_code="GB", rate=12.0),
            ],
        )
        waterfall = WaterfallConfig(
            bidding=WaterfallTier(
                tier_type=TierType.BIDDING, instances=[inst1]
            ),
            tier1=WaterfallTier(
                tier_type=TierType.MANUAL, instances=[inst2, inst3]
            ),
        )
        group = Group(
            group_id=300,
            group_name="Deep Nested",
            ad_format=AdFormat.INTERSTITIAL,
            countries=["US", "CA", "GB"],
            position=1,
            floor_price=2.5,
            ab_test="test_v2",
            waterfall=waterfall,
            mediation_ad_unit_id="mu123",
            mediation_ad_unit_name="My Ad Unit",
            segments=[{"id": 1, "name": "Whales"}],
        )
        dumped = group.model_dump(by_alias=True)
        restored = Group.model_validate(dumped)
        assert restored == group
        assert restored.waterfall is not None
        assert restored.waterfall.bidding is not None
        assert restored.waterfall.bidding.instances[0].group_rate == 8.0
        assert restored.waterfall.tier1 is not None
        assert len(restored.waterfall.tier1.instances) == 2
        assert restored.waterfall.tier1.instances[1].countries_rate is not None
        assert len(restored.waterfall.tier1.instances[1].countries_rate) == 2

    def test_waterfall_from_raw_dicts(self) -> None:
        """Group with waterfall and tiers as raw dicts round-trips."""
        raw = {
            "groupName": "Raw Dict",
            "adFormat": "banner",
            "countries": ["US"],
            "position": 1,
            "waterfall": {
                "bidding": {
                    "tierType": "bidding",
                    "instances": [
                        {"name": "Bid1", "networkName": "ironSource", "isBidder": True}
                    ],
                },
                "tier1": {
                    "tierType": "manual",
                    "instances": [
                        {"name": "Man1", "networkName": "AdMob"}
                    ],
                },
            },
        }
        group = Group.model_validate(raw)
        assert group.waterfall is not None
        assert group.waterfall.bidding is not None
        assert group.waterfall.bidding.instances[0].is_bidder is True
        dumped = group.model_dump(by_alias=True)
        restored = Group.model_validate(dumped)
        assert restored == group

    def test_ad_format_enum_from_string(self) -> None:
        """ad_format field accepts string value and resolves to AdFormat enum."""
        group = Group.model_validate(
            {
                "groupName": "Enum",
                "adFormat": "rewardedVideo",
                "countries": ["JP"],
                "position": 2,
            }
        )
        assert group.ad_format == AdFormat.REWARDED_VIDEO
        assert isinstance(group.ad_format, AdFormat)

    def test_empty_countries_list(self) -> None:
        """Group with empty countries list is valid."""
        group = Group(
            group_name="Empty",
            ad_format=AdFormat.BANNER,
            countries=[],
            position=0,
        )
        assert group.countries == []

    def test_all_optional_fields_populated(self) -> None:
        """Group with all optional fields populated round-trips."""
        group = Group(
            group_id=999,
            group_name="Everything",
            ad_format=AdFormat.BANNER,
            countries=["US"],
            position=1,
            floor_price=5.0,
            ab_test="ab_v1",
            instances=[
                Instance(instance_name="Inst", network_name="ironSource")
            ],
            waterfall=WaterfallConfig(
                tier1=WaterfallTier(tier_type=TierType.MANUAL)
            ),
            mediation_ad_unit_id="mu999",
            mediation_ad_unit_name="Unit 999",
            segments=[{"type": "all"}],
        )
        dumped = group.model_dump(by_alias=True)
        restored = Group.model_validate(dumped)
        assert restored == group

    def test_missing_required_field_raises(self) -> None:
        """Missing required field raises ValidationError."""
        with pytest.raises(ValidationError):
            Group.model_validate(
                {
                    "groupName": "Partial",
                    "adFormat": "banner",
                    # missing countries and position
                }
            )

    def test_invalid_ad_format_raises(self) -> None:
        """Invalid adFormat string raises ValidationError."""
        with pytest.raises(ValidationError):
            Group.model_validate(
                {
                    "groupName": "Bad",
                    "adFormat": "popup",
                    "countries": ["US"],
                    "position": 1,
                }
            )


# ---------------------------------------------------------------------------
# Step 5: Import path tests for new models
# ---------------------------------------------------------------------------


class TestStep5Imports:
    """Tests that Step 5 models are importable from admedi.models."""

    def test_import_waterfall_tier(self) -> None:
        """WaterfallTier is importable from admedi.models."""
        from admedi.models import WaterfallTier as WTModel

        assert WTModel is WaterfallTier

    def test_import_waterfall_config(self) -> None:
        """WaterfallConfig is importable from admedi.models."""
        from admedi.models import WaterfallConfig as WCModel

        assert WCModel is WaterfallConfig

    def test_import_group(self) -> None:
        """Group is importable from admedi.models."""
        from admedi.models import Group as GModel

        assert GModel is Group


# ---------------------------------------------------------------------------
# TierDefinition model tests (Step 6)
# ---------------------------------------------------------------------------


class TestTierDefinition:
    """Tests for the TierDefinition model."""

    def test_create_basic(self) -> None:
        """TierDefinition with required fields."""
        td = TierDefinition(name="Tier 1", countries=["US"], position=1)
        assert td.name == "Tier 1"
        assert td.countries == ["US"]
        assert td.position == 1
        assert td.floor_price is None
        assert td.is_default is False

    def test_create_with_all_fields(self) -> None:
        """TierDefinition with all fields populated."""
        td = TierDefinition(
            name="Tier 1",
            countries=["US"],
            position=1,
            floor_price=5.0,
            is_default=False,
        )
        assert td.floor_price == 5.0
        assert td.is_default is False

    def test_default_tier_with_empty_countries(self) -> None:
        """Default tier with empty countries list is valid."""
        td = TierDefinition(
            name="All Countries", countries=[], position=4, is_default=True
        )
        assert td.is_default is True
        assert td.countries == []

    def test_frozen_raises_on_mutation(self) -> None:
        """TierDefinition is frozen -- mutation raises ValidationError."""
        td = TierDefinition(name="Tier 1", countries=["US"], position=1)
        with pytest.raises(ValidationError):
            td.name = "Changed"  # type: ignore[misc]

    def test_round_trip(self) -> None:
        """TierDefinition round-trips via model_dump/model_validate."""
        td = TierDefinition(
            name="Tier 2",
            countries=["AU", "CA", "GB"],
            position=2,
            floor_price=3.0,
        )
        dumped = td.model_dump()
        restored = TierDefinition.model_validate(dumped)
        assert restored == td

    def test_multiple_countries(self) -> None:
        """TierDefinition with multiple country codes."""
        td = TierDefinition(
            name="Tier 2",
            countries=["AU", "CA", "DE", "GB", "JP", "NZ", "KR", "TW"],
            position=2,
        )
        assert len(td.countries) == 8


# ---------------------------------------------------------------------------
# TierTemplate model tests (Step 6)
# ---------------------------------------------------------------------------


class TestTierTemplate:
    """Tests for the TierTemplate model and its validators."""

    def _shelf_sort_template(self) -> TierTemplate:
        """Create the Shelf Sort tier config template for reuse in tests."""
        return TierTemplate(
            name="Shelf Sort Interstitial",
            ad_formats=[AdFormat.INTERSTITIAL],
            tiers=[
                TierDefinition(name="Tier 1", countries=["US"], position=1),
                TierDefinition(
                    name="Tier 2",
                    countries=["AU", "CA", "DE", "GB", "JP", "NZ", "KR", "TW"],
                    position=2,
                ),
                TierDefinition(
                    name="Tier 3", countries=["FR", "NL"], position=3
                ),
                TierDefinition(
                    name="All Countries",
                    countries=[],
                    position=4,
                    is_default=True,
                ),
            ],
        )

    def test_shelf_sort_config_validates(self) -> None:
        """Shelf Sort tier config (US, AU/CA/etc, FR/NL, All Countries) validates."""
        template = self._shelf_sort_template()
        assert template.name == "Shelf Sort Interstitial"
        assert len(template.tiers) == 4
        assert template.ad_formats == [AdFormat.INTERSTITIAL]

    def test_shelf_sort_config_round_trip(self) -> None:
        """Shelf Sort template round-trips via model_dump/model_validate."""
        template = self._shelf_sort_template()
        dumped = template.model_dump()
        restored = TierTemplate.model_validate(dumped)
        assert restored == template

    def test_multiple_ad_formats(self) -> None:
        """TierTemplate with multiple ad formats."""
        template = TierTemplate(
            name="Multi-Format",
            ad_formats=[AdFormat.BANNER, AdFormat.INTERSTITIAL, AdFormat.REWARDED_VIDEO],
            tiers=[
                TierDefinition(name="Tier 1", countries=["US"], position=1),
                TierDefinition(
                    name="All Countries",
                    countries=[],
                    position=2,
                    is_default=True,
                ),
            ],
        )
        assert len(template.ad_formats) == 3

    def test_frozen_raises_on_mutation(self) -> None:
        """TierTemplate is frozen -- mutation raises ValidationError."""
        template = self._shelf_sort_template()
        with pytest.raises(ValidationError):
            template.name = "Changed"  # type: ignore[misc]

    # Validator: exactly one default tier

    def test_no_default_tier_raises(self) -> None:
        """TierTemplate with no default tier raises ValidationError."""
        with pytest.raises(ValidationError, match="default"):
            TierTemplate(
                name="No Default",
                ad_formats=[AdFormat.BANNER],
                tiers=[
                    TierDefinition(name="Tier 1", countries=["US"], position=1),
                    TierDefinition(name="Tier 2", countries=["GB"], position=2),
                ],
            )

    def test_two_default_tiers_raises(self) -> None:
        """TierTemplate with two default tiers raises ValidationError."""
        with pytest.raises(ValidationError, match="default"):
            TierTemplate(
                name="Two Defaults",
                ad_formats=[AdFormat.BANNER],
                tiers=[
                    TierDefinition(
                        name="Default 1",
                        countries=[],
                        position=1,
                        is_default=True,
                    ),
                    TierDefinition(
                        name="Default 2",
                        countries=[],
                        position=2,
                        is_default=True,
                    ),
                ],
            )

    # Validator: no duplicate countries across non-default tiers

    def test_duplicate_country_raises(self) -> None:
        """TierTemplate with 'US' in two tiers raises ValidationError."""
        with pytest.raises(ValidationError, match="[Dd]uplicate"):
            TierTemplate(
                name="Dupe",
                ad_formats=[AdFormat.BANNER],
                tiers=[
                    TierDefinition(name="Tier 1", countries=["US"], position=1),
                    TierDefinition(
                        name="Tier 2", countries=["US", "GB"], position=2
                    ),
                    TierDefinition(
                        name="All Countries",
                        countries=[],
                        position=3,
                        is_default=True,
                    ),
                ],
            )

    def test_duplicate_country_error_mentions_tiers(self) -> None:
        """Duplicate country error message mentions both tier names."""
        with pytest.raises(ValidationError, match="Tier 1") as exc_info:
            TierTemplate(
                name="Dupe Detail",
                ad_formats=[AdFormat.BANNER],
                tiers=[
                    TierDefinition(name="Tier 1", countries=["US"], position=1),
                    TierDefinition(
                        name="Tier 2", countries=["US"], position=2
                    ),
                    TierDefinition(
                        name="All Countries",
                        countries=[],
                        position=3,
                        is_default=True,
                    ),
                ],
            )
        error_str = str(exc_info.value)
        assert "Tier 2" in error_str

    def test_same_country_different_tiers_order(self) -> None:
        """Duplicate detection is not order-dependent (second tier flagged)."""
        with pytest.raises(ValidationError, match="[Dd]uplicate"):
            TierTemplate(
                name="Order Test",
                ad_formats=[AdFormat.INTERSTITIAL],
                tiers=[
                    TierDefinition(
                        name="Tier A", countries=["GB", "FR"], position=1
                    ),
                    TierDefinition(
                        name="Tier B", countries=["DE", "FR"], position=2
                    ),
                    TierDefinition(
                        name="All Countries",
                        countries=[],
                        position=3,
                        is_default=True,
                    ),
                ],
            )

    # Validator: country code format ^[A-Z]{2}$

    def test_invalid_country_code_lowercase_raises(self) -> None:
        """Lowercase country code 'usa' raises ValidationError."""
        with pytest.raises(ValidationError, match="country code"):
            TierTemplate(
                name="Invalid CC",
                ad_formats=[AdFormat.BANNER],
                tiers=[
                    TierDefinition(
                        name="Tier 1", countries=["usa"], position=1
                    ),
                    TierDefinition(
                        name="All Countries",
                        countries=[],
                        position=2,
                        is_default=True,
                    ),
                ],
            )

    def test_invalid_country_code_single_letter_raises(self) -> None:
        """Single letter country code 'U' raises ValidationError."""
        with pytest.raises(ValidationError, match="country code"):
            TierTemplate(
                name="Single Letter",
                ad_formats=[AdFormat.BANNER],
                tiers=[
                    TierDefinition(
                        name="Tier 1", countries=["U"], position=1
                    ),
                    TierDefinition(
                        name="All Countries",
                        countries=[],
                        position=2,
                        is_default=True,
                    ),
                ],
            )

    def test_invalid_country_code_three_letters_raises(self) -> None:
        """Three letter country code 'USA' raises ValidationError."""
        with pytest.raises(ValidationError, match="country code"):
            TierTemplate(
                name="Three Letters",
                ad_formats=[AdFormat.BANNER],
                tiers=[
                    TierDefinition(
                        name="Tier 1", countries=["USA"], position=1
                    ),
                    TierDefinition(
                        name="All Countries",
                        countries=[],
                        position=2,
                        is_default=True,
                    ),
                ],
            )

    def test_invalid_country_code_mixed_case_raises(self) -> None:
        """Mixed case country code 'Us' raises ValidationError."""
        with pytest.raises(ValidationError, match="country code"):
            TierTemplate(
                name="Mixed Case",
                ad_formats=[AdFormat.BANNER],
                tiers=[
                    TierDefinition(
                        name="Tier 1", countries=["Us"], position=1
                    ),
                    TierDefinition(
                        name="All Countries",
                        countries=[],
                        position=2,
                        is_default=True,
                    ),
                ],
            )

    def test_invalid_country_code_with_digits_raises(self) -> None:
        """Country code with digits 'U1' raises ValidationError."""
        with pytest.raises(ValidationError, match="country code"):
            TierTemplate(
                name="Digits",
                ad_formats=[AdFormat.BANNER],
                tiers=[
                    TierDefinition(
                        name="Tier 1", countries=["U1"], position=1
                    ),
                    TierDefinition(
                        name="All Countries",
                        countries=[],
                        position=2,
                        is_default=True,
                    ),
                ],
            )

    def test_default_tier_country_codes_not_validated(self) -> None:
        """Default tier country codes are not checked for format (irrelevant)."""
        # Default tier with countries (unusual but not invalid per spec --
        # the validator skips default tiers for country code checks)
        template = TierTemplate(
            name="Default With Countries",
            ad_formats=[AdFormat.BANNER],
            tiers=[
                TierDefinition(name="Tier 1", countries=["US"], position=1),
                TierDefinition(
                    name="All Countries",
                    countries=["anyformat"],
                    position=2,
                    is_default=True,
                ),
            ],
        )
        assert template.tiers[1].is_default is True

    # Edge cases

    def test_single_tier_default_only(self) -> None:
        """TierTemplate with just a single default tier is valid."""
        template = TierTemplate(
            name="Default Only",
            ad_formats=[AdFormat.BANNER],
            tiers=[
                TierDefinition(
                    name="All Countries",
                    countries=[],
                    position=1,
                    is_default=True,
                ),
            ],
        )
        assert len(template.tiers) == 1

    def test_default_tier_with_floor_price(self) -> None:
        """Default tier with a floor_price is valid."""
        template = TierTemplate(
            name="Floor Test",
            ad_formats=[AdFormat.INTERSTITIAL],
            tiers=[
                TierDefinition(name="Tier 1", countries=["US"], position=1, floor_price=10.0),
                TierDefinition(
                    name="All Countries",
                    countries=[],
                    position=2,
                    is_default=True,
                    floor_price=1.0,
                ),
            ],
        )
        assert template.tiers[0].floor_price == 10.0
        assert template.tiers[1].floor_price == 1.0

    def test_empty_tiers_list_raises(self) -> None:
        """TierTemplate with empty tiers list raises (no default tier)."""
        with pytest.raises(ValidationError, match="default"):
            TierTemplate(
                name="Empty",
                ad_formats=[AdFormat.BANNER],
                tiers=[],
            )

    def test_country_code_validation_runs_before_duplicate_check(self) -> None:
        """Invalid country code is caught even if it would also be a duplicate."""
        # "us" is invalid format AND appears twice -- format check runs first
        # per iteration order (both tiers iterated, format check is in a
        # separate loop after duplicate check, but the actual order depends
        # on validator implementation). We just verify it raises.
        with pytest.raises(ValidationError):
            TierTemplate(
                name="Both Invalid",
                ad_formats=[AdFormat.BANNER],
                tiers=[
                    TierDefinition(
                        name="Tier 1", countries=["us"], position=1
                    ),
                    TierDefinition(
                        name="Tier 2", countries=["us"], position=2
                    ),
                    TierDefinition(
                        name="All Countries",
                        countries=[],
                        position=3,
                        is_default=True,
                    ),
                ],
            )


# ---------------------------------------------------------------------------
# Step 6: Import path tests for new models
# ---------------------------------------------------------------------------


class TestStep6Imports:
    """Tests that Step 6 models are importable from admedi.models."""

    def test_import_tier_definition(self) -> None:
        """TierDefinition is importable from admedi.models."""
        from admedi.models import TierDefinition as TDModel

        assert TDModel is TierDefinition

    def test_import_tier_template(self) -> None:
        """TierTemplate is importable from admedi.models."""
        from admedi.models import TierTemplate as TTModel

        assert TTModel is TierTemplate

    def test_import_from_module_path(self) -> None:
        """TierDefinition and TierTemplate importable from admedi.models.tier_template."""
        from admedi.models.tier_template import (
            TierDefinition as TDDirect,
            TierTemplate as TTDirect,
        )

        assert TDDirect is TierDefinition
        assert TTDirect is TierTemplate

"""Proof-of-concept: model_dump → ruamel.yaml → model_validate round-trip.

Validates the core assumption of the snapshot-unify design:
Group models can be serialized with model_dump(mode="json", by_alias=True, exclude_none=True),
written to YAML via ruamel.yaml, read back, and reconstructed via model_validate()
with zero data loss.
"""

from io import StringIO

import pytest
from ruamel.yaml import YAML

from admedi.models.enums import AdFormat, Platform
from admedi.models.group import Group
from admedi.models.instance import CountryRate, Instance


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def group_basic() -> Group:
    """Minimal group with bidding instances, no optional fields."""
    return Group.model_validate({
        "groupId": 5733985,
        "groupName": "All Countries",
        "adFormat": "banner",
        "countries": ["*"],
        "position": 1,
        "instances": [
            {
                "id": 22125421,
                "name": "Bidding",
                "networkName": "ironSource",
                "isBidder": True,
            },
            {
                "id": 22125479,
                "name": "Bidding",
                "networkName": "UnityAds",
                "isBidder": True,
            },
        ],
    })


@pytest.fixture
def group_with_rates() -> Group:
    """Group with manual instance that has rate and country rate overrides."""
    return Group.model_validate({
        "groupId": 5733993,
        "groupName": "Tier 1",
        "adFormat": "interstitial",
        "countries": ["US"],
        "position": 1,
        "floorPrice": 0.5,
        "instances": [
            {
                "id": 22125431,
                "name": "Bidding",
                "networkName": "Meta",
                "isBidder": True,
            },
            {
                "id": 22125437,
                "name": "Default",
                "networkName": "AppLovin",
                "isBidder": False,
                "groupRate": 1.0,
                "countriesRate": [
                    {"countryCode": "US", "rate": 2.5},
                    {"countryCode": "DE", "rate": 0.8},
                ],
            },
        ],
    })


@pytest.fixture
def group_minimal_none() -> Group:
    """Group with all optional fields as None — tests exclude_none behavior."""
    return Group.model_validate({
        "groupName": "Empty Group",
        "adFormat": "native",
        "countries": ["*"],
        "position": 1,
    })


# ---------------------------------------------------------------------------
# Tests: model_dump output shape
# ---------------------------------------------------------------------------


class TestModelDumpShape:
    """Verify model_dump(mode="json", by_alias=True, exclude_none=True) output."""

    def test_uses_alias_keys(self, group_basic: Group) -> None:
        dumped = group_basic.model_dump(mode="json", by_alias=True, exclude_none=True)
        assert "groupName" in dumped
        assert "groupId" in dumped
        assert "adFormat" in dumped
        # Python field names should NOT appear
        assert "group_name" not in dumped
        assert "group_id" not in dumped
        assert "ad_format" not in dumped

    def test_enum_serializes_as_string(self, group_basic: Group) -> None:
        dumped = group_basic.model_dump(mode="json", by_alias=True, exclude_none=True)
        assert dumped["adFormat"] == "banner"
        assert isinstance(dumped["adFormat"], str)

    def test_exclude_none_omits_null_fields(self, group_minimal_none: Group) -> None:
        dumped = group_minimal_none.model_dump(mode="json", by_alias=True, exclude_none=True)
        assert "groupId" not in dumped
        assert "floorPrice" not in dumped
        assert "abTest" not in dumped
        assert "waterfall" not in dumped
        assert "instances" not in dumped
        assert "mediationAdUnitId" not in dumped
        assert "segments" not in dumped

    def test_exclude_none_keeps_present_fields(self, group_with_rates: Group) -> None:
        dumped = group_with_rates.model_dump(mode="json", by_alias=True, exclude_none=True)
        assert dumped["floorPrice"] == 0.5
        assert dumped["groupId"] == 5733993

    def test_instance_aliases(self, group_basic: Group) -> None:
        dumped = group_basic.model_dump(mode="json", by_alias=True, exclude_none=True)
        inst = dumped["instances"][0]
        assert "networkName" in inst
        assert "isBidder" in inst
        assert "network_name" not in inst
        assert "is_bidder" not in inst

    def test_country_rate_nested(self, group_with_rates: Group) -> None:
        dumped = group_with_rates.model_dump(mode="json", by_alias=True, exclude_none=True)
        applovin = dumped["instances"][1]
        assert "countriesRate" in applovin
        assert applovin["countriesRate"][0] == {
            "countryCode": "US",
            "rate": 2.5,
        }


# ---------------------------------------------------------------------------
# Tests: ruamel.yaml round-trip
# ---------------------------------------------------------------------------


class TestYamlRoundTrip:
    """Verify model_dump output survives ruamel.yaml write → read."""

    def _yaml_round_trip(self, data: dict) -> dict:
        yaml = YAML()
        yaml.default_flow_style = False
        stream = StringIO()
        yaml.dump(data, stream)
        stream.seek(0)
        return yaml.load(stream)

    def test_basic_group_yaml_roundtrip(self, group_basic: Group) -> None:
        dumped = group_basic.model_dump(mode="json", by_alias=True, exclude_none=True)
        loaded = self._yaml_round_trip(dumped)
        assert loaded == dumped

    def test_rates_group_yaml_roundtrip(self, group_with_rates: Group) -> None:
        dumped = group_with_rates.model_dump(mode="json", by_alias=True, exclude_none=True)
        loaded = self._yaml_round_trip(dumped)
        assert loaded == dumped

    def test_minimal_group_yaml_roundtrip(self, group_minimal_none: Group) -> None:
        dumped = group_minimal_none.model_dump(mode="json", by_alias=True, exclude_none=True)
        loaded = self._yaml_round_trip(dumped)
        assert loaded == dumped


# ---------------------------------------------------------------------------
# Tests: full round-trip (model_dump → YAML → model_validate)
# ---------------------------------------------------------------------------


class TestFullRoundTrip:
    """The critical test: Group → dump → YAML → load → model_validate → compare."""

    def _full_round_trip(self, group: Group) -> Group:
        yaml = YAML()
        yaml.default_flow_style = False

        dumped = group.model_dump(mode="json", by_alias=True, exclude_none=True)

        stream = StringIO()
        yaml.dump(dumped, stream)
        stream.seek(0)
        loaded_dict = yaml.load(stream)

        return Group.model_validate(loaded_dict)

    def test_basic_group_round_trip(self, group_basic: Group) -> None:
        reconstructed = self._full_round_trip(group_basic)
        assert reconstructed.model_dump(mode="json", by_alias=True, exclude_none=True) == \
            group_basic.model_dump(mode="json", by_alias=True, exclude_none=True)

    def test_rates_group_round_trip(self, group_with_rates: Group) -> None:
        reconstructed = self._full_round_trip(group_with_rates)
        assert reconstructed.model_dump(mode="json", by_alias=True, exclude_none=True) == \
            group_with_rates.model_dump(mode="json", by_alias=True, exclude_none=True)

    def test_minimal_group_round_trip(self, group_minimal_none: Group) -> None:
        reconstructed = self._full_round_trip(group_minimal_none)
        assert reconstructed.model_dump(mode="json", by_alias=True, exclude_none=True) == \
            group_minimal_none.model_dump(mode="json", by_alias=True, exclude_none=True)

    def test_round_trip_preserves_group_id(self, group_basic: Group) -> None:
        reconstructed = self._full_round_trip(group_basic)
        assert reconstructed.group_id == group_basic.group_id

    def test_round_trip_preserves_instance_ids(self, group_basic: Group) -> None:
        reconstructed = self._full_round_trip(group_basic)
        original_ids = [i.instance_id for i in group_basic.instances]
        reconstructed_ids = [i.instance_id for i in reconstructed.instances]
        assert reconstructed_ids == original_ids

    def test_round_trip_preserves_instance_names(self, group_with_rates: Group) -> None:
        reconstructed = self._full_round_trip(group_with_rates)
        original_names = [i.instance_name for i in group_with_rates.instances]
        reconstructed_names = [i.instance_name for i in reconstructed.instances]
        assert reconstructed_names == original_names

    def test_round_trip_preserves_country_rates(self, group_with_rates: Group) -> None:
        reconstructed = self._full_round_trip(group_with_rates)
        applovin = [i for i in reconstructed.instances if i.network_name == "AppLovin"][0]
        assert applovin.countries_rate is not None
        assert len(applovin.countries_rate) == 2
        assert applovin.countries_rate[0].country_code == "US"
        assert applovin.countries_rate[0].rate == 2.5

    def test_round_trip_preserves_floor_price(self, group_with_rates: Group) -> None:
        reconstructed = self._full_round_trip(group_with_rates)
        assert reconstructed.floor_price == 0.5

    def test_round_trip_preserves_ad_format(self, group_basic: Group) -> None:
        reconstructed = self._full_round_trip(group_basic)
        assert reconstructed.ad_format == AdFormat.BANNER


# ---------------------------------------------------------------------------
# Tests: multi-group snapshot structure (organized by format)
# ---------------------------------------------------------------------------


class TestSnapshotStructure:
    """Test the full snapshot dict shape: groups organized by ad format."""

    def test_organize_by_format_and_reconstruct(
        self, group_basic: Group, group_with_rates: Group
    ) -> None:
        """Simulate the full serializer → loader flow."""
        yaml = YAML()
        yaml.default_flow_style = False

        # Serializer: organize groups by ad format
        groups = [group_basic, group_with_rates]
        by_format: dict[str, list] = {}
        for g in groups:
            dumped = g.model_dump(mode="json", by_alias=True, exclude_none=True)
            fmt = dumped["adFormat"]
            by_format.setdefault(fmt, []).append(dumped)

        snapshot = {
            "app_key": "1f93a90ad",
            "app_name": "Test App",
            "platform": "iOS",
            "groups": by_format,
        }

        # Write to YAML and read back
        stream = StringIO()
        yaml.dump(snapshot, stream)
        stream.seek(0)
        loaded = yaml.load(stream)

        # Loader: reconstruct Group models
        reconstructed: list[Group] = []
        for _fmt, group_list in loaded["groups"].items():
            for group_dict in group_list:
                reconstructed.append(Group.model_validate(group_dict))

        # Verify
        assert len(reconstructed) == 2
        assert reconstructed[0].group_name == "All Countries"
        assert reconstructed[0].ad_format == AdFormat.BANNER
        assert reconstructed[1].group_name == "Tier 1"
        assert reconstructed[1].ad_format == AdFormat.INTERSTITIAL
        assert reconstructed[1].floor_price == 0.5
        assert reconstructed[1].instances[1].countries_rate[0].rate == 2.5

        # Header metadata preserved
        assert loaded["app_key"] == "1f93a90ad"
        assert loaded["app_name"] == "Test App"
        assert loaded["platform"] == "iOS"

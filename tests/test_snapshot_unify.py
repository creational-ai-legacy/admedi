"""Tests for unified snapshot functions (save_raw_snapshot, load_snapshot, SnapshotData).

Covers:
- Step 1: save_raw_snapshot
  - Directory creation
  - File path and output location
  - YAML header comments (captured by, app info, timestamp)
  - Header fields in YAML body (app_key, app_name, platform)
  - Platform omission when None
  - Groups organized by ad format key
  - Pydantic alias serialization (camelCase for aliased fields)
  - Instance alias serialization (id, name, networkName, isBidder, groupRate)
  - CountryRate nested model serialization (countryCode, rate)
  - exclude_none behavior (no null fields in output)
  - Empty group list handling
  - Return value matches written file path

- Step 2: SnapshotData model and load_snapshot
  - Round-trip: save -> load -> compare model_dump dicts
  - Round-trip preserves group IDs, instance IDs, instance names
  - Round-trip preserves countriesRate nested model
  - Round-trip preserves floorPrice when present
  - Round-trip preserves adFormat enum values
  - SnapshotData metadata matches after round-trip
  - Error handling: FileNotFoundError, ValueError
  - SnapshotData is a proper Pydantic BaseModel
  - Round-trip with empty groups
  - Round-trip with multiple ad formats

- Step 3: Unified show command and display updates
  - CLI show command produces both snapshots/ and settings/ files
  - display_show() with both paths shows both in footer
  - display_show() with only snapshot_path shows only snapshot path
  - display_show() with only settings_path shows only settings path
  - display_show() with neither path shows no footer paths
  - Empty-groups path also shows both paths when provided
  - --output flag affects only settings filename, not snapshot filename

- Step 4: Legacy code removal verification
  - generate_snapshot is no longer importable from admedi.engine or admedi.engine.snapshot
  - display_snapshot_info is no longer importable from admedi.cli.display
  - ConfigEngine no longer has a snapshot attribute
  - snapshot subcommand is not listed in CLI help output
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console
from ruamel.yaml import YAML
from typer.testing import CliRunner

from admedi.cli.display import display_show
from admedi.cli.main import app as cli_app
from admedi.engine.snapshot import (
    SnapshotData,
    _extract_tiers,
    load_snapshot,
    save_modular_snapshot,
    save_raw_snapshot,
)
from admedi.models.app import App
from admedi.models.enums import AdFormat, Mediator, Platform
from admedi.models.group import Group


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def group_banner() -> Group:
    """Banner group with bidding instances, no optional fields."""
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
def group_interstitial_with_rates() -> Group:
    """Interstitial group with floor price, manual instance, and country rates."""
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
    """Group with all optional fields as None -- tests exclude_none behavior."""
    return Group.model_validate({
        "groupName": "Empty Group",
        "adFormat": "native",
        "countries": ["*"],
        "position": 1,
    })


# ---------------------------------------------------------------------------
# Tests: Directory creation and file path
# ---------------------------------------------------------------------------


class TestSaveRawSnapshotDirectoryAndPath:
    """Tests for directory creation and output file path."""

    def test_creates_snapshots_directory(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """save_raw_snapshot creates the snapshots directory if it does not exist."""
        snapshots_dir = tmp_path / "snapshots"
        assert not snapshots_dir.exists()

        save_raw_snapshot(
            groups=[group_banner],
            app_key="abc123",
            app_name="Test App",
            alias="test-app",
            snapshots_dir=str(snapshots_dir),
        )

        assert snapshots_dir.exists()
        assert snapshots_dir.is_dir()

    def test_creates_nested_directory(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """save_raw_snapshot creates nested directories via parents=True."""
        snapshots_dir = tmp_path / "deep" / "nested" / "snapshots"

        save_raw_snapshot(
            groups=[group_banner],
            app_key="abc123",
            app_name="Test App",
            alias="test-app",
            snapshots_dir=str(snapshots_dir),
        )

        assert snapshots_dir.exists()

    def test_output_file_path(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Output file is written to {snapshots_dir}/{alias}.yaml."""
        snapshots_dir = tmp_path / "snapshots"

        save_raw_snapshot(
            groups=[group_banner],
            app_key="abc123",
            app_name="Test App",
            alias="ss-ios",
            snapshots_dir=str(snapshots_dir),
        )

        expected_file = snapshots_dir / "ss-ios.yaml"
        assert expected_file.exists()
        assert expected_file.is_file()

    def test_return_value_matches_file_path(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Return value matches the written file path."""
        snapshots_dir = tmp_path / "snapshots"

        result = save_raw_snapshot(
            groups=[group_banner],
            app_key="abc123",
            app_name="Test App",
            alias="ss-ios",
            snapshots_dir=str(snapshots_dir),
        )

        expected = str(snapshots_dir / "ss-ios.yaml")
        assert result == expected


# ---------------------------------------------------------------------------
# Tests: YAML header comments
# ---------------------------------------------------------------------------


class TestSaveRawSnapshotHeader:
    """Tests for YAML header comment lines."""

    def _read_raw(self, tmp_path: Path, **kwargs) -> str:
        """Helper to save and read the raw YAML text."""
        snapshots_dir = tmp_path / "snapshots"
        path = save_raw_snapshot(snapshots_dir=str(snapshots_dir), **kwargs)
        return Path(path).read_text(encoding="utf-8")

    def test_header_contains_captured_by(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """YAML header contains '# Captured by admedi show'."""
        content = self._read_raw(
            tmp_path,
            groups=[group_banner],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        assert "# Captured by admedi show" in content

    def test_header_contains_app_info(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """YAML header contains app name and key."""
        content = self._read_raw(
            tmp_path,
            groups=[group_banner],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        assert "# App: Test App (abc123)" in content

    def test_header_contains_timestamp(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """YAML header contains a Captured timestamp."""
        content = self._read_raw(
            tmp_path,
            groups=[group_banner],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        assert "# Captured:" in content


# ---------------------------------------------------------------------------
# Tests: YAML body header fields
# ---------------------------------------------------------------------------


class TestSaveRawSnapshotBodyFields:
    """Tests for app_key, app_name, platform in YAML body."""

    def _load_yaml(self, tmp_path: Path, **kwargs) -> dict:
        """Helper to save and parse the YAML body."""
        snapshots_dir = tmp_path / "snapshots"
        path = save_raw_snapshot(snapshots_dir=str(snapshots_dir), **kwargs)
        yaml = YAML()
        return yaml.load(Path(path))

    def test_contains_app_key(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Output YAML contains app_key header field."""
        data = self._load_yaml(
            tmp_path,
            groups=[group_banner],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        assert data["app_key"] == "abc123"

    def test_contains_app_name(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Output YAML contains app_name header field."""
        data = self._load_yaml(
            tmp_path,
            groups=[group_banner],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        assert data["app_name"] == "Test App"

    def test_contains_platform_when_provided(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Output YAML contains platform field when provided."""
        data = self._load_yaml(
            tmp_path,
            groups=[group_banner],
            app_key="abc123",
            app_name="Test App",
            alias="test",
            platform=Platform.IOS,
        )
        assert data["platform"] == "iOS"

    def test_omits_platform_when_none(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Platform is omitted from YAML when None."""
        data = self._load_yaml(
            tmp_path,
            groups=[group_banner],
            app_key="abc123",
            app_name="Test App",
            alias="test",
            platform=None,
        )
        assert "platform" not in data


# ---------------------------------------------------------------------------
# Tests: Groups organized by ad format
# ---------------------------------------------------------------------------


class TestSaveRawSnapshotGroupsByFormat:
    """Tests for groups organized by adFormat value as dict keys."""

    def _load_yaml(self, tmp_path: Path, **kwargs) -> dict:
        """Helper to save and parse the YAML body."""
        snapshots_dir = tmp_path / "snapshots"
        path = save_raw_snapshot(snapshots_dir=str(snapshots_dir), **kwargs)
        yaml = YAML()
        return yaml.load(Path(path))

    def test_single_format_groups(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Single ad format creates one key in groups dict."""
        data = self._load_yaml(
            tmp_path,
            groups=[group_banner],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        assert "banner" in data["groups"]
        assert len(data["groups"]) == 1

    def test_multiple_format_groups(
        self,
        tmp_path: Path,
        group_banner: Group,
        group_interstitial_with_rates: Group,
    ) -> None:
        """Multiple ad formats create separate keys in groups dict."""
        data = self._load_yaml(
            tmp_path,
            groups=[group_banner, group_interstitial_with_rates],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        assert "banner" in data["groups"]
        assert "interstitial" in data["groups"]
        assert len(data["groups"]) == 2

    def test_groups_are_lists(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Each format key maps to a list of group dicts."""
        data = self._load_yaml(
            tmp_path,
            groups=[group_banner],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        assert isinstance(data["groups"]["banner"], list)
        assert len(data["groups"]["banner"]) == 1

    def test_empty_groups(self, tmp_path: Path) -> None:
        """Empty group list produces valid YAML with empty groups dict."""
        data = self._load_yaml(
            tmp_path,
            groups=[],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        assert data["groups"] == {}


# ---------------------------------------------------------------------------
# Tests: Alias serialization (camelCase for aliased fields)
# ---------------------------------------------------------------------------


class TestSaveRawSnapshotAliasSerialization:
    """Tests for Pydantic alias (camelCase) serialization in output YAML."""

    def _load_yaml(self, tmp_path: Path, **kwargs) -> dict:
        """Helper to save and parse the YAML body."""
        snapshots_dir = tmp_path / "snapshots"
        path = save_raw_snapshot(snapshots_dir=str(snapshots_dir), **kwargs)
        yaml = YAML()
        return yaml.load(Path(path))

    def test_group_alias_fields(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Group fields with aliases use camelCase names."""
        data = self._load_yaml(
            tmp_path,
            groups=[group_banner],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        group = data["groups"]["banner"][0]

        # Aliased fields use camelCase
        assert "groupId" in group
        assert "groupName" in group
        assert "adFormat" in group

        # Python field names should NOT appear
        assert "group_id" not in group
        assert "group_name" not in group
        assert "ad_format" not in group

    def test_group_non_alias_fields(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Group fields without aliases retain Python names."""
        data = self._load_yaml(
            tmp_path,
            groups=[group_banner],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        group = data["groups"]["banner"][0]

        assert "countries" in group
        assert "position" in group

    def test_instance_alias_fields(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Instance fields with aliases use alias names."""
        data = self._load_yaml(
            tmp_path,
            groups=[group_banner],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        instance = data["groups"]["banner"][0]["instances"][0]

        # Aliased fields
        assert "id" in instance
        assert "name" in instance
        assert "networkName" in instance
        assert "isBidder" in instance

        # Python names should NOT appear
        assert "instance_id" not in instance
        assert "instance_name" not in instance
        assert "network_name" not in instance
        assert "is_bidder" not in instance

    def test_instance_group_rate_alias(
        self, tmp_path: Path, group_interstitial_with_rates: Group
    ) -> None:
        """Instance groupRate is serialized with alias name."""
        data = self._load_yaml(
            tmp_path,
            groups=[group_interstitial_with_rates],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        # AppLovin instance has groupRate
        applovin = data["groups"]["interstitial"][0]["instances"][1]
        assert "groupRate" in applovin
        assert applovin["groupRate"] == 1.0
        assert "group_rate" not in applovin

    def test_countries_rate_alias(
        self, tmp_path: Path, group_interstitial_with_rates: Group
    ) -> None:
        """countriesRate is serialized with alias name and nested countryCode."""
        data = self._load_yaml(
            tmp_path,
            groups=[group_interstitial_with_rates],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        applovin = data["groups"]["interstitial"][0]["instances"][1]
        assert "countriesRate" in applovin
        assert "countries_rate" not in applovin

        # Nested CountryRate model
        cr = applovin["countriesRate"][0]
        assert "countryCode" in cr
        assert cr["countryCode"] == "US"
        assert cr["rate"] == 2.5

    def test_countries_rate_all_entries(
        self, tmp_path: Path, group_interstitial_with_rates: Group
    ) -> None:
        """All countriesRate entries serialize correctly."""
        data = self._load_yaml(
            tmp_path,
            groups=[group_interstitial_with_rates],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        applovin = data["groups"]["interstitial"][0]["instances"][1]
        rates = applovin["countriesRate"]
        assert len(rates) == 2
        assert rates[0] == {"countryCode": "US", "rate": 2.5}
        assert rates[1] == {"countryCode": "DE", "rate": 0.8}

    def test_floor_price_alias(
        self, tmp_path: Path, group_interstitial_with_rates: Group
    ) -> None:
        """floorPrice is serialized with alias name."""
        data = self._load_yaml(
            tmp_path,
            groups=[group_interstitial_with_rates],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        group = data["groups"]["interstitial"][0]
        assert "floorPrice" in group
        assert group["floorPrice"] == 0.5
        assert "floor_price" not in group


# ---------------------------------------------------------------------------
# Tests: exclude_none behavior
# ---------------------------------------------------------------------------


class TestSaveRawSnapshotExcludeNone:
    """Tests for exclude_none=True omitting null fields."""

    def _load_yaml(self, tmp_path: Path, **kwargs) -> dict:
        """Helper to save and parse the YAML body."""
        snapshots_dir = tmp_path / "snapshots"
        path = save_raw_snapshot(snapshots_dir=str(snapshots_dir), **kwargs)
        yaml = YAML()
        return yaml.load(Path(path))

    def test_no_null_optional_fields(
        self, tmp_path: Path, group_minimal_none: Group
    ) -> None:
        """exclude_none=True omits null fields -- no floorPrice: null, etc."""
        data = self._load_yaml(
            tmp_path,
            groups=[group_minimal_none],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        group = data["groups"]["native"][0]

        # These are all None on group_minimal_none and should be omitted
        assert "groupId" not in group
        assert "floorPrice" not in group
        assert "abTest" not in group
        assert "waterfall" not in group
        assert "instances" not in group
        assert "mediationAdUnitId" not in group
        assert "mediationAdUnitName" not in group
        assert "segments" not in group

    def test_present_fields_kept(
        self, tmp_path: Path, group_interstitial_with_rates: Group
    ) -> None:
        """Fields with values are kept even when exclude_none is active."""
        data = self._load_yaml(
            tmp_path,
            groups=[group_interstitial_with_rates],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        group = data["groups"]["interstitial"][0]
        assert group["floorPrice"] == 0.5
        assert group["groupId"] == 5733993


# ===========================================================================
# Step 2: SnapshotData model and load_snapshot tests
# ===========================================================================


# ---------------------------------------------------------------------------
# Tests: SnapshotData model
# ---------------------------------------------------------------------------


class TestSnapshotDataModel:
    """Tests for SnapshotData Pydantic model."""

    def test_is_pydantic_basemodel(self) -> None:
        """SnapshotData is a proper Pydantic BaseModel."""
        from pydantic import BaseModel

        assert issubclass(SnapshotData, BaseModel)

    def test_construction_with_required_fields(self) -> None:
        """SnapshotData constructs with required fields."""
        data = SnapshotData(
            app_key="abc123",
            app_name="Test App",
            groups=[],
        )
        assert data.app_key == "abc123"
        assert data.app_name == "Test App"
        assert data.platform is None
        assert data.groups == []

    def test_construction_with_platform(self) -> None:
        """SnapshotData accepts platform parameter."""
        data = SnapshotData(
            app_key="abc123",
            app_name="Test App",
            platform=Platform.IOS,
            groups=[],
        )
        assert data.platform == Platform.IOS

    def test_construction_with_groups(
        self, group_banner: Group
    ) -> None:
        """SnapshotData accepts a list of Group models."""
        data = SnapshotData(
            app_key="abc123",
            app_name="Test App",
            groups=[group_banner],
        )
        assert len(data.groups) == 1
        assert data.groups[0].group_name == "All Countries"


# ---------------------------------------------------------------------------
# Tests: load_snapshot error handling
# ---------------------------------------------------------------------------


class TestLoadSnapshotErrors:
    """Tests for load_snapshot error cases."""

    def test_file_not_found(self, tmp_path: Path) -> None:
        """load_snapshot raises FileNotFoundError for non-existent path."""
        with pytest.raises(FileNotFoundError, match="Snapshot file not found"):
            load_snapshot(str(tmp_path / "nonexistent.yaml"))

    def test_invalid_yaml_missing_groups(self, tmp_path: Path) -> None:
        """load_snapshot raises ValueError for YAML missing groups key."""
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text(
            "app_key: abc123\napp_name: Test\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="missing 'groups' key"):
            load_snapshot(str(yaml_file))

    def test_invalid_yaml_empty_file(self, tmp_path: Path) -> None:
        """load_snapshot raises ValueError for empty YAML file."""
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="missing 'groups' key"):
            load_snapshot(str(yaml_file))


# ---------------------------------------------------------------------------
# Tests: Round-trip (save -> load -> compare)
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Tests for save_raw_snapshot -> load_snapshot round-trip fidelity."""

    def _round_trip(
        self,
        tmp_path: Path,
        groups: list[Group],
        app_key: str = "abc123",
        app_name: str = "Test App",
        alias: str = "test",
        platform: Platform | None = None,
    ) -> SnapshotData:
        """Helper to save and load, returning SnapshotData."""
        snapshots_dir = tmp_path / "snapshots"
        path = save_raw_snapshot(
            groups=groups,
            app_key=app_key,
            app_name=app_name,
            alias=alias,
            snapshots_dir=str(snapshots_dir),
            platform=platform,
        )
        return load_snapshot(path)

    def test_round_trip_single_group(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Round-trip preserves a single group via model_dump comparison."""
        original_dump = group_banner.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )

        loaded = self._round_trip(tmp_path, groups=[group_banner])
        loaded_dump = loaded.groups[0].model_dump(
            mode="json", by_alias=True, exclude_none=True
        )

        assert original_dump == loaded_dump

    def test_round_trip_preserves_group_ids(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Round-trip preserves group IDs."""
        loaded = self._round_trip(tmp_path, groups=[group_banner])
        assert loaded.groups[0].group_id == 5733985

    def test_round_trip_preserves_instance_ids(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Round-trip preserves instance IDs."""
        loaded = self._round_trip(tmp_path, groups=[group_banner])
        instances = loaded.groups[0].instances
        assert instances is not None
        assert instances[0].instance_id == 22125421
        assert instances[1].instance_id == 22125479

    def test_round_trip_preserves_instance_names(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Round-trip preserves instance names."""
        loaded = self._round_trip(tmp_path, groups=[group_banner])
        instances = loaded.groups[0].instances
        assert instances is not None
        assert instances[0].instance_name == "Bidding"
        assert instances[0].network_name == "ironSource"

    def test_round_trip_preserves_countries_rate(
        self, tmp_path: Path, group_interstitial_with_rates: Group
    ) -> None:
        """Round-trip preserves countriesRate nested model."""
        loaded = self._round_trip(
            tmp_path, groups=[group_interstitial_with_rates]
        )
        instances = loaded.groups[0].instances
        assert instances is not None
        applovin = instances[1]
        assert applovin.countries_rate is not None
        assert len(applovin.countries_rate) == 2
        assert applovin.countries_rate[0].country_code == "US"
        assert applovin.countries_rate[0].rate == 2.5
        assert applovin.countries_rate[1].country_code == "DE"
        assert applovin.countries_rate[1].rate == 0.8

    def test_round_trip_preserves_floor_price(
        self, tmp_path: Path, group_interstitial_with_rates: Group
    ) -> None:
        """Round-trip preserves floorPrice when present."""
        loaded = self._round_trip(
            tmp_path, groups=[group_interstitial_with_rates]
        )
        assert loaded.groups[0].floor_price == 0.5

    def test_round_trip_preserves_ad_format_enum(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Round-trip preserves adFormat enum values."""
        loaded = self._round_trip(tmp_path, groups=[group_banner])
        assert loaded.groups[0].ad_format == AdFormat.BANNER

    def test_round_trip_full_model_dump_comparison(
        self, tmp_path: Path, group_interstitial_with_rates: Group
    ) -> None:
        """Round-trip: full model_dump comparison for group with all field types."""
        original_dump = group_interstitial_with_rates.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )

        loaded = self._round_trip(
            tmp_path, groups=[group_interstitial_with_rates]
        )
        loaded_dump = loaded.groups[0].model_dump(
            mode="json", by_alias=True, exclude_none=True
        )

        assert original_dump == loaded_dump


# ---------------------------------------------------------------------------
# Tests: Round-trip metadata
# ---------------------------------------------------------------------------


class TestRoundTripMetadata:
    """Tests for SnapshotData metadata after round-trip."""

    def _round_trip(
        self,
        tmp_path: Path,
        groups: list[Group],
        app_key: str = "abc123",
        app_name: str = "Test App",
        alias: str = "test",
        platform: Platform | None = None,
    ) -> SnapshotData:
        """Helper to save and load, returning SnapshotData."""
        snapshots_dir = tmp_path / "snapshots"
        path = save_raw_snapshot(
            groups=groups,
            app_key=app_key,
            app_name=app_name,
            alias=alias,
            snapshots_dir=str(snapshots_dir),
            platform=platform,
        )
        return load_snapshot(path)

    def test_app_key_preserved(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """SnapshotData.app_key matches original value after round-trip."""
        loaded = self._round_trip(
            tmp_path, groups=[group_banner], app_key="key-xyz"
        )
        assert loaded.app_key == "key-xyz"

    def test_app_name_preserved(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """SnapshotData.app_name matches original value after round-trip."""
        loaded = self._round_trip(
            tmp_path, groups=[group_banner], app_name="Shelf Sort iOS"
        )
        assert loaded.app_name == "Shelf Sort iOS"

    def test_platform_preserved(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """SnapshotData.platform matches original value after round-trip."""
        loaded = self._round_trip(
            tmp_path, groups=[group_banner], platform=Platform.IOS
        )
        assert loaded.platform == Platform.IOS

    def test_platform_none_preserved(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """SnapshotData.platform is None when not provided."""
        loaded = self._round_trip(
            tmp_path, groups=[group_banner], platform=None
        )
        assert loaded.platform is None


# ---------------------------------------------------------------------------
# Tests: Round-trip edge cases
# ---------------------------------------------------------------------------


class TestRoundTripEdgeCases:
    """Tests for round-trip with empty groups and multiple ad formats."""

    def _round_trip(
        self,
        tmp_path: Path,
        groups: list[Group],
        app_key: str = "abc123",
        app_name: str = "Test App",
        alias: str = "test",
        platform: Platform | None = None,
    ) -> SnapshotData:
        """Helper to save and load, returning SnapshotData."""
        snapshots_dir = tmp_path / "snapshots"
        path = save_raw_snapshot(
            groups=groups,
            app_key=app_key,
            app_name=app_name,
            alias=alias,
            snapshots_dir=str(snapshots_dir),
            platform=platform,
        )
        return load_snapshot(path)

    def test_empty_groups_round_trip(self, tmp_path: Path) -> None:
        """Round-trip with empty groups list produces SnapshotData with empty groups."""
        loaded = self._round_trip(tmp_path, groups=[])
        assert loaded.groups == []
        assert loaded.app_key == "abc123"

    def test_multiple_ad_formats_round_trip(
        self,
        tmp_path: Path,
        group_banner: Group,
        group_interstitial_with_rates: Group,
    ) -> None:
        """Round-trip with multiple ad formats preserves all groups across formats."""
        original_groups = [group_banner, group_interstitial_with_rates]
        loaded = self._round_trip(tmp_path, groups=original_groups)

        assert len(loaded.groups) == 2

        # Compare each original group's model_dump with the loaded group
        original_dumps = [
            g.model_dump(mode="json", by_alias=True, exclude_none=True)
            for g in original_groups
        ]
        loaded_dumps = [
            g.model_dump(mode="json", by_alias=True, exclude_none=True)
            for g in loaded.groups
        ]

        # Sort both by groupName for stable comparison (load order may differ
        # because groups are organized by format key in YAML)
        original_sorted = sorted(original_dumps, key=lambda d: d["groupName"])
        loaded_sorted = sorted(loaded_dumps, key=lambda d: d["groupName"])

        assert original_sorted == loaded_sorted

    def test_minimal_group_round_trip(
        self, tmp_path: Path, group_minimal_none: Group
    ) -> None:
        """Round-trip with minimal group (all optional fields None) preserves data."""
        original_dump = group_minimal_none.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )

        loaded = self._round_trip(tmp_path, groups=[group_minimal_none])
        loaded_dump = loaded.groups[0].model_dump(
            mode="json", by_alias=True, exclude_none=True
        )

        assert original_dump == loaded_dump


# ===========================================================================
# Step 3: Unified show command and display updates
# ===========================================================================


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_console() -> tuple[Console, StringIO]:
    """Create a Console with a StringIO buffer for test output capture."""
    buf = StringIO()
    console = Console(file=buf, highlight=False, width=120)
    return console, buf


def _make_app(**overrides) -> App:
    """Create a test App model with defaults."""
    defaults = {
        "appKey": "abc123",
        "appName": "Test App",
        "platform": "iOS",
    }
    defaults.update(overrides)
    return App.model_validate(defaults)


def _mock_credential() -> MagicMock:
    """Create a mock credential that passes through adapter construction."""
    cred = MagicMock()
    cred.secret_key = "test-key"
    cred.refresh_token = "test-token"
    cred.mediator = Mediator.LEVELPLAY
    return cred


runner = CliRunner()


# ---------------------------------------------------------------------------
# Tests: display_show() footer with both paths
# ---------------------------------------------------------------------------


class TestDisplayShowBothPaths:
    """Tests for display_show() rendering both snapshot and settings paths."""

    def test_both_paths_shown_in_footer(
        self, group_banner: Group
    ) -> None:
        """display_show() with both paths shows both in footer output."""
        console, buf = _make_console()
        app = _make_app()

        display_show(
            app, [group_banner],
            snapshot_path="snapshots/ss-ios.yaml",
            settings_path="settings/ss-ios.yaml",
            console=console,
        )
        output = buf.getvalue()

        assert "Snapshot saved to:" in output
        assert "snapshots/ss-ios.yaml" in output
        assert "Settings saved to:" in output
        assert "settings/ss-ios.yaml" in output

    def test_only_snapshot_path(
        self, group_banner: Group
    ) -> None:
        """display_show() with only snapshot_path shows only snapshot path."""
        console, buf = _make_console()
        app = _make_app()

        display_show(
            app, [group_banner],
            snapshot_path="snapshots/ss-ios.yaml",
            console=console,
        )
        output = buf.getvalue()

        assert "Snapshot saved to:" in output
        assert "snapshots/ss-ios.yaml" in output
        assert "Settings saved to:" not in output

    def test_only_settings_path(
        self, group_banner: Group
    ) -> None:
        """display_show() with only settings_path shows only settings path."""
        console, buf = _make_console()
        app = _make_app()

        display_show(
            app, [group_banner],
            settings_path="settings/ss-ios.yaml",
            console=console,
        )
        output = buf.getvalue()

        assert "Settings saved to:" in output
        assert "settings/ss-ios.yaml" in output
        assert "Snapshot saved to:" not in output

    def test_neither_path(
        self, group_banner: Group
    ) -> None:
        """display_show() with neither path shows no footer paths."""
        console, buf = _make_console()
        app = _make_app()

        display_show(
            app, [group_banner],
            console=console,
        )
        output = buf.getvalue()

        assert "Snapshot saved to:" not in output
        assert "Settings saved to:" not in output


# ---------------------------------------------------------------------------
# Tests: display_show() empty-groups path with both paths
# ---------------------------------------------------------------------------


class TestDisplayShowEmptyGroupsPaths:
    """Tests for display_show() empty-groups path showing both paths."""

    def test_empty_groups_both_paths(self) -> None:
        """Empty-groups path shows both paths when provided."""
        console, buf = _make_console()
        app = _make_app()

        display_show(
            app, [],
            snapshot_path="snapshots/ss-ios.yaml",
            settings_path="settings/ss-ios.yaml",
            console=console,
        )
        output = buf.getvalue()

        assert "No mediation groups configured" in output
        assert "Snapshot saved to:" in output
        assert "snapshots/ss-ios.yaml" in output
        assert "Settings saved to:" in output
        assert "settings/ss-ios.yaml" in output

    def test_empty_groups_no_paths(self) -> None:
        """Empty-groups path shows no footer paths when neither provided."""
        console, buf = _make_console()
        app = _make_app()

        display_show(app, [], console=console)
        output = buf.getvalue()

        assert "No mediation groups configured" in output
        assert "Snapshot saved to:" not in output
        assert "Settings saved to:" not in output


# ---------------------------------------------------------------------------
# Tests: CLI show command produces both files
# ---------------------------------------------------------------------------


class TestShowCommandDualOutput:
    """Tests for CLI show command producing both snapshot and settings files."""

    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_show_produces_both_files(
        self,
        mock_load_cred: MagicMock,
        mock_adapter_cls: MagicMock,
        tmp_path: Path,
        group_banner: Group,
    ) -> None:
        """CLI show command produces both snapshots/{alias}.yaml and settings/{alias}.yaml."""
        mock_load_cred.return_value = _mock_credential()

        test_app = _make_app()
        mock_adapter = AsyncMock()
        mock_adapter.list_apps = AsyncMock(return_value=[test_app])
        mock_adapter.get_groups = AsyncMock(return_value=[group_banner])
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(
            return_value=mock_adapter
        )
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        # Patch at the source module (imports are local inside _show_async)
        with (
            patch(
                "admedi.engine.snapshot.save_raw_snapshot",
                wraps=save_raw_snapshot,
            ) as mock_raw,
            patch(
                "admedi.engine.snapshot.save_modular_snapshot",
            ) as mock_modular,
        ):
            # Redirect saves to tmp_path
            mock_raw.side_effect = lambda *args, **kwargs: save_raw_snapshot(
                *args, **{**kwargs, "snapshots_dir": str(tmp_path / "snapshots")}
            )
            mock_modular.return_value = (str(tmp_path / "settings" / "abc123.yaml"), [])

            result = runner.invoke(cli_app, ["show", "--app", "abc123"])

        assert result.exit_code == 0
        mock_raw.assert_called_once()
        mock_modular.assert_called_once()

    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_show_output_flag_affects_only_settings(
        self,
        mock_load_cred: MagicMock,
        mock_adapter_cls: MagicMock,
        tmp_path: Path,
        group_banner: Group,
    ) -> None:
        """--output flag affects only settings filename, not snapshot filename."""
        mock_load_cred.return_value = _mock_credential()

        test_app = _make_app()
        mock_adapter = AsyncMock()
        mock_adapter.list_apps = AsyncMock(return_value=[test_app])
        mock_adapter.get_groups = AsyncMock(return_value=[group_banner])
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(
            return_value=mock_adapter
        )
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        raw_calls: list[dict] = []
        modular_calls: list[dict] = []

        def capture_raw(*args, **kwargs):
            raw_calls.append(kwargs)
            return str(tmp_path / "snapshots" / "abc123.yaml")

        def capture_modular(*args, **kwargs):
            modular_calls.append(kwargs)
            return (str(tmp_path / "settings" / "custom-output.yaml"), [])

        with (
            patch("admedi.engine.snapshot.save_raw_snapshot", side_effect=capture_raw),
            patch("admedi.engine.snapshot.save_modular_snapshot", side_effect=capture_modular),
        ):
            result = runner.invoke(
                cli_app, ["show", "--app", "abc123", "--output", "custom-output.yaml"]
            )

        assert result.exit_code == 0

        # Raw snapshot uses alias (abc123), NOT the --output value
        assert raw_calls[0]["alias"] == "abc123"

        # Modular settings uses the --output value
        assert modular_calls[0]["app_file"] == "custom-output.yaml"

    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_show_with_profile_alias(
        self,
        mock_load_cred: MagicMock,
        mock_adapter_cls: MagicMock,
        tmp_path: Path,
        group_banner: Group,
    ) -> None:
        """show command uses profile alias for both snapshot and settings filenames."""
        mock_load_cred.return_value = _mock_credential()

        test_app = _make_app()
        mock_adapter = AsyncMock()
        mock_adapter.list_apps = AsyncMock(return_value=[test_app])
        mock_adapter.get_groups = AsyncMock(return_value=[group_banner])
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(
            return_value=mock_adapter
        )
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        raw_calls: list[dict] = []
        modular_calls: list[dict] = []

        def capture_raw(*args, **kwargs):
            raw_calls.append(kwargs)
            return str(tmp_path / "snapshots" / "ss-ios.yaml")

        def capture_modular(*args, **kwargs):
            modular_calls.append(kwargs)
            return (str(tmp_path / "settings" / "ss-ios.yaml"), [])

        with (
            patch("admedi.engine.snapshot.save_raw_snapshot", side_effect=capture_raw),
            patch("admedi.engine.snapshot.save_modular_snapshot", side_effect=capture_modular),
            patch("admedi.cli.main._resolve_app_key", return_value="abc123"),
        ):
            result = runner.invoke(cli_app, ["show", "--app", "ss-ios"])

        assert result.exit_code == 0

        # Both use the alias "ss-ios" (not the resolved key "abc123")
        assert raw_calls[0]["alias"] == "ss-ios"
        assert modular_calls[0]["app_file"] == "ss-ios.yaml"

    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_show_displays_warnings_when_present(
        self,
        mock_load_cred: MagicMock,
        mock_adapter_cls: MagicMock,
        tmp_path: Path,
        group_banner: Group,
    ) -> None:
        """show command displays tier warnings when save_modular_snapshot returns them."""
        mock_load_cred.return_value = _mock_credential()

        test_app = _make_app()
        mock_adapter = AsyncMock()
        mock_adapter.list_apps = AsyncMock(return_value=[test_app])
        mock_adapter.get_groups = AsyncMock(return_value=[group_banner])
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(
            return_value=mock_adapter
        )
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        warning_text = (
            "Tier 'Tier 2': countries differ across formats "
            "(interstitial: [AU, NL], rewarded: [AU, NZ]) -- merged to union [AU, NL, NZ]"
        )

        with (
            patch(
                "admedi.engine.snapshot.save_raw_snapshot",
                return_value=str(tmp_path / "snapshots" / "abc123.yaml"),
            ),
            patch(
                "admedi.engine.snapshot.save_modular_snapshot",
                return_value=(str(tmp_path / "settings" / "abc123.yaml"), [warning_text]),
            ),
        ):
            result = runner.invoke(cli_app, ["show", "--app", "abc123"])

        assert result.exit_code == 0
        assert "Tier 2" in result.output
        assert "countries differ" in result.output

    @patch("admedi.cli.main.LevelPlayAdapter")
    @patch("admedi.cli.main.load_credential_from_env")
    def test_show_no_warnings_when_empty(
        self,
        mock_load_cred: MagicMock,
        mock_adapter_cls: MagicMock,
        tmp_path: Path,
        group_banner: Group,
    ) -> None:
        """show command produces no warning panel when warnings list is empty."""
        mock_load_cred.return_value = _mock_credential()

        test_app = _make_app()
        mock_adapter = AsyncMock()
        mock_adapter.list_apps = AsyncMock(return_value=[test_app])
        mock_adapter.get_groups = AsyncMock(return_value=[group_banner])
        mock_adapter_cls.return_value.__aenter__ = AsyncMock(
            return_value=mock_adapter
        )
        mock_adapter_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "admedi.engine.snapshot.save_raw_snapshot",
                return_value=str(tmp_path / "snapshots" / "abc123.yaml"),
            ),
            patch(
                "admedi.engine.snapshot.save_modular_snapshot",
                return_value=(str(tmp_path / "settings" / "abc123.yaml"), []),
            ),
        ):
            result = runner.invoke(cli_app, ["show", "--app", "abc123"])

        assert result.exit_code == 0
        assert "Tier Warnings" not in result.output


# ===========================================================================
# Step 4: Legacy code removal verification
# ===========================================================================


class TestLegacyCodeRemoved:
    """Verify that legacy snapshot code has been removed."""

    def test_generate_snapshot_not_importable_from_engine(self) -> None:
        """generate_snapshot is no longer importable from admedi.engine."""
        with pytest.raises(ImportError):
            from admedi.engine import generate_snapshot  # noqa: F401

    def test_generate_snapshot_not_importable_from_snapshot_module(self) -> None:
        """generate_snapshot is no longer importable from admedi.engine.snapshot."""
        import admedi.engine.snapshot as snap_mod

        assert not hasattr(snap_mod, "generate_snapshot")

    def test_display_snapshot_info_not_importable(self) -> None:
        """display_snapshot_info is no longer importable from admedi.cli.display."""
        import admedi.cli.display as disp_mod

        assert not hasattr(disp_mod, "display_snapshot_info")

    def test_config_engine_no_snapshot_attribute(self) -> None:
        """ConfigEngine no longer has a snapshot attribute."""
        from admedi.engine.engine import ConfigEngine

        assert not hasattr(ConfigEngine, "snapshot")

    def test_snapshot_not_in_cli_help(self) -> None:
        """The 'snapshot' subcommand is not listed in CLI help output."""
        result = runner.invoke(cli_app, ["--help"])

        assert result.exit_code == 0
        assert "snapshot" not in result.output


# ===========================================================================
# Step 5: Per-app tiers and networks files
# ===========================================================================


class TestPerAppTiersAndNetworks:
    """Verify save_modular_snapshot writes per-app tiers/networks files."""

    def test_per_app_tiers_file(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Tiers written to {alias_stem}-tiers.yaml, not shared tiers.yaml."""
        save_modular_snapshot(
            [group_banner],
            app_key="abc123",
            app_name="Test App",
            app_file="ss-ios.yaml",
            settings_dir=str(tmp_path),
        )

        assert (tmp_path / "ss-ios-tiers.yaml").exists()
        assert not (tmp_path / "tiers.yaml").exists()

    def test_per_app_networks_file(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Networks written to {alias_stem}-networks.yaml, not shared networks.yaml."""
        save_modular_snapshot(
            [group_banner],
            app_key="abc123",
            app_name="Test App",
            app_file="ss-ios.yaml",
            settings_dir=str(tmp_path),
        )

        assert (tmp_path / "ss-ios-networks.yaml").exists()
        assert not (tmp_path / "networks.yaml").exists()

    def test_no_shared_files_created(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Neither shared tiers.yaml nor networks.yaml is created."""
        save_modular_snapshot(
            [group_banner],
            app_key="abc123",
            app_name="Test App",
            app_file="hexar-ios.yaml",
            settings_dir=str(tmp_path),
        )

        assert not (tmp_path / "tiers.yaml").exists()
        assert not (tmp_path / "networks.yaml").exists()

    def test_two_apps_produce_independent_files(
        self, tmp_path: Path
    ) -> None:
        """Two apps with different tiers produce independent files."""
        # App 1: Shelf Sort with Tier 1 = US, Tier 2 = AU, CA, DE, GB, JP, KR, NL, TW
        groups_ss = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["AU", "CA", "DE", "GB", "JP", "KR", "NL", "TW"],
                "position": 2,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]

        # App 2: Hexar with Tier 1 = US, Tier 2 = AU, CA, DE, GB, JP, NZ
        groups_hexar = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 3, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["AU", "CA", "DE", "GB", "JP", "NZ"],
                "position": 2,
                "instances": [
                    {"id": 4, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]

        save_modular_snapshot(
            groups_ss,
            app_key="ss123",
            app_name="Shelf Sort",
            app_file="ss-ios.yaml",
            settings_dir=str(tmp_path),
        )
        save_modular_snapshot(
            groups_hexar,
            app_key="hex123",
            app_name="Hexar",
            app_file="hexar-ios.yaml",
            settings_dir=str(tmp_path),
        )

        yaml = YAML()

        # Shelf Sort tiers preserved
        ss_tiers = yaml.load(tmp_path / "ss-ios-tiers.yaml")
        assert ss_tiers["tiers"]["Tier 2"]["countries"] == [
            "AU", "CA", "DE", "GB", "JP", "KR", "NL", "TW"
        ]
        assert ss_tiers["tiers"]["Tier 2"]["formats"] == ["interstitial"]

        # Hexar tiers preserved independently
        hexar_tiers = yaml.load(tmp_path / "hexar-ios-tiers.yaml")
        assert hexar_tiers["tiers"]["Tier 2"]["countries"] == [
            "AU", "CA", "DE", "GB", "JP", "NZ"
        ]
        assert hexar_tiers["tiers"]["Tier 2"]["formats"] == ["interstitial"]

    def test_tiers_file_content_structure(
        self, tmp_path: Path, group_interstitial_with_rates: Group
    ) -> None:
        """Per-app tiers file has correct YAML structure with header and formats."""
        save_modular_snapshot(
            [group_interstitial_with_rates],
            app_key="abc123",
            app_name="Test App",
            app_file="ss-google.yaml",
            settings_dir=str(tmp_path),
        )

        content = (tmp_path / "ss-google-tiers.yaml").read_text()
        assert content.startswith("# Admedi tier definitions")

        yaml = YAML()
        data = yaml.load(tmp_path / "ss-google-tiers.yaml")
        assert "tiers" in data
        assert "Tier 1" in data["tiers"]
        assert data["tiers"]["Tier 1"]["countries"] == ["US"]
        assert data["tiers"]["Tier 1"]["formats"] == ["interstitial"]

    def test_networks_file_content_structure(
        self, tmp_path: Path, group_interstitial_with_rates: Group
    ) -> None:
        """Per-app networks file has correct YAML structure with header."""
        save_modular_snapshot(
            [group_interstitial_with_rates],
            app_key="abc123",
            app_name="Test App",
            app_file="ss-google.yaml",
            settings_dir=str(tmp_path),
        )

        content = (tmp_path / "ss-google-networks.yaml").read_text()
        assert content.startswith("# Admedi network presets")

        yaml = YAML()
        data = yaml.load(tmp_path / "ss-google-networks.yaml")
        assert "presets" in data

    def test_overwrite_on_second_call(
        self, tmp_path: Path
    ) -> None:
        """Second call overwrites per-app file (no stale merge)."""
        groups_v1 = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["AU", "CA"],
                "position": 2,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        groups_v2 = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US", "GB"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]

        save_modular_snapshot(
            groups_v1,
            app_key="abc123",
            app_name="Test",
            app_file="ss-ios.yaml",
            settings_dir=str(tmp_path),
        )
        save_modular_snapshot(
            groups_v2,
            app_key="abc123",
            app_name="Test",
            app_file="ss-ios.yaml",
            settings_dir=str(tmp_path),
        )

        yaml = YAML()
        tiers = yaml.load(tmp_path / "ss-ios-tiers.yaml")

        # Only Tier 1 from v2 — Tier 2 from v1 is gone (overwritten, not merged)
        assert "Tier 1" in tiers["tiers"]
        assert "Tier 2" not in tiers["tiers"]
        assert tiers["tiers"]["Tier 1"]["countries"] == ["US", "GB"]
        assert tiers["tiers"]["Tier 1"]["formats"] == ["interstitial"]


# ---------------------------------------------------------------------------
# Tests: _extract_tiers formats field and default-tier ordering
# ---------------------------------------------------------------------------


class TestExtractTiersFormats:
    """Verify _extract_tiers() includes formats field and sorts default tiers last."""

    def test_single_format_tier_has_single_element_formats_list(
        self, tmp_path: Path
    ) -> None:
        """A tier appearing in only one format has a single-element formats list."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "banner",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        save_modular_snapshot(
            groups,
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )

        yaml = YAML()
        data = yaml.load(tmp_path / "test-tiers.yaml")
        assert data["tiers"]["Tier 1"]["formats"] == ["banner"]

    def test_multi_format_tier_collects_all_formats(
        self, tmp_path: Path
    ) -> None:
        """A tier shared across interstitial and rewarded has both formats listed."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "rewarded",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        save_modular_snapshot(
            groups,
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )

        yaml = YAML()
        data = yaml.load(tmp_path / "test-tiers.yaml")
        assert data["tiers"]["Tier 1"]["formats"] == ["interstitial", "rewarded"]

    def test_three_format_tier_sorted_alphabetically(
        self, tmp_path: Path
    ) -> None:
        """Formats list is sorted alphabetically regardless of group order."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "rewarded",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "banner",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 3, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        save_modular_snapshot(
            groups,
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )

        yaml = YAML()
        data = yaml.load(tmp_path / "test-tiers.yaml")
        assert data["tiers"]["Tier 1"]["formats"] == [
            "banner", "interstitial", "rewarded"
        ]

    def test_default_tier_sorted_last(
        self, tmp_path: Path
    ) -> None:
        """Default tier (with '*' in countries) appears last in dict."""
        groups = [
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "banner",
                "countries": ["*"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "banner",
                "countries": ["US"],
                "position": 2,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        save_modular_snapshot(
            groups,
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )

        yaml = YAML()
        data = yaml.load(tmp_path / "test-tiers.yaml")
        tier_names = list(data["tiers"].keys())
        assert tier_names[-1] == "All Countries"
        assert tier_names[0] == "Tier 1"

    def test_default_tier_last_with_multiple_regular_tiers(
        self, tmp_path: Path
    ) -> None:
        """Default tier is last even when there are multiple non-default tiers."""
        groups = [
            Group.model_validate({
                "groupName": "Default",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 2,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["GB", "DE"],
                "position": 3,
                "instances": [
                    {"id": 3, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        save_modular_snapshot(
            groups,
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )

        yaml = YAML()
        data = yaml.load(tmp_path / "test-tiers.yaml")
        tier_names = list(data["tiers"].keys())
        assert tier_names[-1] == "Default"
        assert "Tier 1" in tier_names[:-1]
        assert "Tier 2" in tier_names[:-1]

    def test_formats_field_is_list_not_set(
        self, tmp_path: Path
    ) -> None:
        """Formats field is serialized as a YAML list (not !!set)."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "rewarded",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        save_modular_snapshot(
            groups,
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )

        # Read raw text to ensure no !!set tag
        content = (tmp_path / "test-tiers.yaml").read_text()
        assert "!!set" not in content

        yaml = YAML()
        data = yaml.load(tmp_path / "test-tiers.yaml")
        assert isinstance(data["tiers"]["Tier 1"]["formats"], list)

    def test_countries_preserved_with_formats(
        self, tmp_path: Path
    ) -> None:
        """Countries data is preserved alongside the new formats field."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US", "GB", "DE"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["JP", "KR"],
                "position": 2,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        save_modular_snapshot(
            groups,
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )

        yaml = YAML()
        data = yaml.load(tmp_path / "test-tiers.yaml")
        assert data["tiers"]["Tier 1"]["countries"] == ["US", "GB", "DE"]
        assert data["tiers"]["Tier 1"]["formats"] == ["interstitial"]
        assert data["tiers"]["Tier 2"]["countries"] == ["JP", "KR"]
        assert data["tiers"]["Tier 2"]["formats"] == ["interstitial"]

    def test_mixed_single_and_multi_format_tiers(
        self, tmp_path: Path
    ) -> None:
        """Some tiers have one format, others have multiple."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "rewarded",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "banner",
                "countries": ["GB"],
                "position": 2,
                "instances": [
                    {"id": 3, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 3,
                "instances": [
                    {"id": 4, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        save_modular_snapshot(
            groups,
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )

        yaml = YAML()
        data = yaml.load(tmp_path / "test-tiers.yaml")

        # Tier 1: shared across interstitial + rewarded
        assert data["tiers"]["Tier 1"]["formats"] == ["interstitial", "rewarded"]

        # Tier 2: banner only
        assert data["tiers"]["Tier 2"]["formats"] == ["banner"]

        # All Countries (default): interstitial only, sorted last
        assert data["tiers"]["All Countries"]["formats"] == ["interstitial"]
        tier_names = list(data["tiers"].keys())
        assert tier_names[-1] == "All Countries"

    def test_every_tier_has_both_countries_and_formats(
        self, tmp_path: Path
    ) -> None:
        """Every tier entry in the output has both countries and formats keys."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Default",
                "adFormat": "banner",
                "countries": ["*"],
                "position": 2,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        save_modular_snapshot(
            groups,
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )

        yaml = YAML()
        data = yaml.load(tmp_path / "test-tiers.yaml")
        for tier_name, tier_data in data["tiers"].items():
            assert "countries" in tier_data, f"{tier_name} missing 'countries'"
            assert "formats" in tier_data, f"{tier_name} missing 'formats'"


# ---------------------------------------------------------------------------
# Tests: _extract_tiers() per-format country variance (union + warnings)
# ---------------------------------------------------------------------------


class TestExtractTiersCountryVariance:
    """Verify _extract_tiers() detects per-format country differences and returns union + warnings."""

    def test_identical_countries_returns_empty_warnings(self) -> None:
        """When all formats share the same countries for a tier, warnings list is empty."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US", "GB"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "rewarded",
                "countries": ["US", "GB"],
                "position": 1,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        tiers, warnings = _extract_tiers(groups)
        assert warnings == []
        assert tiers["Tier 1"]["countries"] == ["US", "GB"]

    def test_different_countries_returns_sorted_union(self) -> None:
        """When countries differ across formats, the result is the sorted union."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["AU", "NL"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "rewarded",
                "countries": ["AU", "NZ"],
                "position": 1,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        tiers, warnings = _extract_tiers(groups)
        assert tiers["Tier 2"]["countries"] == ["AU", "NL", "NZ"]

    def test_different_countries_returns_warning_with_tier_name(self) -> None:
        """Warning string contains the tier name when countries differ across formats."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["AU", "NL"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "rewarded",
                "countries": ["AU", "NZ"],
                "position": 1,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        tiers, warnings = _extract_tiers(groups)
        assert len(warnings) == 1
        assert "Tier 2" in warnings[0]
        assert "interstitial" in warnings[0]
        assert "rewarded" in warnings[0]
        assert "merged to union" in warnings[0]

    def test_single_format_no_warning(self) -> None:
        """A tier with only one format never produces a warning."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "banner",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        tiers, warnings = _extract_tiers(groups)
        assert warnings == []
        assert tiers["Tier 1"]["countries"] == ["US"]

    def test_three_formats_with_variance_produces_one_warning(self) -> None:
        """Three formats with different countries produce one warning per tier."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "banner",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US", "GB"],
                "position": 1,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "rewarded",
                "countries": ["US", "DE"],
                "position": 1,
                "instances": [
                    {"id": 3, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        tiers, warnings = _extract_tiers(groups)
        assert tiers["Tier 1"]["countries"] == ["DE", "GB", "US"]
        assert len(warnings) == 1
        assert "Tier 1" in warnings[0]

    def test_multiple_tiers_with_variance_produce_multiple_warnings(self) -> None:
        """Each tier with country variance produces its own warning."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "rewarded",
                "countries": ["US", "GB"],
                "position": 1,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["JP"],
                "position": 2,
                "instances": [
                    {"id": 3, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "rewarded",
                "countries": ["JP", "KR"],
                "position": 2,
                "instances": [
                    {"id": 4, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        tiers, warnings = _extract_tiers(groups)
        assert len(warnings) == 2
        warning_text = " ".join(warnings)
        assert "Tier 1" in warning_text
        assert "Tier 2" in warning_text

    def test_return_type_is_tuple(self) -> None:
        """_extract_tiers() returns a 2-tuple (dict, list)."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "banner",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        result = _extract_tiers(groups)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], dict)
        assert isinstance(result[1], list)


class TestSaveModularSnapshotWarnings:
    """Verify save_modular_snapshot() returns (str, list[str]) tuple with warnings."""

    def test_returns_tuple(self, tmp_path: Path) -> None:
        """save_modular_snapshot() returns a 2-tuple (str, list[str])."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "banner",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        result = save_modular_snapshot(
            groups,
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        path, warnings = result
        assert isinstance(path, str)
        assert isinstance(warnings, list)

    def test_no_variance_returns_empty_warnings(self, tmp_path: Path) -> None:
        """When all formats agree on countries, warnings list is empty."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "rewarded",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        _path, warnings = save_modular_snapshot(
            groups,
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )
        assert warnings == []

    def test_variance_returns_nonempty_warnings(self, tmp_path: Path) -> None:
        """When formats have different countries, warnings list is non-empty."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["AU", "NL"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "rewarded",
                "countries": ["AU", "NZ"],
                "position": 1,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        _path, warnings = save_modular_snapshot(
            groups,
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )
        assert len(warnings) > 0
        assert "Tier 2" in warnings[0]

    def test_variance_tiers_file_has_union_countries(self, tmp_path: Path) -> None:
        """Tiers file written by save_modular_snapshot uses the union countries."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["AU", "NL"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "rewarded",
                "countries": ["AU", "NZ"],
                "position": 1,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        save_modular_snapshot(
            groups,
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )

        yaml = YAML()
        data = yaml.load(tmp_path / "test-tiers.yaml")
        assert data["tiers"]["Tier 2"]["countries"] == ["AU", "NL", "NZ"]


# ---------------------------------------------------------------------------
# Tests: _build_app_yaml() waterfall section
# ---------------------------------------------------------------------------


class TestBuildAppYamlWaterfall:
    """Verify _build_app_yaml() produces flat waterfall mapping instead of groups."""

    def test_app_file_has_waterfall_not_groups(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """App file has waterfall key, not groups key."""
        save_modular_snapshot(
            [group_banner],
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )

        yaml = YAML()
        data = yaml.load(tmp_path / "test.yaml")
        assert "waterfall" in data
        assert "groups" not in data

    def test_waterfall_has_all_four_levelplay_formats(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Waterfall section has exactly 4 keys: banner, interstitial, native, rewarded."""
        save_modular_snapshot(
            [group_banner],
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )

        yaml = YAML()
        data = yaml.load(tmp_path / "test.yaml")
        waterfall = data["waterfall"]
        assert set(waterfall.keys()) == {"banner", "interstitial", "native", "rewarded"}

    def test_format_with_no_groups_maps_to_none(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Formats with no groups have value 'none'."""
        save_modular_snapshot(
            [group_banner],
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )

        yaml = YAML()
        data = yaml.load(tmp_path / "test.yaml")
        # Only banner has groups; others should be "none"
        assert data["waterfall"]["interstitial"] == "none"
        assert data["waterfall"]["native"] == "none"
        assert data["waterfall"]["rewarded"] == "none"

    def test_format_with_groups_maps_to_preset_name(
        self, tmp_path: Path, group_interstitial_with_rates: Group
    ) -> None:
        """Formats with groups map to the resolved preset name."""
        save_modular_snapshot(
            [group_interstitial_with_rates],
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )

        yaml = YAML()
        data = yaml.load(tmp_path / "test.yaml")
        # group_interstitial_with_rates has Meta (bidder) + AppLovin (manual)
        # Expected preset name: "meta-applovin"
        assert data["waterfall"]["interstitial"] != "none"
        assert isinstance(data["waterfall"]["interstitial"], str)

    def test_metadata_fields_preserved(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """app_key, app_name, and platform fields are preserved."""
        save_modular_snapshot(
            [group_banner],
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
            platform=Platform.IOS,
        )

        yaml = YAML()
        data = yaml.load(tmp_path / "test.yaml")
        assert data["app_key"] == "abc123"
        assert data["app_name"] == "Test App"
        assert data["platform"] == "iOS"

    def test_header_comments_preserved(
        self, tmp_path: Path, group_banner: Group
    ) -> None:
        """Header comments (app name, captured timestamp) are present."""
        save_modular_snapshot(
            [group_banner],
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )

        content = (tmp_path / "test.yaml").read_text()
        assert content.startswith("# Generated by admedi show")
        assert "# App: Test App (abc123)" in content
        assert "# Captured:" in content

    def test_multiple_formats_with_groups(
        self, tmp_path: Path
    ) -> None:
        """Multiple formats with groups each resolve to their preset name."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "rewarded",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        save_modular_snapshot(
            groups,
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )

        yaml = YAML()
        data = yaml.load(tmp_path / "test.yaml")
        # Both interstitial and rewarded have groups with the same waterfall
        assert data["waterfall"]["interstitial"] != "none"
        assert data["waterfall"]["rewarded"] != "none"
        # banner and native have no groups
        assert data["waterfall"]["banner"] == "none"
        assert data["waterfall"]["native"] == "none"

    def test_waterfall_preset_name_matches_network_file(
        self, tmp_path: Path
    ) -> None:
        """Waterfall preset name in app file matches a key in the networks file."""
        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                    {"id": 2, "name": "Bidding", "networkName": "UnityAds", "isBidder": True},
                ],
            }),
        ]
        save_modular_snapshot(
            groups,
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )

        yaml = YAML()
        app_data = yaml.load(tmp_path / "test.yaml")
        networks_data = yaml.load(tmp_path / "test-networks.yaml")

        preset_name = app_data["waterfall"]["interstitial"]
        assert preset_name in networks_data["presets"]

    def test_empty_groups_all_formats_none(
        self, tmp_path: Path
    ) -> None:
        """When no groups are provided, all formats map to 'none'."""
        save_modular_snapshot(
            [],
            app_key="abc123",
            app_name="Test App",
            app_file="test.yaml",
            settings_dir=str(tmp_path),
        )

        yaml = YAML()
        data = yaml.load(tmp_path / "test.yaml")
        for fmt_name, preset in data["waterfall"].items():
            assert preset == "none", f"{fmt_name} should be 'none' with no groups"

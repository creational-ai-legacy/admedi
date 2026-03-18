"""Tests for snapshot functions (save_raw_snapshot, load_snapshot, generate_app_settings).

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

- Step 3: Display updates
  - display_pull() with both paths shows both in footer
  - display_pull() with only snapshot_path shows only snapshot path
  - display_pull() with only settings_path shows only settings path
  - display_pull() with neither path shows no footer paths
  - Empty-groups path also shows both paths when provided

- Step 4: Legacy code removal verification
  - generate_snapshot is no longer importable from admedi.engine or admedi.engine.snapshot
  - display_snapshot_info is no longer importable from admedi.cli.display
  - ConfigEngine no longer has a snapshot attribute
  - snapshot subcommand is not listed in CLI help output

- Step 6: generate_app_settings and country matching
  - Country matching: exact set match, catch-all match, auto-create
  - Per-app settings generation in new format (alias + format -> tier list)
  - Bootstrap: fresh portfolio creates countries.yaml, tiers.yaml, per-app settings
  - Existing shared files reused when all groups match
  - New entries appended to existing shared files
  - Auto-naming: single country -> code, multi-country -> lowercased hyphenated name
  - Public helpers: extract_network_presets, waterfall_signature, write_yaml_file
  - Removed functions: save_modular_snapshot, _extract_tiers, _build_app_yaml, _build_network_lookup
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console
from ruamel.yaml import YAML
from typer.testing import CliRunner

from admedi.cli.display import display_pull
from admedi.cli.main import app as cli_app
from admedi.engine.loader import Profile, resolve_app_tiers
from admedi.engine.snapshot import (
    SnapshotData,
    _auto_name_network,
    _write_per_app_settings,
    extract_network_presets,
    generate_app_settings,
    load_snapshot,
    save_raw_snapshot,
    waterfall_signature,
    write_yaml_file,
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
        """YAML header contains '# Captured by admedi pull'."""
        content = self._read_raw(
            tmp_path,
            groups=[group_banner],
            app_key="abc123",
            app_name="Test App",
            alias="test",
        )
        assert "# Captured by admedi pull" in content

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
# Tests: display_pull() footer with both paths
# ---------------------------------------------------------------------------


class TestDisplayPullBothPaths:
    """Tests for display_pull() rendering both snapshot and settings paths."""

    def test_both_paths_shown_in_footer(
        self, group_banner: Group
    ) -> None:
        """display_pull() with both paths shows both in footer output."""
        console, buf = _make_console()
        app = _make_app()

        display_pull(
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
        """display_pull() with only snapshot_path shows only snapshot path."""
        console, buf = _make_console()
        app = _make_app()

        display_pull(
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
        """display_pull() with only settings_path shows only settings path."""
        console, buf = _make_console()
        app = _make_app()

        display_pull(
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
        """display_pull() with neither path shows no footer paths."""
        console, buf = _make_console()
        app = _make_app()

        display_pull(
            app, [group_banner],
            console=console,
        )
        output = buf.getvalue()

        assert "Snapshot saved to:" not in output
        assert "Settings saved to:" not in output


# ---------------------------------------------------------------------------
# Tests: display_pull() empty-groups path with both paths
# ---------------------------------------------------------------------------


class TestDisplayPullEmptyGroupsPaths:
    """Tests for display_pull() empty-groups path showing both paths."""

    def test_empty_groups_both_paths(self) -> None:
        """Empty-groups path shows both paths when provided."""
        console, buf = _make_console()
        app = _make_app()

        display_pull(
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

        display_pull(app, [], console=console)
        output = buf.getvalue()

        assert "No mediation groups configured" in output
        assert "Snapshot saved to:" not in output
        assert "Settings saved to:" not in output


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
# Step 6: Removed functions verification
# ===========================================================================


class TestRemovedFunctions:
    """Verify that save_modular_snapshot, _extract_tiers, _build_app_yaml, _build_network_lookup are removed."""

    def test_save_modular_snapshot_removed(self) -> None:
        """save_modular_snapshot is no longer in snapshot module."""
        import admedi.engine.snapshot as snap_mod

        assert not hasattr(snap_mod, "save_modular_snapshot")

    def test_extract_tiers_removed(self) -> None:
        """_extract_tiers is no longer in snapshot module."""
        import admedi.engine.snapshot as snap_mod

        assert not hasattr(snap_mod, "_extract_tiers")

    def test_build_app_yaml_removed(self) -> None:
        """_build_app_yaml is no longer in snapshot module."""
        import admedi.engine.snapshot as snap_mod

        assert not hasattr(snap_mod, "_build_app_yaml")

    def test_build_network_lookup_removed(self) -> None:
        """_build_network_lookup is no longer in snapshot module."""
        import admedi.engine.snapshot as snap_mod

        assert not hasattr(snap_mod, "_build_network_lookup")


# ===========================================================================
# Step 6: Public helper tests (renamed from private)
# ===========================================================================


class TestWaterfallSignature:
    """Verify waterfall_signature() is public and produces correct signatures."""

    def test_importable(self) -> None:
        """waterfall_signature is importable from snapshot module."""
        from admedi.engine.snapshot import waterfall_signature as ws
        assert callable(ws)

    def test_signature_for_bidders_only(self, group_banner: Group) -> None:
        """Groups with only bidders produce a tuple of (sorted_bidders, empty_manuals)."""
        sig = waterfall_signature(group_banner)
        assert isinstance(sig, tuple)
        assert len(sig) == 2
        bidders, manuals = sig
        assert bidders == ("UnityAds", "ironSource")
        assert manuals == ()

    def test_signature_for_mixed_group(
        self, group_interstitial_with_rates: Group
    ) -> None:
        """Groups with both bidders and manuals produce correct signature."""
        sig = waterfall_signature(group_interstitial_with_rates)
        bidders, manuals = sig
        assert bidders == ("Meta",)
        # Unique instance within (network, bidder) pair -> name is None
        assert manuals == (("AppLovin", 1.0, None),)


class TestExtractNetworkPresets:
    """Verify extract_network_presets() is public and works correctly."""

    def test_importable(self) -> None:
        """extract_network_presets is importable from snapshot module."""
        from admedi.engine.snapshot import extract_network_presets as enp
        assert callable(enp)

    def test_extracts_unique_presets(self, group_banner: Group) -> None:
        """Extracts unique network presets from groups."""
        presets = extract_network_presets([group_banner])
        assert isinstance(presets, dict)
        assert len(presets) >= 1

    def test_empty_groups_returns_empty_dict(self) -> None:
        """Empty groups list returns empty presets dict."""
        presets = extract_network_presets([])
        assert presets == {}

    def test_deduplicates_same_waterfall(self) -> None:
        """Two groups with identical waterfalls produce one preset entry."""
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
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["GB"],
                "position": 2,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        presets = extract_network_presets(groups)
        assert len(presets) == 1


class TestWriteYamlFile:
    """Verify write_yaml_file() is public and writes correctly."""

    def test_importable(self) -> None:
        """write_yaml_file is importable from snapshot module."""
        from admedi.engine.snapshot import write_yaml_file as wyf
        assert callable(wyf)

    def test_writes_file_with_header(self, tmp_path: Path) -> None:
        """write_yaml_file writes a file with the header prepended."""
        path = tmp_path / "test.yaml"
        write_yaml_file(
            path,
            {"entry1": [{"key": "value"}]},
            header="# Test header\n\n",
        )
        content = path.read_text()
        assert content.startswith("# Test header")

    def test_writes_under_root_key(self, tmp_path: Path) -> None:
        """Data is written under the specified root key."""
        path = tmp_path / "test.yaml"
        write_yaml_file(
            path,
            {"entry1": [{"key": "value"}]},
            header="# Test\n\n",
            root_key="custom",
        )
        yaml = YAML()
        data = yaml.load(path)
        assert "custom" in data


# ===========================================================================
# Step 2 (sync-networks): Improved snapshot helper tests
# ===========================================================================


class TestWaterfallSignatureSorting:
    """Verify waterfall_signature() sorts instances for deterministic signatures."""

    def test_same_signature_regardless_of_instance_order(self) -> None:
        """Instances in different iteration order produce identical signature."""
        group_a = Group.model_validate({
            "groupName": "T1", "adFormat": "banner",
            "countries": ["US"], "position": 1,
            "instances": [
                {"id": 1, "name": "B", "networkName": "ironSource", "isBidder": True},
                {"id": 2, "name": "B", "networkName": "Meta", "isBidder": True},
                {"id": 3, "name": "Low", "networkName": "AppLovin", "isBidder": False, "groupRate": 0.5},
                {"id": 4, "name": "High", "networkName": "Google AdMob", "isBidder": False, "groupRate": 2.0},
            ],
        })
        # Same instances in reversed order
        group_b = Group.model_validate({
            "groupName": "T1", "adFormat": "banner",
            "countries": ["US"], "position": 1,
            "instances": [
                {"id": 4, "name": "High", "networkName": "Google AdMob", "isBidder": False, "groupRate": 2.0},
                {"id": 3, "name": "Low", "networkName": "AppLovin", "isBidder": False, "groupRate": 0.5},
                {"id": 2, "name": "B", "networkName": "Meta", "isBidder": True},
                {"id": 1, "name": "B", "networkName": "ironSource", "isBidder": True},
            ],
        })
        assert waterfall_signature(group_a) == waterfall_signature(group_b)

    def test_instance_name_differentiates_duplicate_networks(self) -> None:
        """Two instances of same network with different names produce different signature."""
        group_one = Group.model_validate({
            "groupName": "T1", "adFormat": "banner",
            "countries": ["US"], "position": 1,
            "instances": [
                {"id": 1, "name": "High", "networkName": "Google AdMob", "isBidder": False, "groupRate": 2.0},
            ],
        })
        group_two = Group.model_validate({
            "groupName": "T2", "adFormat": "banner",
            "countries": ["US"], "position": 1,
            "instances": [
                {"id": 1, "name": "High", "networkName": "Google AdMob", "isBidder": False, "groupRate": 2.0},
                {"id": 2, "name": "Low", "networkName": "Google AdMob", "isBidder": False, "groupRate": 0.5},
            ],
        })
        assert waterfall_signature(group_one) != waterfall_signature(group_two)

    def test_none_instances_handled(self) -> None:
        """Group with instances=None returns empty bidders and manuals."""
        group = Group.model_validate({
            "groupName": "Empty", "adFormat": "native",
            "countries": ["*"], "position": 1,
        })
        assert group.instances is None
        sig = waterfall_signature(group)
        bidders, manuals = sig
        assert bidders == ()
        assert manuals == ()


class TestAutoNameNetworkRules:
    """Verify _auto_name_network() implements all 5 naming rules."""

    def test_rule1_bidders_only_two(self) -> None:
        """Rule 1: bidders-only <=3 joins first words with '+'."""
        result = _auto_name_network(["Google", "InMobi"], [])
        assert result == "google+inmobi"

    def test_rule1_bidders_only_three(self) -> None:
        """Rule 1: exactly 3 bidders joins first words."""
        result = _auto_name_network(["ironSource", "Meta", "UnityAds"], [])
        assert result == "ironsource+meta+unityads"

    def test_rule1_single_bidder(self) -> None:
        """Rule 1: single bidder uses first word lowercased."""
        result = _auto_name_network(["Meta"], [])
        assert result == "meta"

    def test_rule2_bidders_over_three(self) -> None:
        """Rule 2: >3 bidders produces 'bidding-{count}'."""
        result = _auto_name_network(
            ["A", "B", "C", "D", "E", "F", "G"], []
        )
        assert result == "bidding-7"

    def test_rule2_exactly_four_bidders(self) -> None:
        """Rule 2: exactly 4 bidders produces 'bidding-4'."""
        result = _auto_name_network(["A", "B", "C", "D"], [])
        assert result == "bidding-4"

    def test_rule3_with_manuals_short_names(self) -> None:
        """Rule 3: <=3 manual networks uses last word lowercased."""
        manuals = [
            {"network": "Google AdMob", "bidder": False},
        ]
        result = _auto_name_network(["Meta"], manuals)
        assert "admob" in result
        assert result == "meta-admob"

    def test_rule3_multiple_manuals_sorted(self) -> None:
        """Rule 3: manual short names are sorted alphabetically."""
        manuals = [
            {"network": "Google AdMob", "bidder": False},
            {"network": "AppLovin", "bidder": False},
        ]
        result = _auto_name_network(["Meta"], manuals)
        # "admob" before "applovin" alphabetically
        assert result == "meta-admob+applovin"

    def test_rule3_single_word_network_full_name(self) -> None:
        """Rule 3: single-word network name uses full name lowercased."""
        manuals = [
            {"network": "Fyber", "bidder": False},
        ]
        result = _auto_name_network(["Meta"], manuals)
        assert result == "meta-fyber"

    def test_rule4_manuals_over_three(self) -> None:
        """Rule 4: >3 unique manual networks produces 'manual-{count}'."""
        manuals = [
            {"network": "Google AdMob", "bidder": False},
            {"network": "AppLovin", "bidder": False},
            {"network": "Fyber", "bidder": False},
            {"network": "Pangle", "bidder": False},
        ]
        result = _auto_name_network(["Meta"], manuals)
        assert result == "meta-manual-4"

    def test_rule6_manuals_only(self) -> None:
        """Rule 6: zero bidders omits bidder prefix."""
        manuals = [
            {"network": "Google AdMob", "bidder": False},
            {"network": "AppLovin", "bidder": False},
        ]
        result = _auto_name_network([], manuals)
        assert result == "admob+applovin"

    def test_none_inputs(self) -> None:
        """No bidders and no manuals returns 'none'."""
        result = _auto_name_network([], [])
        assert result == "none"

    def test_rule3_dedups_manual_networks(self) -> None:
        """Rule 3: duplicate manual network names count as one unique."""
        manuals = [
            {"network": "Google AdMob", "bidder": False},
            {"network": "Google AdMob", "bidder": False},
        ]
        result = _auto_name_network(["Meta"], manuals)
        assert result == "meta-admob"


class TestExtractNetworkPresetsNameField:
    """Verify extract_network_presets() name field emission logic."""

    def test_no_name_for_unique_instances(self) -> None:
        """Single instance per network does not emit 'name' field."""
        groups = [
            Group.model_validate({
                "groupName": "T1", "adFormat": "banner",
                "countries": ["US"], "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "Meta", "isBidder": True},
                    {"id": 2, "name": "Default", "networkName": "AppLovin", "isBidder": False, "groupRate": 1.0},
                ],
            }),
        ]
        presets = extract_network_presets(groups)
        assert len(presets) == 1
        entries = list(presets.values())[0]
        for entry in entries:
            assert "name" not in entry

    def test_name_for_duplicate_manual_network(self) -> None:
        """Multiple instances of same manual network emit 'name' for disambiguation."""
        groups = [
            Group.model_validate({
                "groupName": "T1", "adFormat": "banner",
                "countries": ["US"], "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "Meta", "isBidder": True},
                    {"id": 2, "name": "High", "networkName": "Google AdMob", "isBidder": False, "groupRate": 2.0},
                    {"id": 3, "name": "Low", "networkName": "Google AdMob", "isBidder": False, "groupRate": 0.5},
                ],
            }),
        ]
        presets = extract_network_presets(groups)
        entries = list(presets.values())[0]

        # Bidder (Meta) should not have name field
        bidder_entries = [e for e in entries if e.get("bidder") is True]
        for be in bidder_entries:
            assert "name" not in be

        # Both Google AdMob manuals should have name field
        admob_entries = [e for e in entries if e["network"] == "Google AdMob"]
        assert len(admob_entries) == 2
        names = {e["name"] for e in admob_entries}
        assert names == {"High", "Low"}

    def test_bidders_only_no_name_field(self) -> None:
        """Bidder-only groups never emit 'name' field."""
        groups = [
            Group.model_validate({
                "groupName": "T1", "adFormat": "banner",
                "countries": ["US"], "position": 1,
                "instances": [
                    {"id": 1, "name": "B1", "networkName": "ironSource", "isBidder": True},
                    {"id": 2, "name": "B2", "networkName": "Meta", "isBidder": True},
                ],
            }),
        ]
        presets = extract_network_presets(groups)
        entries = list(presets.values())[0]
        for entry in entries:
            assert "name" not in entry

    def test_none_instances_group_skipped(self) -> None:
        """Group with instances=None is skipped (no error, no entry)."""
        groups = [
            Group.model_validate({
                "groupName": "Empty", "adFormat": "native",
                "countries": ["*"], "position": 1,
            }),
        ]
        presets = extract_network_presets(groups)
        assert presets == {}

    def test_bidder_field_on_all_entries(self) -> None:
        """All entries have a 'bidder' field (True for bidders, False for manuals)."""
        groups = [
            Group.model_validate({
                "groupName": "T1", "adFormat": "banner",
                "countries": ["US"], "position": 1,
                "instances": [
                    {"id": 1, "name": "B", "networkName": "Meta", "isBidder": True},
                    {"id": 2, "name": "D", "networkName": "AppLovin", "isBidder": False, "groupRate": 1.0},
                ],
            }),
        ]
        presets = extract_network_presets(groups)
        entries = list(presets.values())[0]
        for entry in entries:
            assert "bidder" in entry
            assert isinstance(entry["bidder"], bool)


class TestWriteYamlFileRootKeyNone:
    """Verify write_yaml_file() with root_key=None."""

    def test_root_key_none_no_wrapping(self, tmp_path: Path) -> None:
        """root_key=None dumps entries directly as top-level YAML content."""
        path = tmp_path / "test.yaml"
        entries = {
            "preset-a": [{"network": "Meta", "bidder": True}],
            "preset-b": [{"network": "AppLovin", "bidder": False}],
        }
        write_yaml_file(path, entries, header="# Header\n\n", root_key=None)

        yaml = YAML()
        data = yaml.load(path)

        # Entries are at top level, not nested under a key
        assert "preset-a" in data
        assert "preset-b" in data
        assert "presets" not in data

    def test_root_key_presets_wraps_content(self, tmp_path: Path) -> None:
        """root_key='presets' wraps entries under 'presets' key (default behavior)."""
        path = tmp_path / "test.yaml"
        entries = {"entry1": [{"key": "value"}]}
        write_yaml_file(path, entries, header="# H\n\n", root_key="presets")

        yaml = YAML()
        data = yaml.load(path)

        assert "presets" in data
        assert data["presets"]["entry1"] == [{"key": "value"}]

    def test_root_key_none_preserves_header(self, tmp_path: Path) -> None:
        """Header is still prepended when root_key=None."""
        path = tmp_path / "test.yaml"
        write_yaml_file(
            path, {"a": 1}, header="# My custom header\n\n", root_key=None
        )
        content = path.read_text()
        assert content.startswith("# My custom header")

    def test_default_root_key_is_presets(self, tmp_path: Path) -> None:
        """Default root_key is 'presets' (backward compatible)."""
        path = tmp_path / "test.yaml"
        write_yaml_file(path, {"x": 1}, header="# H\n\n")

        yaml = YAML()
        data = yaml.load(path)
        assert "presets" in data


# ===========================================================================
# Step 6: generate_app_settings tests
# ===========================================================================


def _make_profile(**overrides) -> Profile:
    """Create a test Profile with defaults."""
    defaults = {
        "alias": "hexar-ios",
        "app_key": "676996cd",
        "app_name": "Hexar.io iOS",
        "platform": Platform.IOS,
    }
    defaults.update(overrides)
    return Profile(**defaults)


def _write_shared_files(
    project_root: Path,
    countries: dict[str, list[str]] | None = None,
    tiers: dict[str, str] | None = None,
) -> None:
    """Write countries.yaml to the project root.

    Note: tiers parameter is accepted but ignored (legacy compat).
    """
    yaml = YAML()
    yaml.default_flow_style = False

    if countries is not None:
        stream = StringIO()
        yaml.dump(dict(countries), stream)
        (project_root / "countries.yaml").write_text(
            "# countries\n\n" + stream.getvalue(), encoding="utf-8"
        )


class TestGenerateAppSettingsBootstrap:
    """Verify generate_app_settings() bootstraps from scratch on fresh portfolio."""

    def test_creates_countries_yaml_on_bootstrap(self, tmp_path: Path) -> None:
        """Bootstrap creates countries.yaml when it doesn't exist."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        assert (tmp_path / "countries.yaml").exists()

    def test_no_tiers_yaml_on_bootstrap(self, tmp_path: Path) -> None:
        """Bootstrap does NOT create tiers.yaml (eliminated)."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        assert not (tmp_path / "tiers.yaml").exists()

    def test_creates_per_app_settings_on_bootstrap(self, tmp_path: Path) -> None:
        """Bootstrap creates settings/{alias}.yaml."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        path, _net_path, _messages = generate_app_settings(
            groups, profile, settings_dir=str(settings_dir)
        )

        assert Path(path).exists()
        assert "hexar-ios.yaml" in path

    def test_bootstrap_single_country_group_name(self, tmp_path: Path) -> None:
        """Auto-naming: single country -> country code (e.g., 'US')."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml = YAML(typ="safe")
        countries = yaml.load(tmp_path / "countries.yaml")
        assert "US" in countries

    def test_bootstrap_multi_country_group_name(self, tmp_path: Path) -> None:
        """Auto-naming: multi-country -> lowercased hyphenated group name."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["AU", "CA", "DE"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml = YAML(typ="safe")
        countries = yaml.load(tmp_path / "countries.yaml")
        assert "tier-2" in countries
        assert sorted(countries["tier-2"]) == ["AU", "CA", "DE"]

    def test_bootstrap_catch_all_group(self, tmp_path: Path) -> None:
        """Bootstrap: catch-all group with ['*'] creates per-app entry with '*' ref."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml = YAML(typ="safe")
        app_data = yaml.load(settings_dir / "hexar-ios.yaml")
        # Catch-all entry is {display_name: {countries: '*'}}
        assert len(app_data["interstitial"]) == 1
        entry = app_data["interstitial"][0]
        assert entry["All Countries"]["countries"] == "*"

    def test_bootstrap_returns_info_messages(self, tmp_path: Path) -> None:
        """Bootstrap returns info messages about created country groups."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        _path, _net_path, messages = generate_app_settings(
            groups, profile, settings_dir=str(settings_dir)
        )

        assert len(messages) > 0
        assert "Created" in messages[0]
        assert "US" in messages[0]  # country group name for single-country

    def test_bootstrap_with_empty_groups(self, tmp_path: Path) -> None:
        """Bootstrap with empty groups creates empty countries.yaml."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        profile = _make_profile()

        path, _net_path, messages = generate_app_settings([], profile, settings_dir=str(settings_dir))

        assert Path(path).exists()
        assert (tmp_path / "countries.yaml").exists()
        assert not (tmp_path / "tiers.yaml").exists()
        assert messages == []


class TestGenerateAppSettingsCountryMatching:
    """Verify country matching algorithm: exact set match, catch-all, auto-create."""

    def test_exact_set_match_reuses_existing_group(self, tmp_path: Path) -> None:
        """Group with country set matching existing group uses that group ref."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        _write_shared_files(
            tmp_path,
            countries={"US": ["US"]},
        )

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        _path, _net_path, messages = generate_app_settings(
            groups, profile, settings_dir=str(settings_dir)
        )

        # No new entries created -- exact match
        assert len(messages) == 0

        # Per-app settings references the existing group
        yaml = YAML(typ="safe")
        app_data = yaml.load(settings_dir / "hexar-ios.yaml")
        assert app_data["interstitial"] == [{"Tier 1": {"countries": "US"}}]

    def test_set_match_is_order_independent(self, tmp_path: Path) -> None:
        """Country order doesn't matter — set comparison matches regardless."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        # countries.yaml has alphabetical order
        _write_shared_files(
            tmp_path,
            countries={"high-value": ["AU", "CA", "DE", "GB"]},
        )

        # Live group returns countries in reverse order
        groups = [
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["GB", "DE", "CA", "AU"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        _path, _net_path, messages = generate_app_settings(
            groups, profile, settings_dir=str(settings_dir)
        )

        # Matched existing group despite different order — no new group created
        assert len(messages) == 0

        yaml = YAML(typ="safe")
        app_data = yaml.load(settings_dir / "hexar-ios.yaml")
        assert app_data["interstitial"] == [{"Tier 2": {"countries": "high-value"}}]

    def test_catch_all_uses_star_ref(self, tmp_path: Path) -> None:
        """Group with ['*'] gets '*' as group ref."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        _write_shared_files(
            tmp_path,
            countries={"US": ["US"]},
        )

        groups = [
            Group.model_validate({
                "groupName": "Default",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        _path, _net_path, messages = generate_app_settings(
            groups, profile, settings_dir=str(settings_dir)
        )

        # Catch-all is not a "created" entry -- just returns '*'
        assert len(messages) == 0

        yaml = YAML(typ="safe")
        app_data = yaml.load(settings_dir / "hexar-ios.yaml")
        assert app_data["interstitial"] == [{"Default": {"countries": "*"}}]

    def test_no_match_auto_creates_country_group(self, tmp_path: Path) -> None:
        """Group with unknown country set triggers auto-create of country group."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        _write_shared_files(
            tmp_path,
            countries={"US": ["US"]},
        )

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["AU", "CA", "DE"],
                "position": 2,
            }),
        ]
        profile = _make_profile()

        _path, _net_path, messages = generate_app_settings(
            groups, profile, settings_dir=str(settings_dir)
        )

        # One new country group created
        assert len(messages) == 1
        assert "tier-2" in messages[0]

        # countries.yaml updated
        yaml = YAML(typ="safe")
        countries = yaml.load(tmp_path / "countries.yaml")
        assert "tier-2" in countries
        assert sorted(countries["tier-2"]) == ["AU", "CA", "DE"]

        # No tiers.yaml created
        assert not (tmp_path / "tiers.yaml").exists()

    def test_existing_files_not_modified_when_all_match(self, tmp_path: Path) -> None:
        """When all groups match, countries.yaml is NOT rewritten."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        _write_shared_files(
            tmp_path,
            countries={"US": ["US"], "high-value": ["AU", "CA", "DE"]},
        )

        # Record file timestamps
        countries_mtime = (tmp_path / "countries.yaml").stat().st_mtime

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["AU", "CA", "DE"],
                "position": 2,
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 3,
            }),
        ]
        profile = _make_profile()

        _path, _net_path, messages = generate_app_settings(
            groups, profile, settings_dir=str(settings_dir)
        )

        assert len(messages) == 0

        # countries.yaml should not have been modified
        assert (tmp_path / "countries.yaml").stat().st_mtime == countries_mtime


class TestGenerateAppSettingsPerAppFormat:
    """Verify per-app settings file has correct format."""

    def test_per_app_file_has_alias_field(self, tmp_path: Path) -> None:
        """Per-app settings file has alias field matching the profile alias."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml = YAML(typ="safe")
        data = yaml.load(settings_dir / "hexar-ios.yaml")
        assert data["alias"] == "hexar-ios"

    def test_per_app_file_has_format_sections(self, tmp_path: Path) -> None:
        """Per-app settings file has format sections with tier lists."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 2,
            }),
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "rewarded",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml = YAML(typ="safe")
        data = yaml.load(settings_dir / "hexar-ios.yaml")
        assert "interstitial" in data
        assert "rewarded" in data
        assert isinstance(data["interstitial"], list)
        assert isinstance(data["rewarded"], list)

    def test_per_app_format_tier_order_by_position(self, tmp_path: Path) -> None:
        """Tier list within each format is ordered by position."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["GB"],
                "position": 2,
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 3,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml = YAML(typ="safe")
        data = yaml.load(settings_dir / "hexar-ios.yaml")
        # Each entry is a single-key dict: {display_name: group_ref}
        display_names = [next(iter(e)) for e in data["interstitial"]]
        assert display_names == ["Tier 1", "Tier 2", "All Countries"]

    def test_only_formats_with_live_groups_appear(self, tmp_path: Path) -> None:
        """Formats without live groups are omitted from per-app settings."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml = YAML(typ="safe")
        data = yaml.load(settings_dir / "hexar-ios.yaml")
        assert "interstitial" in data
        assert "banner" not in data
        assert "rewarded" not in data
        assert "native" not in data

    def test_returns_tuple_of_path_and_messages(self, tmp_path: Path) -> None:
        """generate_app_settings returns (str, str | None, list[str])."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        result = generate_app_settings(
            groups, profile, settings_dir=str(settings_dir)
        )
        assert isinstance(result, tuple)
        assert len(result) == 3
        path, networks_path, messages = result
        assert isinstance(path, str)
        assert networks_path is None or isinstance(networks_path, str)
        assert isinstance(messages, list)

    def test_per_format_tier_differences(self, tmp_path: Path) -> None:
        """Different formats can have different tier lists."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["GB"],
                "position": 2,
            }),
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "rewarded",
                "countries": ["US"],
                "position": 1,
            }),
            # rewarded has no Tier 2 -- different tier list
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml = YAML(typ="safe")
        data = yaml.load(settings_dir / "hexar-ios.yaml")
        int_names = [next(iter(e)) for e in data["interstitial"]]
        rew_names = [next(iter(e)) for e in data["rewarded"]]
        assert "Tier 2" in int_names
        assert "Tier 2" not in rew_names


class TestGenerateAppSettingsNewEntriesAppended:
    """Verify new entries are appended to existing shared files."""

    def test_new_entries_appended_to_countries(self, tmp_path: Path) -> None:
        """New country groups are appended to existing countries.yaml."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        _write_shared_files(
            tmp_path,
            countries={"US": ["US"]},
        )

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["GB", "DE"],
                "position": 2,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml = YAML(typ="safe")
        countries = yaml.load(tmp_path / "countries.yaml")
        # Original entry preserved
        assert "US" in countries
        assert countries["US"] == ["US"]
        # New entry added
        assert "tier-2" in countries
        assert sorted(countries["tier-2"]) == ["DE", "GB"]

    def test_no_tiers_yaml_written(self, tmp_path: Path) -> None:
        """No tiers.yaml is written (eliminated from two-layer format)."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        _write_shared_files(
            tmp_path,
            countries={"US": ["US"]},
        )

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["GB"],
                "position": 2,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        assert not (tmp_path / "tiers.yaml").exists()


class TestGenerateAppSettingsMultiApp:
    """Verify shared files work correctly across multiple app pulls."""

    def test_second_app_reuses_shared_tiers(self, tmp_path: Path) -> None:
        """Second app pull reuses tiers created by the first."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups_app1 = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 2,
            }),
        ]
        profile_app1 = _make_profile(alias="hexar-ios")

        generate_app_settings(
            groups_app1, profile_app1, settings_dir=str(settings_dir)
        )

        # Second app with same tier structure
        groups_app2 = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 2,
            }),
        ]
        profile_app2 = _make_profile(alias="ss-ios", app_key="ss123")

        _path, _net_path, messages = generate_app_settings(
            groups_app2, profile_app2, settings_dir=str(settings_dir)
        )

        # No new entries -- all matched existing
        assert len(messages) == 0

        # Both per-app files exist
        assert (settings_dir / "hexar-ios.yaml").exists()
        assert (settings_dir / "ss-ios.yaml").exists()

    def test_second_app_adds_new_country_groups(self, tmp_path: Path) -> None:
        """Second app with different country set adds new entries to countries.yaml."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups_app1 = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        profile_app1 = _make_profile(alias="hexar-ios")

        generate_app_settings(
            groups_app1, profile_app1, settings_dir=str(settings_dir)
        )

        groups_app2 = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["JP", "KR"],
                "position": 2,
            }),
        ]
        profile_app2 = _make_profile(alias="ss-ios", app_key="ss123")

        _path, _net_path, messages = generate_app_settings(
            groups_app2, profile_app2, settings_dir=str(settings_dir)
        )

        # One new country group
        assert len(messages) == 1
        assert "tier-2" in messages[0]

        yaml = YAML(typ="safe")
        countries = yaml.load(tmp_path / "countries.yaml")
        assert "US" in countries
        assert "tier-2" in countries


class TestGenerateAppSettingsNetworkFile:
    """Verify generate_app_settings writes shared networks.yaml, not per-app files."""

    def test_no_per_app_networks_file_written(self, tmp_path: Path) -> None:
        """generate_app_settings does not write per-app networks file."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

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
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        # No per-app networks file
        assert not (settings_dir / "hexar-ios-networks.yaml").exists()
        # Shared networks.yaml IS written (groups have instances)
        assert (tmp_path / "networks.yaml").exists()

    def test_no_networks_when_no_instances(self, tmp_path: Path) -> None:
        """generate_app_settings does not write networks.yaml when groups have no instances."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        # No networks.yaml when no groups have instances
        assert not (tmp_path / "networks.yaml").exists()


class TestGenerateAppSettingsKeyValueFormat:
    """Verify per-app settings use key-value format (display_name: group_ref)."""

    def test_per_app_entries_are_single_key_dicts(self, tmp_path: Path) -> None:
        """Each entry in a format section is a single-key dict."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 2,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml = YAML(typ="safe")
        data = yaml.load(settings_dir / "hexar-ios.yaml")
        for entry in data["interstitial"]:
            assert isinstance(entry, dict)
            assert len(entry) == 1

    def test_display_name_is_levelplay_group_name(self, tmp_path: Path) -> None:
        """Display name (dict key) matches the LevelPlay group name."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "My Custom Name",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml = YAML(typ="safe")
        data = yaml.load(settings_dir / "hexar-ios.yaml")
        entry = data["interstitial"][0]
        assert "My Custom Name" in entry

    def test_group_ref_is_country_group_key(self, tmp_path: Path) -> None:
        """Group ref (dict value) is a valid countries.yaml key."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        _write_shared_files(
            tmp_path,
            countries={"high-value": ["AU", "CA", "DE"]},
        )

        groups = [
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["AU", "CA", "DE"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml = YAML(typ="safe")
        data = yaml.load(settings_dir / "hexar-ios.yaml")
        entry = data["interstitial"][0]
        assert entry["Tier 2"]["countries"] == "high-value"

    def test_catch_all_ref_is_star(self, tmp_path: Path) -> None:
        """Catch-all group gets '*' as group ref."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml = YAML(typ="safe")
        data = yaml.load(settings_dir / "hexar-ios.yaml")
        entry = data["interstitial"][0]
        assert entry["All Countries"]["countries"] == "*"

    def test_catch_all_does_not_create_country_group(self, tmp_path: Path) -> None:
        """Catch-all group does NOT create an entry in countries.yaml."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        _path, _net_path, messages = generate_app_settings(
            groups, profile, settings_dir=str(settings_dir)
        )

        # No country group created for catch-all
        assert len(messages) == 0

    def test_single_country_group_ref_is_country_code(self, tmp_path: Path) -> None:
        """Auto-naming: single-country group gets country code as ref."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml = YAML(typ="safe")
        data = yaml.load(settings_dir / "hexar-ios.yaml")
        entry = data["interstitial"][0]
        assert entry["Tier 1"]["countries"] == "US"

    def test_multi_country_group_ref_is_hyphenated_name(self, tmp_path: Path) -> None:
        """Auto-naming: multi-country group gets lowercased hyphenated name."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["AU", "CA", "DE"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml = YAML(typ="safe")
        data = yaml.load(settings_dir / "hexar-ios.yaml")
        entry = data["interstitial"][0]
        assert entry["Tier 2"]["countries"] == "tier-2"


class TestWritePerAppSettingsDictFormat:
    """Verify _write_per_app_settings outputs flow-style {countries: ref, networks: ref} dicts."""

    def test_output_contains_flow_style_countries_key(self, tmp_path: Path) -> None:
        """Written YAML contains flow-style dict with 'countries' key."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        raw_text = (settings_dir / "hexar-ios.yaml").read_text()
        assert "{countries:" in raw_text

    def test_output_flow_style_with_network_ref(self, tmp_path: Path) -> None:
        """Written YAML contains flow-style dict with both countries and networks keys."""
        from admedi.engine.snapshot import _write_per_app_settings

        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        group_to_ref = {("Tier 1", frozenset(["US"])): "US"}
        group_to_network_ref = {("Tier 1", frozenset(["US"]), "interstitial"): "bidding-6"}

        _write_per_app_settings(
            settings_dir=settings_dir,
            alias="hexar-ios",
            groups=groups,
            group_to_ref=group_to_ref,
            group_to_network_ref=group_to_network_ref,
        )

        raw_text = (settings_dir / "hexar-ios.yaml").read_text()
        assert "{countries: US, networks: bidding-6}" in raw_text

    def test_output_omits_networks_when_none(self, tmp_path: Path) -> None:
        """Written YAML omits 'networks' key when network ref is None."""
        from admedi.engine.snapshot import _write_per_app_settings

        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 1,
            }),
        ]
        group_to_ref = {("All Countries", frozenset(["*"])): "*"}
        group_to_network_ref = {("All Countries", frozenset(["*"]), "interstitial"): None}

        _write_per_app_settings(
            settings_dir=settings_dir,
            alias="hexar-ios",
            groups=groups,
            group_to_ref=group_to_ref,
            group_to_network_ref=group_to_network_ref,
        )

        raw_text = (settings_dir / "hexar-ios.yaml").read_text()
        assert "networks" not in raw_text
        assert "{countries:" in raw_text

    def test_round_trip_dict_format(self, tmp_path: Path) -> None:
        """Write settings then read back with resolve_app_tiers and verify field values."""
        from admedi.engine.snapshot import _write_per_app_settings

        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        # Write shared files for resolve_app_tiers
        _write_profiles_yaml(tmp_path)
        yaml = YAML()
        yaml.default_flow_style = False
        stream = StringIO()
        yaml.dump({"US": ["US"]}, stream)
        (tmp_path / "countries.yaml").write_text(
            "# countries\n\n" + stream.getvalue(), encoding="utf-8"
        )

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 2,
            }),
        ]
        group_to_ref = {
            ("Tier 1", frozenset(["US"])): "US",
            ("All Countries", frozenset(["*"])): "*",
        }
        group_to_network_ref = {
            ("Tier 1", frozenset(["US"]), "interstitial"): "bidding-6",
            ("All Countries", frozenset(["*"]), "interstitial"): None,
        }

        _write_per_app_settings(
            settings_dir=settings_dir,
            alias="hexar-ios",
            groups=groups,
            group_to_ref=group_to_ref,
            group_to_network_ref=group_to_network_ref,
        )

        # Read back and verify
        tiers = resolve_app_tiers("hexar-ios", settings_dir=str(settings_dir))

        assert len(tiers) == 2
        by_name = {t.name: t for t in tiers}
        assert by_name["Tier 1"].network_preset == "bidding-6"
        assert by_name["Tier 1"].countries == ["US"]
        assert by_name["All Countries"].network_preset is None
        assert by_name["All Countries"].countries == ["*"]

    def test_generate_app_settings_no_network_ref(self, tmp_path: Path) -> None:
        """generate_app_settings (without group_to_network_ref) outputs countries-only dicts."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml_loader = YAML(typ="safe")
        data = yaml_loader.load(settings_dir / "hexar-ios.yaml")
        entry = data["interstitial"][0]
        # Value is a dict with only 'countries' key (no 'networks')
        assert isinstance(entry["Tier 1"], dict)
        assert "countries" in entry["Tier 1"]
        assert "networks" not in entry["Tier 1"]


class TestResolveAppTiersMultiKeyDict:
    """Verify resolve_app_tiers rejects multi-key dict entries."""

    def test_multi_key_dict_raises_error(self, tmp_path: Path) -> None:
        """Multi-key dict entry in format section raises ConfigValidationError."""
        from admedi.exceptions import ConfigValidationError

        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        countries_yaml = "US:\n  - US\n"
        profiles_yaml = """\
profiles:
  hexar-ios:
    app_key: 676996cd
    app_name: Hexar.io iOS
    platform: iOS
"""
        (tmp_path / "countries.yaml").write_text(countries_yaml)
        (tmp_path / "profiles.yaml").write_text(profiles_yaml)

        # Write a per-app file with a multi-key dict entry
        app_content = """\
alias: hexar-ios
interstitial:
  - Tier 1: US
    Tier 2: high-value
"""
        (settings_dir / "hexar-ios.yaml").write_text(app_content)

        with pytest.raises(ConfigValidationError, match="single-key mapping"):
            resolve_app_tiers("hexar-ios", settings_dir=str(settings_dir))


# ---------------------------------------------------------------------------
# Step 8: Round-trip integration test (generate -> resolve -> verify)
# ---------------------------------------------------------------------------


def _write_profiles_yaml(project_root: Path) -> None:
    """Write a standard profiles.yaml to the project root for integration tests."""
    profiles_content = """\
profiles:
  hexar-ios:
    app_key: 676996cd
    app_name: Hexar.io iOS
    platform: iOS
"""
    (project_root / "profiles.yaml").write_text(profiles_content, encoding="utf-8")


class TestRoundTripIntegration:
    """Integration test: generate_app_settings -> resolve_app_tiers -> verify.

    Proves that the settings generation and three-layer resolution are
    inverses: generate a per-app settings file from live groups, then
    load it back via resolve_app_tiers(), and verify the returned
    list[PortfolioTier] matches the original input.
    """

    def test_round_trip_single_format(self, tmp_path: Path) -> None:
        """Round-trip with a single ad format preserves tier data."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()
        _write_profiles_yaml(tmp_path)

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 2,
            }),
        ]
        profile = _make_profile()

        # Generate settings files (bootstrap mode -- creates shared files)
        settings_path, _net_path2, messages = generate_app_settings(
            groups, profile, settings_dir=str(settings_dir)
        )

        # Resolve back through the three-layer chain
        tiers = resolve_app_tiers("hexar-ios", settings_dir=str(settings_dir))

        # Verify: 2 tiers, both for interstitial
        assert len(tiers) == 2

        tier_names = [t.name for t in tiers]
        assert "Tier 1" in tier_names
        assert "All Countries" in tier_names

        # Verify positions
        by_name = {t.name: t for t in tiers}
        assert by_name["Tier 1"].position == 1
        assert by_name["All Countries"].position == 2

        # Verify countries
        assert by_name["Tier 1"].countries == ["US"]
        assert by_name["All Countries"].countries == ["*"]
        assert by_name["All Countries"].is_default is True

        # Verify ad formats
        assert by_name["Tier 1"].ad_formats == [AdFormat.INTERSTITIAL]
        assert by_name["All Countries"].ad_formats == [AdFormat.INTERSTITIAL]

    def test_round_trip_multiple_formats(self, tmp_path: Path) -> None:
        """Round-trip with multiple ad formats preserves per-format tiers."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()
        _write_profiles_yaml(tmp_path)

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 2,
            }),
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "rewarded",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "rewarded",
                "countries": ["*"],
                "position": 2,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        tiers = resolve_app_tiers("hexar-ios", settings_dir=str(settings_dir))

        # 2 tiers x 2 formats = 4 PortfolioTier objects (Option A)
        assert len(tiers) == 4

        # Group by format
        interstitial = [t for t in tiers if AdFormat.INTERSTITIAL in t.ad_formats]
        rewarded = [t for t in tiers if AdFormat.REWARDED in t.ad_formats]
        assert len(interstitial) == 2
        assert len(rewarded) == 2

        # Each format has both tier names
        int_names = {t.name for t in interstitial}
        rew_names = {t.name for t in rewarded}
        assert int_names == {"Tier 1", "All Countries"}
        assert rew_names == {"Tier 1", "All Countries"}

    def test_round_trip_per_format_tier_differences(self, tmp_path: Path) -> None:
        """Round-trip preserves per-format tier differences."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()
        _write_profiles_yaml(tmp_path)

        # Interstitial has 3 tiers, rewarded has 2 tiers (different tier lists)
        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["GB", "DE"],
                "position": 2,
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 3,
            }),
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "rewarded",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "rewarded",
                "countries": ["*"],
                "position": 2,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        tiers = resolve_app_tiers("hexar-ios", settings_dir=str(settings_dir))

        # 3 interstitial + 2 rewarded = 5 PortfolioTier objects
        assert len(tiers) == 5

        interstitial = [t for t in tiers if AdFormat.INTERSTITIAL in t.ad_formats]
        rewarded = [t for t in tiers if AdFormat.REWARDED in t.ad_formats]
        assert len(interstitial) == 3
        assert len(rewarded) == 2

    def test_round_trip_multi_country_tier(self, tmp_path: Path) -> None:
        """Round-trip preserves multi-country tier with exact country list."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()
        _write_profiles_yaml(tmp_path)

        groups = [
            Group.model_validate({
                "groupName": "EU Tier",
                "adFormat": "interstitial",
                "countries": ["DE", "FR", "GB", "NL"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 2,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        tiers = resolve_app_tiers("hexar-ios", settings_dir=str(settings_dir))

        eu_tier = [t for t in tiers if t.name == "EU Tier"]
        assert len(eu_tier) == 1
        # Country codes are preserved (may be in different order)
        assert set(eu_tier[0].countries) == {"DE", "FR", "GB", "NL"}


class TestSyncIntegration:
    """Integration test: three-layer settings -> compute_diff -> zero drift.

    Proves that settings generated from live groups, when compared against
    those same groups via compute_diff(), produce zero drift.
    """

    def test_zero_drift_after_generate(self, tmp_path: Path) -> None:
        """generate -> resolve -> compute_diff against same groups = zero drift."""
        from admedi.engine.differ import compute_diff

        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()
        _write_profiles_yaml(tmp_path)

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 2,
            }),
        ]
        profile = _make_profile()

        # Step 1: Generate settings from live groups
        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        # Step 2: Resolve settings back to tiers
        tiers = resolve_app_tiers("hexar-ios", settings_dir=str(settings_dir))

        # Step 3: Compute diff against the same groups
        report = compute_diff(tiers, groups, "676996cd", "Hexar.io iOS")

        # Zero drift: no creates, no updates
        from admedi.models.diff import DiffAction

        creates = [d for d in report.group_diffs if d.action == DiffAction.CREATE]
        updates = [d for d in report.group_diffs if d.action == DiffAction.UPDATE]
        assert len(creates) == 0, f"Unexpected creates: {[c.group_name for c in creates]}"
        assert len(updates) == 0, f"Unexpected updates: {[u.group_name for u in updates]}"

    def test_zero_drift_multi_format(self, tmp_path: Path) -> None:
        """Zero drift with multiple ad formats (interstitial + rewarded)."""
        from admedi.engine.differ import compute_diff
        from admedi.models.diff import DiffAction

        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()
        _write_profiles_yaml(tmp_path)

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 2,
            }),
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "rewarded",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "rewarded",
                "countries": ["*"],
                "position": 2,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))
        tiers = resolve_app_tiers("hexar-ios", settings_dir=str(settings_dir))
        report = compute_diff(tiers, groups, "676996cd", "Hexar.io iOS")

        creates = [d for d in report.group_diffs if d.action == DiffAction.CREATE]
        updates = [d for d in report.group_diffs if d.action == DiffAction.UPDATE]
        assert len(creates) == 0
        assert len(updates) == 0

    def test_zero_drift_with_multi_country_tiers(self, tmp_path: Path) -> None:
        """Zero drift when tiers have multi-country groups."""
        from admedi.engine.differ import compute_diff
        from admedi.models.diff import DiffAction

        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()
        _write_profiles_yaml(tmp_path)

        groups = [
            Group.model_validate({
                "groupName": "US Only",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
            }),
            Group.model_validate({
                "groupName": "EU Markets",
                "adFormat": "interstitial",
                "countries": ["DE", "FR", "GB"],
                "position": 2,
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 3,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))
        tiers = resolve_app_tiers("hexar-ios", settings_dir=str(settings_dir))
        report = compute_diff(tiers, groups, "676996cd", "Hexar.io iOS")

        creates = [d for d in report.group_diffs if d.action == DiffAction.CREATE]
        updates = [d for d in report.group_diffs if d.action == DiffAction.UPDATE]
        assert len(creates) == 0
        assert len(updates) == 0


# ---------------------------------------------------------------------------
# Step 8: Dead code verification tests
# ---------------------------------------------------------------------------


class TestDeadCodeRemoval:
    """Verify dead code from prior steps was properly removed.

    These tests confirm that functions and exports removed in Steps 5-7
    are no longer present in production code.
    """

    def test_extract_tiers_absent_from_snapshot(self) -> None:
        """_extract_tiers() was removed from snapshot.py in Step 6."""
        import admedi.engine.snapshot as snap_mod

        assert not hasattr(snap_mod, "_extract_tiers")

    def test_build_app_yaml_absent_from_snapshot(self) -> None:
        """_build_app_yaml() was removed from snapshot.py in Step 6."""
        import admedi.engine.snapshot as snap_mod

        assert not hasattr(snap_mod, "_build_app_yaml")

    def test_build_network_lookup_absent_from_snapshot(self) -> None:
        """_build_network_lookup() was removed from snapshot.py in Step 6."""
        import admedi.engine.snapshot as snap_mod

        assert not hasattr(snap_mod, "_build_network_lookup")

    def test_load_template_absent_from_engine_exports(self) -> None:
        """load_template is not exported from admedi.engine (removed in Step 5)."""
        import admedi.engine

        assert "load_template" not in admedi.engine.__all__

    def test_portfolio_config_absent_from_engine_exports(self) -> None:
        """PortfolioConfig is not exported from admedi.engine (removed in Step 5)."""
        import admedi.engine

        assert "PortfolioConfig" not in admedi.engine.__all__

    def test_load_template_absent_from_engine_engine(self) -> None:
        """load_template import was removed from engine.py in Step 5."""
        import admedi.engine.engine as engine_mod

        assert not hasattr(engine_mod, "load_template")

    def test_resolve_app_key_absent_from_cli(self) -> None:
        """_resolve_app_key() was removed from cli/main.py in Step 7."""
        import admedi.cli.main as cli_mod

        assert not hasattr(cli_mod, "_resolve_app_key")

    def test_display_show_absent_from_display(self) -> None:
        """display_show() was removed from display.py in Step 7."""
        import admedi.cli.display as disp_mod

        assert not hasattr(disp_mod, "display_show")

    def test_display_tier_warnings_absent_from_display(self) -> None:
        """display_tier_warnings() was removed from display.py in Step 7."""
        import admedi.cli.display as disp_mod

        assert not hasattr(disp_mod, "display_tier_warnings")

    def test_no_admedi_show_in_cli_commands(self) -> None:
        """'show' command is not registered in the CLI app."""
        runner = CliRunner()
        result = runner.invoke(cli_app, ["show", "--app", "hexar-ios"])
        assert result.exit_code == 2  # typer usage error for unknown command

    def test_admedi_pull_in_cli_commands(self) -> None:
        """'pull' command is registered in the CLI app."""
        runner = CliRunner()
        result = runner.invoke(cli_app, ["--help"])
        assert "pull" in result.output

    def test_no_load_tiers_settings_in_engine_exports(self) -> None:
        """load_tiers_settings is not exported from admedi.engine."""
        import admedi.engine

        assert "load_tiers_settings" not in admedi.engine.__all__


# ===========================================================================
# Step 5: generate_app_settings -- network matching and networks.yaml
# ===========================================================================


def _write_networks_yaml(project_root: Path, presets: dict) -> None:
    """Write a networks.yaml to the project root for incremental pull tests."""
    yaml = YAML()
    yaml.default_flow_style = False
    stream = StringIO()
    yaml.dump(presets, stream)
    (project_root / "networks.yaml").write_text(
        "# Admedi network presets\n\n" + stream.getvalue(), encoding="utf-8"
    )


class TestGenerateAppSettingsFreshPull:
    """Verify generate_app_settings generates networks.yaml on fresh pull."""

    def test_fresh_pull_creates_networks_yaml(self, tmp_path: Path) -> None:
        """Fresh pull with instances creates networks.yaml at project root."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "Meta", "isBidder": True},
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        profile = _make_profile()

        _path, net_path, _messages = generate_app_settings(
            groups, profile, settings_dir=str(settings_dir)
        )

        assert net_path is not None
        assert Path(net_path).exists()
        assert "networks.yaml" in net_path

    def test_fresh_pull_networks_has_preset_entries(self, tmp_path: Path) -> None:
        """Fresh pull writes preset entries with network and bidder fields."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "Meta", "isBidder": True},
                    {"id": 2, "name": "Default", "networkName": "AppLovin", "isBidder": False, "groupRate": 1.5},
                ],
            }),
        ]
        profile = _make_profile()

        _path, net_path, _messages = generate_app_settings(
            groups, profile, settings_dir=str(settings_dir)
        )

        yaml = YAML(typ="safe")
        presets = yaml.load(Path(net_path))
        assert isinstance(presets, dict)
        assert len(presets) == 1

        preset_name = next(iter(presets))
        entries = presets[preset_name]
        assert any(e["network"] == "Meta" and e["bidder"] is True for e in entries)
        assert any(e["network"] == "AppLovin" and e["bidder"] is False for e in entries)

    def test_fresh_pull_settings_has_network_ref(self, tmp_path: Path) -> None:
        """Fresh pull writes settings with networks ref pointing to the preset."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "Meta", "isBidder": True},
                ],
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml_loader = YAML(typ="safe")
        data = yaml_loader.load(settings_dir / "hexar-ios.yaml")
        entry = data["interstitial"][0]
        # Should have a networks key pointing to a preset name
        assert "networks" in entry["Tier 1"]
        assert isinstance(entry["Tier 1"]["networks"], str)

    def test_fresh_pull_returns_3_tuple(self, tmp_path: Path) -> None:
        """generate_app_settings returns (str, str | None, list[str])."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "Meta", "isBidder": True},
                ],
            }),
        ]
        profile = _make_profile()

        result = generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        assert isinstance(result, tuple)
        assert len(result) == 3
        settings_path, networks_path, messages = result
        assert isinstance(settings_path, str)
        assert isinstance(networks_path, str)
        assert isinstance(messages, list)

    def test_fresh_pull_no_instances_returns_none_networks(self, tmp_path: Path) -> None:
        """Groups without instances return None for networks_path."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "native",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        _path, net_path, _messages = generate_app_settings(
            groups, profile, settings_dir=str(settings_dir)
        )

        assert net_path is None


class TestGenerateAppSettingsIncrementalPull:
    """Verify incremental pull matches existing presets by signature."""

    def test_incremental_pull_reuses_existing_preset(self, tmp_path: Path) -> None:
        """When existing networks.yaml has matching preset, reuse its name."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        # Write existing networks.yaml with a preset
        existing_presets = {
            "my-custom-name": [
                {"network": "Meta", "bidder": True},
                {"network": "ironSource", "bidder": True},
            ],
        }
        _write_networks_yaml(tmp_path, existing_presets)

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "Meta", "isBidder": True},
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        # Check settings file references the existing preset name
        yaml_loader = YAML(typ="safe")
        data = yaml_loader.load(settings_dir / "hexar-ios.yaml")
        entry = data["interstitial"][0]
        assert entry["Tier 1"]["networks"] == "my-custom-name"

        # Check networks.yaml was not duplicated (still has 1 preset)
        presets = yaml_loader.load(tmp_path / "networks.yaml")
        assert len(presets) == 1
        assert "my-custom-name" in presets

    def test_incremental_pull_adds_new_preset(self, tmp_path: Path) -> None:
        """New waterfall pattern creates a new preset alongside existing ones."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        # Write existing networks.yaml with one preset
        existing_presets = {
            "existing-preset": [
                {"network": "Meta", "bidder": True},
            ],
        }
        _write_networks_yaml(tmp_path, existing_presets)

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "UnityAds", "isBidder": True},
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml_loader = YAML(typ="safe")
        presets = yaml_loader.load(tmp_path / "networks.yaml")
        # Should have 2 presets: existing + new
        assert len(presets) == 2
        assert "existing-preset" in presets


class TestGenerateAppSettingsNoInstances:
    """Verify groups without instances get None network ref."""

    def test_no_instances_omits_networks_key(self, tmp_path: Path) -> None:
        """Group with no instances omits networks key from settings entry."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Native Group",
                "adFormat": "native",
                "countries": ["US"],
                "position": 1,
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml_loader = YAML(typ="safe")
        data = yaml_loader.load(settings_dir / "hexar-ios.yaml")
        entry = data["native"][0]
        assert "networks" not in entry["Native Group"]
        assert "countries" in entry["Native Group"]

    def test_mixed_groups_with_and_without_instances(self, tmp_path: Path) -> None:
        """Mix of groups with and without instances produces correct network refs."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "Meta", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "All Countries",
                "adFormat": "interstitial",
                "countries": ["*"],
                "position": 2,
                # No instances
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml_loader = YAML(typ="safe")
        data = yaml_loader.load(settings_dir / "hexar-ios.yaml")
        entries = data["interstitial"]
        by_name = {next(iter(e)): e[next(iter(e))] for e in entries}
        assert "networks" in by_name["Tier 1"]
        assert "networks" not in by_name["All Countries"]


class TestGenerateAppSettingsSharedPresets:
    """Verify groups with identical waterfalls share the same preset."""

    def test_same_waterfall_shares_preset(self, tmp_path: Path) -> None:
        """Two groups with identical instances share the same network preset name."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "Meta", "isBidder": True},
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["GB"],
                "position": 2,
                "instances": [
                    {"id": 3, "name": "Bidding", "networkName": "Meta", "isBidder": True},
                    {"id": 4, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml_loader = YAML(typ="safe")
        data = yaml_loader.load(settings_dir / "hexar-ios.yaml")
        entries = data["interstitial"]
        by_name = {next(iter(e)): e[next(iter(e))] for e in entries}
        assert by_name["Tier 1"]["networks"] == by_name["Tier 2"]["networks"]

        # Only 1 preset in networks.yaml
        presets = yaml_loader.load(tmp_path / "networks.yaml")
        assert len(presets) == 1

    def test_different_waterfalls_get_different_presets(self, tmp_path: Path) -> None:
        """Two groups with different instances get different preset names."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "Meta", "isBidder": True},
                ],
            }),
            Group.model_validate({
                "groupName": "Tier 2",
                "adFormat": "interstitial",
                "countries": ["GB"],
                "position": 2,
                "instances": [
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml_loader = YAML(typ="safe")
        data = yaml_loader.load(settings_dir / "hexar-ios.yaml")
        entries = data["interstitial"]
        by_name = {next(iter(e)): e[next(iter(e))] for e in entries}
        assert by_name["Tier 1"]["networks"] != by_name["Tier 2"]["networks"]

        # 2 presets in networks.yaml
        presets = yaml_loader.load(tmp_path / "networks.yaml")
        assert len(presets) == 2


class TestGenerateAppSettingsAutoNaming:
    """Verify auto-naming produces readable preset names."""

    def test_bidders_only_few_uses_plus_names(self, tmp_path: Path) -> None:
        """<=3 bidders produce plus-joined first-word names (e.g., 'meta+ironsource')."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "Meta", "isBidder": True},
                    {"id": 2, "name": "Bidding", "networkName": "ironSource", "isBidder": True},
                ],
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml_loader = YAML(typ="safe")
        presets = yaml_loader.load(tmp_path / "networks.yaml")
        preset_name = next(iter(presets))
        assert "+" in preset_name
        assert "meta" in preset_name.lower()

    def test_bidders_many_uses_count(self, tmp_path: Path) -> None:
        """>3 bidders produce 'bidding-N' name."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": i, "name": f"B{i}", "networkName": name, "isBidder": True}
                    for i, name in enumerate(
                        ["Meta", "ironSource", "UnityAds", "AppLovin", "InMobi", "Pangle", "Vungle"],
                        start=1,
                    )
                ],
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml_loader = YAML(typ="safe")
        presets = yaml_loader.load(tmp_path / "networks.yaml")
        preset_name = next(iter(presets))
        assert preset_name == "bidding-7"

    def test_name_collision_resolved_with_suffix(self, tmp_path: Path) -> None:
        """Name collision appends '-2' suffix."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        # Write existing networks.yaml with a preset that would collide
        existing_presets = {
            "meta": [{"network": "Meta", "bidder": True}],
        }
        _write_networks_yaml(tmp_path, existing_presets)

        # Create a group whose auto-name would be "meta" but with different signature
        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "Meta", "isBidder": True},
                    {"id": 2, "name": "Default", "networkName": "AppLovin", "isBidder": False, "groupRate": 1.0},
                ],
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml_loader = YAML(typ="safe")
        presets = yaml_loader.load(tmp_path / "networks.yaml")
        assert len(presets) == 2
        # Original name preserved, new one has different name
        assert "meta" in presets
        # The new preset has a name that doesn't collide with "meta"
        new_name = [n for n in presets if n != "meta"][0]
        assert new_name != "meta"

    def test_networks_yaml_no_root_key_wrapper(self, tmp_path: Path) -> None:
        """networks.yaml uses top-level preset keys (no root key wrapper)."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "Meta", "isBidder": True},
                ],
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml_loader = YAML(typ="safe")
        presets = yaml_loader.load(tmp_path / "networks.yaml")
        # Top-level keys should be preset names, not "presets" wrapper
        assert "presets" not in presets
        assert any(isinstance(v, list) for v in presets.values())

    def test_manual_rate_preserved_in_preset(self, tmp_path: Path) -> None:
        """Manual instance rate is preserved in the preset entry."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        groups = [
            Group.model_validate({
                "groupName": "Tier 1",
                "adFormat": "interstitial",
                "countries": ["US"],
                "position": 1,
                "instances": [
                    {"id": 1, "name": "Bidding", "networkName": "Meta", "isBidder": True},
                    {"id": 2, "name": "Default", "networkName": "AppLovin", "isBidder": False, "groupRate": 1.5},
                ],
            }),
        ]
        profile = _make_profile()

        generate_app_settings(groups, profile, settings_dir=str(settings_dir))

        yaml_loader = YAML(typ="safe")
        presets = yaml_loader.load(tmp_path / "networks.yaml")
        preset_name = next(iter(presets))
        entries = presets[preset_name]
        manual = [e for e in entries if not e["bidder"]][0]
        assert manual["rate"] == 1.5


class TestEngineExportsCorrectness:
    """Verify engine/__init__.py exports are complete and correct."""

    def test_engine_all_has_nine_entries(self) -> None:
        """Engine __all__ has exactly 9 entries."""
        import admedi.engine

        assert len(admedi.engine.__all__) == 9

    def test_engine_exports_all_expected_names(self) -> None:
        """Engine __all__ contains all expected names."""
        import admedi.engine

        expected = {
            "Applier",
            "ConfigEngine",
            "Profile",
            "SyncScope",
            "compute_diff",
            "load_country_groups",
            "load_network_presets",
            "load_profiles",
            "resolve_app_tiers",
        }
        assert set(admedi.engine.__all__) == expected

    def test_load_country_groups_importable_from_engine(self) -> None:
        """load_country_groups is importable from admedi.engine."""
        from admedi.engine import load_country_groups

        assert callable(load_country_groups)

    def test_load_tiers_definition_not_in_engine_all(self) -> None:
        """load_tiers_definition is not exported from admedi.engine (removed)."""
        import admedi.engine

        assert "load_tiers_definition" not in admedi.engine.__all__

    def test_load_profiles_importable_from_engine(self) -> None:
        """load_profiles is importable from admedi.engine."""
        from admedi.engine import load_profiles

        assert callable(load_profiles)

    def test_resolve_app_tiers_importable_from_engine(self) -> None:
        """resolve_app_tiers is importable from admedi.engine."""
        from admedi.engine import resolve_app_tiers

        assert callable(resolve_app_tiers)

    def test_profile_importable_from_engine(self) -> None:
        """Profile is importable from admedi.engine."""
        from admedi.engine import Profile

        assert Profile is not None

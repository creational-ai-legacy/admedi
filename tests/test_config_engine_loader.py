"""Tests for the config engine YAML template loader.

Covers:
- Valid Shelf Sort template parsing
- Missing file handling
- Malformed YAML handling
- Each validation rejection via PortfolioConfig validators
- Error message readability
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from admedi.engine.loader import (
    Profile,
    load_country_groups,
    load_network_presets,
    load_profiles,
    load_template,
    load_tiers_definition,
    load_tiers_settings,
    resolve_app_tiers,
)
from admedi.exceptions import ConfigValidationError
from admedi.models.enums import AdFormat, Mediator, Platform
from admedi.models.portfolio import PortfolioConfig, PortfolioTier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path: Path, content: str, filename: str = "template.yaml") -> Path:
    """Write YAML content to a temp file and return the path."""
    file_path = tmp_path / filename
    file_path.write_text(dedent(content))
    return file_path


# A valid Shelf Sort YAML fixture matching the plan specification.
VALID_SHELF_SORT_YAML = """\
schema_version: 1
mediator: levelplay

portfolio:
  - app_key: "1f93a90ad"
    name: "Shelf Sort iOS"
    platform: iOS
  - app_key: "2a84b91be"
    name: "Shelf Sort Android"
    platform: Android
  - app_key: "3c75c82cf"
    name: "Shelf Sort Amazon"
    platform: Amazon

tiers:
  - name: "Tier 1"
    countries: ["US"]
    position: 1
    ad_formats: [interstitial, rewarded]

  - name: "Tier 2"
    countries: ["AU", "CA", "DE", "GB", "JP", "NZ", "KR", "TW"]
    position: 2
    ad_formats: [interstitial, rewarded]

  - name: "Tier 3"
    countries: ["FR", "NL"]
    position: 3
    ad_formats: [interstitial, rewarded]

  - name: "All Countries"
    countries: ["*"]
    position: 4
    is_default: true
    ad_formats: [banner, interstitial, rewarded, native]
"""


# ---------------------------------------------------------------------------
# Test Classes
# ---------------------------------------------------------------------------


class TestLoadTemplateValid:
    """Tests for successful template loading."""

    def test_valid_shelf_sort_returns_portfolio_config(self, tmp_path: Path) -> None:
        """Valid Shelf Sort YAML returns a PortfolioConfig."""
        file_path = _write_yaml(tmp_path, VALID_SHELF_SORT_YAML)

        config = load_template(file_path)

        assert isinstance(config, PortfolioConfig)

    def test_valid_shelf_sort_schema_version(self, tmp_path: Path) -> None:
        """Returned config has schema_version=1."""
        file_path = _write_yaml(tmp_path, VALID_SHELF_SORT_YAML)

        config = load_template(file_path)

        assert config.schema_version == 1

    def test_valid_shelf_sort_mediator(self, tmp_path: Path) -> None:
        """Returned config has mediator=LEVELPLAY."""
        file_path = _write_yaml(tmp_path, VALID_SHELF_SORT_YAML)

        config = load_template(file_path)

        assert config.mediator == Mediator.LEVELPLAY

    def test_valid_shelf_sort_portfolio_count(self, tmp_path: Path) -> None:
        """Returned config has 3 portfolio apps."""
        file_path = _write_yaml(tmp_path, VALID_SHELF_SORT_YAML)

        config = load_template(file_path)

        assert len(config.portfolio) == 3

    def test_valid_shelf_sort_portfolio_platforms(self, tmp_path: Path) -> None:
        """Portfolio apps have correct platforms."""
        file_path = _write_yaml(tmp_path, VALID_SHELF_SORT_YAML)

        config = load_template(file_path)
        platforms = {app.platform for app in config.portfolio}

        assert platforms == {Platform.IOS, Platform.ANDROID, Platform.AMAZON}

    def test_valid_shelf_sort_tier_count(self, tmp_path: Path) -> None:
        """Returned config has 4 tiers."""
        file_path = _write_yaml(tmp_path, VALID_SHELF_SORT_YAML)

        config = load_template(file_path)

        assert len(config.tiers) == 4

    def test_valid_shelf_sort_tier_1_countries(self, tmp_path: Path) -> None:
        """Tier 1 has countries=["US"]."""
        file_path = _write_yaml(tmp_path, VALID_SHELF_SORT_YAML)

        config = load_template(file_path)
        tier_1 = config.tiers[0]

        assert tier_1.name == "Tier 1"
        assert tier_1.countries == ["US"]

    def test_valid_shelf_sort_tier_1_ad_formats(self, tmp_path: Path) -> None:
        """Tier 1 has ad_formats=[interstitial, rewarded]."""
        file_path = _write_yaml(tmp_path, VALID_SHELF_SORT_YAML)

        config = load_template(file_path)
        tier_1 = config.tiers[0]

        assert tier_1.ad_formats == [AdFormat.INTERSTITIAL, AdFormat.REWARDED]

    def test_valid_shelf_sort_default_tier(self, tmp_path: Path) -> None:
        """Default tier is 'All Countries' with countries=["*"]."""
        file_path = _write_yaml(tmp_path, VALID_SHELF_SORT_YAML)

        config = load_template(file_path)
        default_tiers = [t for t in config.tiers if t.is_default]

        assert len(default_tiers) == 1
        assert default_tiers[0].name == "All Countries"
        assert default_tiers[0].countries == ["*"]

    def test_valid_shelf_sort_default_tier_ad_formats(self, tmp_path: Path) -> None:
        """Default tier has all 4 ad formats."""
        file_path = _write_yaml(tmp_path, VALID_SHELF_SORT_YAML)

        config = load_template(file_path)
        default_tier = next(t for t in config.tiers if t.is_default)

        assert set(default_tier.ad_formats) == {
            AdFormat.BANNER,
            AdFormat.INTERSTITIAL,
            AdFormat.REWARDED,
            AdFormat.NATIVE,
        }

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        """load_template accepts a string path (not just Path objects)."""
        file_path = _write_yaml(tmp_path, VALID_SHELF_SORT_YAML)

        config = load_template(str(file_path))

        assert isinstance(config, PortfolioConfig)


class TestLoadTemplateFileErrors:
    """Tests for file-level error handling."""

    def test_missing_file_raises_file_not_found_error(self) -> None:
        """Non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Template file not found"):
            load_template("nonexistent.yaml")

    def test_missing_file_error_includes_path(self) -> None:
        """FileNotFoundError message includes the file path."""
        with pytest.raises(FileNotFoundError, match="nonexistent.yaml"):
            load_template("nonexistent.yaml")


class TestLoadTemplateMalformedYAML:
    """Tests for malformed YAML handling."""

    def test_malformed_yaml_raises_config_validation_error(self, tmp_path: Path) -> None:
        """Malformed YAML raises ConfigValidationError, not raw YAMLError."""
        file_path = _write_yaml(tmp_path, ":\n  invalid: [yaml\n  missing bracket")

        with pytest.raises(ConfigValidationError, match="Malformed YAML"):
            load_template(file_path)

    def test_malformed_yaml_error_is_admedi_exception(self, tmp_path: Path) -> None:
        """ConfigValidationError for malformed YAML is an AdmediError subclass."""
        file_path = _write_yaml(tmp_path, ":\n  invalid: [yaml\n  missing bracket")

        with pytest.raises(ConfigValidationError) as exc_info:
            load_template(file_path)

        assert exc_info.value.detail is not None

    def test_empty_file_raises_config_validation_error(self, tmp_path: Path) -> None:
        """Empty YAML file raises ConfigValidationError."""
        file_path = _write_yaml(tmp_path, "")

        with pytest.raises(ConfigValidationError, match="empty"):
            load_template(file_path)

    def test_yaml_list_raises_config_validation_error(self, tmp_path: Path) -> None:
        """YAML that parses to a list (not a mapping) raises ConfigValidationError."""
        file_path = _write_yaml(tmp_path, "- item1\n- item2\n")

        with pytest.raises(ConfigValidationError, match="mapping"):
            load_template(file_path)


class TestLoadTemplateValidationErrors:
    """Tests for pydantic validation failures wrapped in ConfigValidationError."""

    def test_unsupported_schema_version_raises_error(self, tmp_path: Path) -> None:
        """schema_version=2 raises ConfigValidationError."""
        yaml_content = VALID_SHELF_SORT_YAML.replace(
            "schema_version: 1", "schema_version: 2"
        )
        file_path = _write_yaml(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="schema_version"):
            load_template(file_path)

    def test_empty_portfolio_raises_error(self, tmp_path: Path) -> None:
        """Empty portfolio list raises ConfigValidationError."""
        yaml_content = """\
        schema_version: 1
        mediator: levelplay
        portfolio: []
        tiers:
          - name: "All Countries"
            countries: ["*"]
            position: 1
            is_default: true
            ad_formats: [interstitial]
        """
        file_path = _write_yaml(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="portfolio"):
            load_template(file_path)

    def test_missing_default_tier_raises_error(self, tmp_path: Path) -> None:
        """Template with no default tier raises ConfigValidationError."""
        yaml_content = """\
        schema_version: 1
        mediator: levelplay
        portfolio:
          - app_key: "abc123"
            name: "Test App"
            platform: iOS
        tiers:
          - name: "Tier 1"
            countries: ["US"]
            position: 1
            ad_formats: [interstitial]
        """
        file_path = _write_yaml(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="default"):
            load_template(file_path)

    def test_invalid_country_code_lowercase_raises_error(self, tmp_path: Path) -> None:
        """Lowercase country code (e.g., 'us') raises ConfigValidationError."""
        yaml_content = """\
        schema_version: 1
        mediator: levelplay
        portfolio:
          - app_key: "abc123"
            name: "Test App"
            platform: iOS
        tiers:
          - name: "Tier 1"
            countries: ["us"]
            position: 1
            ad_formats: [interstitial]
          - name: "All Countries"
            countries: ["*"]
            position: 2
            is_default: true
            ad_formats: [interstitial]
        """
        file_path = _write_yaml(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="country"):
            load_template(file_path)

    def test_duplicate_country_same_format_raises_error(self, tmp_path: Path) -> None:
        """Duplicate country in same format scope raises ConfigValidationError."""
        yaml_content = """\
        schema_version: 1
        mediator: levelplay
        portfolio:
          - app_key: "abc123"
            name: "Test App"
            platform: iOS
        tiers:
          - name: "Tier 1"
            countries: ["US"]
            position: 1
            ad_formats: [interstitial]
          - name: "Tier 2"
            countries: ["US"]
            position: 2
            ad_formats: [interstitial]
          - name: "All Countries"
            countries: ["*"]
            position: 3
            is_default: true
            ad_formats: [interstitial]
        """
        file_path = _write_yaml(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="Duplicate country"):
            load_template(file_path)

    def test_duplicate_position_same_format_raises_error(self, tmp_path: Path) -> None:
        """Duplicate position in same format scope raises ConfigValidationError."""
        yaml_content = """\
        schema_version: 1
        mediator: levelplay
        portfolio:
          - app_key: "abc123"
            name: "Test App"
            platform: iOS
        tiers:
          - name: "Tier 1"
            countries: ["US"]
            position: 1
            ad_formats: [interstitial]
          - name: "Tier 2"
            countries: ["GB"]
            position: 1
            ad_formats: [interstitial]
          - name: "All Countries"
            countries: ["*"]
            position: 2
            is_default: true
            ad_formats: [interstitial]
        """
        file_path = _write_yaml(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="Duplicate position"):
            load_template(file_path)

    def test_rewarded_video_with_levelplay_raises_error(self, tmp_path: Path) -> None:
        """rewardedVideo format with levelplay mediator raises ConfigValidationError."""
        yaml_content = """\
        schema_version: 1
        mediator: levelplay
        portfolio:
          - app_key: "abc123"
            name: "Test App"
            platform: iOS
        tiers:
          - name: "All Countries"
            countries: ["*"]
            position: 1
            is_default: true
            ad_formats: [rewardedVideo]
        """
        file_path = _write_yaml(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="rewardedVideo"):
            load_template(file_path)

    def test_rewarded_video_error_mentions_incompatibility(self, tmp_path: Path) -> None:
        """Error for rewardedVideo mentions the incompatibility with levelplay."""
        yaml_content = """\
        schema_version: 1
        mediator: levelplay
        portfolio:
          - app_key: "abc123"
            name: "Test App"
            platform: iOS
        tiers:
          - name: "All Countries"
            countries: ["*"]
            position: 1
            is_default: true
            ad_formats: [rewardedVideo]
        """
        file_path = _write_yaml(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError) as exc_info:
            load_template(file_path)

        assert "incompatible" in exc_info.value.message.lower() or \
               "levelplay" in exc_info.value.message.lower()


class TestLoadTemplateErrorMessages:
    """Tests verifying error messages are human-readable."""

    def test_validation_error_has_message_attribute(self, tmp_path: Path) -> None:
        """ConfigValidationError has a human-readable .message attribute."""
        yaml_content = """\
        schema_version: 1
        mediator: levelplay
        portfolio: []
        tiers:
          - name: "All Countries"
            countries: ["*"]
            position: 1
            is_default: true
            ad_formats: [interstitial]
        """
        file_path = _write_yaml(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError) as exc_info:
            load_template(file_path)

        assert exc_info.value.message  # Non-empty
        assert "template.yaml" in exc_info.value.message  # Includes filename

    def test_validation_error_has_detail(self, tmp_path: Path) -> None:
        """ConfigValidationError includes the full pydantic detail."""
        yaml_content = """\
        schema_version: 1
        mediator: levelplay
        portfolio: []
        tiers:
          - name: "All Countries"
            countries: ["*"]
            position: 1
            is_default: true
            ad_formats: [interstitial]
        """
        file_path = _write_yaml(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError) as exc_info:
            load_template(file_path)

        assert exc_info.value.detail is not None

    def test_validation_error_not_raw_pydantic_traceback(self, tmp_path: Path) -> None:
        """Error message is human-readable, not a raw pydantic traceback."""
        yaml_content = """\
        schema_version: 2
        mediator: levelplay
        portfolio:
          - app_key: "abc123"
            name: "Test App"
            platform: iOS
        tiers:
          - name: "All Countries"
            countries: ["*"]
            position: 1
            is_default: true
            ad_formats: [interstitial]
        """
        file_path = _write_yaml(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError) as exc_info:
            load_template(file_path)

        message = exc_info.value.message
        # Should be a clean sentence, not a raw pydantic ValidationError repr
        assert "Validation error" in message
        assert "template.yaml" in message

    def test_malformed_yaml_error_includes_filename(self, tmp_path: Path) -> None:
        """Malformed YAML error includes the filename for identification."""
        file_path = _write_yaml(tmp_path, ":\n  bad: [yaml\n  no bracket", "my-config.yaml")

        with pytest.raises(ConfigValidationError) as exc_info:
            load_template(file_path)

        assert "my-config.yaml" in exc_info.value.message


# ---------------------------------------------------------------------------
# Shelf Sort Template File Tests (Step 4)
# ---------------------------------------------------------------------------

# Absolute path to the real example template, resolved relative to the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SHELF_SORT_TEMPLATE = _REPO_ROOT / "examples" / "shelf-sort-tiers.yaml"


class TestShelfSortTemplate:
    """Tests that the real Shelf Sort example template validates through the Loader."""

    def test_load_template_succeeds(self) -> None:
        """load_template() on the real Shelf Sort template returns without errors."""
        config = load_template(_SHELF_SORT_TEMPLATE)

        assert isinstance(config, PortfolioConfig)

    def test_schema_version_is_1(self) -> None:
        """Shelf Sort template has schema_version=1."""
        config = load_template(_SHELF_SORT_TEMPLATE)

        assert config.schema_version == 1

    def test_mediator_is_levelplay(self) -> None:
        """Shelf Sort template has mediator=LEVELPLAY."""
        config = load_template(_SHELF_SORT_TEMPLATE)

        assert config.mediator == Mediator.LEVELPLAY

    def test_portfolio_has_6_apps(self) -> None:
        """Shelf Sort portfolio has 6 apps (2 variants x 3 platforms)."""
        config = load_template(_SHELF_SORT_TEMPLATE)

        assert len(config.portfolio) == 6

    def test_portfolio_covers_all_platforms(self) -> None:
        """Portfolio apps span all 3 platforms."""
        config = load_template(_SHELF_SORT_TEMPLATE)
        platforms = {app.platform for app in config.portfolio}

        assert platforms == {Platform.IOS, Platform.ANDROID, Platform.AMAZON}

    def test_portfolio_has_two_ios_apps(self) -> None:
        """Portfolio includes 2 iOS apps (Shelf Sort + Shelf Sort Plus)."""
        config = load_template(_SHELF_SORT_TEMPLATE)
        ios_apps = [app for app in config.portfolio if app.platform == Platform.IOS]

        assert len(ios_apps) == 2

    def test_has_4_tiers(self) -> None:
        """Shelf Sort template defines 4 tiers."""
        config = load_template(_SHELF_SORT_TEMPLATE)

        assert len(config.tiers) == 4

    def test_tier_1_countries(self) -> None:
        """Tier 1 targets US only."""
        config = load_template(_SHELF_SORT_TEMPLATE)
        tier_1 = config.tiers[0]

        assert tier_1.name == "Tier 1"
        assert tier_1.countries == ["US"]

    def test_tier_1_ad_formats(self) -> None:
        """Tier 1 has ad_formats=[interstitial, rewarded]."""
        config = load_template(_SHELF_SORT_TEMPLATE)
        tier_1 = config.tiers[0]

        assert tier_1.ad_formats == [AdFormat.INTERSTITIAL, AdFormat.REWARDED]

    def test_tier_2_countries(self) -> None:
        """Tier 2 targets 8 high-value international markets."""
        config = load_template(_SHELF_SORT_TEMPLATE)
        tier_2 = config.tiers[1]

        assert tier_2.name == "Tier 2"
        assert tier_2.countries == ["AU", "CA", "DE", "GB", "JP", "NZ", "KR", "TW"]

    def test_tier_3_countries(self) -> None:
        """Tier 3 targets FR and NL."""
        config = load_template(_SHELF_SORT_TEMPLATE)
        tier_3 = config.tiers[2]

        assert tier_3.name == "Tier 3"
        assert tier_3.countries == ["FR", "NL"]

    def test_default_tier_is_all_countries(self) -> None:
        """The default tier has countries=['*'] and is_default=True."""
        config = load_template(_SHELF_SORT_TEMPLATE)
        default_tiers = [t for t in config.tiers if t.is_default]

        assert len(default_tiers) == 1
        assert default_tiers[0].name == "All Countries"
        assert default_tiers[0].countries == ["*"]

    def test_default_tier_has_all_4_formats(self) -> None:
        """The default tier includes banner, interstitial, rewarded, and native."""
        config = load_template(_SHELF_SORT_TEMPLATE)
        default_tier = next(t for t in config.tiers if t.is_default)

        assert set(default_tier.ad_formats) == {
            AdFormat.BANNER,
            AdFormat.INTERSTITIAL,
            AdFormat.REWARDED,
            AdFormat.NATIVE,
        }

    def test_default_tier_position_is_4(self) -> None:
        """The default tier is at position 4 (lowest priority)."""
        config = load_template(_SHELF_SORT_TEMPLATE)
        default_tier = next(t for t in config.tiers if t.is_default)

        assert default_tier.position == 4

    def test_template_file_contains_yaml_comments(self) -> None:
        """The YAML template file contains human-readable comments."""
        content = _SHELF_SORT_TEMPLATE.read_text()

        assert content.count("#") >= 5, "Template should contain multiple YAML comments"
        assert "Shelf Sort" in content, "Template should reference Shelf Sort in comments"


# ---------------------------------------------------------------------------
# load_tiers_settings() Tests
# ---------------------------------------------------------------------------

# Helper to write a tiers settings file in the expected directory structure.
def _write_tiers_file(
    tmp_path: Path, content: str, alias: str = "test-app"
) -> str:
    """Write a tiers settings YAML file and return the settings_dir path."""
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir(exist_ok=True)
    file_path = settings_dir / f"{alias}-tiers.yaml"
    file_path.write_text(dedent(content))
    return str(settings_dir)


# A valid tiers settings YAML with multiple tiers including formats.
VALID_TIERS_SETTINGS_YAML = """\
tiers:
  Tier 1:
    countries:
    - US
    formats:
    - interstitial
    - rewarded
  Tier 2:
    countries:
    - AU
    - CA
    - DE
    - GB
    formats:
    - interstitial
    - rewarded
  Tier 3:
    countries:
    - FR
    - NL
    formats:
    - interstitial
    - rewarded
  All Countries:
    countries:
    - '*'
    formats:
    - banner
    - interstitial
    - native
    - rewarded
"""


class TestLoadTiersSettingsValid:
    """Tests for successful tiers settings loading."""

    def test_returns_list_of_portfolio_tier(self, tmp_path: Path) -> None:
        """Valid tiers file returns a list of PortfolioTier objects."""
        settings_dir = _write_tiers_file(tmp_path, VALID_TIERS_SETTINGS_YAML)

        result = load_tiers_settings("test-app", settings_dir=settings_dir)

        assert isinstance(result, list)
        assert all(isinstance(t, PortfolioTier) for t in result)

    def test_correct_tier_count(self, tmp_path: Path) -> None:
        """Valid tiers file with 4 tiers returns 4 PortfolioTier objects."""
        settings_dir = _write_tiers_file(tmp_path, VALID_TIERS_SETTINGS_YAML)

        result = load_tiers_settings("test-app", settings_dir=settings_dir)

        assert len(result) == 4

    def test_tier_names_from_dict_keys(self, tmp_path: Path) -> None:
        """Tier names come from the dict keys in the YAML."""
        settings_dir = _write_tiers_file(tmp_path, VALID_TIERS_SETTINGS_YAML)

        result = load_tiers_settings("test-app", settings_dir=settings_dir)

        names = [t.name for t in result]
        assert names == ["Tier 1", "Tier 2", "Tier 3", "All Countries"]

    def test_position_from_dict_order(self, tmp_path: Path) -> None:
        """Position is 1-based index from overall dict order."""
        settings_dir = _write_tiers_file(tmp_path, VALID_TIERS_SETTINGS_YAML)

        result = load_tiers_settings("test-app", settings_dir=settings_dir)

        positions = [t.position for t in result]
        assert positions == [1, 2, 3, 4]

    def test_countries_preserved(self, tmp_path: Path) -> None:
        """Countries field is preserved correctly."""
        settings_dir = _write_tiers_file(tmp_path, VALID_TIERS_SETTINGS_YAML)

        result = load_tiers_settings("test-app", settings_dir=settings_dir)

        assert result[0].countries == ["US"]
        assert result[1].countries == ["AU", "CA", "DE", "GB"]
        assert result[3].countries == ["*"]

    def test_is_default_true_for_wildcard(self, tmp_path: Path) -> None:
        """Tier with '*' in countries has is_default=True."""
        settings_dir = _write_tiers_file(tmp_path, VALID_TIERS_SETTINGS_YAML)

        result = load_tiers_settings("test-app", settings_dir=settings_dir)

        # "All Countries" has '*' -> is_default=True
        all_countries = result[3]
        assert all_countries.is_default is True

    def test_is_default_false_for_non_wildcard(self, tmp_path: Path) -> None:
        """Tiers without '*' in countries have is_default=False."""
        settings_dir = _write_tiers_file(tmp_path, VALID_TIERS_SETTINGS_YAML)

        result = load_tiers_settings("test-app", settings_dir=settings_dir)

        assert result[0].is_default is False
        assert result[1].is_default is False
        assert result[2].is_default is False

    def test_ad_formats_are_enum_instances(self, tmp_path: Path) -> None:
        """ad_formats contains AdFormat enum instances, not strings."""
        settings_dir = _write_tiers_file(tmp_path, VALID_TIERS_SETTINGS_YAML)

        result = load_tiers_settings("test-app", settings_dir=settings_dir)

        for tier in result:
            for fmt in tier.ad_formats:
                assert isinstance(fmt, AdFormat)

    def test_tier_1_ad_formats(self, tmp_path: Path) -> None:
        """Tier 1 has ad_formats=[interstitial, rewarded]."""
        settings_dir = _write_tiers_file(tmp_path, VALID_TIERS_SETTINGS_YAML)

        result = load_tiers_settings("test-app", settings_dir=settings_dir)

        assert result[0].ad_formats == [AdFormat.INTERSTITIAL, AdFormat.REWARDED]

    def test_default_tier_has_all_formats(self, tmp_path: Path) -> None:
        """Default tier has all 4 LevelPlay-compatible formats."""
        settings_dir = _write_tiers_file(tmp_path, VALID_TIERS_SETTINGS_YAML)

        result = load_tiers_settings("test-app", settings_dir=settings_dir)

        assert set(result[3].ad_formats) == {
            AdFormat.BANNER,
            AdFormat.INTERSTITIAL,
            AdFormat.NATIVE,
            AdFormat.REWARDED,
        }

    def test_default_tier_last_gets_highest_position(self, tmp_path: Path) -> None:
        """Default tier (last in dict from Step 1 sorting) gets the highest position."""
        settings_dir = _write_tiers_file(tmp_path, VALID_TIERS_SETTINGS_YAML)

        result = load_tiers_settings("test-app", settings_dir=settings_dir)

        default_tier = next(t for t in result if t.is_default)
        max_position = max(t.position for t in result)
        assert default_tier.position == max_position

    def test_empty_tiers_dict_returns_empty_list(self, tmp_path: Path) -> None:
        """Empty tiers dict returns an empty list."""
        yaml_content = """\
        tiers: {}
        """
        settings_dir = _write_tiers_file(tmp_path, yaml_content)

        result = load_tiers_settings("test-app", settings_dir=settings_dir)

        assert result == []

    def test_single_format_tier(self, tmp_path: Path) -> None:
        """A tier with a single format has a single-element ad_formats list."""
        yaml_content = """\
        tiers:
          Banner Only:
            countries:
            - '*'
            formats:
            - banner
        """
        settings_dir = _write_tiers_file(tmp_path, yaml_content)

        result = load_tiers_settings("test-app", settings_dir=settings_dir)

        assert len(result) == 1
        assert result[0].ad_formats == [AdFormat.BANNER]


class TestLoadTiersSettingsErrors:
    """Tests for error handling in load_tiers_settings()."""

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        """Non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Tiers settings file not found"):
            load_tiers_settings("nonexistent", settings_dir=str(tmp_path / "settings"))

    def test_missing_file_error_includes_path(self, tmp_path: Path) -> None:
        """FileNotFoundError message includes the file path."""
        with pytest.raises(FileNotFoundError, match="nonexistent-tiers.yaml"):
            load_tiers_settings("nonexistent", settings_dir=str(tmp_path / "settings"))

    def test_malformed_yaml_raises_config_validation_error(self, tmp_path: Path) -> None:
        """Malformed YAML raises ConfigValidationError."""
        yaml_content = ":\n  invalid: [yaml\n  missing bracket"
        settings_dir = _write_tiers_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="Malformed YAML"):
            load_tiers_settings("test-app", settings_dir=settings_dir)

    def test_missing_tiers_key_raises_config_validation_error(self, tmp_path: Path) -> None:
        """YAML without 'tiers' key raises ConfigValidationError."""
        yaml_content = """\
        something_else:
          Tier 1:
            countries:
            - US
        """
        settings_dir = _write_tiers_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="missing required 'tiers' key"):
            load_tiers_settings("test-app", settings_dir=settings_dir)

    def test_empty_file_raises_config_validation_error(self, tmp_path: Path) -> None:
        """Empty YAML file raises ConfigValidationError."""
        settings_dir = _write_tiers_file(tmp_path, "")

        with pytest.raises(ConfigValidationError, match="must contain a YAML mapping"):
            load_tiers_settings("test-app", settings_dir=settings_dir)

    def test_invalid_format_string_raises_config_validation_error(self, tmp_path: Path) -> None:
        """Invalid format string in formats list raises ConfigValidationError."""
        yaml_content = """\
        tiers:
          Bad Tier:
            countries:
            - US
            formats:
            - baner
        """
        settings_dir = _write_tiers_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="Invalid ad format 'baner'"):
            load_tiers_settings("test-app", settings_dir=settings_dir)

    def test_invalid_format_error_mentions_tier_name(self, tmp_path: Path) -> None:
        """Error for invalid format mentions the tier name."""
        yaml_content = """\
        tiers:
          My Custom Tier:
            countries:
            - US
            formats:
            - invalidformat
        """
        settings_dir = _write_tiers_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="My Custom Tier"):
            load_tiers_settings("test-app", settings_dir=settings_dir)

    def test_rewarded_video_is_rejected(self, tmp_path: Path) -> None:
        """Legacy 'rewardedVideo' format string is rejected."""
        yaml_content = """\
        tiers:
          Legacy Tier:
            countries:
            - '*'
            formats:
            - rewardedVideo
        """
        settings_dir = _write_tiers_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="Invalid ad format 'rewardedVideo'"):
            load_tiers_settings("test-app", settings_dir=settings_dir)

    def test_null_tiers_returns_empty_list(self, tmp_path: Path) -> None:
        """YAML with 'tiers: null' returns empty list (null is falsy)."""
        yaml_content = """\
        tiers:
        """
        settings_dir = _write_tiers_file(tmp_path, yaml_content)

        result = load_tiers_settings("test-app", settings_dir=settings_dir)

        assert result == []


class TestLoadTiersSettingsImport:
    """Tests verifying load_tiers_settings is importable from admedi.engine.loader."""

    def test_importable_from_loader_module(self) -> None:
        """load_tiers_settings is importable from admedi.engine.loader (legacy)."""
        from admedi.engine.loader import load_tiers_settings as lts

        assert callable(lts)


# ---------------------------------------------------------------------------
# load_country_groups() Tests
# ---------------------------------------------------------------------------

# Helper to write a countries.yaml file in the expected directory structure.
# load_country_groups() resolves countries.yaml from Path(settings_dir).parent,
# so we create: tmp_path/countries.yaml and pass tmp_path/settings as settings_dir.
def _write_countries_file(tmp_path: Path, content: str) -> str:
    """Write a countries.yaml file and return the settings_dir path.

    Creates the directory structure expected by load_country_groups():
    - tmp_path/countries.yaml (the file)
    - tmp_path/settings/ (the settings_dir whose .parent is tmp_path)
    """
    file_path = tmp_path / "countries.yaml"
    file_path.write_text(dedent(content))
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir(exist_ok=True)
    return str(settings_dir)


# A valid countries.yaml with multiple groups of varying sizes.
VALID_COUNTRIES_YAML = """\
US:
  - US
high-value:
  - AU
  - CA
  - DE
  - GB
  - JP
  - NZ
  - KR
  - TW
mid-value:
  - FR
  - NL
"""


class TestLoadCountryGroupsValid:
    """Tests for successful country groups loading."""

    def test_returns_dict(self, tmp_path: Path) -> None:
        """Valid countries file returns a dict."""
        settings_dir = _write_countries_file(tmp_path, VALID_COUNTRIES_YAML)

        result = load_country_groups(settings_dir=settings_dir)

        assert isinstance(result, dict)

    def test_correct_group_count(self, tmp_path: Path) -> None:
        """Valid countries file with 3 groups returns 3 entries."""
        settings_dir = _write_countries_file(tmp_path, VALID_COUNTRIES_YAML)

        result = load_country_groups(settings_dir=settings_dir)

        assert len(result) == 3

    def test_group_names_are_keys(self, tmp_path: Path) -> None:
        """Group names from the YAML are dict keys."""
        settings_dir = _write_countries_file(tmp_path, VALID_COUNTRIES_YAML)

        result = load_country_groups(settings_dir=settings_dir)

        assert set(result.keys()) == {"US", "high-value", "mid-value"}

    def test_single_country_group(self, tmp_path: Path) -> None:
        """Single-country group (US: [US]) loads correctly."""
        settings_dir = _write_countries_file(tmp_path, VALID_COUNTRIES_YAML)

        result = load_country_groups(settings_dir=settings_dir)

        assert result["US"] == ["US"]

    def test_multi_country_group(self, tmp_path: Path) -> None:
        """Multi-country group loads all country codes correctly."""
        settings_dir = _write_countries_file(tmp_path, VALID_COUNTRIES_YAML)

        result = load_country_groups(settings_dir=settings_dir)

        assert result["high-value"] == ["AU", "CA", "DE", "GB", "JP", "NZ", "KR", "TW"]

    def test_all_values_are_lists_of_strings(self, tmp_path: Path) -> None:
        """All values in the returned dict are lists of strings."""
        settings_dir = _write_countries_file(tmp_path, VALID_COUNTRIES_YAML)

        result = load_country_groups(settings_dir=settings_dir)

        for group_name, codes in result.items():
            assert isinstance(codes, list), f"Group '{group_name}' value is not a list"
            for code in codes:
                assert isinstance(code, str), f"Code {code!r} in '{group_name}' is not a str"

    def test_same_country_in_different_groups_is_valid(self, tmp_path: Path) -> None:
        """The same country code in different groups is valid (groups are independent)."""
        yaml_content = """\
        US:
          - US
        north-america:
          - US
          - CA
          - MX
        """
        settings_dir = _write_countries_file(tmp_path, yaml_content)

        result = load_country_groups(settings_dir=settings_dir)

        assert result["US"] == ["US"]
        assert "US" in result["north-america"]


class TestLoadCountryGroupsValidation:
    """Tests for validation rejections in load_country_groups()."""

    def test_empty_list_raises_config_validation_error(self, tmp_path: Path) -> None:
        """Empty country list raises ConfigValidationError."""
        yaml_content = """\
        empty-group: []
        """
        settings_dir = _write_countries_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="non-empty list"):
            load_country_groups(settings_dir=settings_dir)

    def test_empty_list_error_mentions_group_name(self, tmp_path: Path) -> None:
        """Error for empty list includes the group name."""
        yaml_content = """\
        my-empty-group: []
        """
        settings_dir = _write_countries_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="my-empty-group"):
            load_country_groups(settings_dir=settings_dir)

    def test_lowercase_country_code_raises_error(self, tmp_path: Path) -> None:
        """Lowercase country code (e.g., 'us') raises ConfigValidationError."""
        yaml_content = """\
        bad-group:
          - us
        """
        settings_dir = _write_countries_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="us"):
            load_country_groups(settings_dir=settings_dir)

    def test_three_letter_code_raises_error(self, tmp_path: Path) -> None:
        """Three-letter code (e.g., 'USA') raises ConfigValidationError."""
        yaml_content = """\
        bad-group:
          - USA
        """
        settings_dir = _write_countries_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="USA"):
            load_country_groups(settings_dir=settings_dir)

    def test_wildcard_in_group_raises_error(self, tmp_path: Path) -> None:
        """Wildcard '*' in group definition raises ConfigValidationError."""
        yaml_content = """\
        catch-all:
          - '*'
        """
        settings_dir = _write_countries_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match=r"\*"):
            load_country_groups(settings_dir=settings_dir)

    def test_single_letter_code_raises_error(self, tmp_path: Path) -> None:
        """Single-letter code raises ConfigValidationError."""
        yaml_content = """\
        bad-group:
          - A
        """
        settings_dir = _write_countries_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="A"):
            load_country_groups(settings_dir=settings_dir)

    def test_duplicate_code_within_group_raises_error(self, tmp_path: Path) -> None:
        """Duplicate country code within a single group raises ConfigValidationError."""
        yaml_content = """\
        duplicates:
          - US
          - US
        """
        settings_dir = _write_countries_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="Duplicate country code 'US'"):
            load_country_groups(settings_dir=settings_dir)

    def test_duplicate_code_error_mentions_group_name(self, tmp_path: Path) -> None:
        """Error for duplicate code includes the group name."""
        yaml_content = """\
        my-dups:
          - GB
          - GB
        """
        settings_dir = _write_countries_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="my-dups"):
            load_country_groups(settings_dir=settings_dir)

    def test_invalid_code_error_mentions_two_uppercase_letters(self, tmp_path: Path) -> None:
        """Error for invalid code explains the required format."""
        yaml_content = """\
        bad:
          - abc
        """
        settings_dir = _write_countries_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="2 uppercase letters"):
            load_country_groups(settings_dir=settings_dir)

    def test_non_string_country_code_raises_error(self, tmp_path: Path) -> None:
        """Non-string country code (e.g., integer) raises ConfigValidationError."""
        yaml_content = """\
        bad:
          - 42
        """
        settings_dir = _write_countries_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="must be a string"):
            load_country_groups(settings_dir=settings_dir)

    def test_non_list_value_raises_error(self, tmp_path: Path) -> None:
        """Non-list value (e.g., a string) raises ConfigValidationError."""
        yaml_content = """\
        bad-group: US
        """
        settings_dir = _write_countries_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="non-empty list"):
            load_country_groups(settings_dir=settings_dir)


class TestLoadCountryGroupsFileErrors:
    """Tests for file-level error handling in load_country_groups()."""

    def test_missing_file_raises_file_not_found_error(self, tmp_path: Path) -> None:
        """Non-existent file raises FileNotFoundError."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir(exist_ok=True)

        with pytest.raises(FileNotFoundError, match="Country groups file not found"):
            load_country_groups(settings_dir=str(settings_dir))

    def test_missing_file_error_includes_path(self, tmp_path: Path) -> None:
        """FileNotFoundError message includes the file path."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir(exist_ok=True)

        with pytest.raises(FileNotFoundError, match="countries.yaml"):
            load_country_groups(settings_dir=str(settings_dir))

    def test_malformed_yaml_raises_config_validation_error(self, tmp_path: Path) -> None:
        """Malformed YAML raises ConfigValidationError."""
        yaml_content = ":\n  invalid: [yaml\n  missing bracket"
        settings_dir = _write_countries_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="Malformed YAML"):
            load_country_groups(settings_dir=settings_dir)

    def test_empty_file_raises_config_validation_error(self, tmp_path: Path) -> None:
        """Empty YAML file raises ConfigValidationError."""
        settings_dir = _write_countries_file(tmp_path, "")

        with pytest.raises(ConfigValidationError, match="empty"):
            load_country_groups(settings_dir=settings_dir)

    def test_yaml_list_raises_config_validation_error(self, tmp_path: Path) -> None:
        """YAML that parses to a list (not a mapping) raises ConfigValidationError."""
        yaml_content = "- US\n- GB\n"
        settings_dir = _write_countries_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError, match="mapping"):
            load_country_groups(settings_dir=settings_dir)


class TestLoadCountryGroupsErrorMessages:
    """Tests verifying error messages include filename and details."""

    def test_empty_list_error_includes_filename(self, tmp_path: Path) -> None:
        """Error for empty list includes the filename."""
        yaml_content = """\
        empty: []
        """
        settings_dir = _write_countries_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError) as exc_info:
            load_country_groups(settings_dir=settings_dir)

        assert "countries.yaml" in exc_info.value.message

    def test_invalid_code_error_includes_filename(self, tmp_path: Path) -> None:
        """Error for invalid code includes the filename."""
        yaml_content = """\
        bad:
          - us
        """
        settings_dir = _write_countries_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError) as exc_info:
            load_country_groups(settings_dir=settings_dir)

        assert "countries.yaml" in exc_info.value.message

    def test_duplicate_code_error_includes_filename(self, tmp_path: Path) -> None:
        """Error for duplicate code includes the filename."""
        yaml_content = """\
        dups:
          - US
          - US
        """
        settings_dir = _write_countries_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError) as exc_info:
            load_country_groups(settings_dir=settings_dir)

        assert "countries.yaml" in exc_info.value.message

    def test_malformed_yaml_error_includes_filename(self, tmp_path: Path) -> None:
        """Malformed YAML error includes the filename."""
        yaml_content = ":\n  bad: [yaml\n  no bracket"
        settings_dir = _write_countries_file(tmp_path, yaml_content)

        with pytest.raises(ConfigValidationError) as exc_info:
            load_country_groups(settings_dir=settings_dir)

        assert "countries.yaml" in exc_info.value.message


class TestLoadCountryGroupsImport:
    """Tests verifying load_country_groups is importable from admedi.engine."""

    def test_importable_from_engine_package(self) -> None:
        """load_country_groups is importable from admedi.engine."""
        from admedi.engine import load_country_groups as lcg

        assert callable(lcg)


# ---------------------------------------------------------------------------
# load_tiers_definition() Tests
# ---------------------------------------------------------------------------

# Helper to write both countries.yaml and tiers.yaml in the expected
# directory structure.  load_tiers_definition() resolves tiers.yaml from
# Path(settings_dir).parent, so we create:
#   tmp_path/countries.yaml
#   tmp_path/tiers.yaml
#   tmp_path/settings/  (the settings_dir whose .parent is tmp_path)
def _write_tiers_definition_files(
    tmp_path: Path,
    tiers_content: str,
    countries_content: str = VALID_COUNTRIES_YAML,
) -> str:
    """Write countries.yaml and tiers.yaml, return the settings_dir path.

    Creates the directory structure expected by load_tiers_definition():
    - tmp_path/countries.yaml
    - tmp_path/tiers.yaml
    - tmp_path/settings/ (the settings_dir whose .parent is tmp_path)
    """
    (tmp_path / "countries.yaml").write_text(dedent(countries_content))
    (tmp_path / "tiers.yaml").write_text(dedent(tiers_content))
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir(exist_ok=True)
    return str(settings_dir)


# A valid tiers.yaml referencing groups from VALID_COUNTRIES_YAML.
VALID_TIERS_DEFINITION_YAML = """\
Tier 1: US
Tier 2: high-value
Tier 3: mid-value
All Countries: '*'
"""


class TestLoadTiersDefinitionValid:
    """Tests for successful tiers definition loading."""

    def test_returns_dict(self, tmp_path: Path) -> None:
        """Valid tiers file returns a dict."""
        settings_dir = _write_tiers_definition_files(
            tmp_path, VALID_TIERS_DEFINITION_YAML
        )

        result = load_tiers_definition(settings_dir=settings_dir)

        assert isinstance(result, dict)

    def test_correct_tier_count(self, tmp_path: Path) -> None:
        """Valid tiers file with 4 tiers returns 4 entries."""
        settings_dir = _write_tiers_definition_files(
            tmp_path, VALID_TIERS_DEFINITION_YAML
        )

        result = load_tiers_definition(settings_dir=settings_dir)

        assert len(result) == 4

    def test_tier_names_are_keys(self, tmp_path: Path) -> None:
        """Tier names from the YAML are dict keys."""
        settings_dir = _write_tiers_definition_files(
            tmp_path, VALID_TIERS_DEFINITION_YAML
        )

        result = load_tiers_definition(settings_dir=settings_dir)

        assert set(result.keys()) == {"Tier 1", "Tier 2", "Tier 3", "All Countries"}

    def test_tier_references_group_name(self, tmp_path: Path) -> None:
        """Tier value is the country group name string (not resolved codes)."""
        settings_dir = _write_tiers_definition_files(
            tmp_path, VALID_TIERS_DEFINITION_YAML
        )

        result = load_tiers_definition(settings_dir=settings_dir)

        assert result["Tier 1"] == "US"
        assert result["Tier 2"] == "high-value"
        assert result["Tier 3"] == "mid-value"

    def test_wildcard_catch_all_loads(self, tmp_path: Path) -> None:
        """The '*' catch-all tier loads correctly."""
        settings_dir = _write_tiers_definition_files(
            tmp_path, VALID_TIERS_DEFINITION_YAML
        )

        result = load_tiers_definition(settings_dir=settings_dir)

        assert result["All Countries"] == "*"

    def test_all_values_are_strings(self, tmp_path: Path) -> None:
        """All values in the returned dict are strings."""
        settings_dir = _write_tiers_definition_files(
            tmp_path, VALID_TIERS_DEFINITION_YAML
        )

        result = load_tiers_definition(settings_dir=settings_dir)

        for tier_name, group_ref in result.items():
            assert isinstance(group_ref, str), (
                f"Tier '{tier_name}' value is not a string"
            )

    def test_single_tier_with_wildcard(self, tmp_path: Path) -> None:
        """A tiers file with only a '*' catch-all is valid."""
        tiers_content = """\
        All Countries: '*'
        """
        settings_dir = _write_tiers_definition_files(tmp_path, tiers_content)

        result = load_tiers_definition(settings_dir=settings_dir)

        assert len(result) == 1
        assert result["All Countries"] == "*"

    def test_accepts_pre_loaded_country_groups(self, tmp_path: Path) -> None:
        """When country_groups is provided, does not require countries.yaml."""
        # Write only tiers.yaml -- no countries.yaml
        tiers_content = "Tier 1: US\n"
        (tmp_path / "tiers.yaml").write_text(tiers_content)
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir(exist_ok=True)

        pre_loaded = {"US": ["US"]}
        result = load_tiers_definition(
            settings_dir=str(settings_dir),
            country_groups=pre_loaded,
        )

        assert result["Tier 1"] == "US"

    def test_bare_wildcard_yaml_is_alias_syntax_error(self, tmp_path: Path) -> None:
        """Bare * (without quotes) in YAML is treated as an alias, causing a parse error.

        This is a design risk explicit test per the plan specification.
        ruamel.yaml's safe loader treats bare ``*`` as a YAML alias
        indicator (``*anchor``), not a plain string. Since there is no
        anchor name after ``*``, parsing fails. Users must always quote
        the wildcard: ``'*'``.
        """
        # Use bare * without quotes in YAML
        tiers_content = "All Countries: *\n"
        settings_dir = _write_tiers_definition_files(tmp_path, tiers_content)

        with pytest.raises(ConfigValidationError, match="Malformed YAML"):
            load_tiers_definition(settings_dir=settings_dir)

    def test_two_tiers_different_country_sets_valid(self, tmp_path: Path) -> None:
        """Two tiers referencing different country groups is valid."""
        tiers_content = """\
        Tier 1: US
        Tier 2: high-value
        """
        settings_dir = _write_tiers_definition_files(tmp_path, tiers_content)

        result = load_tiers_definition(settings_dir=settings_dir)

        assert len(result) == 2


class TestLoadTiersDefinitionValidation:
    """Tests for validation rejections in load_tiers_definition()."""

    def test_empty_tiers_raises_config_validation_error(self, tmp_path: Path) -> None:
        """Empty tiers mapping raises ConfigValidationError."""
        tiers_content = "{}\n"
        settings_dir = _write_tiers_definition_files(tmp_path, tiers_content)

        with pytest.raises(ConfigValidationError, match="at least one tier"):
            load_tiers_definition(settings_dir=settings_dir)

    def test_missing_group_reference_raises_error(self, tmp_path: Path) -> None:
        """Tier referencing non-existent country group raises ConfigValidationError."""
        tiers_content = """\
        Tier 1: nonexistent-group
        """
        settings_dir = _write_tiers_definition_files(tmp_path, tiers_content)

        with pytest.raises(ConfigValidationError, match="nonexistent-group"):
            load_tiers_definition(settings_dir=settings_dir)

    def test_missing_group_error_mentions_tier_name(self, tmp_path: Path) -> None:
        """Error for missing group reference includes the tier name."""
        tiers_content = """\
        My Special Tier: missing-group
        """
        settings_dir = _write_tiers_definition_files(tmp_path, tiers_content)

        with pytest.raises(ConfigValidationError, match="My Special Tier"):
            load_tiers_definition(settings_dir=settings_dir)

    def test_missing_group_error_mentions_countries_yaml(self, tmp_path: Path) -> None:
        """Error for missing group reference mentions countries.yaml."""
        tiers_content = """\
        Tier 1: missing-group
        """
        settings_dir = _write_tiers_definition_files(tmp_path, tiers_content)

        with pytest.raises(ConfigValidationError, match="countries.yaml"):
            load_tiers_definition(settings_dir=settings_dir)

    def test_non_string_value_raises_error(self, tmp_path: Path) -> None:
        """Non-string tier value (e.g., a list) raises ConfigValidationError."""
        tiers_content = """\
        Tier 1:
          - US
          - CA
        """
        settings_dir = _write_tiers_definition_files(tmp_path, tiers_content)

        with pytest.raises(ConfigValidationError, match="string"):
            load_tiers_definition(settings_dir=settings_dir)

    def test_non_string_value_error_mentions_tier_name(self, tmp_path: Path) -> None:
        """Error for non-string value includes the tier name."""
        tiers_content = """\
        Bad Tier:
          - US
        """
        settings_dir = _write_tiers_definition_files(tmp_path, tiers_content)

        with pytest.raises(ConfigValidationError, match="Bad Tier"):
            load_tiers_definition(settings_dir=settings_dir)

    def test_integer_value_raises_error(self, tmp_path: Path) -> None:
        """Integer tier value raises ConfigValidationError."""
        tiers_content = """\
        Tier 1: 42
        """
        settings_dir = _write_tiers_definition_files(tmp_path, tiers_content)

        with pytest.raises(ConfigValidationError, match="string"):
            load_tiers_definition(settings_dir=settings_dir)

    def test_duplicate_resolved_country_set_raises_error(self, tmp_path: Path) -> None:
        """Two tiers resolving to the same country set raises ConfigValidationError."""
        # Create two different group names that map to the same countries
        countries_content = """\
        us-group: [US]
        america: [US]
        """
        tiers_content = """\
        Tier A: us-group
        Tier B: america
        """
        settings_dir = _write_tiers_definition_files(
            tmp_path, tiers_content, countries_content=countries_content
        )

        with pytest.raises(ConfigValidationError, match="same country set"):
            load_tiers_definition(settings_dir=settings_dir)

    def test_duplicate_set_error_mentions_both_tier_names(self, tmp_path: Path) -> None:
        """Error for duplicate country set mentions both conflicting tier names."""
        countries_content = """\
        group-a: [US, CA]
        group-b: [CA, US]
        """
        tiers_content = """\
        First Tier: group-a
        Second Tier: group-b
        """
        settings_dir = _write_tiers_definition_files(
            tmp_path, tiers_content, countries_content=countries_content
        )

        with pytest.raises(ConfigValidationError) as exc_info:
            load_tiers_definition(settings_dir=settings_dir)

        error_msg = exc_info.value.message
        assert "First Tier" in error_msg
        assert "Second Tier" in error_msg

    def test_wildcard_excluded_from_duplicate_set_check(self, tmp_path: Path) -> None:
        """Two '*' catch-all tiers are both allowed (no duplicate set check for '*')."""
        tiers_content = """\
        Default Banner: '*'
        Default Interstitial: '*'
        """
        settings_dir = _write_tiers_definition_files(tmp_path, tiers_content)

        # Should not raise -- '*' tiers are excluded from the duplicate set check
        result = load_tiers_definition(settings_dir=settings_dir)

        assert len(result) == 2


class TestLoadTiersDefinitionFileErrors:
    """Tests for file-level error handling in load_tiers_definition()."""

    def test_missing_file_raises_file_not_found_error(self, tmp_path: Path) -> None:
        """Non-existent tiers.yaml raises FileNotFoundError."""
        # Write countries.yaml but not tiers.yaml
        (tmp_path / "countries.yaml").write_text(VALID_COUNTRIES_YAML)
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir(exist_ok=True)

        with pytest.raises(FileNotFoundError, match="Tiers definition file not found"):
            load_tiers_definition(settings_dir=str(settings_dir))

    def test_missing_file_error_includes_path(self, tmp_path: Path) -> None:
        """FileNotFoundError message includes the file path."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir(exist_ok=True)

        with pytest.raises(FileNotFoundError, match="tiers.yaml"):
            load_tiers_definition(settings_dir=str(settings_dir))

    def test_malformed_yaml_raises_config_validation_error(self, tmp_path: Path) -> None:
        """Malformed YAML raises ConfigValidationError."""
        tiers_content = ":\n  invalid: [yaml\n  missing bracket"
        settings_dir = _write_tiers_definition_files(tmp_path, tiers_content)

        with pytest.raises(ConfigValidationError, match="Malformed YAML"):
            load_tiers_definition(settings_dir=settings_dir)

    def test_empty_file_raises_config_validation_error(self, tmp_path: Path) -> None:
        """Empty YAML file raises ConfigValidationError."""
        settings_dir = _write_tiers_definition_files(tmp_path, "")

        with pytest.raises(ConfigValidationError, match="empty"):
            load_tiers_definition(settings_dir=settings_dir)

    def test_yaml_list_raises_config_validation_error(self, tmp_path: Path) -> None:
        """YAML that parses to a list (not a mapping) raises ConfigValidationError."""
        tiers_content = "- Tier 1\n- Tier 2\n"
        settings_dir = _write_tiers_definition_files(tmp_path, tiers_content)

        with pytest.raises(ConfigValidationError, match="mapping"):
            load_tiers_definition(settings_dir=settings_dir)


class TestLoadTiersDefinitionErrorMessages:
    """Tests verifying error messages include filename and details."""

    def test_empty_tiers_error_includes_filename(self, tmp_path: Path) -> None:
        """Error for empty tiers includes the filename."""
        tiers_content = "{}\n"
        settings_dir = _write_tiers_definition_files(tmp_path, tiers_content)

        with pytest.raises(ConfigValidationError) as exc_info:
            load_tiers_definition(settings_dir=settings_dir)

        assert "tiers.yaml" in exc_info.value.message

    def test_missing_group_error_includes_filename(self, tmp_path: Path) -> None:
        """Error for missing group reference includes the filename."""
        tiers_content = """\
        Tier 1: missing
        """
        settings_dir = _write_tiers_definition_files(tmp_path, tiers_content)

        with pytest.raises(ConfigValidationError) as exc_info:
            load_tiers_definition(settings_dir=settings_dir)

        assert "tiers.yaml" in exc_info.value.message

    def test_non_string_error_includes_filename(self, tmp_path: Path) -> None:
        """Error for non-string value includes the filename."""
        tiers_content = """\
        Tier 1:
          - US
        """
        settings_dir = _write_tiers_definition_files(tmp_path, tiers_content)

        with pytest.raises(ConfigValidationError) as exc_info:
            load_tiers_definition(settings_dir=settings_dir)

        assert "tiers.yaml" in exc_info.value.message

    def test_duplicate_set_error_includes_filename(self, tmp_path: Path) -> None:
        """Error for duplicate country set includes the filename."""
        countries_content = """\
        same-a: [US]
        same-b: [US]
        """
        tiers_content = """\
        Tier A: same-a
        Tier B: same-b
        """
        settings_dir = _write_tiers_definition_files(
            tmp_path, tiers_content, countries_content=countries_content
        )

        with pytest.raises(ConfigValidationError) as exc_info:
            load_tiers_definition(settings_dir=settings_dir)

        assert "tiers.yaml" in exc_info.value.message

    def test_malformed_yaml_error_includes_filename(self, tmp_path: Path) -> None:
        """Malformed YAML error includes the filename."""
        tiers_content = ":\n  bad: [yaml\n  no bracket"
        settings_dir = _write_tiers_definition_files(tmp_path, tiers_content)

        with pytest.raises(ConfigValidationError) as exc_info:
            load_tiers_definition(settings_dir=settings_dir)

        assert "tiers.yaml" in exc_info.value.message


class TestLoadTiersDefinitionImport:
    """Tests verifying load_tiers_definition is importable from admedi.engine.loader."""

    def test_importable_from_loader_module(self) -> None:
        """load_tiers_definition is still importable from admedi.engine.loader (deprecated)."""
        from admedi.engine.loader import load_tiers_definition as ltd

        assert callable(ltd)

    def test_not_in_engine_all(self) -> None:
        """load_tiers_definition is NOT exported from admedi.engine (removed from __all__)."""
        import admedi.engine

        assert "load_tiers_definition" not in admedi.engine.__all__


# ---------------------------------------------------------------------------
# load_profiles() tests
# ---------------------------------------------------------------------------


def _write_profiles_file(tmp_path: Path, content: str) -> Path:
    """Write profiles YAML content to a temp file and return the path."""
    file_path = tmp_path / "profiles.yaml"
    file_path.write_text(dedent(content))
    return file_path


VALID_PROFILES_YAML = """\
profiles:
  ss-ios:
    app_key: "1f93a90ad"
    app_name: "Shelf Sort iOS"
    platform: iOS
  ss-google:
    app_key: "1f93aca35"
    app_name: "Shelf Sort Android"
    platform: Android
  hexar-ios:
    app_key: "676996cd"
    app_name: "Hexar.io iOS"
    platform: iOS
  hexar-google:
    app_key: "67695d45"
    app_name: "Hexar.io Android"
    platform: Android
"""


class TestLoadProfilesValid:
    """Tests for loading valid expanded profiles."""

    def test_loads_all_four_profiles(self, tmp_path: Path) -> None:
        """All 4 profiles are loaded and accessible by alias."""
        profiles_path = _write_profiles_file(tmp_path, VALID_PROFILES_YAML)
        profiles = load_profiles(profiles_path)
        assert len(profiles) == 4
        assert set(profiles.keys()) == {"ss-ios", "ss-google", "hexar-ios", "hexar-google"}

    def test_profile_has_correct_fields(self, tmp_path: Path) -> None:
        """Each Profile has alias, app_key, app_name, and platform."""
        profiles_path = _write_profiles_file(tmp_path, VALID_PROFILES_YAML)
        profiles = load_profiles(profiles_path)

        ss_ios = profiles["ss-ios"]
        assert ss_ios.alias == "ss-ios"
        assert ss_ios.app_key == "1f93a90ad"
        assert ss_ios.app_name == "Shelf Sort iOS"
        assert ss_ios.platform == Platform.IOS

    def test_profile_platform_is_enum(self, tmp_path: Path) -> None:
        """Platform field is a Platform enum, not a raw string."""
        profiles_path = _write_profiles_file(tmp_path, VALID_PROFILES_YAML)
        profiles = load_profiles(profiles_path)

        assert profiles["ss-google"].platform == Platform.ANDROID
        assert isinstance(profiles["ss-google"].platform, Platform)

    def test_nonexistent_alias_returns_none_via_get(self, tmp_path: Path) -> None:
        """Looking up a nonexistent alias via .get() returns None."""
        profiles_path = _write_profiles_file(tmp_path, VALID_PROFILES_YAML)
        profiles = load_profiles(profiles_path)
        assert profiles.get("nonexistent") is None

    def test_profile_is_pydantic_model(self, tmp_path: Path) -> None:
        """Profile instances are Pydantic BaseModel subclasses."""
        profiles_path = _write_profiles_file(tmp_path, VALID_PROFILES_YAML)
        profiles = load_profiles(profiles_path)

        profile = profiles["hexar-ios"]
        assert isinstance(profile, Profile)
        # Verify Pydantic model_dump works
        data = profile.model_dump()
        assert data["alias"] == "hexar-ios"
        assert data["app_key"] == "676996cd"

    def test_single_profile(self, tmp_path: Path) -> None:
        """A profiles file with a single entry loads correctly."""
        content = """\
        profiles:
          test-app:
            app_key: "abc123"
            app_name: "Test App"
            platform: iOS
        """
        profiles_path = _write_profiles_file(tmp_path, content)
        profiles = load_profiles(profiles_path)
        assert len(profiles) == 1
        assert profiles["test-app"].app_key == "abc123"

    def test_amazon_platform(self, tmp_path: Path) -> None:
        """Amazon platform is a valid Platform value."""
        content = """\
        profiles:
          test-amazon:
            app_key: "xyz789"
            app_name: "Test Amazon"
            platform: Amazon
        """
        profiles_path = _write_profiles_file(tmp_path, content)
        profiles = load_profiles(profiles_path)
        assert profiles["test-amazon"].platform == Platform.AMAZON


class TestLoadProfilesValidation:
    """Tests for profiles validation error handling."""

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        """Missing profiles.yaml raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Profiles file not found"):
            load_profiles(tmp_path / "profiles.yaml")

    def test_missing_app_key_raises_error(self, tmp_path: Path) -> None:
        """Missing app_key field raises ConfigValidationError."""
        content = """\
        profiles:
          bad-app:
            app_name: "Bad App"
            platform: iOS
        """
        profiles_path = _write_profiles_file(tmp_path, content)
        with pytest.raises(ConfigValidationError, match="missing.*app_key"):
            load_profiles(profiles_path)

    def test_empty_app_key_raises_error(self, tmp_path: Path) -> None:
        """Empty app_key field raises ConfigValidationError."""
        content = """\
        profiles:
          bad-app:
            app_key: ""
            app_name: "Bad App"
            platform: iOS
        """
        profiles_path = _write_profiles_file(tmp_path, content)
        with pytest.raises(ConfigValidationError, match="missing.*app_key"):
            load_profiles(profiles_path)

    def test_missing_app_name_raises_error(self, tmp_path: Path) -> None:
        """Missing app_name field raises ConfigValidationError."""
        content = """\
        profiles:
          bad-app:
            app_key: "abc123"
            platform: iOS
        """
        profiles_path = _write_profiles_file(tmp_path, content)
        with pytest.raises(ConfigValidationError, match="missing.*app_name"):
            load_profiles(profiles_path)

    def test_missing_platform_raises_error(self, tmp_path: Path) -> None:
        """Missing platform field raises ConfigValidationError."""
        content = """\
        profiles:
          bad-app:
            app_key: "abc123"
            app_name: "Bad App"
        """
        profiles_path = _write_profiles_file(tmp_path, content)
        with pytest.raises(ConfigValidationError, match="missing.*platform"):
            load_profiles(profiles_path)

    def test_invalid_platform_raises_error(self, tmp_path: Path) -> None:
        """Invalid platform value raises ConfigValidationError."""
        content = """\
        profiles:
          bad-app:
            app_key: "abc123"
            app_name: "Bad App"
            platform: Windows
        """
        profiles_path = _write_profiles_file(tmp_path, content)
        with pytest.raises(ConfigValidationError, match="invalid.*platform.*Windows"):
            load_profiles(profiles_path)

    def test_non_mapping_entry_raises_error(self, tmp_path: Path) -> None:
        """Non-mapping profile entry raises ConfigValidationError."""
        content = """\
        profiles:
          bad-app: "just-a-string"
        """
        profiles_path = _write_profiles_file(tmp_path, content)
        with pytest.raises(ConfigValidationError, match="bad-app.*must be a mapping"):
            load_profiles(profiles_path)

    def test_missing_profiles_key_raises_error(self, tmp_path: Path) -> None:
        """Missing 'profiles' top-level key raises ConfigValidationError."""
        content = """\
        not_profiles:
          ss-ios:
            app_key: "abc123"
        """
        profiles_path = _write_profiles_file(tmp_path, content)
        with pytest.raises(ConfigValidationError, match="missing required 'profiles' key"):
            load_profiles(profiles_path)

    def test_empty_file_raises_error(self, tmp_path: Path) -> None:
        """Empty profiles file raises ConfigValidationError."""
        profiles_path = tmp_path / "profiles.yaml"
        profiles_path.write_text("")
        with pytest.raises(ConfigValidationError, match="must contain a YAML mapping"):
            load_profiles(profiles_path)

    def test_malformed_yaml_raises_error(self, tmp_path: Path) -> None:
        """Malformed YAML raises ConfigValidationError."""
        profiles_path = tmp_path / "profiles.yaml"
        profiles_path.write_text("profiles: [bad: yaml: content")
        with pytest.raises(ConfigValidationError, match="Malformed YAML"):
            load_profiles(profiles_path)

    def test_non_string_app_key_raises_error(self, tmp_path: Path) -> None:
        """Non-string app_key raises ConfigValidationError."""
        content = """\
        profiles:
          bad-app:
            app_key: 12345
            app_name: "Bad App"
            platform: iOS
        """
        profiles_path = _write_profiles_file(tmp_path, content)
        with pytest.raises(ConfigValidationError, match="app_key.*must be a string"):
            load_profiles(profiles_path)


class TestLoadProfilesErrorMessages:
    """Tests for error message quality in profiles loading."""

    def test_error_includes_alias_name(self, tmp_path: Path) -> None:
        """Error messages include the problematic alias name."""
        content = """\
        profiles:
          my-broken-app:
            app_name: "Broken"
            platform: iOS
        """
        profiles_path = _write_profiles_file(tmp_path, content)
        with pytest.raises(ConfigValidationError) as exc_info:
            load_profiles(profiles_path)
        assert "my-broken-app" in exc_info.value.message

    def test_error_includes_filename(self, tmp_path: Path) -> None:
        """Error messages include the profiles filename."""
        content = """\
        profiles:
          bad-app:
            app_key: "abc"
            app_name: "Bad"
            platform: BadPlatform
        """
        profiles_path = _write_profiles_file(tmp_path, content)
        with pytest.raises(ConfigValidationError) as exc_info:
            load_profiles(profiles_path)
        assert "profiles.yaml" in exc_info.value.message

    def test_invalid_platform_shows_valid_values(self, tmp_path: Path) -> None:
        """Invalid platform error shows the list of valid Platform values."""
        content = """\
        profiles:
          bad-app:
            app_key: "abc"
            app_name: "Bad"
            platform: Symbian
        """
        profiles_path = _write_profiles_file(tmp_path, content)
        with pytest.raises(ConfigValidationError) as exc_info:
            load_profiles(profiles_path)
        # Should mention valid values
        assert "Android" in exc_info.value.message or "iOS" in exc_info.value.message


class TestLoadProfilesImport:
    """Tests verifying Profile and load_profiles are importable from admedi.engine."""

    def test_load_profiles_importable_from_engine(self) -> None:
        """load_profiles is importable from admedi.engine."""
        from admedi.engine import load_profiles as lp

        assert callable(lp)

    def test_profile_importable_from_engine(self) -> None:
        """Profile is importable from admedi.engine."""
        from admedi.engine import Profile as P

        assert P is Profile

    def test_profile_importable_from_loader(self) -> None:
        """Profile is importable from admedi.engine.loader."""
        from admedi.engine.loader import Profile as P

        assert P is Profile


# ---------------------------------------------------------------------------
# resolve_app_tiers() Tests
# ---------------------------------------------------------------------------


def _write_three_layer_files(
    tmp_path: Path,
    *,
    countries_yaml: str = "",
    tiers_yaml: str = "",
    profiles_yaml: str = "",
    app_yaml: str = "",
    alias: str = "hexar-ios",
) -> str:
    """Write two-layer config files and return the settings_dir path.

    Directory structure:
        tmp_path/
            countries.yaml
            profiles.yaml
            settings/
                {alias}.yaml

    Note: tiers_yaml parameter is accepted but ignored (legacy compat).
    """
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir(exist_ok=True)

    if countries_yaml:
        (tmp_path / "countries.yaml").write_text(dedent(countries_yaml))
    if profiles_yaml:
        (tmp_path / "profiles.yaml").write_text(dedent(profiles_yaml))
    if app_yaml:
        (settings_dir / f"{alias}.yaml").write_text(dedent(app_yaml))

    return str(settings_dir)


# Standard two-layer fixture data
_COUNTRIES_YAML = """\
US:
  - US
high-value:
  - AU
  - CA
  - DE
  - GB
europe:
  - FR
  - NL
"""

_TIERS_YAML = """\
Tier 1: US
Tier 2: high-value
Tier 3: europe
All Countries: '*'
"""

_PROFILES_YAML = """\
profiles:
  hexar-ios:
    app_key: 676996cd
    app_name: Hexar.io iOS
    platform: iOS
  hexar-google:
    app_key: 789abc12
    app_name: Hexar.io Android
    platform: Android
"""

_APP_YAML_HEXAR_IOS = """\
alias: hexar-ios

rewarded:
  - Tier 1: {countries: US}
  - Tier 2: {countries: high-value}
  - All Countries: {countries: '*'}

interstitial:
  - Tier 1: {countries: US}
  - Tier 2: {countries: high-value}
  - Tier 3: {countries: europe}
  - All Countries: {countries: '*'}
"""


class TestResolveAppTiersValid:
    """Tests for successful two-layer resolution via resolve_app_tiers()."""

    def test_returns_list_of_portfolio_tier(self, tmp_path: Path) -> None:
        """resolve_app_tiers returns a list of PortfolioTier objects."""
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=_APP_YAML_HEXAR_IOS,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        assert isinstance(result, list)
        assert all(isinstance(t, PortfolioTier) for t in result)

    def test_correct_tier_count(self, tmp_path: Path) -> None:
        """Two format sections with 3 and 4 tiers produce 7 PortfolioTier objects."""
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=_APP_YAML_HEXAR_IOS,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        assert len(result) == 7  # 3 rewarded + 4 interstitial

    def test_each_tier_has_single_ad_format(self, tmp_path: Path) -> None:
        """Each PortfolioTier has exactly one ad_formats entry (Option A)."""
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=_APP_YAML_HEXAR_IOS,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        for tier in result:
            assert len(tier.ad_formats) == 1

    def test_rewarded_tiers_have_correct_format(self, tmp_path: Path) -> None:
        """First 3 tiers (rewarded section) have AdFormat.REWARDED."""
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=_APP_YAML_HEXAR_IOS,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        # First 3 are from the rewarded section
        for tier in result[:3]:
            assert tier.ad_formats == [AdFormat.REWARDED]

    def test_interstitial_tiers_have_correct_format(self, tmp_path: Path) -> None:
        """Last 4 tiers (interstitial section) have AdFormat.INTERSTITIAL."""
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=_APP_YAML_HEXAR_IOS,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        # Last 4 are from the interstitial section
        for tier in result[3:]:
            assert tier.ad_formats == [AdFormat.INTERSTITIAL]

    def test_position_per_format_section(self, tmp_path: Path) -> None:
        """Position is 1-based within each format section."""
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=_APP_YAML_HEXAR_IOS,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        # Rewarded: positions 1, 2, 3
        rewarded = [t for t in result if t.ad_formats == [AdFormat.REWARDED]]
        assert [t.position for t in rewarded] == [1, 2, 3]

        # Interstitial: positions 1, 2, 3, 4
        interstitial = [t for t in result if t.ad_formats == [AdFormat.INTERSTITIAL]]
        assert [t.position for t in interstitial] == [1, 2, 3, 4]

    def test_countries_resolved_from_two_layers(self, tmp_path: Path) -> None:
        """Display names resolve through countries.yaml to country codes."""
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=_APP_YAML_HEXAR_IOS,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        # First rewarded tier is "Tier 1" -> US group -> ["US"]
        assert result[0].name == "Tier 1"
        assert result[0].countries == ["US"]

        # Second rewarded tier is "Tier 2" -> high-value -> ["AU", "CA", "DE", "GB"]
        assert result[1].name == "Tier 2"
        assert result[1].countries == ["AU", "CA", "DE", "GB"]

    def test_wildcard_tier_resolves_correctly(self, tmp_path: Path) -> None:
        """Entry referencing '*' resolves to countries=['*'] and is_default=True."""
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=_APP_YAML_HEXAR_IOS,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        # "All Countries" tiers (last in each section) have is_default=True
        all_countries_tiers = [t for t in result if t.name == "All Countries"]
        assert len(all_countries_tiers) == 2  # one per format
        for tier in all_countries_tiers:
            assert tier.countries == ["*"]
            assert tier.is_default is True

    def test_non_wildcard_tier_is_default_false(self, tmp_path: Path) -> None:
        """Tiers not referencing '*' have is_default=False."""
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=_APP_YAML_HEXAR_IOS,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        non_default = [t for t in result if t.name != "All Countries"]
        assert all(t.is_default is False for t in non_default)

    def test_per_format_tier_differences(self, tmp_path: Path) -> None:
        """Different format sections can have different tier lists."""
        app_yaml = """\
        alias: hexar-ios

        banner:
          - All Countries: {countries: '*'}

        rewarded:
          - Tier 1: {countries: US}
          - All Countries: {countries: '*'}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        banner_tiers = [t for t in result if t.ad_formats == [AdFormat.BANNER]]
        rewarded_tiers = [t for t in result if t.ad_formats == [AdFormat.REWARDED]]

        assert len(banner_tiers) == 1
        assert banner_tiers[0].name == "All Countries"
        assert len(rewarded_tiers) == 2
        assert [t.name for t in rewarded_tiers] == ["Tier 1", "All Countries"]

    def test_empty_format_section_produces_no_tiers(self, tmp_path: Path) -> None:
        """An empty format section (empty list) produces no tiers for that format."""
        app_yaml = """\
        alias: hexar-ios

        rewarded:
          - Tier 1: {countries: US}

        interstitial: []
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        assert len(result) == 1
        assert result[0].ad_formats == [AdFormat.REWARDED]

    def test_all_four_formats_supported(self, tmp_path: Path) -> None:
        """All four valid ad formats can appear as sections."""
        app_yaml = """\
        alias: hexar-ios

        banner:
          - All Countries: {countries: '*'}
        interstitial:
          - All Countries: {countries: '*'}
        rewarded:
          - All Countries: {countries: '*'}
        native:
          - All Countries: {countries: '*'}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        formats_found = {t.ad_formats[0] for t in result}
        assert formats_found == {
            AdFormat.BANNER, AdFormat.INTERSTITIAL,
            AdFormat.REWARDED, AdFormat.NATIVE,
        }

    def test_single_format_single_tier(self, tmp_path: Path) -> None:
        """Minimal per-app file with one format and one tier works."""
        app_yaml = """\
        alias: hexar-ios

        banner:
          - All Countries: {countries: '*'}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        assert len(result) == 1
        assert result[0].name == "All Countries"
        assert result[0].countries == ["*"]
        assert result[0].is_default is True
        assert result[0].position == 1
        assert result[0].ad_formats == [AdFormat.BANNER]


class TestResolveAppTiersErrors:
    """Tests for error handling in resolve_app_tiers()."""

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        """Non-existent per-app file raises FileNotFoundError."""
        settings_dir = str(tmp_path / "settings")
        (tmp_path / "settings").mkdir(exist_ok=True)

        with pytest.raises(FileNotFoundError, match="Per-app settings file not found"):
            resolve_app_tiers("nonexistent", settings_dir=settings_dir)

    def test_missing_file_error_includes_path(self, tmp_path: Path) -> None:
        """FileNotFoundError message includes the file path."""
        settings_dir = str(tmp_path / "settings")
        (tmp_path / "settings").mkdir(exist_ok=True)

        with pytest.raises(FileNotFoundError, match="nonexistent.yaml"):
            resolve_app_tiers("nonexistent", settings_dir=settings_dir)

    def test_malformed_yaml_raises_config_validation_error(self, tmp_path: Path) -> None:
        """Malformed YAML raises ConfigValidationError."""
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=":\n  invalid: [yaml\n  missing bracket",
        )

        with pytest.raises(ConfigValidationError, match="Malformed YAML"):
            resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

    def test_missing_alias_field_raises_error(self, tmp_path: Path) -> None:
        """Per-app file without 'alias' field raises ConfigValidationError."""
        app_yaml = """\
        rewarded:
          - Tier 1: US
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        with pytest.raises(ConfigValidationError, match="missing required 'alias' field"):
            resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

    def test_alias_mismatch_raises_error(self, tmp_path: Path) -> None:
        """File alias mismatch raises ConfigValidationError."""
        app_yaml = """\
        alias: ss-ios

        rewarded:
          - Tier 1: {countries: US}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        with pytest.raises(ConfigValidationError, match="Alias mismatch"):
            resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

    def test_alias_mismatch_mentions_both_aliases(self, tmp_path: Path) -> None:
        """Alias mismatch error mentions both the file alias and the caller alias."""
        app_yaml = """\
        alias: ss-ios

        rewarded:
          - Tier 1: {countries: US}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        with pytest.raises(ConfigValidationError, match="ss-ios.*hexar-ios"):
            resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

    def test_alias_not_in_profiles_raises_error(self, tmp_path: Path) -> None:
        """Alias not found in profiles.yaml raises ConfigValidationError."""
        profiles_yaml = """\
        profiles:
          ss-ios:
            app_key: abc123
            app_name: Shelf Sort iOS
            platform: iOS
        """
        app_yaml = """\
        alias: hexar-ios

        rewarded:
          - Tier 1: {countries: US}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=profiles_yaml,
            app_yaml=app_yaml,
        )

        with pytest.raises(ConfigValidationError, match="not found in profiles.yaml"):
            resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

    def test_unknown_group_ref_raises_error(self, tmp_path: Path) -> None:
        """Country group ref not in countries.yaml raises ConfigValidationError."""
        app_yaml = """\
        alias: hexar-ios

        rewarded:
          - Tier 1: {countries: US}
          - Nonexistent Tier: {countries: nonexistent-group}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        with pytest.raises(ConfigValidationError, match="nonexistent-group.*not found in countries.yaml"):
            resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

    def test_unknown_group_ref_mentions_format_section(self, tmp_path: Path) -> None:
        """Error for unknown group ref mentions the format section it appeared in."""
        app_yaml = """\
        alias: hexar-ios

        interstitial:
          - Bad Tier: {countries: nonexistent-group}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        with pytest.raises(ConfigValidationError, match="interstitial"):
            resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

    def test_invalid_format_section_key_raises_error(self, tmp_path: Path) -> None:
        """Invalid format section key raises ConfigValidationError."""
        app_yaml = """\
        alias: hexar-ios

        invalidformat:
          - Tier 1: {countries: US}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        with pytest.raises(ConfigValidationError, match="Invalid format section 'invalidformat'"):
            resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

    def test_rewarded_video_format_rejected(self, tmp_path: Path) -> None:
        """Legacy 'rewardedVideo' format section key is rejected."""
        app_yaml = """\
        alias: hexar-ios

        rewardedVideo:
          - Tier 1: {countries: US}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        with pytest.raises(ConfigValidationError, match="Invalid format section 'rewardedVideo'"):
            resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

    def test_empty_file_raises_error(self, tmp_path: Path) -> None:
        """Empty YAML file raises ConfigValidationError."""
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
        )
        # Write an empty file manually (helper skips empty strings)
        (tmp_path / "settings" / "hexar-ios.yaml").write_text("")

        with pytest.raises(ConfigValidationError, match="must contain a YAML mapping"):
            resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

    def test_format_section_not_a_list_raises_error(self, tmp_path: Path) -> None:
        """Format section value that is not a list raises ConfigValidationError."""
        app_yaml = """\
        alias: hexar-ios

        rewarded: Tier 1
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        with pytest.raises(ConfigValidationError, match="must be a list of tier names"):
            resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

    def test_plain_string_entry_raises_error(self, tmp_path: Path) -> None:
        """Plain string entry (old format) raises ConfigValidationError with migration hint."""
        app_yaml = """\
        alias: hexar-ios

        rewarded:
          - Tier 1
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        with pytest.raises(ConfigValidationError, match="must be a mapping.*admedi pull"):
            resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

    def test_non_dict_entry_raises_error(self, tmp_path: Path) -> None:
        """Non-dict, non-string entry in format section raises ConfigValidationError."""
        app_yaml = """\
        alias: hexar-ios

        rewarded:
          - 123
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        with pytest.raises(ConfigValidationError, match="must be a single-key mapping"):
            resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

    def test_alias_only_file_returns_empty_list(self, tmp_path: Path) -> None:
        """A per-app file with only the alias field (no format sections) returns empty list."""
        app_yaml = """\
        alias: hexar-ios
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        assert result == []


class TestResolveAppTiersErrorMessages:
    """Tests verifying error messages include helpful context."""

    def test_unknown_group_ref_mentions_display_name(self, tmp_path: Path) -> None:
        """Error for unknown group ref includes the display name."""
        app_yaml = """\
        alias: hexar-ios

        rewarded:
          - My Custom Tier: {countries: nonexistent}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        with pytest.raises(ConfigValidationError, match="My Custom Tier"):
            resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

    def test_invalid_format_lists_valid_formats(self, tmp_path: Path) -> None:
        """Error for invalid format section lists valid format values."""
        app_yaml = """\
        alias: hexar-ios

        badformat:
          - Tier 1: {countries: US}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        with pytest.raises(ConfigValidationError, match="banner"):
            resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

    def test_alias_not_in_profiles_lists_available(self, tmp_path: Path) -> None:
        """Error for alias not in profiles lists available profiles."""
        profiles_yaml = """\
        profiles:
          ss-ios:
            app_key: abc123
            app_name: Shelf Sort iOS
            platform: iOS
        """
        app_yaml = """\
        alias: hexar-ios

        rewarded:
          - Tier 1: {countries: US}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=profiles_yaml,
            app_yaml=app_yaml,
        )

        with pytest.raises(ConfigValidationError, match="ss-ios"):
            resolve_app_tiers("hexar-ios", settings_dir=settings_dir)


class TestResolveAppTiersImport:
    """Tests verifying resolve_app_tiers is importable from admedi.engine."""

    def test_importable_from_engine_package(self) -> None:
        """resolve_app_tiers is importable from admedi.engine."""
        from admedi.engine import resolve_app_tiers as rat

        assert callable(rat)

    def test_importable_from_loader_module(self) -> None:
        """resolve_app_tiers is importable from admedi.engine.loader."""
        from admedi.engine.loader import resolve_app_tiers as rat

        assert callable(rat)


# ---------------------------------------------------------------------------
# Test Classes: resolve_app_tiers() dict format (Step 4)
# ---------------------------------------------------------------------------


class TestResolveAppTiersDictFormat:
    """Tests for resolve_app_tiers() with {countries: ref, networks: ref} dict format."""

    def test_string_value_raises_error_with_migration_hint(self, tmp_path: Path) -> None:
        """Plain string value (old format 'Tier 1: US') raises ConfigValidationError."""
        app_yaml = """\
        alias: hexar-ios

        rewarded:
          - Tier 1: US
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        with pytest.raises(ConfigValidationError, match="must be a dict.*admedi pull"):
            resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

    def test_dict_without_countries_key_raises_error(self, tmp_path: Path) -> None:
        """Dict entry missing 'countries' key raises ConfigValidationError."""
        app_yaml = """\
        alias: hexar-ios

        rewarded:
          - Tier 1: {networks: bidding-6}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        with pytest.raises(ConfigValidationError, match="missing required 'countries' key"):
            resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

    def test_dict_with_networks_stores_network_preset(self, tmp_path: Path) -> None:
        """Dict with 'networks' key stores network_preset on PortfolioTier."""
        app_yaml = """\
        alias: hexar-ios

        rewarded:
          - Tier 1: {countries: US, networks: bidding-6}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        assert len(result) == 1
        assert result[0].network_preset == "bidding-6"

    def test_dict_without_networks_stores_none(self, tmp_path: Path) -> None:
        """Dict without 'networks' key stores network_preset=None on PortfolioTier."""
        app_yaml = """\
        alias: hexar-ios

        rewarded:
          - Tier 1: {countries: US}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        assert len(result) == 1
        assert result[0].network_preset is None

    def test_wildcard_dict_with_no_networks(self, tmp_path: Path) -> None:
        """Wildcard entry '{countries: '*'}' resolves correctly with no network_preset."""
        app_yaml = """\
        alias: hexar-ios

        banner:
          - All Countries: {countries: '*'}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        assert len(result) == 1
        assert result[0].countries == ["*"]
        assert result[0].is_default is True
        assert result[0].network_preset is None

    def test_mixed_entries_with_and_without_networks(self, tmp_path: Path) -> None:
        """Mix of entries with and without 'networks' key stores correctly."""
        app_yaml = """\
        alias: hexar-ios

        interstitial:
          - Tier 1: {countries: US, networks: bidding-6}
          - All Countries: {countries: '*'}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        assert len(result) == 2
        assert result[0].network_preset == "bidding-6"
        assert result[1].network_preset is None

    def test_countries_still_resolved_from_dict(self, tmp_path: Path) -> None:
        """Country group ref in dict value is resolved via countries.yaml."""
        app_yaml = """\
        alias: hexar-ios

        rewarded:
          - Tier 2: {countries: high-value, networks: bidding-3}
        """
        settings_dir = _write_three_layer_files(
            tmp_path,
            countries_yaml=_COUNTRIES_YAML,
            profiles_yaml=_PROFILES_YAML,
            app_yaml=app_yaml,
        )

        result = resolve_app_tiers("hexar-ios", settings_dir=settings_dir)

        assert result[0].countries == ["AU", "CA", "DE", "GB"]
        assert result[0].network_preset == "bidding-3"


# ---------------------------------------------------------------------------
# Test Classes: load_network_presets()
# ---------------------------------------------------------------------------


def _setup_network_presets(tmp_path: Path, content: str) -> str:
    """Write networks.yaml and return settings_dir path for load_network_presets.

    Creates the file structure expected by load_network_presets():
    ``tmp_path/networks.yaml`` with ``settings_dir = tmp_path / "settings"``.
    """
    networks_path = tmp_path / "networks.yaml"
    networks_path.write_text(dedent(content))
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir(exist_ok=True)
    return str(settings_dir)


class TestLoadNetworkPresetsValid:
    """Tests for successful network preset loading."""

    def test_valid_presets_with_bidders_and_manuals(self, tmp_path: Path) -> None:
        """Valid presets with bidders and manual entries load correctly."""
        settings_dir = _setup_network_presets(tmp_path, """\
            bidding-2:
              - network: Meta Audience Network
                bidder: true
              - network: Google AdMob
                bidder: false
        """)

        result = load_network_presets(settings_dir=settings_dir)

        assert "bidding-2" in result
        assert len(result["bidding-2"]) == 2
        assert result["bidding-2"][0]["network"] == "Meta Audience Network"
        assert result["bidding-2"][0]["bidder"] is True
        assert result["bidding-2"][1]["network"] == "Google AdMob"
        assert result["bidding-2"][1]["bidder"] is False

    def test_valid_preset_with_name_field(self, tmp_path: Path) -> None:
        """Preset entries with optional name field are preserved."""
        settings_dir = _setup_network_presets(tmp_path, """\
            admob-multi:
              - network: Google AdMob
                bidder: false
                name: Google AdMob High
              - network: Google AdMob
                bidder: false
                name: Google AdMob Low
        """)

        result = load_network_presets(settings_dir=settings_dir)

        assert result["admob-multi"][0]["name"] == "Google AdMob High"
        assert result["admob-multi"][1]["name"] == "Google AdMob Low"

    def test_valid_preset_with_rate_field(self, tmp_path: Path) -> None:
        """Preset entries with optional rate field are preserved."""
        settings_dir = _setup_network_presets(tmp_path, """\
            manual-rates:
              - network: Google AdMob
                bidder: false
                rate: 12.5
              - network: InMobi
                bidder: false
                rate: 8.0
        """)

        result = load_network_presets(settings_dir=settings_dir)

        assert result["manual-rates"][0]["rate"] == 12.5
        assert result["manual-rates"][1]["rate"] == 8.0

    def test_empty_preset_list_is_valid(self, tmp_path: Path) -> None:
        """Empty list as a preset value does not raise an error."""
        settings_dir = _setup_network_presets(tmp_path, """\
            empty-preset: []
        """)

        result = load_network_presets(settings_dir=settings_dir)

        assert result["empty-preset"] == []

    def test_multiple_presets(self, tmp_path: Path) -> None:
        """Multiple presets are loaded correctly."""
        settings_dir = _setup_network_presets(tmp_path, """\
            bidding-1:
              - network: Meta Audience Network
                bidder: true
            manual-1:
              - network: Google AdMob
                bidder: false
        """)

        result = load_network_presets(settings_dir=settings_dir)

        assert len(result) == 2
        assert "bidding-1" in result
        assert "manual-1" in result

    def test_return_type_is_dict(self, tmp_path: Path) -> None:
        """Return type is dict[str, list[dict[str, Any]]]."""
        settings_dir = _setup_network_presets(tmp_path, """\
            preset-a:
              - network: InMobi
                bidder: true
        """)

        result = load_network_presets(settings_dir=settings_dir)

        assert isinstance(result, dict)
        assert isinstance(result["preset-a"], list)
        assert isinstance(result["preset-a"][0], dict)

    def test_entries_are_copies(self, tmp_path: Path) -> None:
        """Returned entries are copies, not references to internal data."""
        settings_dir = _setup_network_presets(tmp_path, """\
            preset-a:
              - network: InMobi
                bidder: true
        """)

        result = load_network_presets(settings_dir=settings_dir)
        result["preset-a"][0]["network"] = "MUTATED"

        # Re-load to verify original is not affected
        result2 = load_network_presets(settings_dir=settings_dir)
        assert result2["preset-a"][0]["network"] == "InMobi"


class TestLoadNetworkPresetsErrors:
    """Tests for error handling in load_network_presets."""

    def test_missing_file_raises_file_not_found_error(self, tmp_path: Path) -> None:
        """Missing networks.yaml raises FileNotFoundError."""
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()

        with pytest.raises(FileNotFoundError, match="Network presets file not found"):
            load_network_presets(settings_dir=str(settings_dir))

    def test_empty_file_raises_config_validation_error(self, tmp_path: Path) -> None:
        """Empty networks.yaml raises ConfigValidationError."""
        settings_dir = _setup_network_presets(tmp_path, "")

        with pytest.raises(ConfigValidationError, match="empty"):
            load_network_presets(settings_dir=settings_dir)

    def test_non_mapping_yaml_raises_config_validation_error(self, tmp_path: Path) -> None:
        """YAML that parses to a list raises ConfigValidationError."""
        settings_dir = _setup_network_presets(tmp_path, """\
            - item1
            - item2
        """)

        with pytest.raises(ConfigValidationError, match="mapping"):
            load_network_presets(settings_dir=settings_dir)

    def test_entry_missing_network_raises_config_validation_error(self, tmp_path: Path) -> None:
        """Entry without 'network' key raises ConfigValidationError."""
        settings_dir = _setup_network_presets(tmp_path, """\
            broken:
              - bidder: true
        """)

        with pytest.raises(ConfigValidationError, match="network"):
            load_network_presets(settings_dir=settings_dir)

    def test_entry_missing_bidder_raises_config_validation_error(self, tmp_path: Path) -> None:
        """Entry without 'bidder' key raises ConfigValidationError."""
        settings_dir = _setup_network_presets(tmp_path, """\
            broken:
              - network: InMobi
        """)

        with pytest.raises(ConfigValidationError, match="bidder"):
            load_network_presets(settings_dir=settings_dir)

    def test_preset_value_not_list_raises_config_validation_error(self, tmp_path: Path) -> None:
        """Preset value that is not a list raises ConfigValidationError."""
        settings_dir = _setup_network_presets(tmp_path, """\
            broken: "not a list"
        """)

        with pytest.raises(ConfigValidationError, match="list"):
            load_network_presets(settings_dir=settings_dir)

    def test_entry_not_dict_raises_config_validation_error(self, tmp_path: Path) -> None:
        """Entry that is not a dict raises ConfigValidationError."""
        settings_dir = _setup_network_presets(tmp_path, """\
            broken:
              - just a string
        """)

        with pytest.raises(ConfigValidationError, match="mapping"):
            load_network_presets(settings_dir=settings_dir)

    def test_malformed_yaml_raises_config_validation_error(self, tmp_path: Path) -> None:
        """Malformed YAML raises ConfigValidationError."""
        settings_dir = _setup_network_presets(tmp_path, ":\n  invalid: [yaml\n  missing bracket")

        with pytest.raises(ConfigValidationError, match="Malformed YAML"):
            load_network_presets(settings_dir=settings_dir)


class TestLoadNetworkPresetsImport:
    """Tests verifying load_network_presets is importable from admedi.engine."""

    def test_importable_from_engine_package(self) -> None:
        """load_network_presets is importable from admedi.engine."""
        from admedi.engine import load_network_presets as lnp

        assert callable(lnp)

    def test_importable_from_loader_module(self) -> None:
        """load_network_presets is importable from admedi.engine.loader."""
        from admedi.engine.loader import load_network_presets as lnp

        assert callable(lnp)

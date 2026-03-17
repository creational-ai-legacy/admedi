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

from admedi.engine.loader import load_template, load_tiers_settings
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
    """Tests verifying load_tiers_settings is importable from admedi.engine."""

    def test_importable_from_engine_package(self) -> None:
        """load_tiers_settings is importable from admedi.engine."""
        from admedi.engine import load_tiers_settings as lts

        assert callable(lts)

"""Portfolio models for config-driven ad mediation.

``PortfolioApp``, ``PortfolioTier``, and ``PortfolioConfig`` represent the
user's desired mediation state parsed from a YAML tier template. These are
the models that the Loader produces and the Differ consumes.

Unlike the existing ``TierTemplate`` / ``TierDefinition`` models (which are
frozen and scoped to a single ad-format template), these models support
per-tier ad_format scoping and are mutable for incremental construction by
the engine.

Validators enforce:
- Exactly one default tier
- Schema version must be supported (currently only ``1``)
- At least one portfolio app
- Country codes match ``^[A-Z]{2}$`` or ``"*"`` (catch-all)
- Per-format: no duplicate countries, no duplicate positions
- Ad format compatibility with the declared mediator

Examples:
    >>> from admedi.models.portfolio import PortfolioApp, PortfolioTier, PortfolioConfig
    >>> from admedi.models.enums import AdFormat, Mediator, Platform
    >>> config = PortfolioConfig(
    ...     mediator=Mediator.LEVELPLAY,
    ...     portfolio=[
    ...         PortfolioApp(app_key="abc123", name="My App", platform=Platform.ANDROID),
    ...     ],
    ...     tiers=[
    ...         PortfolioTier(
    ...             name="Tier 1",
    ...             countries=["US", "GB"],
    ...             position=1,
    ...             ad_formats=[AdFormat.INTERSTITIAL, AdFormat.BANNER],
    ...         ),
    ...         PortfolioTier(
    ...             name="All Countries",
    ...             countries=["*"],
    ...             position=2,
    ...             is_default=True,
    ...             ad_formats=[AdFormat.INTERSTITIAL, AdFormat.BANNER],
    ...         ),
    ...     ],
    ... )
    >>> config.mediator
    <Mediator.LEVELPLAY: 'levelplay'>
"""

from __future__ import annotations

import re
from collections import defaultdict

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from admedi.models.enums import AdFormat, Mediator, Platform

_COUNTRY_CODE_RE = re.compile(r"^[A-Z]{2}$")

_SUPPORTED_SCHEMA_VERSIONS = {1}
"""Set of schema versions this code can handle."""

# Ad formats incompatible with specific mediators.
# LevelPlay Groups v4 uses "rewarded" only; "rewardedVideo" is legacy v2.
_MEDIATOR_INCOMPATIBLE_FORMATS: dict[Mediator, set[AdFormat]] = {
    Mediator.LEVELPLAY: {AdFormat.REWARDED_VIDEO},
}


class PortfolioApp(BaseModel):
    """An application entry in a portfolio tier template.

    Represents a single app that mediation configs will be applied to.

    Attributes:
        app_key: Unique application identifier from LevelPlay. Must be
            non-empty.
        name: Human-readable display name for the application.
        platform: Target mobile platform (Android, iOS, Amazon).
    """

    model_config = ConfigDict(populate_by_name=True)

    app_key: str
    name: str
    platform: Platform

    @field_validator("app_key")
    @classmethod
    def _app_key_non_empty(cls, v: str) -> str:
        """Reject empty app_key values."""
        if not v.strip():
            raise ValueError("app_key must be a non-empty string")
        return v


class PortfolioTier(BaseModel):
    """A tier definition with per-tier ad format scoping.

    Each tier specifies which ad formats it applies to, enabling
    format-specific tier structures (e.g., different country groupings
    for banner vs interstitial).

    Attributes:
        name: Human-readable tier name (e.g., "Tier 1", "All Countries").
        countries: List of ISO 3166-1 alpha-2 country codes, or ``["*"]``
            for the default catch-all tier.
        position: Priority position (1 = highest priority). Must be >= 1.
        is_default: Whether this is the catch-all tier. Exactly one tier
            in a ``PortfolioConfig`` must have ``is_default=True``.
        ad_formats: Ad formats this tier applies to (e.g.,
            ``[AdFormat.INTERSTITIAL, AdFormat.BANNER]``).
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str
    countries: list[str]
    position: int = Field(ge=1)
    is_default: bool = False
    ad_formats: list[AdFormat]


class PortfolioConfig(BaseModel):
    """Full parsed and validated tier template for a portfolio.

    This is the top-level model that the Loader produces from YAML and
    the Differ consumes to compare against live API state. Model validators
    enforce structural integrity across tiers, formats, and mediator
    compatibility.

    Attributes:
        schema_version: Template schema version (currently only ``1``).
        mediator: Which mediation platform this config targets.
        portfolio: List of apps this config applies to.
        tiers: List of tier definitions with per-tier ad format scoping.
    """

    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = 1
    mediator: Mediator
    portfolio: list[PortfolioApp]
    tiers: list[PortfolioTier]

    @model_validator(mode="after")
    def _validate_config(self) -> PortfolioConfig:
        """Validate the full portfolio config after construction.

        Checks (in order):
        1. ``schema_version`` is supported.
        2. At least one portfolio app is defined.
        3. All country codes are valid (``^[A-Z]{2}$`` or ``"*"``).
        4. Ad formats are compatible with the declared mediator.
        5. Exactly one tier has ``is_default=True``.
        6. Per-format: no duplicate country codes across tiers.
        7. Per-format: no duplicate position values across tiers.
        """
        self._check_schema_version()
        self._check_portfolio_not_empty()
        self._check_country_codes()
        self._check_mediator_format_compatibility()
        self._check_exactly_one_default_tier()
        format_groups = self._build_format_groups()
        self._check_no_duplicate_countries_per_format(format_groups)
        self._check_no_duplicate_positions_per_format(format_groups)
        return self

    def _check_schema_version(self) -> None:
        """Verify schema_version is a supported value."""
        if self.schema_version not in _SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"Unsupported schema_version={self.schema_version}; "
                f"supported versions: {sorted(_SUPPORTED_SCHEMA_VERSIONS)}"
            )

    def _check_portfolio_not_empty(self) -> None:
        """Verify at least one app is defined in the portfolio."""
        if not self.portfolio:
            raise ValueError(
                "portfolio must contain at least one app"
            )

    def _check_country_codes(self) -> None:
        """Verify all country codes match ^[A-Z]{2}$ or are '*'."""
        for tier in self.tiers:
            for code in tier.countries:
                if code == "*":
                    continue
                if not _COUNTRY_CODE_RE.match(code):
                    raise ValueError(
                        f"Invalid country code '{code}' in tier '{tier.name}': "
                        f"must be exactly 2 uppercase letters (e.g., 'US', 'GB') "
                        f"or '*' for catch-all"
                    )

    def _check_mediator_format_compatibility(self) -> None:
        """Verify ad_formats are compatible with the declared mediator."""
        incompatible = _MEDIATOR_INCOMPATIBLE_FORMATS.get(self.mediator, set())
        if not incompatible:
            return
        for tier in self.tiers:
            for fmt in tier.ad_formats:
                if fmt in incompatible:
                    raise ValueError(
                        f"Ad format '{fmt.value}' is incompatible with "
                        f"mediator '{self.mediator.value}': "
                        f"LevelPlay Groups v4 uses 'rewarded' instead of "
                        f"'rewardedVideo'"
                    )

    def _check_exactly_one_default_tier(self) -> None:
        """Verify exactly one tier has is_default=True."""
        default_tiers = [t for t in self.tiers if t.is_default]
        if len(default_tiers) == 0:
            raise ValueError(
                "PortfolioConfig requires exactly one default tier "
                "(is_default=True), but none was found"
            )
        if len(default_tiers) > 1:
            names = [t.name for t in default_tiers]
            raise ValueError(
                f"PortfolioConfig requires exactly one default tier, "
                f"but found {len(default_tiers)}: {names}"
            )

    def _build_format_groups(self) -> dict[AdFormat, list[PortfolioTier]]:
        """Build a mapping of ad format to tiers that include that format."""
        groups: dict[AdFormat, list[PortfolioTier]] = defaultdict(list)
        for tier in self.tiers:
            for fmt in tier.ad_formats:
                groups[fmt].append(tier)
        return dict(groups)

    def _check_no_duplicate_countries_per_format(
        self, format_groups: dict[AdFormat, list[PortfolioTier]]
    ) -> None:
        """Verify no duplicate country codes within the same ad format scope.

        The same country in different format scopes is allowed (e.g., US in
        interstitial Tier 1 and banner All Countries).
        """
        for fmt, tiers in format_groups.items():
            seen: dict[str, str] = {}  # country_code -> tier_name
            for tier in tiers:
                if tier.is_default:
                    continue
                for code in tier.countries:
                    if code == "*":
                        continue
                    if code in seen:
                        raise ValueError(
                            f"Duplicate country '{code}' in ad format "
                            f"'{fmt.value}': found in tier '{tier.name}' "
                            f"and tier '{seen[code]}'"
                        )
                    seen[code] = tier.name

    def _check_no_duplicate_positions_per_format(
        self, format_groups: dict[AdFormat, list[PortfolioTier]]
    ) -> None:
        """Verify no duplicate position values within the same ad format scope."""
        for fmt, tiers in format_groups.items():
            seen: dict[int, str] = {}  # position -> tier_name
            for tier in tiers:
                if tier.position in seen:
                    raise ValueError(
                        f"Duplicate position {tier.position} in ad format "
                        f"'{fmt.value}': found in tier '{tier.name}' "
                        f"and tier '{seen[tier.position]}'"
                    )
                seen[tier.position] = tier.name

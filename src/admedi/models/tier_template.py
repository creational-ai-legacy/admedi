"""Tier template models for config-driven ad mediation.

``TierDefinition`` and ``TierTemplate`` represent the user's desired state
loaded from YAML config files. They are frozen (immutable) after construction
because they are declarative templates that should never be mutated at runtime.

Validators enforce:
- Exactly one default tier per template
- No duplicate countries across non-default tiers
- Country codes match ``^[A-Z]{2}$`` format (ISO 3166-1 alpha-2 shape)

Examples:
    >>> from admedi.models.tier_template import TierDefinition, TierTemplate
    >>> from admedi.models.enums import AdFormat
    >>> template = TierTemplate(
    ...     name="Shelf Sort Interstitial",
    ...     ad_formats=[AdFormat.INTERSTITIAL],
    ...     tiers=[
    ...         TierDefinition(name="Tier 1", countries=["US"], position=1),
    ...         TierDefinition(name="Tier 2", countries=["AU", "CA"], position=2),
    ...         TierDefinition(
    ...             name="All Countries", countries=[], position=3, is_default=True
    ...         ),
    ...     ],
    ... )
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, model_validator

from admedi.models.enums import AdFormat

_COUNTRY_CODE_RE = re.compile(r"^[A-Z]{2}$")


class TierDefinition(BaseModel):
    """A single tier within a mediation template.

    Attributes:
        name: Human-readable tier name (e.g., "Tier 1", "All Countries").
        countries: List of ISO 3166-1 alpha-2 country codes assigned to this
            tier. Empty list is valid for the default (catch-all) tier.
        position: User-assigned priority order (1 = highest CPM tier).
        floor_price: Optional CPM floor price for this tier.
        is_default: Whether this tier is the catch-all "All Countries" tier.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    name: str
    countries: list[str]
    position: int
    floor_price: float | None = None
    is_default: bool = False


class TierTemplate(BaseModel):
    """A mediation tier template loaded from YAML config.

    Defines the tier structure, country assignments, and target ad formats
    for a mediation configuration. Validators enforce structural integrity:

    - Exactly one tier must have ``is_default=True``.
    - No country code may appear in more than one non-default tier.
    - All country codes in non-default tiers must match ``^[A-Z]{2}$``.

    Attributes:
        name: Template display name (e.g., "Shelf Sort Interstitial").
        ad_formats: Ad formats this template applies to.
        tiers: Ordered list of tier definitions.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    name: str
    ad_formats: list[AdFormat]
    tiers: list[TierDefinition]

    @model_validator(mode="after")
    def _validate_tiers(self) -> TierTemplate:
        """Validate tier structure after construction.

        Checks:
        1. Exactly one tier has ``is_default=True``.
        2. No duplicate countries across non-default tiers.
        3. All country codes in non-default tiers match ``^[A-Z]{2}$``.
        """
        # 1. Exactly one default tier
        default_tiers = [t for t in self.tiers if t.is_default]
        if len(default_tiers) == 0:
            raise ValueError(
                "TierTemplate requires exactly one default tier "
                "(is_default=True), but none was found"
            )
        if len(default_tiers) > 1:
            names = [t.name for t in default_tiers]
            raise ValueError(
                f"TierTemplate requires exactly one default tier, "
                f"but found {len(default_tiers)}: {names}"
            )

        # 2. No duplicate countries across non-default tiers
        seen: dict[str, str] = {}  # country_code -> tier_name
        for tier in self.tiers:
            if tier.is_default:
                continue
            for code in tier.countries:
                if code in seen:
                    raise ValueError(
                        f"Duplicate country '{code}' found in tier "
                        f"'{tier.name}' and tier '{seen[code]}'"
                    )
                seen[code] = tier.name

        # 3. Country codes in non-default tiers must match ^[A-Z]{2}$
        for tier in self.tiers:
            if tier.is_default:
                continue
            for code in tier.countries:
                if not _COUNTRY_CODE_RE.match(code):
                    raise ValueError(
                        f"Invalid country code '{code}' in tier '{tier.name}': "
                        f"must be exactly 2 uppercase letters (e.g., 'US', 'GB')"
                    )

        return self

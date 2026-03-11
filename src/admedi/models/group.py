"""Group model for LevelPlay mediation groups.

A ``Group`` represents a country-based mediation group as returned by the
LevelPlay Groups v4 API. Groups contain targeting (countries), ordering
(position), and optionally a full waterfall configuration with nested
tier/instance structures.

Examples:
    >>> from admedi.models.group import Group
    >>> from admedi.models.enums import AdFormat
    >>> group = Group.model_validate({
    ...     "groupName": "US Tier 1",
    ...     "adFormat": "interstitial",
    ...     "countries": ["US"],
    ...     "position": 1,
    ... })
    >>> group.group_name
    'US Tier 1'
    >>> group.ad_format
    <AdFormat.INTERSTITIAL: 'interstitial'>
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from admedi.models.enums import AdFormat
from admedi.models.instance import Instance
from admedi.models.waterfall import WaterfallConfig


class Group(BaseModel):
    """A LevelPlay mediation group.

    Groups define country-targeted mediation configurations with an optional
    waterfall that organizes ad network instances into ordered tiers.

    Attributes:
        group_id: Unique group identifier (``groupId`` in API JSON).
        group_name: Display name of the group.
        ad_format: The ad format this group handles (banner, interstitial,
            or rewardedVideo).
        countries: List of ISO 3166-1 alpha-2 country codes targeted by
            this group.
        position: Priority position of this group (lower = higher priority).
        floor_price: Minimum CPM floor price for this group.
        ab_test: Active A/B test identifier, if any.
        instances: Flat list of instances (API shape for some responses).
        waterfall: Full waterfall configuration with tier/instance nesting.
        mediation_ad_unit_id: Mediation ad unit identifier.
        mediation_ad_unit_name: Mediation ad unit display name.
        segments: Audience segments applied to this group.
    """

    model_config = ConfigDict(populate_by_name=True)

    group_id: int | None = Field(default=None, alias="groupId")
    group_name: str = Field(alias="groupName")
    ad_format: AdFormat = Field(alias="adFormat")
    countries: list[str]
    position: int
    floor_price: float | None = Field(default=None, alias="floorPrice")
    ab_test: str | None = Field(default=None, alias="abTest")
    instances: list[Instance] | None = None
    waterfall: WaterfallConfig | None = None
    mediation_ad_unit_id: str | None = Field(
        default=None, alias="mediationAdUnitId"
    )
    mediation_ad_unit_name: str | None = Field(
        default=None, alias="mediationAdUnitName"
    )
    segments: list[Any] | None = None

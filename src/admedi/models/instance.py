"""Instance and CountryRate models for LevelPlay ad network instances.

``Instance`` is a unified model covering both the Groups v4 embedded shape
(instances nested inside a group/waterfall) and the Standalone Instances API
shape (which includes extra fields like ``ad_unit`` and ``is_live``).

``CountryRate`` represents a per-country rate override within an instance.

Examples:
    >>> from admedi.models.instance import Instance, CountryRate
    >>> inst = Instance.model_validate({
    ...     "id": 1,
    ...     "name": "ironSource Default",
    ...     "networkName": "ironSource",
    ...     "isBidder": True,
    ... })
    >>> inst.instance_name
    'ironSource Default'
    >>> inst.model_dump(by_alias=True)["networkName"]
    'ironSource'
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from admedi.models.enums import AdFormat


class CountryRate(BaseModel):
    """Per-country rate override for an instance.

    Attributes:
        country_code: ISO 3166-1 alpha-2 country code (e.g., "US").
        rate: CPM rate override for this country.
    """

    model_config = ConfigDict(populate_by_name=True)

    country_code: str = Field(alias="countryCode")
    rate: float


class Instance(BaseModel):
    """A LevelPlay ad network instance.

    Unified model for both Groups v4 embedded instances and the Standalone
    Instances API. Common fields are always present; standalone-only fields
    (``ad_unit``, ``is_live``, ``is_optimized``) are optional.

    Attributes:
        instance_id: Unique instance identifier (``id`` in API JSON).
        instance_name: Display name of the instance.
        network_name: Ad network identifier (e.g., "ironSource", "AdMob").
        is_bidder: Whether this instance participates in bidding.
        group_rate: Default CPM rate for the instance within a group.
        countries_rate: Per-country rate overrides.
        ad_unit: Ad format (standalone API only).
        is_live: Whether the instance is live (standalone API only).
        is_optimized: Whether the instance is optimized (standalone API only).
    """

    model_config = ConfigDict(populate_by_name=True)

    instance_id: int | None = Field(default=None, alias="id")
    instance_name: str = Field(alias="name")
    network_name: str = Field(alias="networkName")
    is_bidder: bool = Field(default=False, alias="isBidder")
    group_rate: float | None = Field(default=None, alias="groupRate")
    countries_rate: list[CountryRate] | None = Field(
        default=None, alias="countriesRate"
    )
    ad_unit: AdFormat | None = Field(default=None, alias="adUnit")
    is_live: bool | None = Field(default=None, alias="isLive")
    is_optimized: bool | None = Field(default=None, alias="isOptimized")

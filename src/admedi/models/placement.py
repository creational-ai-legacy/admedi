"""Placement, Capping, and Pacing models for LevelPlay placements.

Includes ``normalize_bool``, a reusable boolean normalization function that
handles the various encodings the LevelPlay API uses for boolean fields
(``0``/``1``, ``"true"``/``"false"``, ``"active"``/``"inactive"``, etc.).

Examples:
    >>> from admedi.models.placement import Placement, Capping, Pacing
    >>> p = Placement.model_validate({
    ...     "name": "Default",
    ...     "adUnit": "banner",
    ...     "adDelivery": 1,
    ... })
    >>> p.ad_delivery
    True
    >>> p = Placement.model_validate({
    ...     "name": "Disabled",
    ...     "adUnit": "interstitial",
    ...     "adDelivery": "inactive",
    ... })
    >>> p.ad_delivery
    False
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from admedi.models.enums import AdFormat


def normalize_bool(value: Any) -> bool:
    """Normalize various LevelPlay boolean encodings to Python ``bool``.

    Handles the following input forms:
    - ``bool``: passed through unchanged
    - ``int``: ``0`` -> ``False``, ``1`` -> ``True``
    - ``str``: ``"0"`` / ``"false"`` / ``"inactive"`` -> ``False``;
      ``"1"`` / ``"true"`` / ``"active"`` / ``"live"`` -> ``True``

    Args:
        value: The raw value from the API response.

    Returns:
        Normalized boolean value.

    Raises:
        ValueError: If the value cannot be interpreted as a boolean.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value == 0:
            return False
        if value == 1:
            return True
        msg = f"Cannot interpret integer {value!r} as bool; expected 0 or 1"
        raise ValueError(msg)
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in ("0", "false", "inactive"):
            return False
        if lowered in ("1", "true", "active", "live"):
            return True
        msg = f"Cannot interpret string {value!r} as bool"
        raise ValueError(msg)
    msg = f"Cannot interpret {type(value).__name__} {value!r} as bool"
    raise ValueError(msg)


class Capping(BaseModel):
    """Frequency capping configuration for a placement.

    Attributes:
        enabled: Whether capping is active.
        amount: Maximum impressions allowed per interval.
        interval: Time window for the cap (e.g., "day", "hour").
    """

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool
    amount: int | None = None
    interval: str | None = None


class Pacing(BaseModel):
    """Pacing (cooldown) configuration for a placement.

    Attributes:
        enabled: Whether pacing is active.
        seconds: Minimum seconds between ad impressions.
    """

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool
    seconds: float | None = None


class Placement(BaseModel):
    """A LevelPlay placement within an application.

    The ``ad_delivery`` field uses ``normalize_bool`` to handle the various
    boolean encodings the LevelPlay API uses (``0``/``1``, ``"active"``/
    ``"inactive"``, ``"true"``/``"false"``).

    Attributes:
        placement_id: Unique placement identifier (``id`` in API JSON).
        name: Display name of the placement.
        ad_unit: Ad format for this placement.
        ad_delivery: Whether the placement is actively delivering ads.
        capping: Frequency capping configuration, if set.
        pacing: Pacing (cooldown) configuration, if set.
    """

    model_config = ConfigDict(populate_by_name=True)

    placement_id: int | None = Field(default=None, alias="id")
    name: str
    ad_unit: AdFormat = Field(alias="adUnit")
    ad_delivery: bool = Field(alias="adDelivery")
    capping: Capping | None = None
    pacing: Pacing | None = None

    @field_validator("ad_delivery", mode="before")
    @classmethod
    def _normalize_ad_delivery(cls, value: Any) -> bool:
        """Normalize ad_delivery from LevelPlay API encodings."""
        return normalize_bool(value)

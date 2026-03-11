"""Waterfall configuration models for LevelPlay group waterfalls.

``WaterfallTier`` represents a single tier slot (bidding, tier1, tier2, tier3)
within a waterfall, holding its ordering strategy and the instances assigned
to it.

``WaterfallConfig`` assembles the full waterfall with a model validator that
rejects the incompatible combination of OPTIMIZED tier type coexisting with
a bidding slot (per ironSource JS lib finding).

Examples:
    >>> from admedi.models.waterfall import WaterfallConfig, WaterfallTier
    >>> from admedi.models.enums import TierType
    >>> wf = WaterfallConfig(
    ...     bidding=WaterfallTier(tier_type=TierType.BIDDING),
    ...     tier1=WaterfallTier(tier_type=TierType.MANUAL),
    ... )
    >>> wf.tier1.tier_type
    <TierType.MANUAL: 'manual'>
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from admedi.models.enums import TierType
from admedi.models.instance import Instance


class WaterfallTier(BaseModel):
    """A single tier slot within a waterfall configuration.

    Attributes:
        tier_type: The ordering strategy for this tier (manual, sortByCpm,
            optimized, or bidding).
        instances: List of ad network instances assigned to this tier.
    """

    model_config = ConfigDict(populate_by_name=True)

    tier_type: TierType = Field(alias="tierType")
    instances: list[Instance] = Field(default_factory=list)


class WaterfallConfig(BaseModel):
    """Full waterfall configuration for a LevelPlay group.

    Contains up to four tier slots: a bidding slot and three numbered tiers.
    A model validator enforces that the OPTIMIZED tier type cannot coexist
    with a bidding slot, per ironSource JS lib behavior.

    Attributes:
        bidding: The bidding tier slot (optional).
        tier1: The first waterfall tier slot (optional).
        tier2: The second waterfall tier slot (optional).
        tier3: The third waterfall tier slot (optional).
    """

    model_config = ConfigDict(populate_by_name=True)

    bidding: WaterfallTier | None = None
    tier1: WaterfallTier | None = None
    tier2: WaterfallTier | None = None
    tier3: WaterfallTier | None = None

    @model_validator(mode="after")
    def _reject_optimized_with_bidding(self) -> WaterfallConfig:
        """Reject OPTIMIZED tier type when a bidding slot is present.

        The LevelPlay platform does not support the OPTIMIZED ordering
        strategy when bidding instances are active in the waterfall.

        Raises:
            ValueError: If any numbered tier uses OPTIMIZED and the bidding
                slot is not None.
        """
        if self.bidding is None:
            return self

        for tier in [self.tier1, self.tier2, self.tier3]:
            if tier is not None and tier.tier_type == TierType.OPTIMIZED:
                msg = (
                    "OPTIMIZED tier type cannot coexist with a bidding tier. "
                    "Use MANUAL or SORT_BY_CPM when bidding instances are present."
                )
                raise ValueError(msg)

        return self

"""Pure-function Differ for config-as-code comparison.

Compares a ``list[PortfolioTier]`` (desired state from YAML template) against
remote ``Group`` models (live LevelPlay state) and produces a structured
``AppDiffReport`` describing the actions needed to reconcile them.

The differ is a pure function with no side effects and no API calls. It
operates on one app at a time -- the ``ConfigEngine`` orchestrator calls
``compute_diff()`` once per app.

Matching strategy (per ad_format):

1. Collect template tiers scoped to this format.
2. Collect remote groups with this format.
3. Match by **name** (primary), then by **position** (secondary).
4. Unmatched template tiers produce ``DiffAction.CREATE``.
5. Unmatched remote groups produce ``DiffAction.EXTRA``.

**Known limitation**: Simultaneous rename + reposition (template renames a
tier AND changes its position) produces CREATE + EXTRA instead of a single
UPDATE, because both name match and position match fail. Renames and
repositions should be done in separate syncs.

Examples:
    >>> from admedi.engine.differ import compute_diff
    >>> from admedi.models.portfolio import PortfolioTier
    >>> from admedi.models.group import Group
    >>> # report = compute_diff(tiers, remote_groups, "abc123", "My App")
    >>> # report.group_diffs  # list of GroupDiff entries
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from admedi.exceptions import ConfigValidationError
from admedi.models.diff import (
    AppDiffReport,
    DiffAction,
    FieldChange,
    GroupDiff,
)
from admedi.models.enums import AdFormat
from admedi.models.group import Group
from admedi.models.portfolio import PortfolioTier, SyncScope


def compute_diff(
    tiers: list[PortfolioTier],
    remote_groups: list[Group],
    app_key: str,
    app_name: str,
    *,
    network_presets: dict[str, list[dict[str, Any]]] | None = None,
    scope: SyncScope | None = None,
) -> AppDiffReport:
    """Compare portfolio tiers against remote groups for one app.

    Pure function -- no side effects, no API calls. Iterates over the
    **union** of template ad_formats and remote ad_formats to ensure
    remote groups in uncovered formats are surfaced as ``EXTRA``.

    Args:
        tiers: The desired portfolio tiers from the template.
        remote_groups: Live groups fetched from the LevelPlay API.
        app_key: The app's unique key.
        app_name: Display name of the app.
        network_presets: Optional mapping of preset names to their entry
            lists (from ``networks.yaml``). When provided, waterfalls are
            compared for tiers that have ``network_preset`` set. When
            ``None``, network comparison is skipped entirely.
        scope: Optional sync scope to control which comparisons are
            performed. When ``None``, defaults to full comparison
            (backward compatible). When ``scope.tiers is False``, tier
            field comparisons (countries, position, name) are skipped.

    Returns:
        An ``AppDiffReport`` with per-group diffs and A/B test detection.
    """
    # Build format -> tiers mapping from template
    template_by_format = _build_template_format_map(tiers)

    # Build format -> groups mapping from remote
    remote_by_format = _build_remote_format_map(remote_groups)

    # Iterate over the union of all formats
    all_formats = set(template_by_format.keys()) | set(remote_by_format.keys())

    group_diffs: list[GroupDiff] = []
    for fmt in sorted(all_formats, key=lambda f: f.value):
        tiers = template_by_format.get(fmt, [])
        groups = remote_by_format.get(fmt, [])
        format_diffs = _diff_format(fmt, tiers, groups, network_presets, scope)
        group_diffs.extend(format_diffs)

    # A/B test detection
    has_ab_test = _detect_ab_test(remote_groups)
    ab_test_warning: str | None = None
    if has_ab_test:
        ab_test_names = [
            g.ab_test
            for g in remote_groups
            if g.ab_test is not None and g.ab_test != "N/A"
        ]
        ab_test_warning = (
            f"Active A/B test(s) detected: {', '.join(ab_test_names)}. "
            f"Review before applying changes."
        )

    return AppDiffReport(
        app_key=app_key,
        app_name=app_name,
        group_diffs=group_diffs,
        has_ab_test=has_ab_test,
        ab_test_warning=ab_test_warning,
    )


def _build_template_format_map(
    tiers: list[PortfolioTier],
) -> dict[AdFormat, list[PortfolioTier]]:
    """Build a mapping of ad format to template tiers that include it."""
    result: dict[AdFormat, list[PortfolioTier]] = defaultdict(list)
    for tier in tiers:
        for fmt in tier.ad_formats:
            result[fmt].append(tier)
    return dict(result)


def _build_remote_format_map(
    remote_groups: list[Group],
) -> dict[AdFormat, list[Group]]:
    """Build a mapping of ad format to remote groups with that format."""
    result: dict[AdFormat, list[Group]] = defaultdict(list)
    for group in remote_groups:
        result[group.ad_format].append(group)
    return dict(result)


def _diff_format(
    ad_format: AdFormat,
    tiers: list[PortfolioTier],
    remote_groups: list[Group],
    network_presets: dict[str, list[dict[str, Any]]] | None = None,
    scope: SyncScope | None = None,
) -> list[GroupDiff]:
    """Produce diffs for one ad_format by matching tiers to remote groups.

    Matching strategy:
    1. Match by name (primary) -- exact string match on tier.name vs group.group_name
    2. Match by position (secondary) -- for unmatched tiers/groups
    3. Unmatched tiers -> CREATE
    4. Unmatched groups -> EXTRA
    """
    diffs: list[GroupDiff] = []

    # Track which tiers and groups have been matched
    matched_tiers: set[int] = set()  # indices into tiers list
    matched_groups: set[int] = set()  # indices into remote_groups list

    # Phase 1: Match by name (primary)
    for t_idx, tier in enumerate(tiers):
        for g_idx, group in enumerate(remote_groups):
            if g_idx in matched_groups:
                continue
            if tier.name == group.group_name:
                matched_tiers.add(t_idx)
                matched_groups.add(g_idx)
                diff = _compare_matched_pair(
                    ad_format, tier, group, len(tiers), len(remote_groups),
                    network_presets, scope,
                )
                diffs.append(diff)
                break

    # Phase 2: Match by position (secondary) for remaining unmatched
    unmatched_tiers = [
        (t_idx, tier)
        for t_idx, tier in enumerate(tiers)
        if t_idx not in matched_tiers
    ]
    unmatched_groups = [
        (g_idx, group)
        for g_idx, group in enumerate(remote_groups)
        if g_idx not in matched_groups
    ]

    for t_idx, tier in unmatched_tiers[:]:
        for g_idx, group in unmatched_groups[:]:
            if tier.position == group.position:
                matched_tiers.add(t_idx)
                matched_groups.add(g_idx)
                unmatched_tiers = [
                    (i, t) for i, t in unmatched_tiers if i != t_idx
                ]
                unmatched_groups = [
                    (i, g) for i, g in unmatched_groups if i != g_idx
                ]
                diff = _compare_matched_pair(
                    ad_format, tier, group, len(tiers), len(remote_groups),
                    network_presets, scope,
                )
                diffs.append(diff)
                break

    # Phase 3: Remaining unmatched tiers -> CREATE
    for t_idx, tier in unmatched_tiers:
        desired = Group(
            group_id=None,
            group_name=tier.name,
            ad_format=ad_format,
            countries=tier.countries,
            position=tier.position,
        )
        diffs.append(
            GroupDiff(
                action=DiffAction.CREATE,
                group_name=tier.name,
                group_id=None,
                ad_format=ad_format,
                changes=[],
                desired_group=desired,
            )
        )

    # Phase 4: Remaining unmatched remote groups -> EXTRA
    for g_idx, group in unmatched_groups:
        diffs.append(
            GroupDiff(
                action=DiffAction.EXTRA,
                group_name=group.group_name,
                group_id=group.group_id,
                ad_format=ad_format,
                changes=[],
                desired_group=None,
            )
        )

    return diffs


def _compare_matched_pair(
    ad_format: AdFormat,
    tier: PortfolioTier,
    group: Group,
    template_count: int,
    remote_count: int,
    network_presets: dict[str, list[dict[str, Any]]] | None = None,
    scope: SyncScope | None = None,
) -> GroupDiff:
    """Compare a matched template tier and remote group, producing a GroupDiff.

    Compares template-controlled fields (countries, position, name) and
    optionally the network waterfall when ``network_presets`` is provided
    and the tier has a ``network_preset`` reference.

    When ``scope`` is provided and ``scope.tiers is False``, tier field
    comparisons (countries, position, name) are skipped. This enables
    ``--networks``-only sync where only waterfall changes are surfaced.

    Args:
        ad_format: The ad format being compared.
        tier: The template tier (desired state).
        group: The remote group (live state).
        template_count: Total number of template tiers for this format.
        remote_count: Total number of remote groups for this format.
        network_presets: Optional preset name -> entries mapping. When
            provided and the tier references a preset, waterfall comparison
            is performed.
        scope: Optional sync scope. When ``scope.tiers is False``, tier
            field comparisons are skipped.

    Returns:
        A ``GroupDiff`` with action UNCHANGED or UPDATE.
    """
    changes: list[FieldChange] = []

    # Compare tier fields only when scope allows (or scope is None)
    compare_tiers = scope is None or scope.tiers

    if compare_tiers:
        # Compare countries (set comparison)
        _compare_countries(tier, group, changes)

        # Compare position (skip if single group in both template and remote)
        skip_position = template_count == 1 and remote_count == 1
        if not skip_position:
            _compare_position(tier, group, changes)

        # Compare name
        _compare_name(tier, group, changes)

    # Compare waterfall (when preset is configured and presets are loaded)
    if tier.network_preset is not None and network_presets is not None:
        if tier.network_preset not in network_presets:
            raise ConfigValidationError(
                f"Tier '{tier.name}' references network preset "
                f"'{tier.network_preset}' which does not exist in "
                f"networks.yaml"
            )
        preset_entries = network_presets[tier.network_preset]
        waterfall_changes = _compare_waterfall(preset_entries, group)
        changes.extend(waterfall_changes)

    if changes:
        desired = Group(
            group_id=group.group_id,
            group_name=tier.name,
            ad_format=ad_format,
            countries=tier.countries,
            position=tier.position,
        )
        return GroupDiff(
            action=DiffAction.UPDATE,
            group_name=group.group_name,
            group_id=group.group_id,
            ad_format=ad_format,
            changes=changes,
            desired_group=desired,
        )

    return GroupDiff(
        action=DiffAction.UNCHANGED,
        group_name=group.group_name,
        group_id=group.group_id,
        ad_format=ad_format,
        changes=[],
        desired_group=None,
    )


def _compare_countries(
    tier: PortfolioTier,
    group: Group,
    changes: list[FieldChange],
) -> None:
    """Compare countries between template tier and remote group.

    Uses set comparison for specific country lists. The wildcard ``["*"]``
    is compared as a literal string value -- it is not expanded to all
    country codes.
    """
    template_countries = set(tier.countries)
    remote_countries = set(group.countries)

    if template_countries != remote_countries:
        added = sorted(template_countries - remote_countries)
        removed = sorted(remote_countries - template_countries)
        parts: list[str] = []
        if added:
            parts.append(f"added {', '.join(added)}")
        if removed:
            parts.append(f"removed {', '.join(removed)}")
        description = "Countries: " + "; ".join(parts)
        changes.append(
            FieldChange(
                field="countries",
                old_value=sorted(remote_countries),
                new_value=sorted(template_countries),
                description=description,
            )
        )


def _compare_position(
    tier: PortfolioTier,
    group: Group,
    changes: list[FieldChange],
) -> None:
    """Compare position between template tier and remote group."""
    if tier.position != group.position:
        changes.append(
            FieldChange(
                field="position",
                old_value=group.position,
                new_value=tier.position,
                description=f"Position: {group.position} -> {tier.position}",
            )
        )


def _compare_name(
    tier: PortfolioTier,
    group: Group,
    changes: list[FieldChange],
) -> None:
    """Compare name between template tier and remote group."""
    if tier.name != group.group_name:
        changes.append(
            FieldChange(
                field="name",
                old_value=group.group_name,
                new_value=tier.name,
                description=f"Name: '{group.group_name}' -> '{tier.name}'",
            )
        )


def _compare_waterfall(
    preset_entries: list[dict[str, Any]],
    group: Group,
) -> list[FieldChange]:
    """Compare a network waterfall preset against a group's live instances.

    Matches preset entries to live instances by ``(networkName, isBidder)``
    and optionally ``instance_name`` when the preset entry has a ``name``
    key. Produces ``FieldChange`` entries for:

    - **Missing**: preset entry has no matching live instance (informational,
      the instance may not be configured in this app).
    - **Extra**: live instance has no matching preset entry (informational;
      ``adSourcePriority`` PUT reorders but cannot remove instances).
    - **Rate change**: preset ``rate`` differs from live ``groupRate``
      beyond a tolerance of 0.01.

    Args:
        preset_entries: List of dicts from ``networks.yaml`` preset
            (each with ``network``, ``bidder``, and optional ``rate``/``name``).
        group: The remote group with live instances.

    Returns:
        List of ``FieldChange`` entries with ``field="waterfall"``.
    """
    changes: list[FieldChange] = []
    live_instances = group.instances or []

    # Handle None instances (group has no waterfall data)
    if group.instances is None:
        changes.append(
            FieldChange(
                field="waterfall",
                old_value=None,
                new_value="preset configured",
                description=(
                    f"Waterfall: group '{group.group_name}' has no instance "
                    f"data (instances=None); cannot compare waterfall"
                ),
            )
        )
        return changes

    # Track which live instances are matched by preset entries
    matched_live_indices: set[int] = set()

    for entry in preset_entries:
        network = entry["network"]
        is_bidder = entry["bidder"]
        preset_name = entry.get("name")
        preset_rate = entry.get("rate")

        # Find matching live instance(s)
        candidates = [
            (idx, inst)
            for idx, inst in enumerate(live_instances)
            if inst.network_name == network and inst.is_bidder == is_bidder
        ]

        # When preset has a name, narrow to name match
        if preset_name is not None:
            candidates = [
                (idx, inst)
                for idx, inst in candidates
                if inst.instance_name == preset_name
            ]

        if not candidates:
            # Missing: preset entry has no match in live -- informational
            entry_desc = f"{network} ({'bidder' if is_bidder else 'manual'})"
            if preset_name:
                entry_desc += f" [{preset_name}]"
            changes.append(
                FieldChange(
                    field="waterfall",
                    old_value=None,
                    new_value=entry_desc,
                    description=(
                        f"Waterfall: {entry_desc} is in preset but not "
                        f"found in live instances (may not be configured "
                        f"in this app)"
                    ),
                )
            )
            continue

        # Use first candidate match
        matched_idx, matched_inst = candidates[0]
        matched_live_indices.add(matched_idx)

        # Check rate (only for entries with a preset rate)
        if preset_rate is not None and matched_inst.group_rate is not None:
            if abs(preset_rate - matched_inst.group_rate) >= 0.01:
                entry_desc = (
                    f"{network} ({'bidder' if is_bidder else 'manual'})"
                )
                if preset_name:
                    entry_desc += f" [{preset_name}]"
                changes.append(
                    FieldChange(
                        field="waterfall",
                        old_value=matched_inst.group_rate,
                        new_value=preset_rate,
                        description=(
                            f"Waterfall: {entry_desc} rate "
                            f"{matched_inst.group_rate} -> {preset_rate}"
                        ),
                    )
                )

    # Check for extra live instances not in preset (informational only).
    # adSourcePriority PUT reorders instances but cannot remove them --
    # instance removal requires the LevelPlay dashboard.
    for idx, inst in enumerate(live_instances):
        if idx not in matched_live_indices:
            inst_desc = (
                f"{inst.network_name} "
                f"({'bidder' if inst.is_bidder else 'manual'})"
            )
            if inst.instance_name:
                inst_desc += f" [{inst.instance_name}]"
            changes.append(
                FieldChange(
                    field="waterfall",
                    old_value=inst_desc,
                    new_value=None,
                    description=(
                        f"Waterfall: {inst_desc} is in live but not in "
                        f"preset (cannot be removed via API -- manage "
                        f"in LevelPlay dashboard)"
                    ),
                )
            )

    return changes


def _detect_ab_test(remote_groups: list[Group]) -> bool:
    """Detect if any remote group has an active A/B test.

    A group is considered to have an active A/B test if ``ab_test`` is
    set to a value other than ``None`` or ``"N/A"``.
    """
    return any(
        group.ab_test is not None and group.ab_test != "N/A"
        for group in remote_groups
    )

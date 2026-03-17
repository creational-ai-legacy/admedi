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

from admedi.models.diff import (
    AppDiffReport,
    DiffAction,
    FieldChange,
    GroupDiff,
)
from admedi.models.enums import AdFormat
from admedi.models.group import Group
from admedi.models.portfolio import PortfolioTier


def compute_diff(
    tiers: list[PortfolioTier],
    remote_groups: list[Group],
    app_key: str,
    app_name: str,
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
        format_diffs = _diff_format(fmt, tiers, groups)
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
                    ad_format, tier, group, len(tiers), len(remote_groups)
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
                    ad_format, tier, group, len(tiers), len(remote_groups)
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
) -> GroupDiff:
    """Compare a matched template tier and remote group, producing a GroupDiff.

    Compares only template-controlled fields: countries, position, name.
    Does NOT compare floor_price, instances, or waterfall.

    Args:
        ad_format: The ad format being compared.
        tier: The template tier (desired state).
        group: The remote group (live state).
        template_count: Total number of template tiers for this format.
        remote_count: Total number of remote groups for this format.

    Returns:
        A ``GroupDiff`` with action UNCHANGED or UPDATE.
    """
    changes: list[FieldChange] = []

    # Compare countries (set comparison)
    _compare_countries(tier, group, changes)

    # Compare position (skip if single group in both template and remote)
    skip_position = template_count == 1 and remote_count == 1
    if not skip_position:
        _compare_position(tier, group, changes)

    # Compare name
    _compare_name(tier, group, changes)

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


def _detect_ab_test(remote_groups: list[Group]) -> bool:
    """Detect if any remote group has an active A/B test.

    A group is considered to have an active A/B test if ``ab_test`` is
    set to a value other than ``None`` or ``"N/A"``.
    """
    return any(
        group.ab_test is not None and group.ab_test != "N/A"
        for group in remote_groups
    )

"""Snapshot generator for LevelPlay mediation groups.

Two output modes:

1. **Settings generation** (``generate_app_settings``): Matches live groups
   against existing ``countries.yaml`` using country-content matching,
   generates per-app settings in the two-layer format (``alias`` + format ->
   display_name: group_ref list), and updates ``countries.yaml`` when new
   country groups are auto-created.  This is the engine layer for ``pull``.

2. **Raw** (``save_raw_snapshot``): Full-fidelity YAML snapshot using
   Pydantic ``model_dump`` serialization. Preserves all API data (group IDs,
   instance IDs, rates, country rate overrides, floor prices, etc.) for
   lossless round-trip via ``load_snapshot`` / ``model_validate``.

The ``pull`` command produces both outputs from a single API fetch.

Shared helpers (public API):

- ``extract_network_presets(groups)`` -- Extract unique waterfall presets.
- ``waterfall_signature(group)`` -- Hashable signature for a group's
  waterfall configuration.
- ``write_yaml_file(path, entries, header, root_key)`` -- Write entries
  to a YAML file with a header comment.

Examples:
    >>> from admedi.engine.snapshot import save_raw_snapshot
    >>> from admedi.models.group import Group
    >>> groups = [
    ...     Group.model_validate({
    ...         "groupName": "US Tier 1",
    ...         "adFormat": "interstitial",
    ...         "countries": ["US"],
    ...         "position": 1,
    ...     })
    ... ]
    >>> path = save_raw_snapshot(groups, "abc123", "My App", "my-app")
    >>> path
    'snapshots/my-app.yaml'
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from pydantic import BaseModel

from admedi.engine.loader import Profile, load_country_groups
from admedi.models.enums import AdFormat, Platform
from admedi.models.group import Group


# ---------------------------------------------------------------------------
# Settings generation (country matching + two-layer file management)
# ---------------------------------------------------------------------------


def generate_app_settings(
    groups: list[Group],
    profile: Profile,
    settings_dir: str = "settings",
) -> tuple[str, list[str]]:
    """Generate per-app settings from live groups with country-content matching.

    Matches each live group's country set against existing country group
    definitions (from ``countries.yaml``). When an exact set match is found,
    the existing group reference is reused. When no match exists, a new
    country group is auto-created.

    For a fresh portfolio (no ``countries.yaml``), all country groups are
    created from scratch (bootstrap).

    The function writes:
    - ``settings/{alias}.yaml`` -- per-app settings (display_name: group_ref)
    - ``countries.yaml`` -- updated if new country groups were created

    It does NOT write the networks file -- that is handled by the CLI's
    ``pull`` flow using ``extract_network_presets()`` and ``write_yaml_file()``.

    Args:
        groups: List of Group models from the live API.
        profile: Profile object with alias, app_key, app_name, platform.
        settings_dir: Directory for per-app settings files. Root-level
            files (``countries.yaml``) are resolved from
            ``Path(settings_dir).parent``.

    Returns:
        A 2-tuple of ``(settings_path, info_messages)`` where
        *settings_path* is the written per-app YAML file path and
        *info_messages* is a list of human-readable strings describing
        actions taken (e.g., new country groups added).

    Examples:
        >>> from admedi.engine.snapshot import generate_app_settings
        >>> from admedi.engine.loader import Profile
        >>> from admedi.models.enums import Platform
        >>> from admedi.models.group import Group
        >>> profile = Profile(
        ...     alias="hexar-ios",
        ...     app_key="676996cd",
        ...     app_name="Hexar.io iOS",
        ...     platform=Platform.IOS,
        ... )
        >>> groups = [
        ...     Group.model_validate({
        ...         "groupName": "Tier 1",
        ...         "adFormat": "interstitial",
        ...         "countries": ["US"],
        ...         "position": 1,
        ...     })
        ... ]
        >>> path, messages = generate_app_settings(
        ...     groups, profile, settings_dir="settings"
        ... )  # doctest: +SKIP
    """
    settings = Path(settings_dir)
    settings.mkdir(parents=True, exist_ok=True)
    project_root = settings.parent

    # --- Load or bootstrap shared files ---
    existing_country_groups: dict[str, list[str]] = {}
    is_bootstrap = False

    countries_path = project_root / "countries.yaml"

    if countries_path.exists():
        existing_country_groups = load_country_groups(settings_dir=settings_dir)
    else:
        is_bootstrap = True

    # --- Resolve each country group's country set for matching ---
    resolved_group_sets: dict[str, frozenset[str]] = {}
    for group_name, country_list in existing_country_groups.items():
        resolved_group_sets[group_name] = frozenset(country_list)

    # --- Match live groups to country groups ---
    info_messages: list[str] = []
    new_country_groups: dict[str, list[str]] = {}

    # Build a mapping from (group_name, countries) to country group ref.
    # Keyed by both name AND countries because the same LevelPlay group name
    # can have different country sets across ad formats (per-format tiers).
    group_to_ref: dict[tuple[str, frozenset[str]], str] = {}

    for group in sorted(groups, key=lambda g: (g.ad_format.value, g.position)):
        group_countries = frozenset(group.countries)
        key = (group.group_name, group_countries)

        if key in group_to_ref:
            # Already matched this (name, countries) pair
            continue

        group_ref = _match_countries_to_group(
            group_countries=group_countries,
            group_name=group.group_name,
            resolved_group_sets=resolved_group_sets,
            existing_country_groups=existing_country_groups,
            new_country_groups=new_country_groups,
            info_messages=info_messages,
        )
        group_to_ref[key] = group_ref

    # --- Update shared files if new entries were created ---
    if new_country_groups:
        merged_groups = {**existing_country_groups, **new_country_groups}
        _write_countries_yaml(countries_path, merged_groups)

    if is_bootstrap and not new_country_groups:
        # Edge case: bootstrap with no groups (empty pull)
        # Still write empty countries.yaml so subsequent pulls work
        if not countries_path.exists():
            _write_countries_yaml(countries_path, {})

    # --- Generate per-app settings file ---
    settings_path = _write_per_app_settings(
        settings_dir=settings,
        alias=profile.alias,
        groups=groups,
        group_to_ref=group_to_ref,
    )

    return settings_path, info_messages


def _match_countries_to_group(
    group_countries: frozenset[str],
    group_name: str,
    resolved_group_sets: dict[str, frozenset[str]],
    existing_country_groups: dict[str, list[str]],
    new_country_groups: dict[str, list[str]],
    info_messages: list[str],
) -> str:
    """Match a group's country set to an existing country group or auto-create one.

    Matching algorithm:
    1. If countries are ``['*']`` -> return ``'*'`` (catch-all)
    2. Exact set match against resolved country group sets
    3. Check newly created groups (from earlier groups in this call)
    4. No match -> auto-create new country group

    Args:
        group_countries: Frozenset of country codes from the live group.
        group_name: LevelPlay group name (used for auto-naming).
        resolved_group_sets: Map of country group name -> resolved country frozenset.
        existing_country_groups: Currently loaded country groups.
        new_country_groups: Accumulator for newly created groups (mutated).
        info_messages: Accumulator for info messages (mutated).

    Returns:
        Country group reference (key in ``countries.yaml``, or ``'*'``).
    """
    # Case 1: Catch-all group
    if group_countries == frozenset(["*"]):
        return "*"

    # Case 2: Exact set match against existing country groups
    for cg_name, cg_countries in resolved_group_sets.items():
        if group_countries == cg_countries:
            return cg_name

    # Case 3: Check newly created groups (from earlier groups in this call)
    for cg_name, country_list in new_country_groups.items():
        if frozenset(country_list) == group_countries:
            return cg_name

    # Case 4: No match -- auto-create country group
    country_group_name = _auto_name_country_group(
        sorted(group_countries), group_name
    )

    # Ensure unique country group name
    all_existing_groups = {**existing_country_groups, **new_country_groups}
    if country_group_name in all_existing_groups:
        # Already exists with same or different countries -- use unique name
        counter = 2
        base_name = country_group_name
        while country_group_name in all_existing_groups:
            country_group_name = f"{base_name}-{counter}"
            counter += 1

    new_country_groups[country_group_name] = sorted(group_countries)

    # Update resolved_group_sets so subsequent groups can match
    resolved_group_sets[country_group_name] = group_countries

    info_messages.append(
        f"Created country group '{country_group_name}' "
        f"(countries: {sorted(group_countries)})"
    )

    return country_group_name


def _auto_name_country_group(countries: list[str], group_name: str) -> str:
    """Generate a country group name for auto-creation.

    Naming rules:
    - Single country: use the country code (e.g., ``US``)
    - Multi-country: use LevelPlay group name, lowercased and hyphenated
      (e.g., ``"Tier 2"`` -> ``tier-2``)
    - ``['*']``: not applicable (catch-all maps to ``'*'`` in tiers, no group)

    Args:
        countries: Sorted list of country codes.
        group_name: LevelPlay group name.

    Returns:
        A string suitable as a country group key in ``countries.yaml``.
    """
    if len(countries) == 1:
        return countries[0]

    # Multi-country: normalize the LevelPlay group name
    # Lowercase, replace spaces with hyphens, remove non-alphanumeric
    name = group_name.lower().strip()
    name = re.sub(r"\s+", "-", name)
    name = re.sub(r"[^a-z0-9-]", "", name)
    return name or "group"


def _write_countries_yaml(
    path: Path, groups: dict[str, list[str]]
) -> None:
    """Write ``countries.yaml`` with portfolio-wide country group definitions.

    Args:
        path: Path to write the file.
        groups: Dict mapping group name to country code list.
    """
    yaml = YAML()
    yaml.default_flow_style = False
    stream = StringIO()
    if groups:
        yaml.dump(dict(groups), stream)
    else:
        stream.write("{}\n")

    header = (
        "# Admedi country groups\n"
        "# Portfolio-wide country group definitions\n\n"
    )
    path.write_text(header + stream.getvalue(), encoding="utf-8")



def _write_per_app_settings(
    settings_dir: Path,
    alias: str,
    groups: list[Group],
    group_to_ref: dict[tuple[str, frozenset[str]], str],
) -> str:
    """Write per-app settings file in the two-layer format.

    Format::

        alias: hexar-ios

        rewarded:
          - Tier 1: US
          - Tier 2: tier-2
          - All Countries: '*'

        interstitial:
          - Tier 1: US
          - Tier 2: tier-2
          - All Countries: '*'

    Each entry is a single-key mapping: display_name -> country group ref.
    The display name is the LevelPlay group name. The group ref resolves
    against ``countries.yaml`` (or ``'*'`` for catch-all).

    Only formats with live groups appear in the output. Omission means
    deletion intent for ``sync`` (per design item #3).

    Args:
        settings_dir: Path to the settings directory.
        alias: App alias for the filename and ``alias`` field.
        groups: List of Group models from the live API.
        group_to_ref: Mapping from (group_name, countries) to country group ref.

    Returns:
        The path to the written per-app settings file (as string).
    """
    # Build format -> ordered tier list (each entry is {display_name: group_ref})
    by_format: dict[str, list[dict[str, str]]] = {}
    for group in sorted(groups, key=lambda g: g.position):
        fmt = group.ad_format.value
        # Skip rewardedVideo (legacy alias -- use "rewarded" only)
        if fmt == AdFormat.REWARDED_VIDEO.value:
            continue
        key = (group.group_name, frozenset(group.countries))
        group_ref = group_to_ref.get(key, group.group_name)
        if fmt not in by_format:
            by_format[fmt] = []
        # Avoid duplicate entries within a format (check by display name)
        existing_names = [next(iter(e)) for e in by_format[fmt]]
        if group.group_name not in existing_names:
            by_format[fmt].append({group.group_name: group_ref})

    # Build YAML content
    data: dict[str, Any] = {"alias": alias}
    for fmt_key in sorted(by_format.keys()):
        data[fmt_key] = by_format[fmt_key]

    yaml = YAML()
    yaml.default_flow_style = False
    stream = StringIO()
    yaml.dump(data, stream)

    file_path = settings_dir / f"{alias}.yaml"
    file_path.write_text(stream.getvalue(), encoding="utf-8")

    return str(file_path)


# ---------------------------------------------------------------------------
# Public helpers (used by CLI pull flow for networks file generation)
# ---------------------------------------------------------------------------


def waterfall_signature(group: Group) -> tuple:
    """Create a hashable signature for a group's waterfall configuration.

    Args:
        group: A Group model from the live API.

    Returns:
        A tuple of ``(bidders, manuals)`` suitable for use as a dict key.
    """
    bidders = tuple(sorted(i.network_name for i in group.instances if i.is_bidder))
    manuals = tuple(
        (i.network_name, i.group_rate)
        for i in group.instances
        if not i.is_bidder
    )
    return (bidders, manuals)


# ---------------------------------------------------------------------------
# Network preset helpers
# ---------------------------------------------------------------------------


def _auto_name_network(
    bidders: list[str], manuals: list[dict[str, Any]]
) -> str:
    """Generate a descriptive network preset name."""
    if not bidders and not manuals:
        return "none"

    parts: list[str] = []
    if len(bidders) == 1:
        parts.append(bidders[0].lower().replace(" ", "-"))
    elif len(bidders) <= 3:
        parts.append("+".join(b.lower().split()[0] for b in bidders))
    elif len(bidders) > 3:
        parts.append(f"bidding-{len(bidders)}")

    for m in manuals:
        parts.append(m["network"].lower().replace(" ", "-"))

    return "-".join(parts) if parts else "custom"


def extract_network_presets(groups: list[Group]) -> dict[str, list[dict[str, Any]]]:
    """Extract unique waterfall presets from groups.

    Deduplicates groups by their waterfall signature (bidder set + manual
    network/rate pairs) and returns a dict of preset name -> instance list.

    Args:
        groups: List of Group models from the live API.

    Returns:
        Dict mapping preset name to a list of instance dicts with
        ``network``, ``bidder``, and optional ``rate`` keys.
    """
    seen: dict[tuple, str] = {}
    presets: dict[str, list[dict[str, Any]]] = {}

    for group in groups:
        sig = waterfall_signature(group)
        if sig in seen:
            continue

        bidders = sorted(i.network_name for i in group.instances if i.is_bidder)
        manuals: list[dict[str, Any]] = []
        for i in group.instances:
            if not i.is_bidder:
                entry: dict[str, Any] = {"network": i.network_name, "bidder": False}
                if i.group_rate is not None:
                    entry["rate"] = i.group_rate
                manuals.append(entry)

        if not bidders and not manuals:
            seen[sig] = "none"
            continue

        name = _auto_name_network(bidders, manuals)
        instances: list[dict[str, Any]] = []
        for b in bidders:
            instances.append({"network": b, "bidder": True})
        instances.extend(manuals)

        seen[sig] = name
        presets[name] = instances

    return presets


def write_yaml_file(
    path: Path,
    entries: dict[str, Any],
    header: str,
    root_key: str = "presets",
) -> None:
    """Write entries to a YAML file (overwrites existing).

    Args:
        path: Path to the output file.
        entries: Dict of entries to write under the root key.
        header: Comment header string to prepend.
        root_key: Top-level YAML key wrapping the entries.
    """
    yaml = YAML()
    yaml.default_flow_style = False
    stream = StringIO()
    yaml.dump({root_key: entries}, stream)
    path.write_text(header + stream.getvalue(), encoding="utf-8")


# ---------------------------------------------------------------------------
# Raw full-fidelity snapshot (Pydantic model_dump serialization)
# ---------------------------------------------------------------------------


def save_raw_snapshot(
    groups: list[Group],
    app_key: str,
    app_name: str,
    alias: str,
    snapshots_dir: str = "snapshots",
    platform: Platform | None = None,
) -> str:
    """Save full-fidelity raw snapshot using Pydantic model_dump serialization.

    Serializes each Group via ``model_dump(mode="json", by_alias=True,
    exclude_none=True)`` and organizes them by ad format. The output YAML
    preserves all API data (group IDs, instance IDs, rates, country rate
    overrides, floor prices, A/B tests, segments) for lossless round-trip
    reconstruction via ``model_validate()``.

    Args:
        groups: List of Group models from the live API.
        app_key: Application key.
        app_name: Application display name.
        alias: Profile alias used as the filename (e.g. ``"ss-ios"``).
        snapshots_dir: Directory for output files.
        platform: Optional platform identifier.

    Returns:
        The path to the saved snapshot YAML file.

    Examples:
        >>> from admedi.engine.snapshot import save_raw_snapshot
        >>> from admedi.models.group import Group
        >>> groups = [
        ...     Group.model_validate({
        ...         "groupName": "US Tier 1",
        ...         "adFormat": "interstitial",
        ...         "countries": ["US"],
        ...         "position": 1,
        ...     })
        ... ]
        >>> path = save_raw_snapshot(groups, "abc123", "My App", "my-app")
        >>> path
        'snapshots/my-app.yaml'
    """
    snapshots = Path(snapshots_dir)
    snapshots.mkdir(parents=True, exist_ok=True)

    # Organize groups by ad format
    by_format: dict[str, list[dict[str, Any]]] = {}
    for group in groups:
        dumped = group.model_dump(mode="json", by_alias=True, exclude_none=True)
        fmt = dumped["adFormat"]
        by_format.setdefault(fmt, []).append(dumped)

    # Build snapshot data dict
    data: dict[str, Any] = {
        "app_key": app_key,
        "app_name": app_name,
    }
    if platform is not None:
        data["platform"] = platform.value
    data["groups"] = by_format

    # Serialize to YAML
    yaml = YAML()
    yaml.default_flow_style = False
    stream = StringIO()
    yaml.dump(data, stream)

    # Build header comment
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    header = (
        f"# Captured by admedi pull\n"
        f"# App: {app_name} ({app_key})\n"
        f"# Captured: {timestamp}\n\n"
    )

    # Write file
    file_path = snapshots / f"{alias}.yaml"
    file_path.write_text(header + stream.getvalue(), encoding="utf-8")

    return str(file_path)


# ---------------------------------------------------------------------------
# Snapshot data model and loader
# ---------------------------------------------------------------------------


class SnapshotData(BaseModel):
    """Data loaded from a raw snapshot YAML file.

    Encapsulates the app metadata and reconstructed Group models from a
    snapshot file written by ``save_raw_snapshot()``.

    Attributes:
        app_key: Application key from the snapshot header.
        app_name: Application display name from the snapshot header.
        platform: Optional platform identifier from the snapshot header.
        groups: List of Group models reconstructed from the snapshot data.

    Examples:
        >>> from admedi.engine.snapshot import SnapshotData
        >>> from admedi.models.group import Group
        >>> data = SnapshotData(
        ...     app_key="abc123",
        ...     app_name="Test App",
        ...     groups=[],
        ... )
        >>> data.app_key
        'abc123'
        >>> len(data.groups)
        0
    """

    app_key: str
    app_name: str
    platform: Platform | None = None
    groups: list[Group]


def load_snapshot(snapshot_path: str | Path) -> SnapshotData:
    """Load a snapshot YAML file and reconstruct Group models.

    Reads a YAML file written by ``save_raw_snapshot()`` and reconstructs
    the original Group models via ``Group.model_validate()``. Since
    ``save_raw_snapshot()`` serializes with ``by_alias=True``, the YAML
    field names are the alias names that ``model_validate()`` accepts
    natively -- no custom mapping needed.

    Args:
        snapshot_path: Path to the snapshot YAML file.

    Returns:
        SnapshotData with app metadata and reconstructed Group models.

    Raises:
        FileNotFoundError: If the snapshot file doesn't exist.
        ValueError: If the snapshot format is invalid (e.g., missing
            ``groups`` key, malformed YAML).

    Examples:
        >>> from admedi.engine.snapshot import load_snapshot
        >>> data = load_snapshot("snapshots/ss-ios.yaml")  # doctest: +SKIP
        >>> data.app_key  # doctest: +SKIP
        'abc123'
    """
    path = Path(snapshot_path)
    if not path.exists():
        raise FileNotFoundError(f"Snapshot file not found: {snapshot_path}")

    yaml = YAML()
    data = yaml.load(path)

    if data is None or "groups" not in data:
        raise ValueError(
            f"Invalid snapshot format: missing 'groups' key in {snapshot_path}"
        )

    # Extract header metadata
    app_key: str = data.get("app_key", "")
    app_name: str = data.get("app_name", "")

    # Parse platform if present
    platform: Platform | None = None
    if "platform" in data:
        platform = Platform(data["platform"])

    # Reconstruct Group models from groups dict (keyed by ad format)
    groups: list[Group] = []
    groups_dict = data["groups"]
    if groups_dict:
        for _ad_format, group_list in groups_dict.items():
            for group_dict in group_list:
                groups.append(Group.model_validate(group_dict))

    return SnapshotData(
        app_key=app_key,
        app_name=app_name,
        platform=platform,
        groups=groups,
    )

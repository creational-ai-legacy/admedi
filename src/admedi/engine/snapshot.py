"""Snapshot generator for LevelPlay mediation groups.

Two output modes:

1. **Modular** (``save_modular_snapshot``): Splits output into per-app files:
   ``{alias}-networks.yaml`` (waterfall presets), ``{alias}-tiers.yaml``
   (country groupings), and a per-app YAML (tier names, preset references).
   This is the default for ``show``.

2. **Raw** (``save_raw_snapshot``): Full-fidelity YAML snapshot using
   Pydantic ``model_dump`` serialization. Preserves all API data (group IDs,
   instance IDs, rates, country rate overrides, floor prices, etc.) for
   lossless round-trip via ``load_snapshot`` / ``model_validate``.

The ``show`` command produces both outputs from a single API fetch.

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

from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from pydantic import BaseModel

from admedi.models.enums import AdFormat, Platform
from admedi.models.group import Group


# ---------------------------------------------------------------------------
# Modular snapshot (per-app networks + tiers + app YAML)
# ---------------------------------------------------------------------------


def save_modular_snapshot(
    groups: list[Group],
    app_key: str,
    app_name: str,
    app_file: str,
    settings_dir: str = "settings",
    platform: Platform | None = None,
) -> tuple[str, list[str]]:
    """Save a modular snapshot: per-app networks, tiers, and app YAML.

    Extracts unique waterfall patterns into ``{alias}-networks.yaml``,
    unique tier structures into ``{alias}-tiers.yaml``, then writes a
    compact per-app file that references both by name.

    Args:
        groups: List of Group models from the live API.
        app_key: Application key.
        app_name: Application display name.
        app_file: Filename for the per-app YAML (e.g. ``"ss-ios.yaml"``).
        settings_dir: Directory for output files.
        platform: Optional platform identifier.

    Returns:
        A 2-tuple of ``(path, warnings)`` where *path* is the saved
        per-app YAML file and *warnings* is a list of human-readable
        strings describing any per-format country differences found
        in tiers (empty when all formats agree).
    """
    settings = Path(settings_dir)
    settings.mkdir(parents=True, exist_ok=True)

    alias_stem = Path(app_file).stem

    # Write per-app network presets (overwrite, no merge)
    network_presets = _extract_network_presets(groups)
    _write_yaml_file(
        settings / f"{alias_stem}-networks.yaml",
        network_presets,
        header="# Admedi network presets\n"
        "# Waterfall configurations referenced by app settings\n\n",
    )
    network_lookup = _build_network_lookup(network_presets)

    # Write per-app tier definitions (overwrite, no merge)
    tier_defs, warnings = _extract_tiers(groups)
    _write_yaml_file(
        settings / f"{alias_stem}-tiers.yaml",
        tier_defs,
        header="# Admedi tier definitions\n"
        "# Country groupings referenced by app settings\n\n",
        root_key="tiers",
    )

    # Write per-app file
    app_path = settings / app_file
    app_yaml = _build_app_yaml(
        groups, app_key, app_name, platform, network_lookup
    )
    app_path.write_text(app_yaml, encoding="utf-8")

    return str(app_path), warnings


def _waterfall_signature(group: Group) -> tuple:
    """Create a hashable signature for a group's waterfall configuration."""
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


def _extract_network_presets(groups: list[Group]) -> dict[str, list[dict[str, Any]]]:
    """Extract unique waterfall presets from groups."""
    seen: dict[tuple, str] = {}
    presets: dict[str, list[dict[str, Any]]] = {}

    for group in groups:
        sig = _waterfall_signature(group)
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


def _build_network_lookup(
    presets: dict[str, list[dict[str, Any]]]
) -> dict[tuple, str]:
    """Build reverse lookup: waterfall signature → preset name."""
    lookup: dict[tuple, str] = {}
    for name, instances in presets.items():
        bidders = tuple(sorted(
            i["network"] for i in instances if i.get("bidder", False)
        ))
        manuals = tuple(
            (i["network"], i.get("rate"))
            for i in instances
            if not i.get("bidder", False)
        )
        lookup[(bidders, manuals)] = name
    return lookup


# ---------------------------------------------------------------------------
# Tier definition helpers
# ---------------------------------------------------------------------------


def _extract_tiers(
    groups: list[Group],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Extract unique tier definitions keyed by group name, with format associations.

    Each tier is stored once with its country list and a ``formats`` field
    listing every ad format that references the tier.  Iterates all groups
    sorted by position; for each group, either creates a new tier entry or
    adds the format to the existing tier's format set.

    When the same tier name appears across multiple formats with different
    country lists, the countries are merged to the **sorted union** and a
    human-readable warning is appended to the warnings list.

    Default tiers (those with ``'*'`` in countries) are sorted last in the
    returned dict so they appear at the bottom of the YAML output.

    Returns:
        A 2-tuple of:
        - Dict mapping tier name to ``{"countries": [...], "formats": [...]}``.
          The ``formats`` value is a sorted list of strings (not a set).
        - List of warning strings describing per-format country differences.
          Empty when all formats share the same countries for each tier.
    """
    tiers: dict[str, dict[str, Any]] = {}
    for g in sorted(groups, key=lambda g: g.position):
        if g.group_name in tiers:
            tiers[g.group_name]["_format_set"].add(g.ad_format.value)
            tiers[g.group_name]["_country_sets"][g.ad_format.value] = set(
                g.countries
            )
        else:
            tiers[g.group_name] = {
                "countries": list(g.countries),
                "_format_set": {g.ad_format.value},
                "_country_sets": {g.ad_format.value: set(g.countries)},
            }

    # Detect per-format country variance, take union, and build warnings
    warnings: list[str] = []
    for tier_name, tier_data in tiers.items():
        country_sets: dict[str, set[str]] = tier_data.pop("_country_sets")
        unique_sets = {frozenset(cs) for cs in country_sets.values()}
        if len(unique_sets) > 1:
            # Countries differ across formats -- take sorted union
            union_countries = sorted(
                set().union(*country_sets.values())
            )
            tier_data["countries"] = union_countries

            # Build warning with per-format detail
            per_format_parts = ", ".join(
                f"{fmt}: {sorted(countries)}"
                for fmt, countries in sorted(country_sets.items())
            )
            warning = (
                f"Tier '{tier_name}': countries differ across formats "
                f"({per_format_parts}) -- merged to union {union_countries}"
            )
            warnings.append(warning)

    # Convert internal format sets to sorted lists and remove the temporary key
    for tier_data in tiers.values():
        tier_data["formats"] = sorted(tier_data.pop("_format_set"))

    # Sort so default tiers ('*' in countries) come last
    tiers = dict(
        sorted(tiers.items(), key=lambda t: "*" in t[1]["countries"])
    )
    return tiers, warnings


# ---------------------------------------------------------------------------
# YAML write helper
# ---------------------------------------------------------------------------


def _write_yaml_file(
    path: Path,
    entries: dict[str, Any],
    header: str,
    root_key: str = "presets",
) -> None:
    """Write entries to a YAML file (overwrites existing)."""
    yaml = YAML()
    yaml.default_flow_style = False
    stream = StringIO()
    yaml.dump({root_key: entries}, stream)
    path.write_text(header + stream.getvalue(), encoding="utf-8")


# ---------------------------------------------------------------------------
# Per-app YAML builder
# ---------------------------------------------------------------------------


def _build_app_yaml(
    groups: list[Group],
    app_key: str,
    app_name: str,
    platform: Platform | None,
    network_lookup: dict[tuple, str],
) -> str:
    """Build a compact per-app YAML with metadata and a flat waterfall mapping.

    The ``waterfall`` section maps each LevelPlay-compatible ad format to its
    resolved network preset name (or ``"none"`` when no groups exist for that
    format).  All four formats (banner, interstitial, native, rewarded) always
    appear, regardless of whether the app has groups configured for them.

    Args:
        groups: List of Group models from the live API.
        app_key: Application key.
        app_name: Application display name.
        platform: Optional platform identifier.
        network_lookup: Reverse lookup from waterfall signature to preset name.

    Returns:
        YAML string with header comments and app data.
    """
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    data: dict[str, Any] = {
        "app_key": app_key,
        "app_name": app_name,
    }
    if platform is not None:
        data["platform"] = platform.value

    # Group by format (explicit string key, no defaultdict side effects)
    by_format: dict[str, list[Group]] = {}
    for g in groups:
        by_format.setdefault(g.ad_format.value, []).append(g)

    # Build flat waterfall mapping for all LevelPlay-compatible formats
    waterfall_data: dict[str, str] = {}
    for fmt in [f for f in AdFormat if f != AdFormat.REWARDED_VIDEO]:
        fmt_groups = by_format.get(fmt.value, [])
        if fmt_groups:
            wf_sig = _waterfall_signature(fmt_groups[0])
            wf_name = network_lookup.get(wf_sig, "none")
        else:
            wf_name = "none"
        waterfall_data[fmt.value] = wf_name

    data["waterfall"] = waterfall_data

    yaml = YAML()
    yaml.default_flow_style = False
    stream = StringIO()
    yaml.dump(data, stream)

    header = (
        f"# Generated by admedi show\n"
        f"# App: {app_name} ({app_key})\n"
        f"# Captured: {timestamp}\n\n"
    )
    return header + stream.getvalue()


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
        f"# Captured by admedi show\n"
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

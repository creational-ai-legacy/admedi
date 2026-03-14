"""Snapshot generator for LevelPlay mediation groups.

Two output modes:

1. **Modular** (``save_modular_snapshot``): Splits output into a shared
   ``networks.yaml`` (waterfall presets defined once) and a per-app YAML
   (tiers, countries, preset references). This is the default for ``show``.

2. **Legacy** (``generate_snapshot``): Single-file YAML with all instance
   data inlined per group. Kept for ``snapshot`` command compatibility.

Examples:
    >>> from admedi.engine.snapshot import generate_snapshot
    >>> from admedi.models.group import Group
    >>> groups = [
    ...     Group.model_validate({
    ...         "groupName": "US Tier 1",
    ...         "adFormat": "interstitial",
    ...         "countries": ["US"],
    ...         "position": 1,
    ...     })
    ... ]
    >>> yaml_str = generate_snapshot(groups, "abc123", "My App")
    >>> "US Tier 1" in yaml_str
    True
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from admedi.models.enums import Platform
from admedi.models.group import Group


# ---------------------------------------------------------------------------
# Modular snapshot (networks.yaml + per-app YAML)
# ---------------------------------------------------------------------------


def save_modular_snapshot(
    groups: list[Group],
    app_key: str,
    app_name: str,
    app_file: str,
    settings_dir: str = "settings",
    platform: Platform | None = None,
) -> str:
    """Save a modular snapshot: networks.yaml + tiers.yaml + per-app YAML.

    Extracts unique waterfall patterns into ``settings/networks.yaml``,
    unique tier structures into ``settings/tiers.yaml``, then writes a
    compact per-app file that references both by name.

    Args:
        groups: List of Group models from the live API.
        app_key: Application key.
        app_name: Application display name.
        app_file: Filename for the per-app YAML (e.g. ``"ss-ios.yaml"``).
        settings_dir: Directory for output files.
        platform: Optional platform identifier.

    Returns:
        The path to the saved per-app YAML file.
    """
    settings = Path(settings_dir)
    settings.mkdir(parents=True, exist_ok=True)

    # Extract and merge network presets
    network_presets = _extract_network_presets(groups)
    _merge_yaml_file(
        settings / "networks.yaml",
        network_presets,
        header="# Admedi network presets\n"
        "# Waterfall configurations referenced by app settings\n\n",
    )
    network_lookup = _build_network_lookup(network_presets)

    # Extract and merge tier definitions
    tier_defs = _extract_tiers(groups)
    _merge_yaml_file(
        settings / "tiers.yaml",
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

    return str(app_path)


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


def _extract_tiers(groups: list[Group]) -> dict[str, dict[str, Any]]:
    """Extract unique tier definitions keyed by group name.

    Each tier is stored once with its country list. Deduplicates across
    formats — if "Tier 1" appears in both interstitial and rewarded with
    the same countries, it's stored once.
    """
    tiers: dict[str, dict[str, Any]] = {}
    for g in sorted(groups, key=lambda g: g.position):
        if g.group_name not in tiers:
            tiers[g.group_name] = {"countries": list(g.countries)}
    return tiers


# ---------------------------------------------------------------------------
# Shared merge helper
# ---------------------------------------------------------------------------


def _merge_yaml_file(
    path: Path,
    new_entries: dict[str, Any],
    header: str,
    root_key: str = "presets",
) -> None:
    """Merge new entries into an existing YAML file, or create it."""
    yaml = YAML()
    yaml.default_flow_style = False

    existing: dict[str, Any] = {}
    if path.exists():
        data = yaml.load(path)
        if data and root_key in data:
            existing = dict(data[root_key])

    merged = {**existing, **new_entries}

    stream = StringIO()
    yaml.dump({root_key: merged}, stream)
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
    """Build a compact per-app YAML with tier lists + waterfall preset references."""
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    data: dict[str, Any] = {
        "app_key": app_key,
        "app_name": app_name,
    }
    if platform is not None:
        data["platform"] = platform.value

    # Group by format
    by_format: dict[str, list[Group]] = defaultdict(list)
    for g in groups:
        by_format[g.ad_format.value].append(g)

    groups_data: dict[str, dict[str, Any]] = {}
    for fmt in sorted(by_format.keys()):
        fmt_groups = by_format[fmt]

        # Tier names in position order
        tier_names = [
            g.group_name
            for g in sorted(fmt_groups, key=lambda g: g.position)
        ]

        # Resolve waterfall preset (use the first group's — all groups in
        # a format typically share the same waterfall)
        wf_sig = _waterfall_signature(fmt_groups[0])
        wf_name = network_lookup.get(wf_sig, "none")

        groups_data[fmt] = {
            "tiers": tier_names,
            "waterfall": wf_name,
        }

    data["groups"] = groups_data

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
# Legacy single-file snapshot (for `snapshot` command compatibility)
# ---------------------------------------------------------------------------


def generate_snapshot(
    groups: list[Group],
    app_key: str,
    app_name: str,
    platform: Platform | None = None,
) -> str:
    """Generate a single-file YAML snapshot with inlined instance data.

    Args:
        groups: List of Group models to serialize.
        app_key: Application key for the snapshot header.
        app_name: Application display name for the snapshot header.
        platform: Optional platform identifier.

    Returns:
        A YAML-formatted string with all instance data inlined.

    Examples:
        >>> yaml_str = generate_snapshot([], "key1", "Test App")
        >>> "app_key: key1" in yaml_str
        True
        >>> "groups:" in yaml_str
        True
    """
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    data: dict[str, Any] = {
        "app_key": app_key,
        "app_name": app_name,
    }

    if platform is not None:
        data["platform"] = platform.value

    groups_by_format: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for group in groups:
        group_dict = _serialize_group(group)
        groups_by_format[group.ad_format.value].append(group_dict)

    data["groups"] = dict(groups_by_format) if groups_by_format else {}

    yaml = YAML()
    yaml.default_flow_style = False

    stream = StringIO()
    yaml.dump(data, stream)
    yaml_body = stream.getvalue()

    header_lines = [
        "# Generated by admedi snapshot",
        f"# App: {app_name} ({app_key})",
        f"# Captured: {timestamp}",
        "",
    ]
    header = "\n".join(header_lines)

    return header + yaml_body


def _serialize_group(group: Group) -> dict[str, Any]:
    """Serialize a Group model to a dict with inlined instance data."""
    group_dict: dict[str, Any] = {
        "name": group.group_name,
        "group_id": group.group_id,
        "countries": list(group.countries),
        "position": group.position,
        "floor_price": group.floor_price,
        "ab_test": group.ab_test,
    }

    instances_list: list[dict[str, Any]] = []
    if group.instances:
        for instance in group.instances:
            instances_list.append(_serialize_instance(instance))

    group_dict["instances"] = instances_list

    return group_dict


def _serialize_instance(instance: "Instance") -> dict[str, Any]:
    """Serialize an Instance model to a dict for legacy snapshot output."""
    from admedi.models.instance import Instance  # noqa: F811

    instance_dict: dict[str, Any] = {
        "id": instance.instance_id,
        "name": instance.instance_name,
        "network": instance.network_name,
        "is_bidder": instance.is_bidder,
    }

    if instance.group_rate is not None:
        instance_dict["rate"] = instance.group_rate

    return instance_dict

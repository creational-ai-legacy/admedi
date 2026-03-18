"""Admedi CLI commands for config-driven ad mediation management.

Provides four typer commands:

- ``admedi pull``        -- Pull live mediation settings and generate per-app settings
- ``admedi audit``       -- Compare per-app settings against live state (profiles-based)
- ``admedi sync``        -- Sync settings to live mediation configs
- ``admedi status``      -- Show portfolio status overview (profiles-based)

Each command bridges synchronous typer to async engine methods via
``asyncio.run()``. The async helper manages the adapter lifecycle with
``async with LevelPlayAdapter(cred) as adapter:``.

Example::

    # Pull live settings for an app (generates settings + snapshot)
    admedi pull --app ss-ios

    # Audit for drift (all apps in profiles.yaml)
    admedi audit

    # Audit a single app
    admedi audit --app hexar-ios

    # Sync tiers with dry-run
    admedi sync --tiers hexar-ios --dry-run

    # Cross-app sync
    admedi sync --tiers hexar-ios hexar-google --dry-run

    # Show portfolio status (all apps in profiles.yaml)
    admedi status
"""

from __future__ import annotations

import asyncio
import json
from enum import Enum
from typing import Annotated

import typer

from admedi.adapters.levelplay import LevelPlayAdapter, load_credential_from_env
from admedi.cli.display import (
    display_apply_result,
    display_audit_table,
    display_pull,
    display_status_table,
    display_sync_preview,
)
from admedi.engine.applier import Applier
from admedi.engine.differ import compute_diff
from admedi.engine.engine import ConfigEngine
from admedi.engine.loader import Profile, load_profiles, resolve_app_tiers
from admedi.engine.snapshot import (
    extract_network_presets,
    generate_app_settings,
    save_raw_snapshot,
    write_yaml_file,
)
from admedi.exceptions import AuthError, ConfigValidationError
from admedi.models.diff import DiffAction, DiffReport
from admedi.storage.local import LocalFileStorageAdapter

app = typer.Typer(name="admedi", help="Config-driven ad mediation management")


class OutputFormat(str, Enum):
    """Output format for CLI commands."""

    TEXT = "text"
    JSON = "json"


def _load_credential() -> None:
    """Load credentials from environment; exit 2 with clear message on failure.

    Returns:
        A Credential instance on success.

    Raises:
        typer.Exit: With code 2 if credentials are missing.
    """
    try:
        return load_credential_from_env()
    except AuthError as exc:
        typer.echo(f"Error: {exc.message}", err=True)
        typer.echo(
            "Set LEVELPLAY_SECRET_KEY and LEVELPLAY_REFRESH_TOKEN "
            "in your environment or .env file.",
            err=True,
        )
        raise typer.Exit(code=2) from exc


def _handle_template_error(exc: ConfigValidationError | FileNotFoundError) -> None:
    """Print a clear error message for template loading failures and exit 2.

    Args:
        exc: The exception from template loading.

    Raises:
        typer.Exit: Always raises with code 2.
    """
    if isinstance(exc, ConfigValidationError):
        typer.echo(f"Error: {exc.message}", err=True)
    else:
        typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(code=2) from exc


def _resolve_profile(value: str) -> Profile:
    """Resolve an app alias to a Profile via profiles.yaml.

    Reads the expanded profiles.yaml and returns the Profile object for
    the given alias. Raises ConfigValidationError if the alias is not
    found -- raw app keys are no longer supported as direct input.

    Args:
        value: Profile alias (e.g., "hexar-ios").

    Returns:
        A Profile instance with app_key, app_name, and platform.

    Raises:
        ConfigValidationError: If the alias is not found in profiles.yaml.
        FileNotFoundError: If profiles.yaml does not exist.
    """
    profiles = load_profiles()
    if value not in profiles:
        available = ", ".join(sorted(profiles.keys()))
        raise ConfigValidationError(
            f"Unknown profile alias '{value}'. "
            f"Available profiles: {available}"
        )
    return profiles[value]


@app.command()
def pull(
    app_key: Annotated[
        str,
        typer.Option("--app", help="Profile alias from profiles.yaml."),
    ],
    output: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Override per-app settings file path."),
    ] = None,
) -> None:
    """Pull live mediation settings for an app.

    Fetches the current mediation groups from LevelPlay, matches them
    against existing tier definitions using country-content matching,
    generates per-app settings in the three-layer format, writes a
    networks file, and saves a raw snapshot.

    The --app flag requires a profile alias from profiles.yaml.

    Exit codes: 0 = success, 2 = error.
    """
    cred = _load_credential()

    try:
        profile = _resolve_profile(app_key)
    except (ConfigValidationError, FileNotFoundError) as exc:
        _handle_template_error(exc)

    async def _pull_async() -> None:
        async with LevelPlayAdapter(cred) as adapter:
            groups = await adapter.get_groups(profile.app_key)
            apps = await adapter.list_apps()

            # Resolve App model for display
            target_app = None
            for a in apps:
                if a.app_key == profile.app_key:
                    target_app = a
                    break

            if target_app is None:
                typer.echo(
                    f"Error: app key '{profile.app_key}' not found.", err=True
                )
                raise typer.Exit(code=2)

            # Check for metadata drift between profiles.yaml and API
            if target_app.app_name != profile.app_name:
                typer.echo(
                    f"Warning: profiles.yaml app_name '{profile.app_name}' "
                    f"differs from API '{target_app.app_name}' for {app_key}",
                    err=True,
                )
            if target_app.platform != profile.platform:
                typer.echo(
                    f"Warning: profiles.yaml platform '{profile.platform.value}' "
                    f"differs from API '{target_app.platform.value}' for {app_key}",
                    err=True,
                )

            # Generate per-app settings (country matching + shared files)
            settings_path, info_messages = generate_app_settings(
                groups, profile
            )

            # Handle --output override: move the generated file
            if output:
                import shutil
                shutil.move(settings_path, output)
                settings_path = output

            # Write networks file
            from pathlib import Path

            presets = extract_network_presets(groups)
            networks_path_obj = Path("settings") / f"{profile.alias}-networks.yaml"
            networks_path_obj.parent.mkdir(parents=True, exist_ok=True)
            networks_path_str: str | None = None
            if presets:
                write_yaml_file(
                    networks_path_obj,
                    presets,
                    header=(
                        f"# Network presets for {profile.alias}\n"
                        f"# Generated by admedi pull\n\n"
                    ),
                    root_key="presets",
                )
                networks_path_str = str(networks_path_obj)

            # Save raw snapshot
            snapshot_path = save_raw_snapshot(
                groups, profile.app_key, target_app.app_name,
                alias=profile.alias,
                platform=target_app.platform,
            )

        display_pull(
            target_app, groups,
            snapshot_path=snapshot_path,
            settings_path=settings_path,
            networks_path=networks_path_str,
            info_messages=info_messages if info_messages else None,
        )

    asyncio.run(_pull_async())


@app.command()
def audit(
    app_key: Annotated[
        str | None,
        typer.Option("--app", help="Filter to a specific profile alias."),
    ] = None,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Output format: text or json."),
    ] = OutputFormat.TEXT,
) -> None:
    """Audit the portfolio for drift against per-app settings.

    Compares per-app settings (resolved through the three-layer chain)
    against live LevelPlay mediation groups and reports any differences.
    Uses profiles.yaml for the app list.

    Exit codes: 0 = no drift, 1 = drift detected, 2 = error.
    """
    cred = _load_credential()

    # Resolve alias list for engine
    aliases: list[str] | None = None
    if app_key:
        try:
            _resolve_profile(app_key)  # validate alias exists
            aliases = [app_key]
        except (ConfigValidationError, FileNotFoundError) as exc:
            _handle_template_error(exc)

    async def _audit_async() -> None:
        async with LevelPlayAdapter(cred) as adapter:
            storage = LocalFileStorageAdapter()
            engine = ConfigEngine(adapter=adapter, storage=storage)

            try:
                report = await engine.audit(aliases=aliases)
            except (ConfigValidationError, FileNotFoundError) as exc:
                _handle_template_error(exc)

        if output_format == OutputFormat.JSON:
            typer.echo(report.model_dump_json(indent=2))
        else:
            display_audit_table(report)

        has_drift = report.total_creates > 0 or report.total_updates > 0
        if has_drift:
            raise typer.Exit(code=1)

    asyncio.run(_audit_async())


@app.command()
def sync(
    source: Annotated[
        str,
        typer.Argument(help="Source app alias (reads its settings files)."),
    ],
    destination: Annotated[
        str | None,
        typer.Argument(help="Target app alias (defaults to source for self-sync)."),
    ] = None,
    tiers: Annotated[
        bool,
        typer.Option("--tiers", help="Sync tier definitions."),
    ] = False,
    networks: Annotated[
        bool,
        typer.Option("--networks", help="Sync network configurations (not yet implemented)."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview changes without applying."),
    ] = False,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Output format: text or json."),
    ] = OutputFormat.TEXT,
) -> None:
    """Sync settings to live LevelPlay mediation groups.

    SOURCE is the app alias whose settings files are used as the desired
    state. DESTINATION is the target app alias to sync against (defaults
    to SOURCE for self-sync).

    Use --tiers to sync tier definitions. Use --dry-run to preview
    changes without applying.

    Exit codes: 0 = success (applied or no drift), 1 = drift detected
    (dry-run), 2 = error.
    """
    # Validate scope flags
    if networks:
        typer.echo("Error: --networks sync is not yet implemented.", err=True)
        raise typer.Exit(code=2)

    # Default to --tiers when no scope flags provided
    if not tiers and not networks:
        tiers = True

    source_alias = source
    target_alias = destination or source

    cred = _load_credential()

    # Resolve target alias via profile
    try:
        target_profile = _resolve_profile(target_alias)
        target_app_key = target_profile.app_key
    except (ConfigValidationError, FileNotFoundError) as exc:
        _handle_template_error(exc)

    # Load tiers from per-app settings (three-layer resolution)
    try:
        tier_list = resolve_app_tiers(source_alias)
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except ConfigValidationError as exc:
        _handle_template_error(exc)

    async def _sync_async() -> None:
        async with LevelPlayAdapter(cred) as adapter:
            storage = LocalFileStorageAdapter()

            # Fetch remote groups for target
            remote_groups = await adapter.get_groups(target_app_key)

            # Resolve target app_name via list_apps
            apps = await adapter.list_apps()
            target_app = None
            for a in apps:
                if a.app_key == target_app_key:
                    target_app = a
                    break

            if target_app is None:
                typer.echo(
                    f"Error: app key '{target_app_key}' not found.",
                    err=True,
                )
                raise typer.Exit(code=2)

            # Compute diff
            app_diff_report = compute_diff(
                tier_list, remote_groups, target_app_key, target_app.app_name
            )

            # Post-process: convert EXTRA -> DELETE (sync means "make it match")
            # The differ remains a pure function; the conversion is a CLI concern.
            for diff in app_diff_report.group_diffs:
                if diff.action == DiffAction.EXTRA:
                    diff.action = DiffAction.DELETE

            # Wrap in DiffReport
            diff_report = DiffReport(app_reports=[app_diff_report])

            # Check for drift (includes deletes so delete-only diffs trigger sync)
            has_drift = (
                diff_report.total_creates > 0
                or diff_report.total_updates > 0
                or diff_report.total_deletes > 0
            )

            if not has_drift:
                if output_format == OutputFormat.JSON:
                    result_data = {
                        "diff_report": json.loads(
                            diff_report.model_dump_json()
                        ),
                        "apply_result": None,
                    }
                    typer.echo(json.dumps(result_data, indent=2))
                else:
                    display_sync_preview(diff_report)
                return  # Exit 0 -- no drift

            if output_format != OutputFormat.JSON:
                display_sync_preview(diff_report)

            if dry_run:
                if output_format == OutputFormat.JSON:
                    result_data = {
                        "diff_report": json.loads(
                            diff_report.model_dump_json()
                        ),
                        "apply_result": None,
                    }
                    typer.echo(json.dumps(result_data, indent=2))
                raise typer.Exit(code=1)

            # Apply changes
            applier = Applier(adapter=adapter, storage=storage)
            apply_result = await applier.apply(diff_report, dry_run=False)

            if output_format == OutputFormat.JSON:
                result_data = {
                    "diff_report": json.loads(
                        diff_report.model_dump_json()
                    ),
                    "apply_result": json.loads(
                        apply_result.model_dump_json()
                    ),
                }
                typer.echo(json.dumps(result_data, indent=2))
            else:
                display_apply_result(apply_result)

    asyncio.run(_sync_async())


@app.command()
def status(
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Output format: text or json."),
    ] = OutputFormat.TEXT,
) -> None:
    """Show current portfolio status.

    Displays group counts, platforms, and last sync times for all
    portfolio apps defined in profiles.yaml.

    Exit codes: 0 = success, 2 = error.
    """
    cred = _load_credential()

    async def _status_async() -> None:
        async with LevelPlayAdapter(cred) as adapter:
            storage = LocalFileStorageAdapter()
            engine = ConfigEngine(adapter=adapter, storage=storage)

            try:
                portfolio_status = await engine.status()
            except (ConfigValidationError, FileNotFoundError) as exc:
                _handle_template_error(exc)

        if output_format == OutputFormat.JSON:
            typer.echo(portfolio_status.model_dump_json(indent=2))
        else:
            display_status_table(portfolio_status)

    asyncio.run(_status_async())


if __name__ == "__main__":
    app()

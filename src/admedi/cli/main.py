"""Admedi CLI commands for config-driven ad mediation management.

Provides four typer commands:

- ``admedi show``        -- Show live mediation settings and save raw snapshot + modular settings
- ``admedi audit``       -- Compare template against live state
- ``admedi sync``        -- Sync settings to live mediation configs
- ``admedi status``      -- Show portfolio status overview

Each command bridges synchronous typer to async engine methods via
``asyncio.run()``. The async helper manages the adapter lifecycle with
``async with LevelPlayAdapter(cred) as adapter:``.

Example::

    # Show live settings for an app (saves snapshot + settings)
    admedi show --app ss-ios

    # Audit for drift
    admedi audit --config examples/shelf-sort-tiers.yaml

    # Sync tiers with dry-run
    admedi sync --tiers hexar-ios --dry-run

    # Cross-app sync
    admedi sync --tiers hexar-ios hexar-google --dry-run

    # Show portfolio status
    admedi status --config examples/shelf-sort-tiers.yaml
"""

from __future__ import annotations

import asyncio
import json
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from ruamel.yaml import YAML

from admedi.adapters.levelplay import LevelPlayAdapter, load_credential_from_env
from admedi.cli.display import (
    display_apply_result,
    display_audit_table,
    display_show,
    display_status_table,
    display_sync_preview,
    display_tier_warnings,
)
from admedi.engine.applier import Applier
from admedi.engine.differ import compute_diff
from admedi.engine.engine import ConfigEngine
from admedi.engine.loader import load_tiers_settings
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


def _resolve_app_key(value: str) -> str:
    """Resolve an app alias to an app key via profiles.yaml.

    If profiles.yaml exists and contains a matching alias, return the
    mapped app key. Otherwise return the value as-is (raw app key).
    """
    profiles_path = Path("profiles.yaml")
    if not profiles_path.exists():
        return value

    yaml = YAML()
    data = yaml.load(profiles_path)
    profiles = data.get("profiles") if data else None
    if profiles and value in profiles:
        return str(profiles[value])
    return value


@app.command()
def show(
    app_key: Annotated[
        str,
        typer.Option("--app", help="App key or profile alias."),
    ],
    output: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Override snapshot file path."),
    ] = None,
) -> None:
    """Show live mediation settings for an app.

    Fetches the current mediation groups from LevelPlay, displays
    them as a Rich-formatted view organized by ad format, and saves
    a YAML snapshot to settings/{app_key}_snapshot.yaml (override with -o).

    The --app flag accepts a profile alias (from profiles.yaml) or a raw app key.

    Exit codes: 0 = success, 2 = error.
    """
    cred = _load_credential()
    resolved_key = _resolve_app_key(app_key)

    async def _show_async() -> None:
        async with LevelPlayAdapter(cred) as adapter:
            apps = await adapter.list_apps()
            groups = await adapter.get_groups(resolved_key)

            # Resolve App model
            target_app = None
            for a in apps:
                if a.app_key == resolved_key:
                    target_app = a
                    break

            if target_app is None:
                typer.echo(f"Error: app key '{resolved_key}' not found.", err=True)
                raise typer.Exit(code=2)

            # Save raw snapshot and modular settings
            from admedi.engine.snapshot import (
                save_modular_snapshot,
                save_raw_snapshot,
            )

            # Use alias as filename if available, otherwise app key
            alias = app_key if app_key != resolved_key else resolved_key

            # Raw snapshot always uses alias (not affected by --output flag)
            snapshot_path = save_raw_snapshot(
                groups, resolved_key, target_app.app_name,
                alias=alias,
                platform=target_app.platform,
            )

            # Modular settings (--output flag overrides filename)
            app_file = output or f"{alias}.yaml"
            settings_path, warnings = save_modular_snapshot(
                groups, resolved_key, target_app.app_name,
                app_file=app_file,
                platform=target_app.platform,
            )

        display_show(
            target_app, groups,
            snapshot_path=snapshot_path,
            settings_path=settings_path,
        )
        display_tier_warnings(warnings)

    asyncio.run(_show_async())


@app.command()
def audit(
    config: Annotated[
        Path,
        typer.Option("--config", help="Path to YAML tier template."),
    ] = Path("admedi.yaml"),
    app_key: Annotated[
        str | None,
        typer.Option("--app", help="Filter to a specific app key or profile alias."),
    ] = None,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Output format: text or json."),
    ] = OutputFormat.TEXT,
) -> None:
    """Audit the portfolio for drift against the tier template.

    Compares the YAML tier template against live LevelPlay mediation
    groups and reports any differences.

    Exit codes: 0 = no drift, 1 = drift detected, 2 = error.
    """
    cred = _load_credential()
    resolved = _resolve_app_key(app_key) if app_key else None
    app_keys = [resolved] if resolved else None

    async def _audit_async() -> None:
        async with LevelPlayAdapter(cred) as adapter:
            storage = LocalFileStorageAdapter()
            engine = ConfigEngine(adapter=adapter, storage=storage)

            try:
                report = await engine.audit(config, app_keys=app_keys)
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
    target_app_key = _resolve_app_key(target_alias)

    # Load tiers from settings
    try:
        tier_list = load_tiers_settings(source_alias)
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
    config: Annotated[
        Path,
        typer.Option("--config", help="Path to YAML tier template."),
    ] = Path("admedi.yaml"),
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Output format: text or json."),
    ] = OutputFormat.TEXT,
) -> None:
    """Show current portfolio status.

    Displays group counts, platforms, and last sync times for all
    portfolio apps defined in the tier template.

    Exit codes: 0 = success, 2 = error.
    """
    cred = _load_credential()

    async def _status_async() -> None:
        async with LevelPlayAdapter(cred) as adapter:
            storage = LocalFileStorageAdapter()
            engine = ConfigEngine(adapter=adapter, storage=storage)

            try:
                portfolio_status = await engine.status(config)
            except (ConfigValidationError, FileNotFoundError) as exc:
                _handle_template_error(exc)

        if output_format == OutputFormat.JSON:
            typer.echo(portfolio_status.model_dump_json(indent=2))
        else:
            display_status_table(portfolio_status)

    asyncio.run(_status_async())


if __name__ == "__main__":
    app()

"""Admedi CLI commands for config-driven ad mediation management.

Provides four typer commands that delegate to :class:`~admedi.engine.ConfigEngine`:

- ``admedi audit``       -- Compare template against live state
- ``admedi sync-tiers``  -- Apply tier template changes
- ``admedi snapshot``    -- Export a YAML snapshot of current groups
- ``admedi status``      -- Show portfolio status overview

Each command bridges synchronous typer to async engine methods via
``asyncio.run()``. The async helper manages the adapter lifecycle with
``async with LevelPlayAdapter(cred) as adapter:``.

Example::

    # Audit for drift
    admedi audit --config examples/shelf-sort-tiers.yaml

    # Sync tiers with dry-run
    admedi sync-tiers --dry-run --config examples/shelf-sort-tiers.yaml

    # Take a snapshot of a single app
    admedi snapshot --app abc123 --output snapshot.yaml

    # Show portfolio status
    admedi status --config examples/shelf-sort-tiers.yaml
"""

from __future__ import annotations

import asyncio
import json
import sys
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from admedi.adapters.levelplay import LevelPlayAdapter, load_credential_from_env
from admedi.cli.display import (
    display_apply_result,
    display_audit_table,
    display_snapshot_info,
    display_status_table,
    display_sync_preview,
)
from admedi.engine.engine import ConfigEngine
from admedi.engine.loader import load_template
from admedi.exceptions import AuthError, ConfigValidationError
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


@app.command()
def audit(
    config: Annotated[
        Path,
        typer.Option("--config", help="Path to YAML tier template."),
    ] = Path("admedi.yaml"),
    app_key: Annotated[
        str | None,
        typer.Option("--app", help="Filter to a specific app key."),
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
    app_keys = [app_key] if app_key else None

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


@app.command(name="sync-tiers")
def sync_tiers(
    config: Annotated[
        Path,
        typer.Option("--config", help="Path to YAML tier template."),
    ] = Path("admedi.yaml"),
    app_key: Annotated[
        str | None,
        typer.Option("--app", help="Filter to a specific app key."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview changes without applying."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt."),
    ] = False,
    output_format: Annotated[
        OutputFormat,
        typer.Option("--format", help="Output format: text or json."),
    ] = OutputFormat.TEXT,
) -> None:
    """Sync tier template changes to live LevelPlay mediation groups.

    Loads the template, computes diffs, shows a preview, and optionally
    applies changes. Use --dry-run to preview without applying.

    Exit codes: 0 = success (applied or no drift), 1 = drift detected
    but not applied (dry-run or user declined), 2 = error.
    """
    cred = _load_credential()
    app_keys = [app_key] if app_key else None

    async def _sync_async() -> None:
        async with LevelPlayAdapter(cred) as adapter:
            storage = LocalFileStorageAdapter()
            engine = ConfigEngine(adapter=adapter, storage=storage)

            try:
                diff_report = await engine.audit(config, app_keys=app_keys)
            except (ConfigValidationError, FileNotFoundError) as exc:
                _handle_template_error(exc)

        has_drift = diff_report.total_creates > 0 or diff_report.total_updates > 0

        if not has_drift:
            if output_format == OutputFormat.JSON:
                result_data = {
                    "diff_report": json.loads(diff_report.model_dump_json()),
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
                    "diff_report": json.loads(diff_report.model_dump_json()),
                    "apply_result": None,
                }
                typer.echo(json.dumps(result_data, indent=2))
            raise typer.Exit(code=1)

        # Prompt for confirmation unless --yes
        if not yes:
            confirmed = typer.confirm("Apply these changes?")
            if not confirmed:
                raise typer.Exit(code=1)

        # Apply changes
        async with LevelPlayAdapter(cred) as adapter:
            storage = LocalFileStorageAdapter()
            engine = ConfigEngine(adapter=adapter, storage=storage)
            _, apply_result = await engine.sync(
                config, app_keys=app_keys, dry_run=False
            )

        if output_format == OutputFormat.JSON:
            result_data = {
                "diff_report": json.loads(diff_report.model_dump_json()),
                "apply_result": json.loads(apply_result.model_dump_json()),
            }
            typer.echo(json.dumps(result_data, indent=2))
        else:
            display_apply_result(apply_result)

    asyncio.run(_sync_async())


@app.command()
def snapshot(
    app_key: Annotated[
        str | None,
        typer.Option("--app", help="App key to snapshot."),
    ] = None,
    output: Annotated[
        str | None,
        typer.Option("--output", "-o", help="Output file path (or directory with --all)."),
    ] = None,
    all_apps: Annotated[
        bool,
        typer.Option("--all", help="Snapshot all portfolio apps (requires --config)."),
    ] = False,
    config: Annotated[
        Path | None,
        typer.Option("--config", help="Path to YAML tier template (required with --all)."),
    ] = None,
) -> None:
    """Export a YAML snapshot of current mediation groups.

    Use --app for a single app snapshot, or --all with --config for
    all portfolio apps.

    Exit codes: 0 = success, 2 = error.
    """
    # Validate mutual exclusivity
    if app_key and all_apps:
        typer.echo("Error: --app and --all are mutually exclusive.", err=True)
        raise typer.Exit(code=2)

    if not app_key and not all_apps:
        typer.echo("Error: either --app or --all is required.", err=True)
        raise typer.Exit(code=2)

    if all_apps and not config:
        typer.echo("Error: --config is required when using --all.", err=True)
        raise typer.Exit(code=2)

    cred = _load_credential()

    if all_apps:
        # --all flow: load template and snapshot each app
        try:
            portfolio_config = load_template(config)
        except (ConfigValidationError, FileNotFoundError) as exc:
            _handle_template_error(exc)

        output_dir = Path(output) if output else Path(".")

        errors: list[tuple[str, str]] = []

        async def _snapshot_all_async() -> None:
            async with LevelPlayAdapter(cred) as adapter:
                storage = LocalFileStorageAdapter()
                engine = ConfigEngine(adapter=adapter, storage=storage)

                for portfolio_app in portfolio_config.portfolio:
                    out_path = output_dir / f"{portfolio_app.app_key}_snapshot.yaml"
                    try:
                        await engine.snapshot(
                            portfolio_app.app_key, output_path=str(out_path)
                        )
                        display_snapshot_info(
                            str(out_path), portfolio_app.name
                        )
                    except Exception as exc:
                        errors.append((portfolio_app.app_key, str(exc)))
                        typer.echo(
                            f"Error snapshotting {portfolio_app.app_key}: {exc}",
                            err=True,
                        )

        asyncio.run(_snapshot_all_async())

        if errors:
            typer.echo(
                f"\n{len(errors)} app(s) failed during snapshot.", err=True
            )
            raise typer.Exit(code=2)
    else:
        # Single app snapshot
        async def _snapshot_async() -> None:
            async with LevelPlayAdapter(cred) as adapter:
                storage = LocalFileStorageAdapter()
                engine = ConfigEngine(adapter=adapter, storage=storage)
                await engine.snapshot(app_key, output_path=output)
                display_snapshot_info(
                    output or f"{app_key}_snapshot.yaml", app_key
                )

        asyncio.run(_snapshot_async())


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

"""Rich terminal display formatters for admedi CLI commands.

All display functions accept an optional ``Console`` parameter for testability.
When ``None``, a default ``Console()`` is created. For tests, inject a
``Console(file=StringIO(), highlight=False)`` to capture output to a buffer.

Examples:
    >>> from io import StringIO
    >>> from rich.console import Console
    >>> from admedi.cli.display import display_show
    >>> buf = StringIO()
    >>> console = Console(file=buf, highlight=False)
    >>> # display_show(app, groups, snapshot_path="...", console=console)
"""

from __future__ import annotations

from collections import defaultdict

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from admedi.models.app import App
from admedi.models.apply_result import (
    ApplyResult,
    ApplyStatus,
    AppApplyResult,
    PortfolioStatus,
)
from admedi.models.diff import DiffAction, DiffReport
from admedi.models.group import Group


def _get_console(console: Console | None) -> Console:
    """Return the provided console or create a default one."""
    return console if console is not None else Console()


def display_audit_table(
    diff_report: DiffReport,
    console: Console | None = None,
) -> None:
    """Render audit results as a rich table.

    Columns: App, Status (OK/DRIFT), Issues (summary of changes).
    EXTRA groups are shown with dimmed styling for visual distinction.

    Args:
        diff_report: The diff report to display.
        console: Optional console for output capture in tests.
    """
    con = _get_console(console)

    if not diff_report.app_reports:
        con.print(Panel("[dim]No apps to audit.[/dim]", title="Audit Results"))
        return

    table = Table(title="Audit Results")
    table.add_column("App", style="cyan", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Issues")

    for app_report in diff_report.app_reports:
        creates = sum(
            1 for d in app_report.group_diffs if d.action == DiffAction.CREATE
        )
        updates = sum(
            1 for d in app_report.group_diffs if d.action == DiffAction.UPDATE
        )
        extras = sum(
            1 for d in app_report.group_diffs if d.action == DiffAction.EXTRA
        )

        has_drift = creates > 0 or updates > 0

        if has_drift:
            status_text = Text("DRIFT", style="bold red")
        else:
            status_text = Text("OK", style="bold green")

        # Build issues summary
        parts: list[str] = []
        if creates > 0:
            parts.append(f"{creates} to create")
        if updates > 0:
            parts.append(f"{updates} to update")
        if extras > 0:
            parts.append(f"{extras} extra")

        if parts:
            issues_text = Text()
            for i, part in enumerate(parts):
                if i > 0:
                    issues_text.append(", ")
                if "extra" in part:
                    issues_text.append(part, style="dim")
                else:
                    issues_text.append(part)
        else:
            issues_text = Text("All groups match", style="dim")

        table.add_row(app_report.app_name, status_text, issues_text)

    con.print(table)

    # Summary line
    total = diff_report.total_creates + diff_report.total_updates
    if total > 0:
        con.print(
            f"\n[bold]{total} change(s)[/bold] across "
            f"{len(diff_report.app_reports)} app(s)"
        )
    else:
        con.print("\n[green]All apps in sync.[/green]")


def display_sync_preview(
    diff_report: DiffReport,
    console: Console | None = None,
) -> None:
    """Render sync preview as a rich table.

    Columns: App, Group, Change. CREATE/UPDATE/DELETE actions are shown
    prominently. EXTRA groups are shown with dimmed styling for visual
    distinction.

    Args:
        diff_report: The diff report to display as a sync preview.
        console: Optional console for output capture in tests.
    """
    con = _get_console(console)

    if not diff_report.app_reports:
        con.print(Panel("[dim]No changes to preview.[/dim]", title="Sync Preview"))
        return

    table = Table(title="Sync Preview")
    table.add_column("App", style="cyan", no_wrap=True)
    table.add_column("Group", no_wrap=True)
    table.add_column("Change")

    has_any_actionable = False

    for app_report in diff_report.app_reports:
        first_row = True
        for group_diff in app_report.group_diffs:
            if group_diff.action == DiffAction.UNCHANGED:
                continue

            app_display = app_report.app_name if first_row else ""
            first_row = False

            match group_diff.action:
                case DiffAction.CREATE:
                    has_any_actionable = True
                    change_text = Text("CREATE", style="bold green")
                    group_text = Text(group_diff.group_name)
                case DiffAction.UPDATE:
                    has_any_actionable = True
                    change_desc = ", ".join(c.description for c in group_diff.changes)
                    change_text = Text(f"UPDATE: {change_desc}", style="bold yellow")
                    group_text = Text(group_diff.group_name)
                case DiffAction.EXTRA:
                    change_text = Text("EXTRA (no action)", style="dim")
                    group_text = Text(group_diff.group_name, style="dim")
                case DiffAction.DELETE:
                    has_any_actionable = True
                    change_text = Text("DELETE", style="bold red")
                    group_text = Text(group_diff.group_name)
                case _:
                    continue

            table.add_row(app_display, group_text, change_text)

        if not first_row:
            table.add_section()

    con.print(table)

    if not has_any_actionable:
        con.print("\n[green]No changes to apply.[/green]")
    else:
        total = (
            diff_report.total_creates
            + diff_report.total_updates
            + diff_report.total_deletes
        )
        con.print(
            f"\n[bold]{total} change(s)[/bold] will be applied "
            f"({diff_report.total_creates} create, "
            f"{diff_report.total_updates} update, "
            f"{diff_report.total_deletes} delete)"
        )


def display_apply_result(
    result: ApplyResult,
    console: Console | None = None,
) -> None:
    """Render post-apply summary.

    Shows success/skipped/failed counts per app. Displays any warnings
    from AppApplyResult entries.

    Args:
        result: The apply result to display.
        console: Optional console for output capture in tests.
    """
    con = _get_console(console)

    if not result.app_results:
        con.print(Panel("[dim]No apps processed.[/dim]", title="Apply Results"))
        return

    title = "Apply Results (DRY RUN)" if result.was_dry_run else "Apply Results"

    table = Table(title=title)
    table.add_column("App", style="cyan", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Created", justify="right")
    table.add_column("Updated", justify="right")
    table.add_column("Deleted", justify="right")

    all_warnings: list[tuple[str, str]] = []

    for app_result in result.app_results:
        status_text = _format_apply_status(app_result)

        table.add_row(
            app_result.app_name or app_result.app_key,
            status_text,
            str(app_result.groups_created),
            str(app_result.groups_updated),
            str(app_result.groups_deleted),
        )

        for warning in app_result.warnings:
            all_warnings.append((app_result.app_name or app_result.app_key, warning))

    con.print(table)

    # Summary counts
    con.print(
        f"\n[bold]Summary[/bold]: "
        f"[green]{result.total_success} success[/green], "
        f"[yellow]{result.total_skipped} skipped[/yellow], "
        f"[red]{result.total_failed} failed[/red]"
    )

    # Display warnings if any
    if all_warnings:
        con.print("\n[bold yellow]Warnings:[/bold yellow]")
        for app_label, warning in all_warnings:
            con.print(f"  [{app_label}] {warning}")


def _format_apply_status(app_result: AppApplyResult) -> Text:
    """Format an apply status as styled rich Text."""
    match app_result.status:
        case ApplyStatus.SUCCESS:
            return Text("SUCCESS", style="bold green")
        case ApplyStatus.SKIPPED:
            return Text("SKIPPED", style="bold yellow")
        case ApplyStatus.FAILED:
            error_msg = f"FAILED: {app_result.error}" if app_result.error else "FAILED"
            return Text(error_msg, style="bold red")
        case ApplyStatus.DRY_RUN:
            return Text("DRY RUN", style="bold blue")
        case _:
            return Text(str(app_result.status), style="dim")


def display_status_table(
    status: PortfolioStatus,
    console: Console | None = None,
) -> None:
    """Render portfolio status table.

    Columns: App, Platform, Groups, Last Sync.

    Args:
        status: The portfolio status to display.
        console: Optional console for output capture in tests.
    """
    con = _get_console(console)

    if not status.apps:
        con.print(
            Panel("[dim]No apps in portfolio.[/dim]", title="Portfolio Status")
        )
        return

    table = Table(title=f"Portfolio Status ({status.mediator.value})")
    table.add_column("App", style="cyan", no_wrap=True)
    table.add_column("Platform", no_wrap=True)
    table.add_column("Groups", justify="right")
    table.add_column("Last Sync", no_wrap=True)

    for app_status in status.apps:
        last_sync_str = (
            app_status.last_sync.strftime("%Y-%m-%d %H:%M")
            if app_status.last_sync
            else "Never"
        )

        table.add_row(
            app_status.app_name,
            app_status.platform.value,
            str(app_status.group_count),
            last_sync_str,
        )

    con.print(table)


def display_show(
    app: App,
    groups: list[Group],
    snapshot_path: str | None = None,
    settings_path: str | None = None,
    console: Console | None = None,
) -> None:
    """Render a Rich view of an app's live mediation settings.

    Shows a header panel with app metadata, then one table per ad format
    with group tiers, country targeting, and waterfall details (bidding
    networks and manual instances with rates).

    Args:
        app: The App model with name, platform, and key.
        groups: List of Group models from the live API.
        snapshot_path: If provided, shown as a footer line for the raw snapshot.
        settings_path: If provided, shown as a footer line for the modular settings.
        console: Optional console for output capture in tests.
    """
    con = _get_console(console)

    # Header panel
    con.print()
    con.print(Panel(
        f"[bold]{app.app_name}[/bold]\n"
        f"[dim]Platform:[/dim] {app.platform.value}  "
        f"[dim]Key:[/dim] {app.app_key}  "
        f"[dim]Groups:[/dim] {len(groups)}",
        title="[bold cyan]admedi[/bold cyan]",
        border_style="cyan",
    ))

    if not groups:
        con.print("[dim]No mediation groups configured.[/dim]")
        if snapshot_path:
            con.print(f"\n[dim]Snapshot saved to:[/dim] [cyan]{snapshot_path}[/cyan]")
        if settings_path:
            con.print(f"[dim]Settings saved to:[/dim] [cyan]{settings_path}[/cyan]")
        con.print()
        return

    # Organize by ad format, sorted by position within each
    by_format: dict[str, list[Group]] = defaultdict(list)
    for g in groups:
        by_format[g.ad_format.value].append(g)
    for fmt in by_format:
        by_format[fmt].sort(key=lambda g: g.position)

    # One table per format
    for fmt in sorted(by_format.keys()):
        fmt_groups = by_format[fmt]

        table = Table(
            title=fmt,
            box=box.ROUNDED,
            title_style="bold",
            border_style="dim",
            show_lines=True,
            padding=(0, 1),
        )
        table.add_column("#", style="dim", justify="right", width=3)
        table.add_column("Group", style="cyan", no_wrap=True)
        table.add_column("Countries")
        table.add_column("Waterfall")

        for g in fmt_groups:
            countries = (
                Text("* (all)", style="dim")
                if g.countries == ["*"]
                else Text(", ".join(g.countries))
            )
            waterfall = _format_waterfall(g)
            table.add_row(str(g.position), g.group_name, countries, waterfall)

        con.print(table)

    if snapshot_path:
        con.print(f"\n[dim]Snapshot saved to:[/dim] [cyan]{snapshot_path}[/cyan]")
    if settings_path:
        con.print(f"[dim]Settings saved to:[/dim] [cyan]{settings_path}[/cyan]")
    con.print()


def display_tier_warnings(
    warnings: list[str],
    console: Console | None = None,
) -> None:
    """Render per-format country variance warnings as a Rich panel.

    When ``_extract_tiers()`` detects that the same tier name has different
    country lists across ad formats, it emits human-readable warning strings.
    This function displays them in a yellow-bordered panel after the show
    command output.

    If *warnings* is empty, nothing is printed.

    Args:
        warnings: List of warning strings from ``_extract_tiers()``.
        console: Optional console for output capture in tests.

    Example::

        >>> from io import StringIO
        >>> from rich.console import Console
        >>> from admedi.cli.display import display_tier_warnings
        >>> buf = StringIO()
        >>> console = Console(file=buf, highlight=False)
        >>> display_tier_warnings(["Tier 2: countries differ..."], console=console)
    """
    if not warnings:
        return

    con = _get_console(console)
    warning_lines = "\n".join(f"[yellow]  {w}[/yellow]" for w in warnings)
    con.print(Panel(
        warning_lines,
        title="[bold yellow]Tier Warnings[/bold yellow]",
        border_style="yellow",
    ))


def _format_waterfall(group: Group) -> Text:
    """Format a group's waterfall instances as styled Text."""
    if not group.instances:
        return Text("(none)", style="dim")

    bidders = [i for i in group.instances if i.is_bidder]
    manuals = [i for i in group.instances if not i.is_bidder]

    wf = Text()

    if bidders:
        networks = ", ".join(sorted({i.network_name for i in bidders}))
        wf.append("Bidding: ", style="bold green")
        wf.append(networks)

    if manuals:
        for j, m in enumerate(manuals):
            if bidders or j > 0:
                wf.append("\n")
            rate_str = f" @ ${m.group_rate:.2f}" if m.group_rate else ""
            wf.append("Manual:  ", style="bold yellow")
            wf.append(f"{m.network_name}{rate_str}")
            if m.instance_name and m.instance_name != "Default":
                wf.append(f" ({m.instance_name})", style="dim")

    return wf

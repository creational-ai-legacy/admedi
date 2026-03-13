"""Admedi CLI commands.

Re-exports the typer app and display functions::

    from admedi.cli import app
    from admedi.cli.display import display_audit_table
"""

from admedi.cli.main import app

__all__ = ["app"]

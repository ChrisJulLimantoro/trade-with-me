"""ATS CLI entrypoint.

Phase 0: --version, --help.
Spec 01 adds: ats db, ats ingest, ats data.
"""

from __future__ import annotations

import typer

from ats import __version__
from ats.cli_commands import data as data_cmd
from ats.cli_commands import db, ingest, process, regime

app = typer.Typer(
    name="ats",
    help=(
        "Agentic Trading System — crypto perpetuals signal engine.\n\n"
        "See specs/00-roadmap.md for the full command surface and milestone map."
    ),
    add_completion=False,
    no_args_is_help=True,
)

app.add_typer(db.app)
app.add_typer(ingest.app)
app.add_typer(data_cmd.app)
app.add_typer(process.app)
app.add_typer(regime.app)


def _version_cb(value: bool) -> None:
    if value:
        typer.echo(f"ats {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    _version: bool = typer.Option(
        None,
        "--version",
        "-v",
        callback=_version_cb,
        is_eager=True,
        expose_value=False,
        help="Show version and exit.",
    ),
) -> None:
    from ats.config import settings
    from ats.logging import configure_logging

    configure_logging(settings.log_level, settings.log_render)

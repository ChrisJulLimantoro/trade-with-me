"""ATS CLI — baseline entrypoint.

Each phase extends this module with its own commands. See `specs/00-overview.md`
for the cumulative command surface. The Phase-0 baseline only exposes
``--version`` and ``--help``.
"""

from __future__ import annotations

import typer

from ats import __version__

app = typer.Typer(
    name="ats",
    help=(
        "Agentic Trading System — crypto perpetuals signal engine.\n\n"
        "Commands are added per-phase. See specs/00-overview.md for the current "
        "command surface and roadmap."
    ),
    add_completion=False,
    no_args_is_help=True,
)


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
    """No-op root callback; phase commands are registered here."""

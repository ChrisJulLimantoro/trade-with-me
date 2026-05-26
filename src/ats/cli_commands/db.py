from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer

app = typer.Typer(name="db", help="Database migration commands.")

_REPO_ROOT = Path(__file__).parents[3]


@app.command()
def migrate() -> None:
    """Apply all pending Alembic migrations (alembic upgrade head)."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_REPO_ROOT,
    )
    raise typer.Exit(result.returncode)


@app.command()
def downgrade(to: str = typer.Option(..., "--to", help="Revision to downgrade to.")) -> None:
    """Downgrade to a specific Alembic revision."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", to],
        cwd=_REPO_ROOT,
    )
    raise typer.Exit(result.returncode)

"""CLI: ats learn — inspect the episodic-memory learnings written on each closed trade."""

from __future__ import annotations

import asyncio
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from ats.db.session import SessionLocal

app = typer.Typer(name="learn", help="Inspect episodic-memory learnings.")
console = Console()


@app.command("list")
def list_(
    limit: int = typer.Option(20, "--limit", "-n", help="Recent learnings to show."),
    direction: str = typer.Option("", "--direction", help="Filter long|short."),
) -> None:
    """List recent learnings (most recent first)."""
    from sqlalchemy import text

    async def _run() -> list[Any]:
        sql = (
            "SELECT created_at, symbol, direction, regime_cell, category, outcome, "
            "pnl_pct, proposed_adjustment FROM learnings"
        )
        params: dict[str, Any] = {"limit": limit}
        if direction:
            sql += " WHERE direction = :direction"
            params["direction"] = direction
        sql += " ORDER BY created_at DESC LIMIT :limit"
        async with SessionLocal() as session:
            return [r._mapping for r in (await session.execute(text(sql), params)).fetchall()]

    rows = asyncio.run(_run())
    if not rows:
        console.print("[yellow]No learnings yet. Run a replay to accumulate some.[/yellow]")
        return

    table = Table(title=f"Learnings (latest {len(rows)})")
    for col in ("when", "symbol", "dir", "regime", "category", "outcome", "pnl", "adjustment"):
        table.add_column(col)
    for r in rows:
        pnl = r["pnl_pct"]
        table.add_row(
            str(r["created_at"])[:19],
            r["symbol"],
            r["direction"],
            r["regime_cell"] or "-",
            r["category"],
            r["outcome"],
            f"{float(pnl) * 100:+.2f}%" if pnl is not None else "-",
            (r["proposed_adjustment"] or "")[:48],
        )
    console.print(table)


@app.command()
def show(learning_id: str = typer.Argument(..., help="Learning UUID.")) -> None:
    """Show one learning in full."""
    from sqlalchemy import text

    async def _run() -> Any:
        async with SessionLocal() as session:
            res = await session.execute(
                text("SELECT * FROM learnings WHERE id = :id"), {"id": learning_id}
            )
            return res.mappings().first()

    row = asyncio.run(_run())
    if row is None:
        console.print(f"[red]No learning {learning_id}.[/red]")
        raise typer.Exit(1)
    for k, v in row.items():
        if k == "fingerprint":
            continue
        console.print(f"[cyan]{k}[/cyan]: {v}")


@app.command()
def validate() -> None:
    """Programmatic smoke test: the learnings table exists and is queryable."""
    from sqlalchemy import text

    async def _run() -> int:
        async with SessionLocal() as session:
            try:
                res = await session.execute(text("SELECT COUNT(*) FROM learnings"))
            except Exception as e:  # noqa: BLE001
                typer.echo(f"learnings not ready — run 'ats db migrate'. ({e})", err=True)
                raise typer.Exit(1) from e
            return int(res.scalar() or 0)

    count = asyncio.run(_run())
    typer.echo(f"learnings rows: {count}")
    typer.echo("OK")

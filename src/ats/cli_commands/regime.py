"""CLI commands for regime inspection."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from ats.db.session import SessionLocal

app = typer.Typer(name="regime", help="Regime inspection commands.")
console = Console()


def _parse_since(s: str) -> timedelta:
    """Parse strings like '7d', '120d', '2h', '30m' into timedelta."""
    m = re.fullmatch(r"(\d+)([dhm])", s.strip())
    if not m:
        raise typer.BadParameter(f"Cannot parse duration '{s}'. Use e.g. 7d, 30d.")
    n, unit = int(m.group(1)), m.group(2)
    if unit == "d":
        return timedelta(days=n)
    if unit == "h":
        return timedelta(hours=n)
    return timedelta(minutes=n)


@app.command()
def show(
    history: str = typer.Option("30d", "--history", help="History window, e.g. 30d."),
) -> None:
    """Show current regime + recent history."""
    from sqlalchemy import text

    delta = _parse_since(history)
    since_dt = datetime.now(UTC) - delta

    async def _run() -> list[Any]:
        async with SessionLocal() as session:
            result = await session.execute(
                text("""
                    SELECT ts, btc_ema_slope_30d, btc_realized_vol_30d, vol_percentile_180d,
                           trend, volatility, regime_cell
                    FROM regimes
                    WHERE ts >= :since
                    ORDER BY ts DESC
                    LIMIT 100
                """),
                {"since": since_dt},
            )
            return list(result.fetchall())

    rows = asyncio.run(_run())

    if not rows:
        console.print("[yellow]No regime data found. Run 'ats process backfill' first.[/yellow]")
        return

    # Current regime (most recent)
    latest = rows[0]
    console.print(f"\n[bold cyan]Current Regime:[/bold cyan] [bold]{latest.regime_cell}[/bold]")
    console.print(
        f"  EMA slope: {float(latest.btc_ema_slope_30d):+.3f}%/d  "
        f"| realized vol: {float(latest.btc_realized_vol_30d):.4f}  "
        f"| vol pct: {float(latest.vol_percentile_180d):.2f}\n"
    )

    t = Table(title=f"Regime History (last {history})")
    t.add_column("ts", style="cyan")
    t.add_column("regime_cell", style="bold")
    t.add_column("trend")
    t.add_column("volatility")
    t.add_column("ema_slope_%/d", justify="right")
    t.add_column("vol_pct", justify="right")

    trend_colors = {"bull": "green", "bear": "red", "side": "yellow"}
    vol_colors = {"high": "red", "low": "blue"}

    for row in rows:
        tc = trend_colors.get(row.trend, "white")
        vc = vol_colors.get(row.volatility, "white")
        t.add_row(
            str(row.ts)[:19],
            row.regime_cell,
            f"[{tc}]{row.trend}[/{tc}]",
            f"[{vc}]{row.volatility}[/{vc}]",
            f"{float(row.btc_ema_slope_30d):+.3f}",
            f"{float(row.vol_percentile_180d):.2f}",
        )

    console.print(t)

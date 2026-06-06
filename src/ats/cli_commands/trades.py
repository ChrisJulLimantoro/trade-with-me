"""CLI: ats trades — inspect paper trades."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import text

from ats.db.session import SessionLocal

app = typer.Typer(name="trades", help="Inspect paper trades.")
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
    status: str = typer.Option("", "--status", help="Filter: open|closed."),
    symbol: str = typer.Option("", "--symbol", help="Filter by symbol."),
    limit: int = typer.Option(50, "--limit", help="Max rows."),
) -> None:
    """List paper trades."""
    where = []
    params: dict[str, object] = {"lim": limit}
    if status:
        where.append("status = :status")
        params["status"] = status
    if symbol:
        where.append("symbol = :symbol")
        params["symbol"] = symbol
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    async def _run() -> list:
        async with SessionLocal() as session:
            res = await session.execute(
                text(
                    "SELECT trade_id, symbol, direction, status, entry_price, exit_price, "
                    "exit_reason, pnl_pct, pnl_usd, leverage, margin_usd, notional_usd, "
                    "risk_usd, entry_time "
                    f"FROM paper_trades{clause} ORDER BY entry_time DESC LIMIT :lim"
                ),
                params,
            )
            return list(res.mappings().all())

    rows = asyncio.run(_run())
    if not rows:
        console.print("[yellow]No trades. Run 'ats engine replay' first.[/yellow]")
        return

    t = Table(title="Paper trades")
    t.add_column("trade_id", style="cyan", no_wrap=True)
    t.add_column("symbol")
    t.add_column("dir", style="bold")
    t.add_column("status")
    t.add_column("entry", justify="right")
    t.add_column("exit", justify="right")
    t.add_column("margin$", justify="right")
    t.add_column("notional$", justify="right")
    t.add_column("lev", justify="right")
    t.add_column("risk$", justify="right")
    t.add_column("reason")
    t.add_column("margin pnl%", justify="right")
    t.add_column("pnl$", justify="right")
    dir_color = {"long": "green", "short": "red"}
    for r in rows:
        dc = dir_color.get(r["direction"], "white")
        pnl = r["pnl_pct"]
        pnl_str = f"{float(pnl) * 100:+.2f}" if pnl is not None else "-"
        pnl_color = "white" if pnl is None else "green" if float(pnl) > 0 else "red"
        t.add_row(
            str(r["trade_id"])[:8],
            r["symbol"],
            f"[{dc}]{r['direction']}[/{dc}]",
            r["status"],
            f"{float(r['entry_price']):.2f}",
            f"{float(r['exit_price']):.2f}" if r["exit_price"] is not None else "-",
            f"{float(r['margin_usd']):.2f}" if r["margin_usd"] is not None else "-",
            f"{float(r['notional_usd']):.2f}" if r["notional_usd"] is not None else "-",
            f"{float(r['leverage']):.2f}x" if r["leverage"] is not None else "-",
            f"{float(r['risk_usd']):.2f}" if r["risk_usd"] is not None else "-",
            r["exit_reason"] or "-",
            f"[{pnl_color}]{pnl_str}[/{pnl_color}]",
            f"{float(r['pnl_usd']):+.2f}" if r["pnl_usd"] is not None else "-",
        )
    console.print(t)


@app.command()
def stats(
    since: str = typer.Option("90d", "--since", help="Window, e.g. 30d, 90d."),
    symbol: str = typer.Option("", "--symbol", help="Filter by symbol."),
) -> None:
    """Aggregate stats over closed trades."""
    since_dt = datetime.now(UTC) - _parse_since(since)
    sym_clause = " AND symbol = :symbol" if symbol else ""
    params: dict[str, object] = {"since": since_dt}
    if symbol:
        params["symbol"] = symbol

    async def _run() -> tuple:
        async with SessionLocal() as session:
            agg = (
                await session.execute(
                    text(
                        "SELECT count(*) AS n, "
                        "count(*) FILTER (WHERE pnl_pct > 0) AS wins, "
                        "round(avg(pnl_pct)::numeric, 4) AS avg_pnl, "
                        "round(avg(pnl_pct) FILTER (WHERE pnl_pct > 0)::numeric, 4) AS avg_win, "
                        "round(avg(pnl_pct) FILTER (WHERE pnl_pct <= 0)::numeric, 4) AS avg_loss, "
                        "round(sum(pnl_usd)::numeric, 2) AS pnl_usd "
                        "FROM paper_trades WHERE status='closed' AND entry_time >= :since"
                        + sym_clause
                    ),
                    params,
                )
            ).mappings().first()
            by_reason = (
                await session.execute(
                    text(
                        "SELECT exit_reason, count(*) AS n, "
                        "round(avg(pnl_pct)::numeric,4) AS avg_pnl "
                        "FROM paper_trades WHERE status='closed' AND entry_time >= :since"
                        + sym_clause
                        + " GROUP BY exit_reason ORDER BY n DESC"
                    ),
                    params,
                )
            ).mappings().all()
            return agg, by_reason

    agg, by_reason = asyncio.run(_run())
    n = agg["n"] or 0
    if n == 0:
        console.print("[yellow]No closed trades in window.[/yellow]")
        return
    wins = agg["wins"] or 0
    win_rate = wins / n
    avg_win = float(agg["avg_win"] or 0)
    avg_loss = float(agg["avg_loss"] or 0)
    expectancy = avg_win * win_rate + avg_loss * (1 - win_rate)
    console.print(
        f"\n[bold cyan]Trade stats[/bold cyan] (last {since}{', ' + symbol if symbol else ''})\n"
        f"  n={n}  win_rate={win_rate * 100:.0f}%  "
        f"avg_margin_pnl={float(agg['avg_pnl'] or 0) * 100:+.3f}%  "
        f"margin_expectancy={expectancy * 100:+.3f}%  total_pnl=${agg['pnl_usd']}"
    )
    t = Table(title="By exit reason")
    t.add_column("reason")
    t.add_column("n", justify="right")
    t.add_column("avg margin pnl%", justify="right")
    for r in by_reason:
        t.add_row(r["exit_reason"] or "-", str(r["n"]), f"{float(r['avg_pnl'] or 0) * 100:+.2f}")
    console.print(t)

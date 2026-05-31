"""CLI: ats engine — run the trading loop (replay / tick / live)."""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta

import typer
from rich.console import Console

from ats.db.session import SessionLocal
from ats.llm.client import get_client

app = typer.Typer(name="engine", help="Run the rule engine + trade loop.")
console = Console()


def _parse_since(s: str) -> timedelta:
    """Parse strings like '7d', '120d', '2h', '30m' into timedelta."""
    m = re.fullmatch(r"(\d+)([dhm])", s.strip())
    if not m:
        raise typer.BadParameter(f"Cannot parse duration '{s}'. Use e.g. 7d, 30d, 2h.")
    n, unit = int(m.group(1)), m.group(2)
    if unit == "d":
        return timedelta(days=n)
    if unit == "h":
        return timedelta(hours=n)
    return timedelta(minutes=n)


@app.command()
def replay(
    symbol: str = typer.Option("BTCUSDT", "--symbol", help="Symbol."),
    timeframe: str = typer.Option("15m", "--timeframe", help="Timeframe, e.g. 15m, 1h, 4h."),
    since: str = typer.Option("30d", "--since", help="How far back to replay, e.g. 30d."),
) -> None:
    """Replay the full loop over historical features (the primary POC demo)."""
    from ats.engine.loop import run_replay

    client = get_client()

    async def _run() -> None:
        async with SessionLocal() as session:
            from sqlalchemy import text

            latest = (
                await session.execute(
                    text(
                        "SELECT max(open_time) FROM features WHERE symbol=:s AND timeframe=:tf"
                    ),
                    {"s": symbol, "tf": timeframe},
                )
            ).scalar()
            if latest is None:
                console.print(
                    f"[red]No features for {symbol} {timeframe}. "
                    f"Run 'ats process backfill' first.[/red]"
                )
                raise typer.Exit(1)
            since_dt = latest - _parse_since(since)
            rep = await run_replay(session, client, symbol=symbol, tf=timeframe, since=since_dt)

        console.print(
            f"\n[bold cyan]Replay {symbol} {timeframe}[/bold cyan] since {str(since_dt)[:19]}"
        )
        console.print(
            f"  bars={rep.bars}  plans={rep.plans_created}  detections={rep.detections}  "
            f"opened={rep.opened}  closed={rep.closed}  invalidations={rep.invalidations}  "
            f"confirm_calls={rep.confirm_calls}"
        )
        await _print_trade_summary(symbol)

    asyncio.run(_run())


@app.command()
def tick(
    symbol: str = typer.Option("BTCUSDT", "--symbol", help="Symbol."),
    timeframe: str = typer.Option("15m", "--timeframe", help="Timeframe."),
) -> None:
    """Single evaluation against the latest computed features."""
    from ats.engine.loop import run_tick

    client = get_client()

    async def _run() -> None:
        async with SessionLocal() as session:
            t = await run_tick(session, client, symbol=symbol, tf=timeframe)
        console.print(
            f"[bold cyan]Tick {symbol} {timeframe}[/bold cyan] @ {str(t.now)[:19]}\n"
            f"  detections={t.detections} opened={t.opened} closed={t.closed} "
            f"confirm_calls={t.confirm_calls} invalidated={t.plan_invalidated}"
        )
        for note in t.notes:
            console.print(f"  [dim]- {note}[/dim]")

    asyncio.run(_run())


@app.command("invalidate-check")
def invalidate_check(
    symbol: str = typer.Option("BTCUSDT", "--symbol", help="Symbol."),
    timeframe: str = typer.Option("15m", "--timeframe", help="Timeframe."),
) -> None:
    """Evaluate the active plan's invalidation rules against the latest closed bar."""
    from ats.engine import state
    from ats.engine.invalidation import evaluate_invalidation

    async def _run() -> None:
        async with SessionLocal() as session:
            plan = await state.active_plan(session, symbol)
            if plan is None:
                console.print("[yellow]No active plan.[/yellow]")
                return
            row = await state.latest_feature_row(session, symbol, timeframe)
            if row is None:
                console.print("[red]No features.[/red]")
                return
            setups = await state.plan_setups(session, plan.plan_id)
            worst = None
            rank = {"warning": 0, "soft": 1, "hard": 2}
            for s in setups:
                sev = evaluate_invalidation(
                    s.invalidation_rules, row, None, candle_closed=True
                )
                if sev and (worst is None or rank[sev] > rank[worst]):
                    worst = sev
            color = {"hard": "red", "soft": "yellow", "warning": "blue"}.get(worst or "", "green")
            console.print(
                f"Plan {str(plan.plan_id)[:8]} invalidation @ {str(row['open_time'])[:19]}: "
                f"[{color}]{worst or 'none'}[/{color}]"
            )

    asyncio.run(_run())


@app.command()
def run(
    symbol: str = typer.Option("BTCUSDT", "--symbol", help="Symbol."),
    timeframe: str = typer.Option("15m", "--timeframe", help="Timeframe."),
    interval: int = typer.Option(60, "--interval", help="Seconds between polls."),
    once: bool = typer.Option(False, "--once", help="Run a single iteration and exit."),
    refresh: bool = typer.Option(
        False, "--refresh", help="Backfill candles + recompute features before each tick."
    ),
) -> None:
    """Live poll loop: (optionally refresh data,) then tick, repeating every --interval.

    Not tick-precise — spec 01 is REST-only. Ctrl-C to stop.
    """
    from ats.engine.loop import run_live

    client = get_client()

    async def _refresh() -> None:
        from ats.ingestion.backfill import run as bf_run
        from ats.processing.features import run_once

        async with SessionLocal() as session:
            await bf_run(session, timedelta(days=2), [symbol])
            await run_once(session, [symbol])
            await session.commit()

    refresh_fn = _refresh if refresh else None

    async def _run() -> None:
        console.print(
            f"[bold cyan]Live engine[/bold cyan] {symbol} {timeframe} "
            f"(interval={interval}s, refresh={refresh}). Ctrl-C to stop."
        )
        await run_live(
            SessionLocal, client, symbol=symbol, tf=timeframe,
            interval=interval, once=once, refresh_fn=refresh_fn,
        )

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[dim]stopped.[/dim]")


async def _print_trade_summary(symbol: str) -> None:
    from sqlalchemy import text

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT count(*) FILTER (WHERE status='closed') AS closed, "
                    "count(*) FILTER (WHERE status='open') AS open, "
                    "count(*) FILTER (WHERE pnl_pct > 0) AS wins, "
                    "round(avg(pnl_pct) FILTER (WHERE status='closed')::numeric, 4) AS avg_pnl, "
                    "round(sum(pnl_usd) FILTER (WHERE status='closed')::numeric, 2) AS pnl_usd "
                    "FROM paper_trades WHERE symbol = :s"
                ),
                {"s": symbol},
            )
        ).mappings().first()
    closed = rows["closed"] or 0
    wins = rows["wins"] or 0
    win_rate = (wins / closed * 100) if closed else 0.0
    console.print(
        f"  trades: closed={closed} open={rows['open'] or 0} "
        f"win_rate={win_rate:.0f}% avg_pnl={rows['avg_pnl'] or 0} pnl_usd=${rows['pnl_usd'] or 0}"
    )

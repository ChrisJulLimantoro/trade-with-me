"""CLI: ats engine — run the trading loop (replay / tick / live)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
import typer
from rich.console import Console

from ats.db.session import SessionLocal
from ats.llm.client import get_client

log = structlog.get_logger(__name__)

app = typer.Typer(name="engine", help="Run the rule engine + trade loop.")
console = Console()


def _parse_dt(s: str) -> datetime:
    """Parse an ISO date ('2026-05-01') or datetime into a UTC-aware datetime."""
    try:
        dt = datetime.fromisoformat(s.strip())
    except ValueError as exc:
        raise typer.BadParameter(
            f"Cannot parse date '{s}'. Use ISO format, e.g. 2026-05-01 or "
            "2026-05-01T12:00:00."
        ) from exc
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


async def _ensure_data(
    session: Any,
    symbol: str,
    timeframe: str,
    from_dt: datetime,
    to_dt: datetime,
) -> None:
    """Backfill candles + compute features/regimes for [from_dt, to_dt] if not covered.

    Idempotent: skips when the features table already spans the window for this
    symbol/timeframe; otherwise re-backfills the whole window (upserts on conflict).
    """
    from sqlalchemy import text

    from ats.ingestion.backfill import run as ingest_backfill
    from ats.processing.features import backfill as feature_backfill

    row = (
        await session.execute(
            text(
                "SELECT min(open_time) AS mn, max(open_time) AS mx "
                "FROM features WHERE symbol=:s AND timeframe=:tf"
            ),
            {"s": symbol, "tf": timeframe},
        )
    ).mappings().first()
    mn, mx = (row["mn"], row["mx"]) if row else (None, None)
    if mn is not None and mn <= from_dt and mx >= to_dt:
        console.print(f"[dim]data covered for {symbol} {timeframe}; skipping backfill[/dim]")
        return

    console.print(
        f"[yellow]backfilling {symbol} {timeframe} "
        f"{str(from_dt)[:19]} → {str(to_dt)[:19]}…[/yellow]"
    )
    # Klines need a 90-day warm-up before from_dt so feature indicators (EMA200/RSI/…)
    # can be computed for the first bars of the window.
    await ingest_backfill(
        session, timedelta(0), [symbol], start=from_dt - timedelta(days=90), end=to_dt
    )
    await feature_backfill(session, from_dt, [symbol])

    # Regimes are BTC-1h derived (mirrors `ats process backfill`).
    if symbol == "BTCUSDT":
        from ats.processing.features import _fetch_candles
        from ats.processing.regime import compute_regime, upsert_regime

        btc_df = await _fetch_candles(session, "BTCUSDT", "1h", since=from_dt)
        if not btc_df.empty and len(btc_df) >= 31:
            prev_vol: str | None = None
            for i in range(30, len(btc_df)):
                regime = compute_regime(btc_df.iloc[: i + 1], prev_volatility=prev_vol)
                prev_vol = regime["volatility"]
                await upsert_regime(session, regime)

    await session.commit()


@app.command()
def replay(
    symbol: str = typer.Option("BTCUSDT", "--symbol", help="Symbol."),
    timeframe: str = typer.Option("15m", "--timeframe", help="Timeframe, e.g. 15m, 1h, 4h."),
    from_: str = typer.Option(..., "--from", help="Start date, ISO e.g. 2026-05-01."),
    to: str = typer.Option(None, "--to", help="End date, ISO. Defaults to now."),
    profile: str = typer.Option(
        "baseline",
        "--profile",
        help="Strategy profile (risk/leverage/hold preset): baseline | scalper.",
    ),
    log_file: str = typer.Option(
        None,
        "--log-file",
        help="Path for the readable plan/confirm/trade trace "
        "(default: logs/replay_<symbol>_<tf>.log).",
    ),
    no_log: bool = typer.Option(
        False, "--no-log", help="Disable the trace file (console summary only)."
    ),
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Delete existing paper_trades for this symbol before replaying "
        "(clears stale open positions and prior-run P&L).",
    ),
    run_label: str = typer.Option(
        None,
        "--run-label",
        help="Human tag for this run (e.g. baseline, mfe-on). Trades are tagged with a "
        "unique run_id + this label + a config hash for the A/B harness "
        "(see 'ats trades runs' / 'ats trades compare').",
    ),
) -> None:
    """Replay the full loop over historical features (the primary POC demo)."""
    import hashlib
    import json
    from pathlib import Path
    from uuid import uuid4

    from sqlalchemy import text

    from ats import trace
    from ats.config import settings
    from ats.engine.loop import run_replay
    from ats.execution.executor import RunTag
    from ats.strategy_profiles import apply_profile

    from_dt = _parse_dt(from_)
    to_dt = _parse_dt(to) if to else datetime.now(UTC)
    if from_dt >= to_dt:
        raise typer.BadParameter(f"--from ({from_}) must be before --to ({to or 'now'}).")

    try:
        applied = apply_profile(profile)
    except (KeyError, AttributeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    # Build the run tag AFTER the profile is applied: hash the behavior-affecting knobs so
    # two runs with identical config share a config_hash, and a knob change is visible.
    behavior_knobs = {
        "profile": profile,
        "symbol": symbol,
        "timeframe": timeframe,
        "max_leverage": settings.risk.max_leverage,
        "risk_per_trade_pct": settings.risk.risk_per_trade_pct,
        "min_rr": settings.risk.min_rr,
        "min_stop_atr_mult": settings.risk.min_stop_atr_mult,
        "max_hold_bars": settings.exits.max_hold_bars,
        "scale_out_frac": settings.exits.scale_out_frac,
        "trail_atr_mult": settings.exits.trail_atr_mult,
        "early_stop_mode": settings.exits.early_stop_mode,
        "early_trail_arm_atr": settings.exits.early_trail_arm_atr,
        "early_trail_atr_mult": settings.exits.early_trail_atr_mult,
        "breakeven_arm_atr": settings.exits.breakeven_arm_atr,
        "regime_filter": settings.plan.regime_filter,
        "fee_bps": settings.risk.fee_bps,
        "slippage_bps": settings.risk.slippage_bps,
    }
    config_hash = hashlib.sha256(
        json.dumps(behavior_knobs, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]
    run = RunTag(run_id=uuid4().hex, run_label=run_label, config_hash=config_hash)
    console.print(
        f"[bold green]run[/bold green] id={run.run_id[:8]} "
        f"label={run_label or '-'} config={config_hash}"
    )

    if applied:
        knobs = "  ".join(f"{k}={v}" for k, v in applied.items())
        console.print(f"[bold magenta]profile[/bold magenta] {profile}: {knobs}")
    else:
        console.print(
            f"[dim]profile {profile}: "
            f"equity=${settings.risk.paper_equity_usd:g} max_lev={settings.risk.max_leverage:g}x "
            f"min_rr={settings.risk.min_rr:g} min_stop_atr={settings.risk.min_stop_atr_mult:g}x "
            f"max_hold={settings.exits.max_hold_bars}b[/dim]"
        )

    if not no_log:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = log_file or f"logs/replay_{symbol}_{timeframe}_{stamp}.log"
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        trace.init(path)
        trace.header(
            f"REPLAY  {symbol} {timeframe}  "
            f"{str(from_dt)[:19]} → {str(to_dt)[:19]}  profile={profile}  "
            f"run={run.run_id[:8]} label={run_label or '-'} config={config_hash}"
        )
        console.print(f"[dim]trace -> {path}  (tail -f to watch live)[/dim]")

    client = get_client()

    async def _run() -> None:
        async with SessionLocal() as session:
            await _ensure_data(session, symbol, timeframe, from_dt, to_dt)
            if reset:
                deleted = (
                    await session.execute(
                        text("DELETE FROM paper_trades WHERE symbol=:s"), {"s": symbol}
                    )
                ).rowcount
                await session.commit()
                console.print(f"[dim]reset: deleted {deleted} paper_trades for {symbol}[/dim]")
            rep = await run_replay(
                session, client, symbol=symbol, tf=timeframe, since=from_dt, until=to_dt,
                run=run,
            )

        # Mirror the end-of-replay summary + trade table into the trace file so the
        # log captures the same PnL view that's shown on the console.
        from io import StringIO

        trace_buf = StringIO()
        trace_console = (
            Console(file=trace_buf, width=140, no_color=True) if trace.is_active() else None
        )

        def emit(renderable: Any) -> None:
            console.print(renderable)
            if trace_console is not None:
                trace_console.print(renderable)

        emit(
            f"\n[bold cyan]Replay {symbol} {timeframe}[/bold cyan] "
            f"{str(from_dt)[:19]} → {str(to_dt)[:19]}"
        )
        # Fraction of plans-with-setups that produced an opened trade.
        plan_confirm_rate = (
            f"{rep.opened / rep.plans_with_setups * 100:.0f}%"
            if rep.plans_with_setups
            else "n/a"
        )
        emit(
            f"  bars={rep.bars}  plans={rep.plans_created}"
            f"  plans_with_setups={rep.plans_with_setups}"
            f"  detections={rep.detections}  "
            f"opened={rep.opened}  closed={rep.closed}  invalidations={rep.invalidations}  "
            f"plan_confirm_rate={plan_confirm_rate}  "
            f"confirm_calls={rep.confirm_calls}  observe_calls={rep.observe_calls}  "
            f"risk_rejected={rep.risk_rejected}"
        )
        if rep.trade_outcomes:
            atr_str = (
                f"  avg_stop_atr={rep.avg_stop_dist_atr:.2f}x"
                if rep.avg_stop_dist_atr is not None
                else ""
            )
            vol_str = (
                f"  volatility={rep.volatility_pct:.2f}%"
                if rep.volatility_pct is not None
                else ""
            )
            sharpe_str = (
                f"  sharpe={rep.sharpe_ratio:.2f}"
                if rep.sharpe_ratio is not None
                else ""
            )
            sortino_str = (
                f"  sortino={rep.sortino_ratio:.2f}"
                if rep.sortino_ratio is not None
                else ""
            )
            emit(
                f"  [bold]trade metrics[/bold]  "
                f"win_rate={rep.win_rate * 100:.0f}%  "
                f"avg_margin_pnl={rep.avg_pnl_pct * 100:+.3f}%  "
                f"margin_expectancy={rep.expectancy_pct * 100:+.3f}%"
                + atr_str + vol_str + sharpe_str + sortino_str
            )
            log.info(
                "replay_summary",
                symbol=symbol,
                timeframe=timeframe,
                bars=rep.bars,
                closed=rep.closed,
                win_rate=round(rep.win_rate, 4),
                avg_margin_pnl_pct=round(rep.avg_pnl_pct * 100, 4),
                expectancy_pct=round(rep.expectancy_pct * 100, 4),
                volatility_pct=round(rep.volatility_pct, 4) if rep.volatility_pct is not None else None,
                sharpe_ratio=round(rep.sharpe_ratio, 4) if rep.sharpe_ratio is not None else None,
                sortino_ratio=round(rep.sortino_ratio, 4) if rep.sortino_ratio is not None else None,
                avg_stop_dist_atr=round(rep.avg_stop_dist_atr, 4) if rep.avg_stop_dist_atr is not None else None,
            )
            from rich.table import Table

            t = Table(title="Closed trades this replay")
            t.add_column("dir")
            t.add_column("entry", justify="right")
            t.add_column("stop", justify="right")
            t.add_column("exit", justify="right")
            t.add_column("reason")
            t.add_column("margin$", justify="right")
            t.add_column("notional$", justify="right")
            t.add_column("lev", justify="right")
            t.add_column("margin pnl%", justify="right")
            t.add_column("pnl$", justify="right")
            t.add_column("stop_dist/ATR", justify="right")
            for oc in rep.trade_outcomes:
                pnl_color = "green" if oc.pnl_pct > 0 else "red"
                atr_ratio = (
                    f"{oc.stop_dist_pts / oc.atr_at_entry:.2f}x"
                    if oc.atr_at_entry and oc.atr_at_entry > 0
                    else "-"
                )
                t.add_row(
                    oc.direction,
                    f"{oc.entry_price:.1f}",
                    f"{oc.stop_loss:.1f}",
                    f"{oc.exit_price:.1f}",
                    oc.exit_reason,
                    f"{oc.margin_usd:,.2f}",
                    f"{oc.notional_usd:,.2f}",
                    f"{oc.leverage:.2f}x" if oc.leverage is not None else "-",
                    f"[{pnl_color}]{oc.pnl_pct * 100:+.3f}%[/{pnl_color}]",
                    f"[{pnl_color}]{oc.pnl_usd:+.2f}[/{pnl_color}]",
                    atr_ratio,
                )
            emit(t)
        for note in rep.notes:
            emit(f"  [dim]- {note}[/dim]")
        await _print_trade_summary(
            symbol, from_dt, emit=emit, plans_created=rep.plans_created, run_id=run.run_id
        )

        if trace_console is not None:
            trace.block(trace_buf.getvalue())

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


async def _print_trade_summary(
    symbol: str,
    since: datetime | None = None,
    emit: Any = None,
    plans_created: int | None = None,
    run_id: str | None = None,
) -> None:
    from sqlalchemy import text

    if emit is None:
        emit = console.print

    params: dict[str, Any] = {"s": symbol}
    where = "symbol = :s"
    if since is not None:
        where += " AND entry_time >= :since"
        params["since"] = since
    if run_id is not None:
        where += " AND run_id = :run_id"
        params["run_id"] = run_id
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT count(*) FILTER (WHERE status='closed') AS closed, "
                    "count(*) FILTER (WHERE status='open') AS open, "
                    "count(*) FILTER (WHERE pnl_pct > 0) AS wins, "
                    "round(avg(pnl_pct) FILTER (WHERE status='closed')::numeric, 4) AS avg_pnl, "
                    "round(sum(pnl_usd) FILTER (WHERE status='closed')::numeric, 2) AS pnl_usd "
                    f"FROM paper_trades WHERE {where}"
                ),
                params,
            )
        ).mappings().first()
    closed = rows["closed"] or 0
    opened = closed + (rows["open"] or 0)
    wins = rows["wins"] or 0
    win_rate = (wins / closed * 100) if closed else 0.0
    scope = f" (since {str(since)[:19]})" if since is not None else ""
    # plan_confirm_rate = trades opened / plans made. Only shown when the caller
    # supplies the plan count (it lives on the replay report, not in paper_trades).
    pcr = ""
    if plans_created:
        pcr = f" plan_confirm_rate={opened / plans_created * 100:.0f}%"
    elif plans_created == 0:
        pcr = " plan_confirm_rate=n/a"
    emit(
        f"  trades{scope}: closed={closed} open={rows['open'] or 0} "
        f"win_rate={win_rate:.0f}% avg_margin_pnl={rows['avg_pnl'] or 0} "
        f"pnl_usd=${rows['pnl_usd'] or 0}{pcr}"
    )

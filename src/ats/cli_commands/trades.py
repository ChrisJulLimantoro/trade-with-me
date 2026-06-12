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


def _build_filter(
    *,
    since: str | None = None,
    symbol: str = "",
    run_id: str = "",
    run_label: str = "",
) -> tuple[str, dict[str, object]]:
    """Build a shared WHERE clause (status='closed' + optional filters) and params."""
    clauses = ["status='closed'"]
    params: dict[str, object] = {}
    if since:
        clauses.append("entry_time >= :since")
        params["since"] = datetime.now(UTC) - _parse_since(since)
    if symbol:
        clauses.append("symbol = :symbol")
        params["symbol"] = symbol
    if run_id:
        clauses.append("run_id = :run_id")
        params["run_id"] = run_id
    if run_label:
        clauses.append("run_label = :run_label")
        params["run_label"] = run_label
    return " AND ".join(clauses), params


async def _stats_block(session, where: str, params: dict[str, object], *, by_regime: bool):
    """Aggregate closed-trade stats for a WHERE clause: (agg, by_reason, by_regime)."""
    agg = (
        await session.execute(
            text(
                "SELECT count(*) AS n, "
                "count(*) FILTER (WHERE pnl_pct > 0) AS wins, "
                "round(avg(pnl_pct)::numeric, 4) AS avg_pnl, "
                "round(avg(pnl_pct) FILTER (WHERE pnl_pct > 0)::numeric, 4) AS avg_win, "
                "round(avg(pnl_pct) FILTER (WHERE pnl_pct <= 0)::numeric, 4) AS avg_loss, "
                "round(sum(pnl_usd)::numeric, 2) AS pnl_usd "
                f"FROM paper_trades WHERE {where}"
            ),
            params,
        )
    ).mappings().first()
    by_reason = (
        await session.execute(
            text(
                "SELECT exit_reason, count(*) AS n, "
                "round(avg(pnl_pct)::numeric,4) AS avg_pnl "
                f"FROM paper_trades WHERE {where} "
                "GROUP BY exit_reason ORDER BY n DESC"
            ),
            params,
        )
    ).mappings().all()
    by_regime_rows = []
    if by_regime:
        by_regime_rows = (
            await session.execute(
                text(
                    "SELECT metadata->>'regime_cell' AS regime, count(*) AS n, "
                    "count(*) FILTER (WHERE pnl_pct > 0) AS wins, "
                    "round(avg(pnl_pct)::numeric,4) AS avg_pnl, "
                    "round(sum(pnl_usd)::numeric,2) AS pnl_usd "
                    f"FROM paper_trades WHERE {where} "
                    "GROUP BY metadata->>'regime_cell' ORDER BY n DESC"
                ),
                params,
            )
        ).mappings().all()
    return agg, by_reason, by_regime_rows


def _expectancy(agg) -> tuple[int, float, float]:
    """Return (n, win_rate, margin_expectancy) from an aggregate row."""
    n = agg["n"] or 0
    if n == 0:
        return 0, 0.0, 0.0
    win_rate = (agg["wins"] or 0) / n
    avg_win = float(agg["avg_win"] or 0)
    avg_loss = float(agg["avg_loss"] or 0)
    return n, win_rate, avg_win * win_rate + avg_loss * (1 - win_rate)


def _render_stats(title: str, agg, by_reason, by_regime_rows) -> None:
    n, win_rate, expectancy = _expectancy(agg)
    if n == 0:
        console.print(f"[yellow]{title}: no closed trades.[/yellow]")
        return
    console.print(
        f"\n[bold cyan]{title}[/bold cyan]\n"
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
    if by_regime_rows:
        rt = Table(title="By regime cell")
        rt.add_column("regime")
        rt.add_column("n", justify="right")
        rt.add_column("win%", justify="right")
        rt.add_column("avg margin pnl%", justify="right")
        rt.add_column("pnl$", justify="right")
        for r in by_regime_rows:
            wr = (r["wins"] or 0) / r["n"] * 100 if r["n"] else 0.0
            rt.add_row(
                r["regime"] or "-", str(r["n"]), f"{wr:.0f}",
                f"{float(r['avg_pnl'] or 0) * 100:+.2f}", f"{r['pnl_usd']}",
            )
        console.print(rt)


@app.command()
def stats(
    since: str = typer.Option("90d", "--since", help="Window, e.g. 30d, 90d."),
    symbol: str = typer.Option("", "--symbol", help="Filter by symbol."),
    run_id: str = typer.Option("", "--run-id", help="Filter by replay run_id."),
    run_label: str = typer.Option("", "--run-label", help="Filter by replay run_label."),
    by_regime: bool = typer.Option(
        False, "--by-regime", help="Break the stats down by regime_cell."
    ),
) -> None:
    """Aggregate stats over closed trades."""
    where, params = _build_filter(
        since=since, symbol=symbol, run_id=run_id, run_label=run_label
    )

    async def _run() -> tuple:
        async with SessionLocal() as session:
            return await _stats_block(session, where, params, by_regime=by_regime)

    agg, by_reason, by_regime_rows = asyncio.run(_run())
    tags = [t for t in (symbol, run_label or run_id) if t]
    title = "Trade stats (last " + since + (", " + ", ".join(tags) if tags else "") + ")"
    _render_stats(title, agg, by_reason, by_regime_rows)


@app.command()
def runs(
    symbol: str = typer.Option("", "--symbol", help="Filter by symbol."),
    limit: int = typer.Option(30, "--limit", help="Max runs."),
) -> None:
    """List replay runs (one row per run_id) with headline stats — a run index."""
    where = "run_id IS NOT NULL"
    params: dict[str, object] = {"lim": limit}
    if symbol:
        where += " AND symbol = :symbol"
        params["symbol"] = symbol

    async def _run() -> list:
        async with SessionLocal() as session:
            res = await session.execute(
                text(
                    "SELECT run_id, max(run_label) AS run_label, max(config_hash) AS config_hash, "
                    "count(*) AS n, count(*) FILTER (WHERE status='closed') AS closed, "
                    "count(*) FILTER (WHERE pnl_pct > 0) AS wins, "
                    "round(sum(pnl_usd)::numeric,2) AS pnl_usd, "
                    "min(entry_time) AS first_entry, max(entry_time) AS last_entry "
                    f"FROM paper_trades WHERE {where} "
                    "GROUP BY run_id ORDER BY max(entry_time) DESC LIMIT :lim"
                ),
                params,
            )
            return list(res.mappings().all())

    rows = asyncio.run(_run())
    if not rows:
        console.print("[yellow]No tagged runs. Run 'ats engine replay --run-label ...'.[/yellow]")
        return
    t = Table(title="Replay runs")
    t.add_column("run_id", style="cyan", no_wrap=True)
    t.add_column("label")
    t.add_column("config", no_wrap=True)
    t.add_column("closed", justify="right")
    t.add_column("win%", justify="right")
    t.add_column("pnl$", justify="right")
    t.add_column("window")
    for r in rows:
        closed = r["closed"] or 0
        wr = (r["wins"] or 0) / closed * 100 if closed else 0.0
        window = f"{str(r['first_entry'])[:10]}→{str(r['last_entry'])[:10]}"
        t.add_row(
            str(r["run_id"])[:8], r["run_label"] or "-", r["config_hash"] or "-",
            str(closed), f"{wr:.0f}", f"{r['pnl_usd']}", window,
        )
    console.print(t)


@app.command()
def compare(
    run_a: str = typer.Option(..., "--run-a", help="Run id or label (side A)."),
    run_b: str = typer.Option(..., "--run-b", help="Run id or label (side B)."),
    by_regime: bool = typer.Option(
        True, "--by-regime/--no-by-regime", help="Break each side down by regime_cell."
    ),
) -> None:
    """Compare two runs side by side (by run id or run label)."""

    def _filter(token: str) -> tuple[str, dict[str, object]]:
        # Match either run_id or run_label so the user can pass whichever they remember.
        return "status='closed' AND (run_id = :tok OR run_label = :tok)", {"tok": token}

    async def _run() -> tuple:
        async with SessionLocal() as session:
            wa, pa = _filter(run_a)
            wb, pb = _filter(run_b)
            a = await _stats_block(session, wa, pa, by_regime=by_regime)
            b = await _stats_block(session, wb, pb, by_regime=by_regime)
            return a, b

    (agg_a, reason_a, regime_a), (agg_b, reason_b, regime_b) = asyncio.run(_run())
    _render_stats(f"Run A: {run_a}", agg_a, reason_a, regime_a)
    _render_stats(f"Run B: {run_b}", agg_b, reason_b, regime_b)

    na, wra, expa = _expectancy(agg_a)
    nb, wrb, expb = _expectancy(agg_b)
    if na and nb:
        d_wr = (wrb - wra) * 100
        d_exp = (expb - expa) * 100
        d_pnl = float(agg_b["pnl_usd"] or 0) - float(agg_a["pnl_usd"] or 0)
        console.print(
            f"\n[bold]Δ (B − A)[/bold]  win_rate={d_wr:+.0f}pts  "
            f"margin_expectancy={d_exp:+.3f}pts  total_pnl=${d_pnl:+.2f}"
        )

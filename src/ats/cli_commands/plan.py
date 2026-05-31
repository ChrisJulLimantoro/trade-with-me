"""CLI: ats plan — create and inspect LLM trading plans."""

from __future__ import annotations

import asyncio
import uuid

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select, text

from ats.db.models import PaperTrade, Plan, Setup
from ats.db.session import SessionLocal
from ats.engine.timeframes import timeframe_to_timedelta  # noqa: F401  (validates tf early)
from ats.llm.client import get_client

app = typer.Typer(name="plan", help="LLM trading plans (create_plan).")
console = Console()


@app.command()
def create(
    symbol: str = typer.Option("BTCUSDT", "--symbol", help="Symbol to plan for."),
    timeframe: str = typer.Option("15m", "--timeframe", help="Timeframe, e.g. 15m, 1h, 4h."),
    as_of: str = typer.Option("", "--as-of", help="ISO timestamp as 'now' (default: latest)."),
) -> None:
    """Run one create_plan cycle and persist the plan + setups."""
    from datetime import datetime

    from ats.planning.create_plan import create_plan

    as_of_dt = datetime.fromisoformat(as_of) if as_of else None
    client = get_client()

    async def _run() -> None:
        async with SessionLocal() as session:
            res = await create_plan(session, client, symbol=symbol, tf=timeframe, as_of=as_of_dt)
            await session.commit()
            if res.plan is None:
                console.print("[red]Plan creation failed (LLM parse error).[/red]")
                raise typer.Exit(1)
            p = res.plan
            console.print(
                f"\n[bold cyan]Plan[/bold cyan] {p.plan_id}  "
                f"[bold]{p.market_bias}[/bold]  regime={p.regime_cell}"
            )
            console.print(
                f"  as_of {str(p.as_of)[:19]}  expires {str(p.expires_at)[:19]}  "
                f"setups={len(res.setups)}"
            )
            if p.rationale:
                console.print(f"  [dim]{p.rationale}[/dim]")
            _print_setups(res.setups)
            llm = res.llm
            console.print(
                f"\n[dim]llm: model={llm.model} mock={llm.mock} parse_ok={llm.parse_ok} "
                f"tokens={llm.input_tokens}/{llm.output_tokens} cost=${llm.cost_usd}[/dim]"
            )

    asyncio.run(_run())


@app.command()
def show(
    symbol: str = typer.Option("", "--symbol", help="Filter by symbol."),
    limit: int = typer.Option(10, "--limit", help="Max plans to show."),
) -> None:
    """List recent plans."""

    async def _run() -> list[Plan]:
        async with SessionLocal() as session:
            stmt = select(Plan).order_by(Plan.created_at.desc()).limit(limit)
            if symbol:
                stmt = stmt.where(Plan.symbol == symbol)
            return list((await session.execute(stmt)).scalars().all())

    plans = asyncio.run(_run())
    if not plans:
        console.print("[yellow]No plans. Run 'ats plan create' first.[/yellow]")
        return

    t = Table(title="Plans")
    t.add_column("plan_id", style="cyan", no_wrap=True)
    t.add_column("symbol")
    t.add_column("bias", style="bold")
    t.add_column("status")
    t.add_column("regime")
    t.add_column("created", style="dim")
    t.add_column("expires", style="dim")
    bias_color = {"bullish": "green", "bearish": "red", "neutral": "yellow"}
    status_color = {
        "active": "green", "expired": "yellow", "invalidated": "red", "superseded": "dim",
    }
    for p in plans:
        bc = bias_color.get(p.market_bias, "white")
        sc = status_color.get(p.status, "white")
        t.add_row(
            str(p.plan_id)[:8],
            p.symbol,
            f"[{bc}]{p.market_bias}[/{bc}]",
            f"[{sc}]{p.status}[/{sc}]",
            p.regime_cell or "-",
            str(p.created_at)[:19],
            str(p.expires_at)[:19],
        )
    console.print(t)


@app.command()
def setups(
    plan_id: str = typer.Option(..., "--plan-id", help="Plan UUID (full or 8-char prefix)."),
) -> None:
    """Show the setups for a plan."""

    async def _run() -> list[Setup]:
        async with SessionLocal() as session:
            # accept a short prefix for convenience
            row = (
                await session.execute(
                    text("SELECT plan_id FROM plans WHERE plan_id::text LIKE :pfx LIMIT 1"),
                    {"pfx": plan_id + "%"},
                )
            ).scalar()
            if row is None:
                return []
            stmt = select(Setup).where(Setup.plan_id == uuid.UUID(str(row)))
            return list((await session.execute(stmt)).scalars().all())

    rows = asyncio.run(_run())
    if not rows:
        console.print("[yellow]No setups for that plan.[/yellow]")
        return
    _print_setups(rows)


@app.command()
def detail(
    plan_id: str = typer.Argument(..., help="Plan UUID (full or 8-char prefix)."),
) -> None:
    """Show full detail for a single plan: header, rationale, setups + rules, and its trades."""

    async def _run() -> tuple[Plan | None, list[Setup], list[PaperTrade]]:
        async with SessionLocal() as session:
            # accept a short prefix for convenience
            resolved = (
                await session.execute(
                    text("SELECT plan_id FROM plans WHERE plan_id::text LIKE :pfx LIMIT 1"),
                    {"pfx": plan_id + "%"},
                )
            ).scalar()
            if resolved is None:
                return None, [], []
            pid = uuid.UUID(str(resolved))
            plan = (
                await session.execute(select(Plan).where(Plan.plan_id == pid))
            ).scalar_one()
            setups = list(
                (await session.execute(select(Setup).where(Setup.plan_id == pid))).scalars().all()
            )
            trades = list(
                (
                    await session.execute(
                        select(PaperTrade)
                        .where(PaperTrade.plan_id == pid)
                        .order_by(PaperTrade.entry_time.desc())
                    )
                )
                .scalars()
                .all()
            )
            return plan, setups, trades

    plan, setups, trades = asyncio.run(_run())
    if plan is None:
        console.print(f"[red]No plan matching '{plan_id}'.[/red]")
        raise typer.Exit(1)

    bias_color = {"bullish": "green", "bearish": "red", "neutral": "yellow"}
    status_color = {
        "active": "green", "expired": "yellow", "invalidated": "red", "superseded": "dim",
    }
    bc = bias_color.get(plan.market_bias, "white")
    sc = status_color.get(plan.status, "white")
    console.print(
        f"\n[bold cyan]Plan[/bold cyan] {plan.plan_id}  "
        f"[{bc}]{plan.market_bias}[/{bc}]  [{sc}]{plan.status}[/{sc}]"
    )
    console.print(
        f"  symbol={plan.symbol}  regime={plan.regime_cell or '-'}\n"
        f"  as_of {str(plan.as_of)[:19]}  created {str(plan.created_at)[:19]}  "
        f"expires {str(plan.expires_at)[:19]}"
    )
    if plan.rationale:
        console.print(f"  [dim]{plan.rationale}[/dim]")

    if not setups:
        console.print("[yellow]  (no setups)[/yellow]")
    else:
        _print_setups(setups)
        for s in setups:
            _print_rules(s)

    if trades:
        _print_trades(trades)


def _fmt_rule(r: dict) -> str:
    base = f"{r.get('left')} {r.get('operator')} {r.get('right')}"
    extras = []
    if r.get("weight") is not None:
        extras.append(f"w={r['weight']}")
    if r.get("severity"):
        extras.append(str(r["severity"]))
    if r.get("on_close") is not None:
        extras.append("on_close" if r["on_close"] else "intrabar")
    return base + (f"  ({', '.join(extras)})" if extras else "")


def _print_rules(s: Setup) -> None:
    console.print(f"\n[bold]Setup {str(s.setup_id)[:8]}[/bold] ({s.direction}) rules:")
    groups = [
        ("hard", s.hard_rules),
        ("soft", s.soft_rules),
        ("invalidation", s.invalidation_rules),
    ]
    for label, rules in groups:
        if not rules:
            console.print(f"  [dim]{label}: (none)[/dim]")
            continue
        console.print(f"  {label}:")
        for r in rules:
            console.print(f"    - {_fmt_rule(r)}")


def _print_trades(rows: list[PaperTrade]) -> None:
    t = Table(title="Trades from this plan")
    t.add_column("trade_id", style="cyan", no_wrap=True)
    t.add_column("dir", style="bold")
    t.add_column("status")
    t.add_column("entry", justify="right")
    t.add_column("exit", justify="right")
    t.add_column("reason")
    t.add_column("pnl%", justify="right")
    dir_color = {"long": "green", "short": "red"}
    for r in rows:
        dc = dir_color.get(r.direction, "white")
        pnl = r.pnl_pct
        pnl_str = f"{float(pnl) * 100:+.2f}" if pnl is not None else "-"
        pnl_color = "white" if pnl is None else "green" if float(pnl) > 0 else "red"
        t.add_row(
            str(r.trade_id)[:8],
            f"[{dc}]{r.direction}[/{dc}]",
            r.status,
            f"{float(r.entry_price):.2f}",
            f"{float(r.exit_price):.2f}" if r.exit_price is not None else "-",
            r.exit_reason or "-",
            f"[{pnl_color}]{pnl_str}[/{pnl_color}]",
        )
    console.print(t)


def _print_setups(rows: list[Setup]) -> None:
    t = Table(title="Setups")
    t.add_column("setup_id", style="cyan", no_wrap=True)
    t.add_column("dir", style="bold")
    t.add_column("status")
    t.add_column("entry_zone", justify="right")
    t.add_column("stop", justify="right")
    t.add_column("targets", justify="right")
    t.add_column("size%", justify="right")
    t.add_column("rules", style="dim")
    dir_color = {"long": "green", "short": "red"}
    for s in rows:
        dc = dir_color.get(s.direction, "white")
        tp = ", ".join(f"{float(x):.2f}" for x in s.take_profit)
        rules = f"{len(s.hard_rules)}H/{len(s.soft_rules)}S/{len(s.invalidation_rules)}I"
        t.add_row(
            str(s.setup_id)[:8],
            f"[{dc}]{s.direction}[/{dc}]",
            s.status,
            f"{float(s.entry_zone_low):.2f}-{float(s.entry_zone_high):.2f}",
            f"{float(s.stop_loss):.2f}",
            tp,
            f"{float(s.size_pct) * 100:.1f}",
            rules,
        )
    console.print(t)

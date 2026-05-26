from __future__ import annotations

import asyncio
import re
from datetime import timedelta

import typer

from ats.db.session import SessionLocal
from ats.ingestion.universe import load_m1_universe, load_xvenue_mapping

app = typer.Typer(name="ingest", help="Data ingestion commands.")

_SYMBOLS_HELP = "Comma-separated symbols. Defaults to M1 universe."


def _parse_since(s: str) -> timedelta:
    """Parse strings like '7d', '120d', '2h', '30m' into timedelta."""
    m = re.fullmatch(r"(\d+)([dhm])", s.strip())
    if not m:
        raise typer.BadParameter(f"Cannot parse duration '{s}'. Use e.g. 7d, 120d, 2h, 30m.")
    n, unit = int(m.group(1)), m.group(2)
    if unit == "d":
        return timedelta(days=n)
    if unit == "h":
        return timedelta(hours=n)
    return timedelta(minutes=n)


@app.command()
def backfill(
    since: str = typer.Option("120d", "--since", help="How far back to backfill, e.g. 7d, 120d."),
    symbols: str = typer.Option("", "--symbols", help=_SYMBOLS_HELP),
) -> None:
    """REST kline + funding + OI + premiumIndex backfill (Tier 1)."""
    from ats.ingestion.backfill import run as bf_run

    sym_list = [s.strip() for s in symbols.split(",") if s.strip()] or load_m1_universe()
    delta = _parse_since(since)

    async def _run() -> None:
        async with SessionLocal() as session:
            await bf_run(session, delta, sym_list)

    asyncio.run(_run())
    typer.echo("Backfill complete.")


@app.command("xvenue-funding")
def xvenue_funding(
    symbols: str = typer.Option("", "--symbols", help=_SYMBOLS_HELP),
) -> None:
    """One-shot cross-venue funding pull from Bybit + OKX + Hyperliquid (Tier 1)."""
    from ats.ingestion.xvenue_funding import pull_all

    sym_list = [s.strip() for s in symbols.split(",") if s.strip()] or load_m1_universe()
    mapping = load_xvenue_mapping()

    async def _run() -> None:
        async with SessionLocal() as session:
            await pull_all(session, sym_list, mapping)

    asyncio.run(_run())
    typer.echo("Cross-venue funding pull complete.")


@app.command("media-pull")
def media_pull() -> None:
    """One-shot RSS + X (optional, disabled if no token) pull (Tier 1)."""
    from ats.ingestion.rss_poller import pull_all as rss_pull
    from ats.ingestion.x_poller import pull_all as x_pull

    async def _run() -> None:
        async with SessionLocal() as session:
            count = await rss_pull(session)
            typer.echo(f"RSS: {count} items ingested.")
            await x_pull(session)

    asyncio.run(_run())


@app.command()
def start() -> None:
    """[Tier 3 / M4] Start WebSocket consumer + continuous pollers. Not available in M1."""
    typer.echo("Tier 3 daemon is M4 — see specs/10-live-operations.md", err=True)
    raise typer.Exit(2)

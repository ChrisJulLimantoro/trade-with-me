from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from ats.db.models import (
    Basis,
    Candle,
    FundingRate,
    FundingRateXVenue,
    MediaItem,
    OpenInterest,
)
from ats.db.session import SessionLocal

app = typer.Typer(name="data", help="Data inspection commands.")
console = Console()


@app.command()
def status() -> None:
    """Show heartbeat statuses + row counts. Exits 1 only if a data class is missing.

    Tier-3-only classes (e.g. mark_price) show as n/a in Tier 1 and never fail.
    """
    from ats.ingestion.freshness import check_freshness, get_data_class_counts

    async def _run() -> tuple[list[dict[str, Any]], dict[str, int]]:
        async with SessionLocal() as session:
            rows = await check_freshness(session)
            counts = await get_data_class_counts(session)
            return rows, counts

    rows, counts = asyncio.run(_run())

    table = Table(title="ATS Data Status")
    table.add_column("data_class", style="cyan")
    table.add_column("status")
    table.add_column("last_seen")
    table.add_column("rows", justify="right")
    table.add_column("detail", style="dim")

    has_missing = False
    status_colors = {
        "ok": "green", "stale": "yellow", "missing": "red",
        "disabled": "dim", "n/a": "dim",
    }

    for r in rows:
        st = r["status"]
        if st == "missing":
            has_missing = True
        color = status_colors.get(st, "white")
        last = str(r["last_seen"])[:19] if r["last_seen"] else "—"
        rows_n = counts.get(r["data_class"], 0)
        table.add_row(
            r["data_class"],
            f"[{color}]{st}[/{color}]",
            last,
            str(rows_n),
            r.get("detail") or "",
        )

    console.print(table)

    if has_missing:
        raise typer.Exit(1)


def _parse_since(since: str) -> datetime:
    """Convert e.g. '7d', '2h', '30m' to a UTC datetime."""
    units = {"d": 86400, "h": 3600, "m": 60}
    unit = since[-1]
    if unit not in units or not since[:-1].isdigit():
        raise typer.BadParameter(f"Expected format like '7d', '4h', '30m', got '{since}'")
    delta = timedelta(seconds=int(since[:-1]) * units[unit])
    return datetime.now(UTC) - delta


_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list[float]) -> str:
    """Map a numeric series to Unicode block characters (oldest→newest, left→right)."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = hi - lo
    if span == 0:
        return _SPARK_BLOCKS[0] * len(values)
    out = []
    for v in values:
        idx = round((v - lo) / span * (len(_SPARK_BLOCKS) - 1))
        out.append(_SPARK_BLOCKS[idx])
    return "".join(out)


def _print_series(label: str, series: list[float], fmt: str = ".2f") -> None:
    """Print a labeled, trend-colored sparkline with first/last/min/max stats."""
    if not series:
        console.print(f"[yellow]No data for {label}.[/yellow]")
        return
    first, last = series[0], series[-1]
    rising = last >= first
    color = "green" if rising else "red"
    arrow = "▲" if rising else "▼"
    spark = _sparkline(series)
    pct = ((last - first) / abs(first) * 100) if first != 0 else 0.0
    console.print(f"[bold]{label}[/bold]  ({len(series)} pts)")
    console.print(f"  [{color}]{spark}[/{color}]")
    console.print(
        f"  first {first:{fmt}}  last [{color}]{last:{fmt}}[/{color}]  "
        f"min {min(series):{fmt}}  max {max(series):{fmt}}  "
        f"[{color}]{arrow} {pct:+.2f}%[/{color}]\n"
    )


async def _recent_values(session: Any, stmt: Any) -> list[float]:
    """Run a 'most-recent-first' select of a single numeric column, return oldest→newest."""
    result = await session.execute(stmt)
    vals = [float(v) for v in result.scalars().all() if v is not None]
    vals.reverse()
    return vals


@app.command()
def summary(
    symbol: str | None = typer.Option(None, "--symbol", "-s", help="Filter by symbol."),
    since: str | None = typer.Option(None, "--since", help="Time window e.g. '7d', '4h'."),
) -> None:
    """Show backfill coverage: row counts, time ranges, and latest values per symbol/tf."""
    since_dt = _parse_since(since) if since else None

    async def _run() -> dict[str, Any]:
        async def latest_close(session: Any, sym: str, tf: str) -> float | None:
            stmt = (
                select(Candle.close)
                .where(Candle.symbol == sym, Candle.timeframe == tf)
                .order_by(Candle.open_time.desc())
                .limit(1)
            )
            return (await session.execute(stmt)).scalar()

        async with SessionLocal() as session:
            # ── Candles ──────────────────────────────────────────────────────
            candle_stmt = select(
                Candle.symbol,
                Candle.timeframe,
                func.count().label("rows"),
                func.min(Candle.open_time).label("first"),
                func.max(Candle.open_time).label("last"),
            ).group_by(Candle.symbol, Candle.timeframe).order_by(Candle.symbol, Candle.timeframe)
            if symbol:
                candle_stmt = candle_stmt.where(Candle.symbol == symbol)
            if since_dt:
                candle_stmt = candle_stmt.where(Candle.open_time >= since_dt)
            candles = (await session.execute(candle_stmt)).all()

            # fetch last_close per group
            candle_rows = []
            for row in candles:
                lc = await latest_close(session, row.symbol, row.timeframe)
                candle_rows.append((*row, lc))

            # ── Funding rates (Binance) ───────────────────────────────────
            fr_stmt = select(
                FundingRate.symbol,
                func.count().label("rows"),
                func.max(FundingRate.funding_time).label("latest_time"),
            ).group_by(FundingRate.symbol).order_by(FundingRate.symbol)
            if symbol:
                fr_stmt = fr_stmt.where(FundingRate.symbol == symbol)
            if since_dt:
                fr_stmt = fr_stmt.where(FundingRate.funding_time >= since_dt)
            funding_rows = (await session.execute(fr_stmt)).all()

            # fetch latest rate per symbol
            funding_out = []
            for row in funding_rows:
                stmt = (
                    select(FundingRate.rate)
                    .where(FundingRate.symbol == row.symbol)
                    .order_by(FundingRate.funding_time.desc())
                    .limit(1)
                )
                rate = (await session.execute(stmt)).scalar()
                funding_out.append((*row, rate))

            # ── Cross-venue funding ───────────────────────────────────────
            xv_stmt = select(
                FundingRateXVenue.exchange,
                FundingRateXVenue.symbol,
                func.count().label("rows"),
                func.max(FundingRateXVenue.funding_time).label("latest_time"),
            ).group_by(FundingRateXVenue.exchange, FundingRateXVenue.symbol).order_by(
                FundingRateXVenue.exchange, FundingRateXVenue.symbol
            )
            if symbol:
                xv_stmt = xv_stmt.where(FundingRateXVenue.symbol == symbol)
            if since_dt:
                xv_stmt = xv_stmt.where(FundingRateXVenue.funding_time >= since_dt)
            xvenue_rows = (await session.execute(xv_stmt)).all()

            xv_out = []
            for row in xvenue_rows:
                stmt = (
                    select(FundingRateXVenue.rate)
                    .where(
                        FundingRateXVenue.exchange == row.exchange,
                        FundingRateXVenue.symbol == row.symbol,
                    )
                    .order_by(FundingRateXVenue.funding_time.desc())
                    .limit(1)
                )
                rate = (await session.execute(stmt)).scalar()
                xv_out.append((*row, rate))

            # ── Open interest (latest per symbol) ────────────────────────
            oi_stmt = select(
                OpenInterest.symbol,
                func.count().label("rows"),
                func.max(OpenInterest.ts).label("latest_ts"),
            ).group_by(OpenInterest.symbol).order_by(OpenInterest.symbol)
            if symbol:
                oi_stmt = oi_stmt.where(OpenInterest.symbol == symbol)
            if since_dt:
                oi_stmt = oi_stmt.where(OpenInterest.ts >= since_dt)
            oi_rows = (await session.execute(oi_stmt)).all()

            oi_out = []
            for row in oi_rows:
                stmt = (
                    select(OpenInterest.oi, OpenInterest.oi_value)
                    .where(OpenInterest.symbol == row.symbol)
                    .order_by(OpenInterest.ts.desc())
                    .limit(1)
                )
                latest = (await session.execute(stmt)).first()
                oi_out.append(
                    (*row, latest.oi if latest else None, latest.oi_value if latest else None)
                )

            # ── Basis (latest per symbol) ─────────────────────────────────
            basis_stmt = select(
                Basis.symbol,
                func.count().label("rows"),
                func.max(Basis.ts).label("latest_ts"),
            ).group_by(Basis.symbol).order_by(Basis.symbol)
            if symbol:
                basis_stmt = basis_stmt.where(Basis.symbol == symbol)
            if since_dt:
                basis_stmt = basis_stmt.where(Basis.ts >= since_dt)
            basis_rows = (await session.execute(basis_stmt)).all()

            basis_out = []
            for row in basis_rows:
                stmt = (
                    select(Basis.mark_price, Basis.index_price, Basis.premium_index)
                    .where(Basis.symbol == row.symbol)
                    .order_by(Basis.ts.desc())
                    .limit(1)
                )
                latest = (await session.execute(stmt)).first()
                basis_out.append(
                    (
                        *row,
                        latest.mark_price if latest else None,
                        latest.premium_index if latest else None,
                    )
                )

            return {
                "candles": candle_rows,
                "funding": funding_out,
                "xvenue": xv_out,
                "oi": oi_out,
                "basis": basis_out,
            }

    data = asyncio.run(_run())
    title_suffix = (
        f"  (symbol={symbol}" + (f", since={since}" if since else "") + ")"
        if (symbol or since)
        else ""
    )

    # ── Candles ──────────────────────────────────────────────────────────────
    t = Table(title=f"Candles{title_suffix}")
    for col in ("symbol", "tf", "rows", "first_open", "last_open", "last_close"):
        t.add_column(col, style="cyan" if col == "symbol" else None)
    for sym_, tf, rows, first, last, lc in data["candles"]:
        t.add_row(
            sym_, tf, str(rows),
            str(first)[:19] if first else "—",
            str(last)[:19] if last else "—",
            f"{lc:.2f}" if lc is not None else "—",
        )
    console.print(t)

    # ── Funding rates (Binance) ──────────────────────────────────────────────
    t2 = Table(title=f"Funding Rates (Binance){title_suffix}")
    for col in ("symbol", "rows", "latest_time", "latest_rate"):
        t2.add_column(col)
    for sym_, rows, lt, rate in data["funding"]:
        t2.add_row(
            sym_, str(rows), str(lt)[:19] if lt else "—",
            f"{float(rate):.6f}" if rate is not None else "—",
        )
    console.print(t2)

    # ── Cross-venue funding ──────────────────────────────────────────────────
    t3 = Table(title=f"Cross-Venue Funding{title_suffix}")
    for col in ("exchange", "symbol", "rows", "latest_time", "latest_rate"):
        t3.add_column(col)
    for exch, sym_, rows, lt, rate in data["xvenue"]:
        t3.add_row(
            exch, sym_, str(rows), str(lt)[:19] if lt else "—",
            f"{float(rate):.6f}" if rate is not None else "—",
        )
    console.print(t3)

    # ── Open interest ────────────────────────────────────────────────────────
    t4 = Table(title=f"Open Interest{title_suffix}")
    for col in ("symbol", "rows", "latest_ts", "oi", "oi_value"):
        t4.add_column(col)
    for sym_, rows, lt, oi, oi_val in data["oi"]:
        t4.add_row(sym_, str(rows), str(lt)[:19] if lt else "—",
                   f"{float(oi):.0f}" if oi is not None else "—",
                   f"{float(oi_val):.0f}" if oi_val is not None else "—")
    console.print(t4)

    # ── Basis ────────────────────────────────────────────────────────────────
    t5 = Table(title=f"Basis (Mark vs Index){title_suffix}")
    for col in ("symbol", "rows", "latest_ts", "mark_price", "premium_index"):
        t5.add_column(col)
    for sym_, rows, lt, mark, prem in data["basis"]:
        t5.add_row(sym_, str(rows), str(lt)[:19] if lt else "—",
                   f"{float(mark):.2f}" if mark is not None else "—",
                   f"{float(prem):.6f}" if prem is not None else "—")
    console.print(t5)


def _render_candle(o: float, h: float, low: float, c: float, height: int = 20) -> None:
    """Print a single candlestick chart scaled to `height` terminal rows."""
    bullish = c >= o
    color = "green" if bullish else "red"
    body_top = max(o, c)
    body_bot = min(o, c)
    price_range = h - low
    if price_range == 0:
        console.print(f"[{color}]Doji — open=close={o:.2f}[/{color}]")
        return

    def price_to_row(p: float) -> int:
        # row 0 = top (high), row height-1 = bottom (low)
        return round((h - p) / price_range * (height - 1))

    row_high = price_to_row(h)       # should be 0
    row_btop = price_to_row(body_top)
    row_bbot = price_to_row(body_bot)
    row_low = price_to_row(low)      # should be height-1

    # price labels at key levels
    labels: dict[int, str] = {
        row_high: f"{h:.2f}  ← high",
        row_btop: f"{body_top:.2f}  ← {'close' if bullish else 'open'}",
        row_bbot: f"{body_bot:.2f}  ← {'open' if bullish else 'close'}",
        row_low:  f"{low:.2f}  ← low",
    }

    for i in range(height):
        in_body = row_btop <= i <= row_bbot
        in_wick = row_high <= i <= row_low
        if in_body:
            char = f"[{color}]█████[/{color}]"
        elif in_wick:
            char = "  │  "
        else:
            char = "     "
        label = f"  {labels[i]}" if i in labels else ""
        console.print(f"  {char}{label}")

    direction = "▲ Bullish" if bullish else "▼ Bearish"
    pct = abs(c - o) / o * 100
    console.print(
        f"\n  [{color}]{direction}[/{color}]  "
        f"O {o:.2f}  H {h:.2f}  L {low:.2f}  C {c:.2f}  "
        f"body {pct:.2f}%"
    )


@app.command()
def candles(
    symbol: str = typer.Option(..., "--symbol", "-s", help="Symbol e.g. BTCUSDT."),
    timeframe: str = typer.Option(..., "--timeframe", "-t", help="Timeframe: 15m, 1h, or 4h."),
    limit: int = typer.Option(20, "--limit", "-n", help="Rows to show (most recent first)."),
    since: str | None = typer.Option(None, "--since", help="Only rows after this window."),
    viz: bool = typer.Option(False, "--viz", help="Sparkline of closes (--bars 1 = one candle)."),
    bars: int | None = typer.Option(None, "--bars", help="With --viz: 1 = single candlestick."),
) -> None:
    """Show the most recent candle rows for a symbol/timeframe."""
    since_dt = _parse_since(since) if since else None

    async def _run() -> list[Any]:
        async with SessionLocal() as session:
            stmt = (
                select(Candle)
                .where(Candle.symbol == symbol, Candle.timeframe == timeframe)
                .order_by(Candle.open_time.desc())
                .limit(limit)
            )
            if since_dt:
                stmt = stmt.where(Candle.open_time >= since_dt)
            result = await session.execute(stmt)
            return result.scalars().all()

    rows = asyncio.run(_run())

    if not rows:
        console.print("[yellow]No rows found for the given filters.[/yellow]")
        return

    if viz:
        if bars == 1:
            latest = rows[0]
            console.print(
                f"\n[bold]{symbol} / {timeframe}[/bold]  {str(latest.open_time)[:19]}\n"
            )
            _render_candle(
                float(latest.open),
                float(latest.high),
                float(latest.low),
                float(latest.close),
            )
            return
        # rows are most-recent-first; reverse to oldest→newest for the sparkline
        closes = [float(r.close) for r in reversed(rows)]
        console.print(
            f"\n[bold]{symbol} / {timeframe}[/bold]  "
            f"{str(rows[-1].open_time)[:19]} → {str(rows[0].open_time)[:19]}\n"
        )
        _print_series("close", closes)
        return

    title = f"Candles  {symbol} / {timeframe}  (latest {limit})"
    if since:
        title += f"  since={since}"
    t = Table(title=title)
    for col in ("open_time", "open", "high", "low", "close", "volume", "taker_buy_vol", "trades"):
        t.add_column(col, style="cyan" if col == "open_time" else None)

    for row in rows:
        t.add_row(
            str(row.open_time)[:19],
            f"{float(row.open):.2f}",
            f"{float(row.high):.2f}",
            f"{float(row.low):.2f}",
            f"{float(row.close):.2f}",
            f"{float(row.volume):.2f}",
            f"{float(row.taker_buy_vol):.2f}",
            str(row.trades) if row.trades is not None else "—",
        )

    console.print(t)


@app.command()
def funding(
    symbol: str = typer.Option(..., "--symbol", "-s", help="Symbol e.g. BTCUSDT."),
    limit: int = typer.Option(30, "--limit", "-n", help="Number of recent points."),
    xvenue: bool = typer.Option(False, "--xvenue", help="Also compare latest rate across venues."),
) -> None:
    """Sparkline of recent Binance funding rates; --xvenue compares peers."""

    async def _run() -> tuple[list[float], list[Any]]:
        async with SessionLocal() as session:
            stmt = (
                select(FundingRate.rate)
                .where(FundingRate.symbol == symbol)
                .order_by(FundingRate.funding_time.desc())
                .limit(limit)
            )
            series = await _recent_values(session, stmt)

            xv: list[Any] = []
            if xvenue:
                # latest rate per exchange for this symbol
                sub = (
                    select(
                        FundingRateXVenue.exchange,
                        func.max(FundingRateXVenue.funding_time).label("ft"),
                    )
                    .where(FundingRateXVenue.symbol == symbol)
                    .group_by(FundingRateXVenue.exchange)
                ).subquery()
                latest = (
                    select(
                        FundingRateXVenue.exchange,
                        FundingRateXVenue.funding_time,
                        FundingRateXVenue.rate,
                    )
                    .join(
                        sub,
                        (FundingRateXVenue.exchange == sub.c.exchange)
                        & (FundingRateXVenue.funding_time == sub.c.ft),
                    )
                    .order_by(FundingRateXVenue.exchange)
                )
                xv = list((await session.execute(latest)).all())
            return series, xv

    series, xv = asyncio.run(_run())
    console.print(f"\n[bold]Funding — {symbol}[/bold] (Binance)\n")
    _print_series("rate", series, fmt=".6f")

    if xvenue:
        t = Table(title=f"Cross-Venue Latest Funding — {symbol}")
        for col in ("exchange", "funding_time", "rate"):
            t.add_column(col, style="cyan" if col == "exchange" else None)
        for exch, ft, rate in xv:
            t.add_row(exch, str(ft)[:19], f"{float(rate):.6f}")
        console.print(t)


@app.command()
def oi(
    symbol: str = typer.Option(..., "--symbol", "-s", help="Symbol e.g. BTCUSDT."),
    limit: int = typer.Option(30, "--limit", "-n", help="Number of recent points."),
) -> None:
    """Sparkline of recent open-interest (contracts)."""

    async def _run() -> list[float]:
        async with SessionLocal() as session:
            stmt = (
                select(OpenInterest.oi)
                .where(OpenInterest.symbol == symbol)
                .order_by(OpenInterest.ts.desc())
                .limit(limit)
            )
            return await _recent_values(session, stmt)

    series = asyncio.run(_run())
    console.print(f"\n[bold]Open Interest — {symbol}[/bold]\n")
    _print_series("oi", series, fmt=".0f")


@app.command()
def basis(
    symbol: str = typer.Option(..., "--symbol", "-s", help="Symbol e.g. BTCUSDT."),
    limit: int = typer.Option(30, "--limit", "-n", help="Number of recent points."),
) -> None:
    """Sparkline of recent basis (premium_index = (mark - index)/index)."""

    async def _run() -> list[float]:
        async with SessionLocal() as session:
            stmt = (
                select(Basis.premium_index)
                .where(Basis.symbol == symbol)
                .order_by(Basis.ts.desc())
                .limit(limit)
            )
            return await _recent_values(session, stmt)

    series = asyncio.run(_run())
    console.print(f"\n[bold]Basis (premium index) — {symbol}[/bold]\n")
    _print_series("premium", series, fmt=".6f")


@app.command()
def media(
    source: str | None = typer.Option(None, "--source", help="Filter by source: 'rss' or 'x'."),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of recent headlines."),
) -> None:
    """List recent media headlines without opening the DB."""

    async def _run() -> list[Any]:
        async with SessionLocal() as session:
            stmt = select(MediaItem).order_by(MediaItem.published_at.desc()).limit(limit)
            if source:
                stmt = stmt.where(MediaItem.source == source)
            return list((await session.execute(stmt)).scalars().all())

    items = asyncio.run(_run())
    if not items:
        console.print("[yellow]No media items found.[/yellow]")
        return

    t = Table(title=f"Media (latest {limit}" + (f", source={source}" if source else "") + ")")
    t.add_column("published", style="cyan")
    t.add_column("src")
    t.add_column("account")
    t.add_column("title")
    for it in items:
        title = (it.title or it.body or "")[:80]
        t.add_row(str(it.published_at)[:19], it.source, it.account, title)
    console.print(t)


@app.command()
def show(
    symbol: str = typer.Option(..., "--symbol", "-s", help="Symbol e.g. BTCUSDT."),
    timeframe: str = typer.Option("1h", "--timeframe", "-t", help="Candle timeframe."),
    limit: int = typer.Option(30, "--limit", "-n", help="Points per sparkline."),
) -> None:
    """One-screen dashboard: price, funding, OI, basis, and recent headlines."""

    async def _run() -> dict[str, Any]:
        async with SessionLocal() as session:
            closes = await _recent_values(
                session,
                select(Candle.close)
                .where(Candle.symbol == symbol, Candle.timeframe == timeframe)
                .order_by(Candle.open_time.desc())
                .limit(limit),
            )
            funding = await _recent_values(
                session,
                select(FundingRate.rate)
                .where(FundingRate.symbol == symbol)
                .order_by(FundingRate.funding_time.desc())
                .limit(limit),
            )
            oi_series = await _recent_values(
                session,
                select(OpenInterest.oi)
                .where(OpenInterest.symbol == symbol)
                .order_by(OpenInterest.ts.desc())
                .limit(limit),
            )
            basis_series = await _recent_values(
                session,
                select(Basis.premium_index)
                .where(Basis.symbol == symbol)
                .order_by(Basis.ts.desc())
                .limit(limit),
            )
            headlines = (
                await session.execute(
                    select(MediaItem)
                    .order_by(MediaItem.published_at.desc())
                    .limit(5)
                )
            ).scalars().all()
            return {
                "closes": closes,
                "funding": funding,
                "oi": oi_series,
                "basis": basis_series,
                "headlines": headlines,
            }

    d = asyncio.run(_run())
    console.print(f"\n[bold cyan]══ {symbol} ══[/bold cyan]\n")
    _print_series(f"price ({timeframe} close)", d["closes"])
    _print_series("funding rate", d["funding"], fmt=".6f")
    _print_series("open interest", d["oi"], fmt=".0f")
    _print_series("basis (premium)", d["basis"], fmt=".6f")

    console.print("[bold]Recent headlines[/bold]")
    if not d["headlines"]:
        console.print("  [dim]none[/dim]")
    for it in d["headlines"]:
        when = str(it.published_at)[:16]
        text = (it.title or it.body or "")[:70]
        console.print(f"  [dim]{when}[/dim] [{it.account}] {text}")
    console.print()


@app.command()
def yahoo(
    ticker: str = typer.Option(..., "--ticker", help="Yahoo ticker e.g. ^GSPC, GC=F, BTC-USD."),
    period: str = typer.Option("1mo", "--period", help="Lookback e.g. 5d, 1mo, 3mo, 1y."),
    interval: str = typer.Option("1d", "--interval", help="Bar interval e.g. 1d, 1h, 15m."),
) -> None:
    """[Exploratory] Fetch Yahoo Finance bars via yfinance. No DB write — feasibility probe."""
    from ats.ingestion.yahoo import fetch_recent

    try:
        rows = asyncio.run(fetch_recent(ticker, period, interval))
    except ModuleNotFoundError:
        console.print(
            "[red]yfinance not installed.[/red] Install the optional extra: "
            "[cyan]uv sync --extra yahoo[/cyan]"
        )
        raise typer.Exit(1) from None

    if not rows:
        console.print(f"[yellow]No data returned for {ticker}.[/yellow]")
        return

    closes = [r["close"] for r in rows]
    console.print(
        f"\n[bold]{ticker}[/bold]  period={period} interval={interval}  "
        f"({str(rows[0]['ts'])[:16]} → {str(rows[-1]['ts'])[:16]})\n"
    )
    _print_series("close", closes)

    t = Table(title=f"Yahoo {ticker} (latest 10)")
    for col in ("ts", "open", "high", "low", "close", "volume"):
        t.add_column(col, style="cyan" if col == "ts" else None)
    for r in rows[-10:]:
        t.add_row(
            str(r["ts"])[:16],
            f"{r['open']:.2f}", f"{r['high']:.2f}", f"{r['low']:.2f}",
            f"{r['close']:.2f}", f"{r['volume']:.0f}",
        )
    console.print(t)


@app.command()
def validate() -> None:
    """Programmatic smoke test: backfill 7d BTCUSDT, check row counts, report."""
    from sqlalchemy import text

    from ats.ingestion.backfill import run as bf_run
    from ats.ingestion.rss_poller import pull_all as rss_pull
    from ats.ingestion.universe import load_xvenue_mapping
    from ats.ingestion.x_poller import pull_all as x_pull
    from ats.ingestion.xvenue_funding import pull_all as xvenue_pull

    sym = ["BTCUSDT"]
    mapping = load_xvenue_mapping()

    async def _run() -> dict[str, Any]:
        async with SessionLocal() as session:
            # 1. Ensure migration ran
            try:
                await session.execute(text("SELECT 1 FROM candles LIMIT 1"))
            except Exception as e:
                typer.echo(f"DB not ready — run 'ats db migrate' first. ({e})", err=True)
                raise typer.Exit(1) from e

            # 2. Backfill
            await bf_run(session, timedelta(days=7), sym)

            # 3. Cross-venue funding
            await xvenue_pull(session, sym, mapping)

            # 4. Media
            await rss_pull(session)
            await x_pull(session)

            # 5. Assertions
            failures = []

            r = await session.execute(
                text("SELECT COUNT(*) FROM candles WHERE symbol='BTCUSDT' AND timeframe='15m'")
            )
            candle_count = r.scalar() or 0
            if candle_count < 1:
                failures.append(f"candles BTCUSDT/15m: {candle_count} rows (expected > 0)")

            r = await session.execute(text(
                "SELECT COUNT(DISTINCT exchange) FROM funding_rates_xvenue"
                " WHERE symbol='BTCUSDT'"
            ))
            xvenue_count = r.scalar() or 0
            if xvenue_count < 2:
                failures.append(
                    f"funding_rates_xvenue distinct exchanges: {xvenue_count} (expected >= 2)"
                )

            r = await session.execute(text("SELECT COUNT(*) FROM media_items WHERE source='rss'"))
            media_count = r.scalar() or 0

            return {
                "candles_btc_15m": candle_count,
                "xvenue_exchanges": xvenue_count,
                "media_rss": media_count,
                "failures": failures,
            }

    result = asyncio.run(_run())
    typer.echo(f"candles BTCUSDT/15m : {result['candles_btc_15m']}")
    typer.echo(f"xvenue exchanges    : {result['xvenue_exchanges']}")
    typer.echo(f"media_items (rss)   : {result['media_rss']}")

    if result["failures"]:
        typer.echo("\nFAILURES:", err=True)
        for f in result["failures"]:
            typer.echo(f"  - {f}", err=True)
        raise typer.Exit(1)
    else:
        typer.echo("\nAll checks passed.")

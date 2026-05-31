"""CLI commands for feature processing."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from ats.cli_commands._viz import print_series
from ats.db.session import SessionLocal
from ats.ingestion.universe import load_m1_universe

app = typer.Typer(name="process", help="Feature processing commands.")
console = Console()

_SYMBOLS_HELP = "Comma-separated symbols. Defaults to M1 universe."

# Feature columns shown by `ats process show`, with display format. The SQL SELECT
# is built from this list so the query and the rendering can never drift apart.
_FEATURE_COLS: list[tuple[str, str]] = [
    ("rsi_14", ".2f"), ("atr_14", ".4f"), ("atr_pct", ".4f"),
    ("ema_20", ".2f"), ("ema_50", ".2f"), ("ema_200", ".2f"),
    ("macd", ".4f"), ("macd_signal", ".4f"), ("macd_hist", ".4f"),
    ("obv", ".0f"), ("vol_zscore_20", ".2f"),
    ("funding_rate", ".6f"), ("funding_z_30d", ".2f"), ("realized_vol_30d", ".4f"),
    ("cvd_30", ".0f"), ("cvd_slope_10", ".2f"), ("momentum_composite", ".3f"),
    ("oi_delta_pct_1h", ".4f"), ("oi_delta_pct_24h", ".4f"),
    ("funding_divergence", ".6f"), ("funding_divergence_z_30d", ".2f"),
    ("funding_peer_count", ".0f"),
    ("basis_premium", ".6f"), ("basis_z_30d", ".2f"),
    ("pr_atr", ".3f"), ("pr_rsi", ".3f"), ("pr_vol_zscore", ".3f"),
    ("pr_oi_delta", ".3f"), ("pr_funding_imbalance", ".3f"),
    ("pr_momentum", ".3f"), ("pr_cvd_divergence", ".3f"),
]

# Columns worth eyeballing as sparklines — MACD foremost.
_SPARK_COLS: list[tuple[str, str]] = [
    ("macd", ".4f"), ("macd_signal", ".4f"), ("macd_hist", ".4f"),
    ("rsi_14", ".2f"), ("atr_14", ".4f"), ("cvd_30", ".0f"),
    ("momentum_composite", ".3f"), ("pr_momentum", ".3f"),
]


def _f(v: Any) -> float | None:
    """Coerce a DB value (Decimal/None) to float, or None if null/non-numeric."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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
def run(
    symbols: str = typer.Option("", "--symbols", help=_SYMBOLS_HELP),
) -> None:
    """Compute features for all M1 symbols at the most recent closed bar."""
    from ats.processing.features import run_once

    sym_list = [s.strip() for s in symbols.split(",") if s.strip()] or load_m1_universe()

    async def _run() -> None:
        async with SessionLocal() as session:
            await run_once(session, sym_list)

    asyncio.run(_run())
    typer.echo("Feature run complete.")


@app.command()
def backfill(
    since: str = typer.Option("90d", "--since", help="How far back, e.g. 90d, 7d."),
    symbols: str = typer.Option("", "--symbols", help=_SYMBOLS_HELP),
) -> None:
    """Compute features for the past N days, idempotent (upserts on conflict)."""
    from ats.processing.features import backfill as bf_run
    from ats.processing.regime import compute_regime, upsert_regime

    sym_list = [s.strip() for s in symbols.split(",") if s.strip()] or load_m1_universe()
    delta = _parse_since(since)
    since_dt = datetime.now(UTC) - delta

    async def _run() -> None:
        async with SessionLocal() as session:
            await bf_run(session, since_dt, sym_list)

            # Also compute regimes if BTC is in the list
            if "BTCUSDT" in sym_list:
                from ats.processing.features import _fetch_candles

                btc_df = await _fetch_candles(session, "BTCUSDT", "1h", since=since_dt)
                if not btc_df.empty and len(btc_df) >= 31:
                    prev_vol: str | None = None
                    # Need at least 31 bars; walk through all bars
                    for i in range(30, len(btc_df)):
                        window = btc_df.iloc[: i + 1]
                        regime = compute_regime(window, prev_volatility=prev_vol)
                        prev_vol = regime["volatility"]
                        await upsert_regime(session, regime)

    asyncio.run(_run())
    typer.echo("Feature backfill complete.")


@app.command()
def show(
    symbol: str = typer.Option(..., "--symbol", "-s", help="Symbol e.g. BTCUSDT."),
    timeframe: str = typer.Option("15m", "--timeframe", "-t", help="Timeframe: 15m, 1h, 4h."),
    limit: int = typer.Option(60, "--limit", "-n", help="Recent feature rows to inspect."),
    viz: bool = typer.Option(True, "--viz/--no-viz", help="Show sparklines (MACD etc.)."),
) -> None:
    """Inspect computed features: coverage summary, latest row, and sparklines.

    Read-only verification tool. Run `ats process backfill` first to populate.
    """
    from sqlalchemy import text

    col_sql = ", ".join(["open_time"] + [c for c, _ in _FEATURE_COLS])

    async def _run() -> list[Any]:
        async with SessionLocal() as session:
            result = await session.execute(
                text(
                    f"SELECT {col_sql} FROM features "
                    "WHERE symbol = :symbol AND timeframe = :tf "
                    "ORDER BY open_time DESC LIMIT :limit"
                ),
                {"symbol": symbol, "tf": timeframe, "limit": limit},
            )
            return [r._mapping for r in result.fetchall()]

    rows = asyncio.run(_run())

    if not rows:
        console.print(
            f"[yellow]No feature rows for {symbol}/{timeframe}. "
            "Run 'ats process backfill' first.[/yellow]"
        )
        return

    latest = rows[0]  # most-recent-first
    total = len(rows)
    console.print(
        f"\n[bold cyan]══ features {symbol} / {timeframe} ══[/bold cyan]  "
        f"latest {str(latest['open_time'])[:19]}  ({total} bars)\n"
    )

    # ── A. Coverage / non-null summary ────────────────────────────────────────
    cov = Table(title="Coverage (non-null over window)")
    cov.add_column("column", style="cyan")
    cov.add_column("non_null", justify="right")
    cov.add_column("latest", justify="right")
    cov.add_column("min", justify="right")
    cov.add_column("max", justify="right")
    for col, fmt in _FEATURE_COLS:
        vals = [v for v in (_f(r[col]) for r in rows) if v is not None]
        n = len(vals)
        color = "green" if n == total else ("red" if n == 0 else "yellow")
        latest_v = _f(latest[col])
        cov.add_row(
            col,
            f"[{color}]{n}/{total}[/{color}]",
            f"{latest_v:{fmt}}" if latest_v is not None else "—",
            f"{min(vals):{fmt}}" if vals else "—",
            f"{max(vals):{fmt}}" if vals else "—",
        )
    console.print(cov)

    # ── B. Latest-row detail ──────────────────────────────────────────────────
    detail = Table(title=f"Latest bar  {str(latest['open_time'])[:19]}")
    detail.add_column("column", style="cyan")
    detail.add_column("value", justify="right")
    for col, fmt in _FEATURE_COLS:
        v = _f(latest[col])
        detail.add_row(col, f"{v:{fmt}}" if v is not None else "[dim]—[/dim]")
    console.print(detail)

    # ── C. Sparklines (oldest→newest) ─────────────────────────────────────────
    if viz:
        console.print()
        ordered = list(reversed(rows))  # oldest→newest
        for col, fmt in _SPARK_COLS:
            series = [v for v in (_f(r[col]) for r in ordered) if v is not None]
            print_series(console, col, series, fmt=fmt)


@app.command()
def watch() -> None:
    """[Tier 3 / M4] Subscribe to candle-close events; compute as bars close."""
    typer.echo("Tier 3 daemon is M4 — see specs/10-live-operations.md", err=True)
    raise typer.Exit(2)


@app.command()
def validate() -> None:
    """Programmatic smoke test: assert candles ≥30d, run backfill 7d, check pr_* bounds."""
    from sqlalchemy import text

    from ats.processing.features import backfill as bf_run

    async def _run() -> dict[str, Any]:
        async with SessionLocal() as session:
            # 1. Ensure tables exist
            try:
                await session.execute(text("SELECT 1 FROM features LIMIT 1"))
            except Exception as e:
                typer.echo(f"DB not ready — run 'ats db migrate' first. ({e})", err=True)
                raise typer.Exit(1) from e

            # 2. Check ≥30d candles for at least one symbol
            r = await session.execute(
                text("""
                    SELECT symbol, MIN(open_time) AS first, MAX(open_time) AS last,
                           COUNT(*) AS rows
                    FROM candles
                    WHERE timeframe = '15m'
                    GROUP BY symbol
                    HAVING MAX(open_time) - MIN(open_time) >= INTERVAL '30 days'
                    LIMIT 1
                """)
            )
            first_sym_row = r.fetchone()
            failures: list[str] = []
            if first_sym_row is None:
                failures.append("No symbol has ≥30d of 15m candles")
                return {"failures": failures, "feature_rows": 0, "pr_violations": 0}

            sym = first_sym_row.symbol

            # 3. Backfill 7d
            since_7d = datetime.now(UTC) - timedelta(days=7)
            await bf_run(session, since_7d, [sym])

            # 4. Check row count (≈7×96 for 15m)
            r2 = await session.execute(
                text("""
                    SELECT COUNT(*) FROM features
                    WHERE symbol = :sym AND timeframe = '15m'
                      AND open_time >= :since
                """),
                {"sym": sym, "since": since_7d},
            )
            feature_count = r2.scalar() or 0
            expected = 7 * 96
            if feature_count < expected // 2:
                failures.append(
                    f"features {sym}/15m: {feature_count} rows (expected ≈{expected})"
                )

            # 5. Check pr_* ∈ [0, 1]
            r3 = await session.execute(
                text("""
                    SELECT COUNT(*) FROM features
                    WHERE (pr_atr < 0 OR pr_atr > 1
                        OR pr_rsi < 0 OR pr_rsi > 1
                        OR pr_momentum < 0 OR pr_momentum > 1)
                    AND open_time >= :since
                """),
                {"since": since_7d},
            )
            pr_violations = r3.scalar() or 0
            if pr_violations > 0:
                failures.append(f"pr_* out of [0,1] bounds: {pr_violations} rows")

            return {
                "failures": failures,
                "feature_rows": feature_count,
                "pr_violations": pr_violations,
                "symbol": sym,
            }

    result = asyncio.run(_run())
    typer.echo(f"symbol          : {result.get('symbol', '—')}")
    typer.echo(f"feature rows    : {result.get('feature_rows', 0)}")
    typer.echo(f"pr_* violations : {result.get('pr_violations', 0)}")

    failures: list[str] = result.get("failures", [])
    if failures:
        typer.echo("\nFAILURES:", err=True)
        for f in failures:
            typer.echo(f"  - {f}", err=True)
        raise typer.Exit(1)
    else:
        typer.echo("\nAll checks passed.")

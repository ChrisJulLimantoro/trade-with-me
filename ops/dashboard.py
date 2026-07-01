#!/usr/bin/env python3
"""Tiny read-only dashboard: open positions, realized + unrealized PnL, recent trades.

Deliberately dependency-free beyond what the project already ships (stdlib http.server +
asyncpg). No FastAPI/uvicorn so it stays light enough for a 1 GB box. Serves a single
auto-refreshing HTML page on :8080, reading paper_trades from Postgres and live prices from
the Binance (testnet) public ticker.

Run:  uv run python ops/dashboard.py   (the compose `dash` service does this)
Env:  DATABASE_URL (required), BINANCE_FUTURES_URL (optional), DASH_PORT (default 8080).
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import asyncpg

PORT = int(os.environ.get("DASH_PORT", "8080"))
PLAN_LIMIT = int(os.environ.get("DASH_PLAN_LIMIT", "15"))  # how many recent plans to show
# asyncpg wants a plain libpq DSN — strip SQLAlchemy's "+asyncpg" driver suffix.
DSN = os.environ.get("DATABASE_URL", "postgresql://ats:ats@db:5432/ats").replace(
    "postgresql+asyncpg://", "postgresql://"
)
PRICE_BASE = (os.environ.get("BINANCE_FUTURES_URL") or "https://testnet.binancefuture.com/fapi").rstrip("/")


def _fnum(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def fetch_price(symbol: str) -> float | None:
    """Last price for one symbol from the public futures ticker (stdlib, ~1 cheap GET)."""
    try:
        url = f"{PRICE_BASE}/v1/ticker/price?symbol={symbol}"
        with urllib.request.urlopen(url, timeout=4) as resp:
            return float(json.loads(resp.read())["price"])
    except Exception:
        return None


async def load_data() -> dict:
    conn = await asyncpg.connect(DSN)
    try:
        open_rows = await conn.fetch(
            "SELECT symbol, direction, entry_price, stop_loss, take_profit, notional_usd, "
            "leverage, venue, entry_order_id, entry_time FROM paper_trades "
            "WHERE status='open' ORDER BY entry_time DESC"
        )
        closed_rows = await conn.fetch(
            "SELECT symbol, direction, entry_price, exit_price, exit_reason, pnl_usd, pnl_pct, "
            "venue, exit_time FROM paper_trades WHERE status='closed' "
            "ORDER BY exit_time DESC LIMIT 50"
        )
        agg = await conn.fetchrow(
            "SELECT COUNT(*) n, COALESCE(SUM(pnl_usd),0) total, "
            "COUNT(*) FILTER (WHERE pnl_usd > 0) wins FROM paper_trades WHERE status='closed'"
        )
        plan_rows = await conn.fetch(
            "SELECT plan_id, symbol, created_at, as_of, market_bias, status, regime_cell, "
            "rationale FROM plans ORDER BY created_at DESC LIMIT $1",
            PLAN_LIMIT,
        )
        plan_ids = [r["plan_id"] for r in plan_rows]
        setup_rows = (
            await conn.fetch(
                "SELECT plan_id, direction, status, entry_zone_low, entry_zone_high, "
                "stop_loss, take_profit FROM setups WHERE plan_id = ANY($1::uuid[])",
                plan_ids,
            )
            if plan_ids
            else []
        )
    finally:
        await conn.close()
    return {
        "open": open_rows,
        "closed": closed_rows,
        "agg": agg,
        "plans": plan_rows,
        "setups": setup_rows,
    }


def _row_unrealized(r, price: float | None) -> float | None:
    if price is None:
        return None
    entry = _fnum(r["entry_price"])
    notional = _fnum(r["notional_usd"])
    if entry <= 0 or notional <= 0:
        return None
    move = (price - entry) / entry
    if r["direction"] == "short":
        move = -move
    return notional * move


def render(data: dict) -> str:
    agg = data["agg"]
    n = agg["n"] if agg else 0
    total = _fnum(agg["total"]) if agg else 0.0
    wins = agg["wins"] if agg else 0
    win_rate = (wins / n * 100) if n else 0.0

    # Live prices for the symbols currently open (one fetch each).
    symbols = {r["symbol"] for r in data["open"]}
    prices = {s: fetch_price(s) for s in symbols}
    unreal_total = 0.0

    def money(v: float | None) -> str:
        if v is None:
            return "<span class=dim>—</span>"
        cls = "pos" if v > 0 else ("neg" if v < 0 else "dim")
        return f"<span class={cls}>{v:+,.2f}</span>"

    open_html = ""
    for r in data["open"]:
        px = prices.get(r["symbol"])
        u = _row_unrealized(r, px)
        if u is not None:
            unreal_total += u
        tps = r["take_profit"]
        tp_str = ", ".join(f"{_fnum(x):g}" for x in (tps or [])) if tps else "—"
        open_html += (
            f"<tr><td>{html.escape(r['symbol'])}</td>"
            f"<td class={'pos' if r['direction']=='long' else 'neg'}>{r['direction']}</td>"
            f"<td>{_fnum(r['entry_price']):g}</td>"
            f"<td>{px if px is not None else '—'}</td>"
            f"<td>{_fnum(r['stop_loss']):g}</td><td>{tp_str}</td>"
            f"<td>{_fnum(r['notional_usd']):,.0f}</td>"
            f"<td>{money(u)}</td>"
            f"<td class=dim>{html.escape(str(r['entry_order_id'] or ''))}</td></tr>"
        )
    if not open_html:
        open_html = "<tr><td colspan=9 class=dim>no open positions</td></tr>"

    closed_html = ""
    for r in data["closed"]:
        when = r["exit_time"].strftime("%m-%d %H:%M") if r["exit_time"] else "—"
        closed_html += (
            f"<tr><td class=dim>{when}</td><td>{html.escape(r['symbol'])}</td>"
            f"<td>{html.escape(r['direction'] or '')}</td>"
            f"<td>{_fnum(r['entry_price']):g} → {_fnum(r['exit_price']):g}</td>"
            f"<td>{html.escape(r['exit_reason'] or '')}</td>"
            f"<td>{money(_fnum(r['pnl_usd']))}</td>"
            f"<td>{_fnum(r['pnl_pct'])*100:+.2f}%</td></tr>"
        )
    if not closed_html:
        closed_html = "<tr><td colspan=7 class=dim>no closed trades yet</td></tr>"

    # Recent plans (+ their setups). Group setups by plan_id.
    setups_by_plan: dict = {}
    for s in data.get("setups", []):
        setups_by_plan.setdefault(s["plan_id"], []).append(s)

    plan_html = ""
    for p in data.get("plans", []):
        setups = setups_by_plan.get(p["plan_id"], [])
        when = p["as_of"].strftime("%m-%d %H:%M") if p["as_of"] else "—"
        bias = p["market_bias"] or "—"
        bias_cls = "pos" if bias == "bullish" else ("neg" if bias == "bearish" else "dim")
        if setups:
            s0 = setups[0]
            d = s0["direction"]
            zone = f"{_fnum(s0['entry_zone_low']):g}–{_fnum(s0['entry_zone_high']):g}"
            tps = ", ".join(f"{_fnum(x):g}" for x in (s0["take_profit"] or []))
            extra = f" (+{len(setups) - 1})" if len(setups) > 1 else ""
            setup_cell = (
                f"<span class={'pos' if d == 'long' else 'neg'}>{d}</span>{extra} "
                f"{zone} · sl {_fnum(s0['stop_loss']):g} · tp {tps} "
                f"<span class=dim>[{html.escape(s0['status'])}]</span>"
            )
        else:
            setup_cell = "<span class=dim>stand-aside</span>"
        rat = (p["rationale"] or "")[:90]
        plan_html += (
            f"<tr><td class=dim>{when}</td><td>{html.escape(p['symbol'])}</td>"
            f"<td class={bias_cls}>{bias}</td>"
            f"<td class=dim>{html.escape(p['regime_cell'] or '—')}</td>"
            f"<td>{html.escape(p['status'])}</td>"
            f"<td>{setup_cell}</td>"
            f"<td class=dim>{html.escape(rat)}</td></tr>"
        )
    if not plan_html:
        plan_html = "<tr><td colspan=7 class=dim>no plans yet</td></tr>"

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta http-equiv=refresh content=10>
<title>ATS testnet dashboard</title>
<style>
 body{{font:14px system-ui,sans-serif;background:#0f1115;color:#d7dae0;margin:0;padding:20px}}
 h1{{font-size:18px;margin:0 0 4px}} h2{{font-size:14px;color:#9aa0aa;margin:24px 0 8px}}
 .cards{{display:flex;gap:14px;flex-wrap:wrap;margin:14px 0}}
 .card{{background:#181b22;border:1px solid #232733;border-radius:10px;padding:12px 16px;min-width:140px}}
 .card .v{{font-size:22px;font-weight:600}} .card .l{{color:#9aa0aa;font-size:12px}}
 table{{border-collapse:collapse;width:100%;background:#181b22;border-radius:10px;overflow:hidden}}
 th,td{{padding:7px 10px;text-align:left;border-bottom:1px solid #232733;white-space:nowrap}}
 th{{color:#9aa0aa;font-weight:600;font-size:12px}}
 .pos{{color:#34d399}} .neg{{color:#f87171}} .dim{{color:#6b7280}}
 .foot{{color:#6b7280;margin-top:18px;font-size:12px}}
</style></head><body>
<h1>ATS — Binance Testnet</h1>
<div class=cards>
 <div class=card><div class=l>Realized PnL</div><div class=v>{money(total)}</div></div>
 <div class=card><div class=l>Unrealized (open)</div><div class=v>{money(unreal_total if symbols else None)}</div></div>
 <div class=card><div class=l>Closed trades</div><div class=v>{n}</div></div>
 <div class=card><div class=l>Win rate</div><div class=v>{win_rate:.0f}%</div></div>
 <div class=card><div class=l>Open now</div><div class=v>{len(data['open'])}</div></div>
</div>
<h2>OPEN POSITIONS</h2>
<table><tr><th>symbol</th><th>dir</th><th>entry</th><th>last</th><th>stop</th><th>take-profit</th>
<th>notional</th><th>uPnL</th><th>entry order</th></tr>{open_html}</table>
<h2>RECENT PLANS (last {PLAN_LIMIT})</h2>
<table><tr><th>bar (as_of)</th><th>symbol</th><th>bias</th><th>regime</th><th>status</th>
<th>setup</th><th>rationale</th></tr>{plan_html}</table>
<h2>RECENT CLOSED (last 50)</h2>
<table><tr><th>exit</th><th>symbol</th><th>dir</th><th>entry → exit</th><th>reason</th>
<th>PnL $</th><th>PnL %</th></tr>{closed_html}</table>
<div class=foot>auto-refresh 10s · {now} · realized = booked PnL on closed trades; uPnL uses live ticker price</div>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        try:
            data = asyncio.run(load_data())
            body = render(data).encode()
        except Exception as exc:  # surface DB/render errors in the page, don't crash
            body = f"<pre>dashboard error: {html.escape(str(exc))}</pre>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # quiet access logs
        pass


if __name__ == "__main__":
    print(f"[dashboard] serving on :{PORT}  (DSN host={DSN.split('@')[-1]})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

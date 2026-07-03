#!/usr/bin/env python3
"""Tiny read-only dashboard: open positions, realized + unrealized PnL, recent trades.

Deliberately dependency-free beyond what the project already ships (stdlib http.server +
asyncpg). No FastAPI/uvicorn so it stays light enough for a 1 GB box. Serves a single
auto-refreshing HTML page on :8080, reading paper_trades from Postgres and live prices from
the Binance (testnet) public ticker.

It also surfaces the REAL testnet account (wallet balance, unrealized PnL, and recent fills with
per-trade realized PnL + fees) via signed reads, so the strategy's paper book can be compared
against what the exchange actually did. The live panel is skipped if API keys are absent.

Run:  uv run python ops/dashboard.py   (the compose `dash` service does this)
Env:  DATABASE_URL (required), BINANCE_FUTURES_URL (optional), DASH_PORT (default 8080),
      DASH_BIND (default 127.0.0.1; set 0.0.0.0 in Docker), BINANCE_API_KEY / BINANCE_API_SECRET
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import os
import time
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode
from pathlib import Path

import asyncpg

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Best-effort .env load for host-side runs (compose injects env in-container)."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(_REPO_ROOT / ".env")

PORT = int(os.environ.get("DASH_PORT", "8080"))
BIND = os.environ.get("DASH_BIND", "0.0.0.0")  # use 0.0.0.0 in Docker so port publish works
PLAN_LIMIT = int(os.environ.get("DASH_PLAN_LIMIT", "15"))  # how many recent plans to show
# asyncpg wants a plain libpq DSN — strip SQLAlchemy's "+asyncpg" driver suffix.
DSN = os.environ.get("DATABASE_URL", "postgresql://ats:ats@db:5432/ats").replace(
    "postgresql+asyncpg://", "postgresql://"
)
PRICE_BASE = (os.environ.get("BINANCE_FUTURES_URL") or "https://testnet.binancefuture.com/fapi").rstrip("/")
# Signed account reads (real testnet fills/PnL). Absent keys → the live panel is skipped and the
# page renders paper-only. Same env the executor uses (see config.binance_api_key).
API_KEY = os.environ.get("BINANCE_API_KEY") or ""
API_SECRET = os.environ.get("BINANCE_API_SECRET") or ""
LIVE_ENABLED = bool(API_KEY and API_SECRET)
# Recent-log tail. LOG_DIR is where bot-entrypoint.sh's rotatelogs writes ats-current.log /
# ats-YYYY-MM-DD.log; the reader also checks a `logs/` subdir to tolerate either mount layout.
LOG_DIR = os.environ.get("DASH_LOG_DIR", "/app/logs")
LOG_LIMIT = int(os.environ.get("DASH_LOG_LIMIT", "25"))
_LOG_MIN_LEVEL = {"info": 1, "warning": 2, "warn": 2, "error": 3, "critical": 4}  # excludes debug


def _fnum(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _tp_list(v) -> list[float]:
    """Coerce a take_profit value into a list of floats.

    ``paper_trades.take_profit`` / ``setups.take_profit`` are JSONB; asyncpg (no json codec here)
    hands them back as a JSON *string* like ``"[78947.4, 79000.0]"``. The old code iterated that
    string character-by-character, so the column rendered as a stream of single digits. Parse it.
    """
    if v is None:
        return []
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except (ValueError, TypeError):
            return []
    if isinstance(v, (list, tuple)):
        return [_fnum(x) for x in v]
    return [_fnum(v)]


def _latest_log_file() -> "Path | None":
    """Newest daily log: ats-current.log if present, else the latest ats-*.log. Checks LOG_DIR
    and LOG_DIR/logs so it works whether the volume mounts the parent or the logs dir itself."""
    for d in (Path(LOG_DIR), Path(LOG_DIR) / "logs"):
        cur = d / "ats-current.log"
        if cur.is_file():
            return cur
        files = sorted(d.glob("ats-*.log")) if d.is_dir() else []
        if files:
            return files[-1]
    return None


def tail_logs(limit: int = LOG_LIMIT) -> list[dict]:
    """Last ``limit`` JSON log records at INFO or above from the current daily log. Best-effort:
    reads only a bounded tail of the file (logs run to multiple MB) and skips non-JSON lines."""
    path = _latest_log_file()
    if path is None:
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))  # last 256 KB is plenty for 25 lines
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    out: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if _LOG_MIN_LEVEL.get(str(rec.get("level", "")).lower(), 0) >= 1:
            out.append(rec)
    return out[-limit:]


def fetch_price(symbol: str) -> float | None:
    """Last price for one symbol from the public futures ticker (stdlib, ~1 cheap GET)."""
    try:
        url = f"{PRICE_BASE}/v1/ticker/price?symbol={symbol}"
        with urllib.request.urlopen(url, timeout=4) as resp:
            return float(json.loads(resp.read())["price"])
    except Exception:
        return None


def _signed_get(path: str, params: dict | None = None):
    """HMAC-SHA256-signed GET against the futures REST base (stdlib only, no python-binance).

    Returns parsed JSON, or None on any failure / missing keys — the live panel treats None as
    "unavailable" and the page still renders paper-only.
    """
    if not LIVE_ENABLED:
        return None
    q = dict(params or {})
    q["recvWindow"] = 5000
    q["timestamp"] = int(time.time() * 1000)
    query = urlencode(q)
    sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{PRICE_BASE}{path}?{query}&signature={sig}"
    try:
        req = urllib.request.Request(url, headers={"X-MBX-APIKEY": API_KEY})
        with urllib.request.urlopen(req, timeout=6) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def fetch_live_account() -> dict | None:
    """Real testnet wallet balance + unrealized PnL from the signed /v2/account endpoint."""
    acct = _signed_get("/v2/account")
    if not isinstance(acct, dict):
        return None
    return {
        "wallet": _fnum(acct.get("totalWalletBalance")),
        "unrealized": _fnum(acct.get("totalUnrealizedProfit")),
        "available": _fnum(acct.get("availableBalance")),
    }


def fetch_user_trades(symbol: str, limit: int = 50) -> list:
    """Recent real fills for one symbol (signed /v1/userTrades). Empty list on failure."""
    rows = _signed_get("/v1/userTrades", {"symbol": symbol, "limit": limit})
    return rows if isinstance(rows, list) else []


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
        tps = _tp_list(r["take_profit"])
        tp_str = ", ".join(f"{x:g}" for x in tps) if tps else "—"
        lev = _fnum(r["leverage"])
        lev_str = f"{lev:g}x" if lev > 0 else "—"
        open_html += (
            f"<tr><td>{html.escape(r['symbol'])}</td>"
            f"<td class={'pos' if r['direction']=='long' else 'neg'}>{r['direction']}</td>"
            f"<td>{_fnum(r['entry_price']):g}</td>"
            f"<td>{px if px is not None else '—'}</td>"
            f"<td>{_fnum(r['stop_loss']):g}</td><td>{tp_str}</td>"
            f"<td>{_fnum(r['notional_usd']):,.0f}</td>"
            f"<td class=dim>{lev_str}</td>"
            f"<td>{money(u)}</td>"
            f"<td class=dim>{html.escape(str(r['entry_order_id'] or ''))}</td></tr>"
        )
    if not open_html:
        open_html = "<tr><td colspan=10 class=dim>no open positions</td></tr>"

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
            tps = ", ".join(f"{x:g}" for x in _tp_list(s0["take_profit"]))
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

    # Recent logs (INFO+). Event + timestamp are rendered as columns; the remaining structlog
    # context keys are flattened into a compact key=val string (capped so the row stays readable).
    logs_html = ""
    for rec in reversed(tail_logs()):  # newest first
        ts = str(rec.get("timestamp", ""))[11:19]  # HH:MM:SS out of the ISO timestamp
        lvl = str(rec.get("level", "")).upper()
        lvl_cls = {"ERROR": "neg", "CRITICAL": "neg", "WARNING": "warn"}.get(lvl, "dim")
        ctx = " ".join(
            f"{k}={v}" for k, v in rec.items() if k not in ("timestamp", "level", "event")
        )
        logs_html += (
            f"<tr><td class=dim>{html.escape(ts)}</td>"
            f"<td class={lvl_cls}>{html.escape(lvl)}</td>"
            f"<td>{html.escape(str(rec.get('event', '')))}</td>"
            f"<td class=dim>{html.escape(ctx[:160])}</td></tr>"
        )
    if not logs_html:
        logs_html = "<tr><td colspan=4 class=dim>no logs found</td></tr>"

    # LIVE panel — real testnet account + fills (signed reads). Symbols come from paper_trades,
    # so we only query userTrades for symbols the strategy actually touched. Skipped without keys.
    live_block = ""
    if LIVE_ENABLED:
        live = fetch_live_account()
        live_symbols = {r["symbol"] for r in data["open"]} | {r["symbol"] for r in data["closed"]}
        fills = []
        for s in sorted(live_symbols):
            fills.extend(fetch_user_trades(s))
        real_realized = sum(_fnum(t.get("realizedPnl")) for t in fills)
        real_fees = sum(_fnum(t.get("commission")) for t in fills)
        fills.sort(key=lambda t: _fnum(t.get("time")), reverse=True)

        fills_html = ""
        for t in fills[:50]:
            ts = _fnum(t.get("time"))
            when = datetime.fromtimestamp(ts / 1000, UTC).strftime("%m-%d %H:%M") if ts else "—"
            side = t.get("side", "")
            fills_html += (
                f"<tr><td class=dim>{when}</td><td>{html.escape(t.get('symbol', ''))}</td>"
                f"<td class={'pos' if side == 'BUY' else 'neg'}>{html.escape(side)}</td>"
                f"<td>{_fnum(t.get('price')):g}</td>"
                f"<td>{_fnum(t.get('qty')):g}</td>"
                f"<td>{money(_fnum(t.get('realizedPnl')))}</td>"
                f"<td class=dim>{_fnum(t.get('commission')):.4f} {html.escape(t.get('commissionAsset', ''))}</td></tr>"
            )
        if not fills_html:
            fills_html = "<tr><td colspan=7 class=dim>no real fills yet</td></tr>"

        wallet = f"{live['wallet']:,.2f}" if live else "<span class=dim>—</span>"
        live_block = f"""<h2>LIVE — REAL BINANCE ACCOUNT (testnet)</h2>
<div class=cards>
 <div class=card><div class=l>Wallet balance</div><div class=v>{wallet}</div></div>
 <div class=card><div class=l>Real unrealized</div><div class=v>{money(live['unrealized'] if live else None)}</div></div>
 <div class=card><div class=l>Realized (recent fills)</div><div class=v>{money(real_realized)}</div></div>
 <div class=card><div class=l>Fees (recent)</div><div class=v>{money(-real_fees) if real_fees else money(0.0)}</div></div>
</div>
<table><tr><th>time</th><th>symbol</th><th>side</th><th>price</th><th>qty</th><th>realized PnL</th>
<th>fee</th></tr>{fills_html}</table>"""

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
 .pos{{color:#34d399}} .neg{{color:#f87171}} .dim{{color:#6b7280}} .warn{{color:#fbbf24}}
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
{live_block}
<h2>OPEN POSITIONS</h2>
<table><tr><th>symbol</th><th>dir</th><th>entry</th><th>last</th><th>stop</th><th>take-profit</th>
<th>notional</th><th>lev</th><th>uPnL</th><th>entry order</th></tr>{open_html}</table>
<h2>RECENT PLANS (last {PLAN_LIMIT})</h2>
<table><tr><th>bar (as_of)</th><th>symbol</th><th>bias</th><th>regime</th><th>status</th>
<th>setup</th><th>rationale</th></tr>{plan_html}</table>
<h2>RECENT CLOSED (last 50)</h2>
<table><tr><th>exit</th><th>symbol</th><th>dir</th><th>entry → exit</th><th>reason</th>
<th>PnL $</th><th>PnL %</th></tr>{closed_html}</table>
<h2>RECENT LOGS (last {LOG_LIMIT}, INFO+)</h2>
<table><tr><th>time</th><th>level</th><th>event</th><th>context</th></tr>{logs_html}</table>
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
    print(f"[dashboard] serving on {BIND}:{PORT}  (DSN host={DSN.split('@')[-1]})", flush=True)
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()

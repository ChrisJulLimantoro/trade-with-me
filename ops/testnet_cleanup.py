"""One-shot testnet clean slate for a symbol: cancel ALL resting orders, flatten the position.

The balance itself cannot be reset; this only guarantees no stale orders (regular limits AND
conditional/algo SL-TP, which /fapi/v1/allOpenOrders does NOT touch) and a flat position, so a
fresh engine run starts from nothing armed on the exchange.

Usage:  uv run python ops/testnet_cleanup.py [SYMBOL ...]   (default: SOLUSDT)
"""

from __future__ import annotations

import asyncio
import os
import sys

from binance import AsyncClient
from dotenv import load_dotenv


async def cleanup(client: AsyncClient, symbol: str) -> None:
    # 1. Conditional/algo orders (STOP_MARKET / TAKE_PROFIT_MARKET protection).
    try:
        algos = await client.futures_get_open_algo_orders(symbol=symbol)
    except Exception as exc:
        print(f"[{symbol}] algo-order list failed: {exc}")
        algos = []
    for o in algos or []:
        algo_id = o.get("algoId") or o.get("orderId")
        try:
            await client.futures_cancel_algo_order(symbol=symbol, algoId=algo_id)
            print(f"[{symbol}] cancelled algo order {algo_id} ({o.get('orderType')})")
        except Exception as exc:
            print(f"[{symbol}] algo cancel {algo_id} failed (may be gone): {exc}")

    # 2. Regular open orders (resting LIMIT entries).
    try:
        open_orders = await client.futures_get_open_orders(symbol=symbol)
        if open_orders:
            await client.futures_cancel_all_open_orders(symbol=symbol)
            print(f"[{symbol}] cancelled {len(open_orders)} regular open order(s)")
        else:
            print(f"[{symbol}] no regular open orders")
    except Exception as exc:
        print(f"[{symbol}] regular-order cancel failed: {exc}")

    # 3. Flatten any position (reduce-only market).
    try:
        positions = await client.futures_position_information(symbol=symbol)
    except Exception as exc:
        print(f"[{symbol}] position query failed: {exc}")
        return
    for p in positions or []:
        amt = float(p.get("positionAmt") or 0.0)
        if amt == 0.0:
            continue
        side = "SELL" if amt > 0 else "BUY"
        try:
            await client.futures_create_order(
                symbol=symbol, side=side, type="MARKET",
                quantity=abs(amt), reduceOnly="true",
            )
            print(f"[{symbol}] flattened position {amt} via {side} MARKET")
        except Exception as exc:
            print(f"[{symbol}] flatten failed: {exc}")
    print(f"[{symbol}] done")


async def main() -> None:
    load_dotenv()
    key = os.environ["BINANCE_API_KEY"]
    secret = os.environ["BINANCE_API_SECRET"]
    symbols = sys.argv[1:] or ["SOLUSDT"]
    client = await AsyncClient.create(key, secret, testnet=True)
    url = os.environ.get("BINANCE_FUTURES_URL")
    if url:
        client.FUTURES_URL = url.rstrip("/")
    try:
        for sym in symbols:
            await cleanup(client, sym)
        acct = await client.futures_account_balance()
        usdt = next((b for b in acct if b.get("asset") == "USDT"), {})
        print(f"wallet USDT balance: {usdt.get('balance')} (available: {usdt.get('availableBalance')})")
    finally:
        await client.close_connection()


if __name__ == "__main__":
    asyncio.run(main())

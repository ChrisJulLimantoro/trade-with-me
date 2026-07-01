#!/usr/bin/env bash
# 24/7 live-testnet bot entrypoint.
#
# 1. migrate the DB (idempotent), 2. warm up indicators (7d backfill + feature compute),
# 3. run the live engine, streaming ALL output through `rotatelogs` so we get one plain-text
#    log file PER DAY under /app/logs (mounted to ./logs on the host), plus a stable
#    `ats-current.log` symlink and the container's own `docker logs` (via rotatelogs -e).
#
# The engine ALSO writes its structured trace + order logs into /app/logs:
#   - logs/live_<sym>_<tf>_<stamp>.log    (human trace: plans / entries / exits)
#   - logs/live_orders_<sym>_<tf>_<stamp>.jsonl  (every order / SL / TP / fill)
#
# Tunables (env, with defaults) — set in the compose `bot` service or .env:
set -euo pipefail

SYMBOL="${SYMBOL:-SOLUSDT}"
TIMEFRAME="${TIMEFRAME:-15m}"
PROFILE="${PROFILE:-scalper}"
EQUITY="${EQUITY:-5000}"
FEED="${FEED:-ws}"
RECONCILE="${RECONCILE:-close}"

LOG_DIR="${LOG_DIR:-/app/logs}"
mkdir -p "$LOG_DIR"

echo "[entrypoint] $(date -u +%FT%TZ) migrate + warmup for ${SYMBOL} ${TIMEFRAME}"
uv run ats db migrate
# 14d warmup so higher-timeframe context (4h EMA50 needs ~8+ days) is populated — fixes the
# htf_context_missing warnings. Upserts persist, so the per-tick 2d refresh never erases it.
uv run ats ingest backfill --since 14d --symbols "$SYMBOL"
uv run ats process run

echo "[entrypoint] $(date -u +%FT%TZ) starting live engine (feed=${FEED} profile=${PROFILE} equity=${EQUITY})"

# All stdout+stderr -> daily file ats-YYYY-MM-DD.log (rotates at 00:00 UTC), with a
# `ats-current.log` symlink, and -e echoes to stdout so `docker logs`/journald still work.
exec uv run ats engine run \
  --symbol "$SYMBOL" --timeframe "$TIMEFRAME" \
  --profile "$PROFILE" --equity "$EQUITY" \
  --feed "$FEED" --live-execute --reconcile-on-start "$RECONCILE" \
  2>&1 | rotatelogs -e -L "${LOG_DIR}/ats-current.log" "${LOG_DIR}/ats-%Y-%m-%d.log" 86400

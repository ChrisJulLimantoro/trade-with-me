#!/usr/bin/env bash
# One-off historical backfill + feature compute into the RUNNING stack's DB, without touching
# the live bot. Spins an ephemeral container (same image/env) that writes to the shared db,
# then exits. The running bot picks up the deeper history on its next tick (it reads from db).
#
# Usage:  ./ops/backfill.sh [SYMBOL] [SINCE]
#   ./ops/backfill.sh                 # SOLUSDT, 180d (defaults)
#   ./ops/backfill.sh ETHUSDT 180d
#
# NOTE (1 GB / t3.micro): 180d feature compute is RAM-heavy. Make sure swap is on. If the box
# OOMs (dmesg | grep -i oom), stop the bot first (docker compose ... stop bot), run this, then
# start it again — or resize to t3.small.
set -euo pipefail

SYMBOL="${1:-SOLUSDT}"
SINCE="${2:-180d}"
COMPOSE="docker compose -f ops/docker-compose.micro.yml"

echo ">> backfilling ${SYMBOL} --since ${SINCE} (ephemeral; live bot keeps running)"
# --rm: throwaway  --no-deps: db already up  --entrypoint "": skip bot-entrypoint.sh (loop)
$COMPOSE run --rm --no-deps --entrypoint "" bot \
  sh -c "uv run ats ingest backfill --since ${SINCE} --symbols ${SYMBOL} && uv run ats process run"

echo ">> done. coverage now:"
$COMPOSE exec bot uv run ats data summary --symbol "${SYMBOL}"

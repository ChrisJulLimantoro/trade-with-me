# Deploy: 24/7 Binance **Testnet** Live Bot (Docker)

Run the trading engine against your Binance Futures **testnet** account, 24/7, in Docker — with
a Postgres/TimescaleDB and per-day log files. Tuned to fit a free-tier **AWS t3.micro** (1 GB RAM).

> **Testnet only.** This places real orders on `testnet.binancefuture.com` (fake money). It hard-
> refuses mainnet (`BINANCE_TESTNET` must be `true`) and refuses to start unless the config is
> deterministic (no LLM). It is paper-only by default; live orders require the `--live-execute`
> path baked into the bot image.

---

## What runs

`docker compose -f ops/docker-compose.micro.yml up -d` starts two services:

| Service | What it is |
|---------|-----------|
| `db`  | TimescaleDB (Postgres 16) + pgvector, memory-capped for 1 GB boxes, data in a named volume |
| `bot` | The engine: migrates the DB → 7-day warmup → runs the live loop, writing per-day logs |

On entry the bot fires a market order **plus** a `STOP_MARKET` (SL) and `TAKE_PROFIT_MARKET` (TP)
on the exchange (the futures-OCO equivalent); the deterministic exit machine moves/cancels them.

---

## Prerequisites

- An **AWS EC2 t3.micro** (or any Linux box), Ubuntu 24.04, **x86_64** (the Timescale image is
  x86; don't use ARM/t4g), 20 GB disk. Security group: SSH from your IP, outbound open.
- A **Binance Futures testnet** account: log in at <https://testnet.binancefuture.com>, open the
  **API Key** tab, generate a key + secret, and fund the demo wallet.

---

## Setup (after cloning)

### 1. Box prep — Docker + swap
```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker
sudo systemctl enable docker

# 4 GB swap — t3.micro has only 1 GB RAM; needed for the first image build
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 2. Clone + checkout
```bash
git clone https://github.com/ChrisJulLimantoro/trade-with-me.git agent-orchestration
cd agent-orchestration
git checkout f/testnet-live-execution
```

### 3. Create `.env` (testnet keys)
```bash
cat > .env <<'EOF'
DATABASE_URL=postgresql+asyncpg://ats:ats@localhost:5435/ats
LLM_MOCK=true
BINANCE_API_KEY=YOUR_TESTNET_KEY
BINANCE_API_SECRET=YOUR_TESTNET_SECRET
BINANCE_TESTNET=true
# Only if orders 404 / error -4120 — point at the newer demo host:
# BINANCE_FUTURES_URL=https://demo-fapi.binance.com/fapi
EOF
nano .env   # paste your real key/secret
```
> The `bot` container overrides `DATABASE_URL` to `db:5432` internally. The `localhost:5435`
> value is only used if you run host-side commands; leave it as-is. **Never commit `.env`.**

### 4. Logs folder + launch
```bash
mkdir -p logs
docker compose -f ops/docker-compose.micro.yml up -d --build
```
First build takes a few minutes on micro (swap covers the RAM).

---

## Configure the bot

Edit the `environment:` block of the `bot` service in `ops/docker-compose.micro.yml`, then
`docker compose -f ops/docker-compose.micro.yml up -d`:

| Var | Default | Meaning |
|-----|---------|---------|
| `SYMBOL` | `SOLUSDT` | Trading pair |
| `TIMEFRAME` | `15m` | Decision timeframe |
| `PROFILE` | `scalper` | Strategy profile (`baseline` \| `scalper`) |
| `EQUITY` | `5000` | **Sizing equity in USD — set to your testnet wallet balance** |
| `FEED` | `ws` | `ws` (event-driven on bar close) or `poll` |
| `RECONCILE` | `close` | Orphan position on startup: `close` \| `adopt` \| `warn` |

One symbol per container. To trade several, duplicate the `bot` service under different names.

---

## Monitor

```bash
docker compose -f ops/docker-compose.micro.yml ps           # services healthy?
docker compose -f ops/docker-compose.micro.yml logs -f bot  # startup: migrate, warmup, banner
```

Logs land in `./logs/` on the host:

| File | Contents |
|------|----------|
| `ats-YYYY-MM-DD.log` | **One plain-text log per day** (rotates 00:00 UTC) — full engine output |
| `ats-current.log` | Symlink to today's file |
| `live_<sym>_<tf>_*.log` | Human trace: plans / entries / exits |
| `live_orders_<sym>_<tf>_*.jsonl` | Every order / SL / TP / fill |

```bash
tail -f logs/ats-current.log
tail -f logs/live_orders_SOLUSDT_15m_*.jsonl
```
Cross-check positions/orders on <https://testnet.binancefuture.com>.

---

## Dashboard

A lightweight read-only web dashboard (`dash` service) starts with the same `up -d`. It shows
open positions, realized + **live unrealized PnL**, win rate, and the last 50 closed trades,
auto-refreshing every 10s. No extra dependencies (stdlib HTTP server + asyncpg).

Open `http://<EC2_PUBLIC_IP>:8080`.

> **Security:** the dashboard has no auth. Open port **8080** in the EC2 security group to **your
> IP only**. (Or skip exposing it and use an SSH tunnel: `ssh -L 8080:localhost:8080 ubuntu@<IP>`.)

## Manage

```bash
# update code
git pull && docker compose -f ops/docker-compose.micro.yml up -d --build

# stop / start just the bot (DB keeps running)
docker compose -f ops/docker-compose.micro.yml stop bot
docker compose -f ops/docker-compose.micro.yml start bot

# full stop
docker compose -f ops/docker-compose.micro.yml down        # add -v to also wipe the DB volume
```

`restart: unless-stopped` on both services means the stack auto-recovers from crashes and reboots
— no systemd unit needed. On startup the bot reconciles against the exchange (`RECONCILE`), so a
restart never leaves a stale/orphan position.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Bot exits / restarts, `dmesg \| grep -i oom` shows kills | 1 GB ceiling — resize to **t3.small** (stop instance → change type → start) |
| Order error `-4120` or HTTP 404 on order placement | Set `BINANCE_FUTURES_URL=https://demo-fapi.binance.com/fapi` in `.env`, then `up -d` |
| Auth error `-2015` / signature invalid | Wrong key/secret, or the testnet key has an IP restriction |
| `Refusing live execution: ... LLM` | Ensure `LLM_MOCK=true` in `.env` (the live path is deterministic, no LLM) |
| `db` unhealthy | `docker compose -f ops/docker-compose.micro.yml logs db` — usually a stale volume; `down -v` then `up` |

---

## Files in this folder

- `docker-compose.micro.yml` — slim, memory-capped stack (DB + bot) for 1 GB boxes
- `docker-compose.yml` — the original DB-only compose (heavier `-ha` image, for dev)
- `bot-entrypoint.sh` — migrate → warmup → run with per-day log rotation
- `init.sql` — enables the `timescaledb` + `vector` extensions

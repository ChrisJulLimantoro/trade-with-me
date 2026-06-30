# Bot image for 24/7 live testnet execution (see ops/docker-compose.micro.yml).
# Slim Python + uv. apache2-utils provides `rotatelogs` for per-day log files.
FROM python:3.12-slim

# rotatelogs (apache2-utils) for daily log rotation; curl/ca-certs for healthchecks + TLS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends apache2-utils curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uv (pinned binary from the official image).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC

WORKDIR /app

# Install deps first (cached layer) using only the lock + manifest.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Project source.
COPY . .
RUN uv sync --frozen

RUN chmod +x ops/bot-entrypoint.sh
ENTRYPOINT ["ops/bot-entrypoint.sh"]

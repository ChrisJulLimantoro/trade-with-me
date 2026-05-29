"""Initial schema — spec 01 tables + hypertables.

Revision ID: 0001
Revises:
Create Date: 2026-05-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "candles",
        sa.Column("symbol", sa.String, primary_key=True, nullable=False),
        sa.Column("timeframe", sa.String, primary_key=True, nullable=False),
        sa.Column("open_time", sa.TIMESTAMP(timezone=True), primary_key=True, nullable=False),
        sa.Column("open", sa.Numeric, nullable=False),
        sa.Column("high", sa.Numeric, nullable=False),
        sa.Column("low", sa.Numeric, nullable=False),
        sa.Column("close", sa.Numeric, nullable=False),
        sa.Column("volume", sa.Numeric, nullable=False),
        sa.Column("quote_volume", sa.Numeric, nullable=False),
        sa.Column("taker_buy_vol", sa.Numeric, nullable=False),
        sa.Column("trades", sa.Integer, nullable=True),
        sa.Column(
            "ingested_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.PrimaryKeyConstraint("symbol", "timeframe", "open_time"),
    )
    op.execute("SELECT create_hypertable('candles', 'open_time', if_not_exists => TRUE)")

    op.create_table(
        "funding_rates",
        sa.Column("symbol", sa.String, primary_key=True, nullable=False),
        sa.Column("funding_time", sa.TIMESTAMP(timezone=True), primary_key=True, nullable=False),
        sa.Column("rate", sa.Numeric, nullable=False),
        sa.Column(
            "ingested_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.PrimaryKeyConstraint("symbol", "funding_time"),
    )

    op.create_table(
        "open_interest",
        sa.Column("symbol", sa.String, primary_key=True, nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), primary_key=True, nullable=False),
        sa.Column("oi", sa.Numeric, nullable=False),
        sa.Column("oi_value", sa.Numeric, nullable=True),
        sa.Column(
            "ingested_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.PrimaryKeyConstraint("symbol", "ts"),
    )
    op.execute("SELECT create_hypertable('open_interest', 'ts', if_not_exists => TRUE)")

    op.create_table(
        "funding_rates_xvenue",
        sa.Column("exchange", sa.String, primary_key=True, nullable=False),
        sa.Column("symbol", sa.String, primary_key=True, nullable=False),
        sa.Column("funding_time", sa.TIMESTAMP(timezone=True), primary_key=True, nullable=False),
        sa.Column("rate", sa.Numeric, nullable=False),
        sa.Column(
            "ingested_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.PrimaryKeyConstraint("exchange", "symbol", "funding_time"),
    )
    op.create_index("idx_xvenue_funding_lookup", "funding_rates_xvenue", ["symbol", "funding_time"])

    op.create_table(
        "basis",
        sa.Column("symbol", sa.String, primary_key=True, nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), primary_key=True, nullable=False),
        sa.Column("mark_price", sa.Numeric, nullable=False),
        sa.Column("index_price", sa.Numeric, nullable=False),
        sa.Column("premium_index", sa.Numeric, nullable=False),
        sa.Column(
            "ingested_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.PrimaryKeyConstraint("symbol", "ts"),
    )
    op.execute("SELECT create_hypertable('basis', 'ts', if_not_exists => TRUE)")

    op.create_table(
        "liquidations",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String, nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("side", sa.String, nullable=False),
        sa.Column("price", sa.Numeric, nullable=False),
        sa.Column("qty", sa.Numeric, nullable=False),
        sa.Column("notional", sa.Numeric, nullable=False),
    )
    op.create_index("idx_liq_symbol_ts", "liquidations", ["symbol", "ts"])

    op.create_table(
        "mark_prices_1m",
        sa.Column("symbol", sa.String, primary_key=True, nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), primary_key=True, nullable=False),
        sa.Column("mark_open", sa.Numeric, nullable=False),
        sa.Column("mark_close", sa.Numeric, nullable=False),
        sa.Column("mark_high", sa.Numeric, nullable=False),
        sa.Column("mark_low", sa.Numeric, nullable=False),
        sa.Column("samples", sa.Integer, nullable=False),
        sa.PrimaryKeyConstraint("symbol", "ts"),
    )
    op.execute("SELECT create_hypertable('mark_prices_1m', 'ts', if_not_exists => TRUE)")

    op.create_table(
        "media_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("source_id", sa.String, nullable=False),
        sa.Column("account", sa.String, nullable=False),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("url", sa.Text, nullable=True),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("raw", postgresql.JSONB, nullable=True),
        sa.Column(
            "ingested_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("source", "source_id", name="uq_media_source_id"),
    )
    op.create_index("idx_media_published", "media_items", ["published_at"])

    op.create_table(
        "heartbeats",
        sa.Column("data_class", sa.String, primary_key=True),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("heartbeats")
    op.drop_index("idx_media_published", table_name="media_items")
    op.drop_table("media_items")
    op.drop_table("mark_prices_1m")
    op.drop_index("idx_liq_symbol_ts", table_name="liquidations")
    op.drop_table("liquidations")
    op.drop_table("basis")
    op.drop_index("idx_xvenue_funding_lookup", table_name="funding_rates_xvenue")
    op.drop_table("funding_rates_xvenue")
    op.drop_table("open_interest")
    op.drop_table("funding_rates")
    op.drop_table("candles")

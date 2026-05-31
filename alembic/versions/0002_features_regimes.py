"""Spec 02 — features (hypertable) + regimes tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "features",
        sa.Column("symbol", sa.String, nullable=False),
        sa.Column("timeframe", sa.String, nullable=False),
        sa.Column("open_time", sa.TIMESTAMP(timezone=True), nullable=False),
        # raw indicators
        sa.Column("atr_14", sa.Numeric, nullable=True),
        sa.Column("atr_pct", sa.Numeric, nullable=True),
        sa.Column("rsi_14", sa.Numeric, nullable=True),
        sa.Column("ema_20", sa.Numeric, nullable=True),
        sa.Column("ema_50", sa.Numeric, nullable=True),
        sa.Column("ema_200", sa.Numeric, nullable=True),
        sa.Column("macd", sa.Numeric, nullable=True),
        sa.Column("macd_signal", sa.Numeric, nullable=True),
        sa.Column("macd_hist", sa.Numeric, nullable=True),
        sa.Column("obv", sa.Numeric, nullable=True),
        sa.Column("vol_zscore_20", sa.Numeric, nullable=True),
        sa.Column("oi_delta_pct_1h", sa.Numeric, nullable=True),
        sa.Column("oi_delta_pct_24h", sa.Numeric, nullable=True),
        sa.Column("funding_rate", sa.Numeric, nullable=True),
        sa.Column("funding_z_30d", sa.Numeric, nullable=True),
        sa.Column("realized_vol_30d", sa.Numeric, nullable=True),
        # order-flow (CVD)
        sa.Column("cvd_30", sa.Numeric, nullable=True),
        sa.Column("cvd_slope_10", sa.Numeric, nullable=True),
        # cross-venue funding
        sa.Column("funding_divergence", sa.Numeric, nullable=True),
        sa.Column("funding_divergence_z_30d", sa.Numeric, nullable=True),
        sa.Column("funding_peer_count", sa.Integer, nullable=True),
        # perp basis
        sa.Column("basis_premium", sa.Numeric, nullable=True),
        sa.Column("basis_z_30d", sa.Numeric, nullable=True),
        # composite
        sa.Column("momentum_composite", sa.Numeric, nullable=True),
        # percentile ranks
        sa.Column("pr_atr", sa.Numeric, nullable=True),
        sa.Column("pr_rsi", sa.Numeric, nullable=True),
        sa.Column("pr_vol_zscore", sa.Numeric, nullable=True),
        sa.Column("pr_oi_delta", sa.Numeric, nullable=True),
        sa.Column("pr_funding_imbalance", sa.Numeric, nullable=True),
        sa.Column("pr_momentum", sa.Numeric, nullable=True),
        sa.Column("pr_cvd_divergence", sa.Numeric, nullable=True),
        # metadata
        sa.Column(
            "computed_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("feature_version", sa.Integer, nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("symbol", "timeframe", "open_time"),
    )
    op.execute(
        "SELECT create_hypertable('features', 'open_time', if_not_exists => TRUE)"
    )

    op.create_table(
        "regimes",
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("btc_ema_slope_30d", sa.Numeric, nullable=False),
        sa.Column("btc_realized_vol_30d", sa.Numeric, nullable=False),
        sa.Column("vol_percentile_180d", sa.Numeric, nullable=False),
        sa.Column("trend", sa.String, nullable=False),
        sa.Column("volatility", sa.String, nullable=False),
        sa.Column("regime_cell", sa.String, nullable=False),
        sa.Column(
            "computed_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.PrimaryKeyConstraint("ts"),
    )
    # regimes is NOT a hypertable (1 row/hour, tiny)


def downgrade() -> None:
    op.drop_table("regimes")
    op.drop_table("features")

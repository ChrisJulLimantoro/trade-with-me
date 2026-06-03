"""Leverage + per-trade risk on paper_trades.

``size_pct`` already meant notional / equity (PnL is booked on notional), so it now carries
leverage directly. These columns make leverage, the liquidation price, and the dollars at
risk first-class for querying/display.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paper_trades", sa.Column("leverage", sa.Numeric, nullable=True))
    op.add_column("paper_trades", sa.Column("liq_price", sa.Numeric, nullable=True))
    op.add_column("paper_trades", sa.Column("risk_usd", sa.Numeric, nullable=True))


def downgrade() -> None:
    op.drop_column("paper_trades", "risk_usd")
    op.drop_column("paper_trades", "liq_price")
    op.drop_column("paper_trades", "leverage")

"""Venue + exchange order ids on paper_trades (Binance testnet live execution).

Real testnet fills reuse the paper_trades table: venue distinguishes them from simulated
rows (default 'paper'), and the order-id columns link a row to its exchange orders. Legacy
and paper rows keep venue='paper' and NULL order ids, so analytics/trace are unchanged.

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "paper_trades",
        sa.Column("venue", sa.String, nullable=False, server_default="paper"),
    )
    op.add_column("paper_trades", sa.Column("entry_order_id", sa.String, nullable=True))
    op.add_column("paper_trades", sa.Column("exit_order_id", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("paper_trades", "exit_order_id")
    op.drop_column("paper_trades", "entry_order_id")
    op.drop_column("paper_trades", "venue")

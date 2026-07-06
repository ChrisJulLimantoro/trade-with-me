"""Real-fill reconciliation columns on paper_trades (live↔sim gap closing).

Adds a "reality track" alongside the simulated entry_price/exit_price/pnl_usd: the price the
exchange actually filled the entry/exit at, plus the commission paid and realized PnL booked by
the exchange for the trade. All nullable — paper/legacy rows leave them NULL and are unchanged.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paper_trades", sa.Column("entry_fill_price", sa.Numeric, nullable=True))
    op.add_column("paper_trades", sa.Column("exit_fill_price", sa.Numeric, nullable=True))
    op.add_column("paper_trades", sa.Column("commission_usd", sa.Numeric, nullable=True))
    op.add_column("paper_trades", sa.Column("realized_pnl_usd", sa.Numeric, nullable=True))


def downgrade() -> None:
    op.drop_column("paper_trades", "realized_pnl_usd")
    op.drop_column("paper_trades", "commission_usd")
    op.drop_column("paper_trades", "exit_fill_price")
    op.drop_column("paper_trades", "entry_fill_price")

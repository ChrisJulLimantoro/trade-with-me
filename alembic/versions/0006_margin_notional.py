"""Margin and notional on paper_trades.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paper_trades", sa.Column("margin_usd", sa.Numeric, nullable=True))
    op.add_column("paper_trades", sa.Column("notional_usd", sa.Numeric, nullable=True))


def downgrade() -> None:
    op.drop_column("paper_trades", "notional_usd")
    op.drop_column("paper_trades", "margin_usd")

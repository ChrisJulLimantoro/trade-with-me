"""Run tagging on paper_trades (run_id / run_label / config_hash).

Lets replay runs be isolated and A/B-compared. Legacy and live rows stay NULL.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paper_trades", sa.Column("run_id", sa.String, nullable=True))
    op.add_column("paper_trades", sa.Column("run_label", sa.String, nullable=True))
    op.add_column("paper_trades", sa.Column("config_hash", sa.String, nullable=True))
    op.create_index("idx_paper_trades_run", "paper_trades", ["run_id"])


def downgrade() -> None:
    op.drop_index("idx_paper_trades_run", table_name="paper_trades")
    op.drop_column("paper_trades", "config_hash")
    op.drop_column("paper_trades", "run_label")
    op.drop_column("paper_trades", "run_id")

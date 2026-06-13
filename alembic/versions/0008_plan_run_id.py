"""Add run_id to plans for concurrent same-symbol replay isolation.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("run_id", sa.String, nullable=True))
    op.create_index("idx_plans_run_id", "plans", ["run_id"])


def downgrade() -> None:
    op.drop_index("idx_plans_run_id", table_name="plans")
    op.drop_column("plans", "run_id")

"""Episodic memory — the learnings table (one post-mortem per closed paper trade).

The ``fingerprint`` is a pgvector column (the vector extension is created in 0001); it is
written and queried via raw SQL with ``CAST(:fp AS vector)`` so the codebase needs no
pgvector Python dependency. Cosine distance (``<=>``) over it powers retrieval into
create_plan.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-02
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# Keep in sync with ats.learning.fingerprint.FINGERPRINT_DIM.
_FINGERPRINT_DIM = 12


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE learnings (
            id                    UUID PRIMARY KEY,
            trade_id              UUID NOT NULL REFERENCES paper_trades(trade_id) ON DELETE CASCADE,
            symbol                TEXT NOT NULL,
            direction             TEXT NOT NULL,
            regime_cell           TEXT,
            category              TEXT NOT NULL,
            hypothesis            TEXT NOT NULL,
            evidence              TEXT NOT NULL DEFAULT '',
            proposed_adjustment   TEXT NOT NULL DEFAULT '',
            confidence_in_lesson  NUMERIC NOT NULL DEFAULT 0.5,
            outcome               TEXT NOT NULL,
            pnl_pct               NUMERIC,
            fingerprint           vector({_FINGERPRINT_DIM}) NOT NULL,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.create_index("idx_learnings_direction_created", "learnings", ["direction", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_learnings_direction_created", table_name="learnings")
    op.execute("DROP TABLE IF EXISTS learnings")

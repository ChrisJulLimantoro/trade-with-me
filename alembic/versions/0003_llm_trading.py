"""POC LLM-plan trading layer — plans, setups, paper_trades, llm_calls.

See LLM_BASED_ARCHITECTURE.md. UUID PKs are generated app-side (uuid.uuid4),
matching media_items — no pgcrypto extension required.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("plan_id", UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String, nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("as_of", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("market_bias", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="active"),
        sa.Column("regime_cell", sa.String, nullable=True),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.PrimaryKeyConstraint("plan_id"),
    )
    op.create_index("idx_plans_symbol_status", "plans", ["symbol", "status", "created_at"])

    op.create_table(
        "setups",
        sa.Column("setup_id", UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String, nullable=False),
        sa.Column("direction", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="proposed"),
        sa.Column("entry_zone_low", sa.Numeric, nullable=False),
        sa.Column("entry_zone_high", sa.Numeric, nullable=False),
        sa.Column("take_profit", JSONB, nullable=False),
        sa.Column("stop_loss", sa.Numeric, nullable=False),
        sa.Column("size_pct", sa.Numeric, nullable=False),
        sa.Column("hard_rules", JSONB, nullable=False, server_default="[]"),
        sa.Column("soft_rules", JSONB, nullable=False, server_default="[]"),
        sa.Column("invalidation_rules", JSONB, nullable=False, server_default="[]"),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("detected_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.PrimaryKeyConstraint("setup_id"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.plan_id"], ondelete="CASCADE"),
    )
    op.create_index("idx_setups_plan", "setups", ["plan_id"])
    op.create_index("idx_setups_symbol_status", "setups", ["symbol", "status"])

    op.create_table(
        "paper_trades",
        sa.Column("trade_id", UUID(as_uuid=True), nullable=False),
        sa.Column("setup_id", UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String, nullable=False),
        sa.Column("direction", sa.String, nullable=False),
        sa.Column("size_pct", sa.Numeric, nullable=False),
        sa.Column("entry_price", sa.Numeric, nullable=False),
        sa.Column("entry_time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("stop_loss", sa.Numeric, nullable=False),
        sa.Column("take_profit", JSONB, nullable=False),
        sa.Column("exit_price", sa.Numeric, nullable=True),
        sa.Column("exit_time", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("exit_reason", sa.String, nullable=True),
        sa.Column("pnl_pct", sa.Numeric, nullable=True),
        sa.Column("pnl_usd", sa.Numeric, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="open"),
        sa.Column("reasons", ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
        sa.PrimaryKeyConstraint("trade_id"),
        sa.ForeignKeyConstraint(["setup_id"], ["setups.setup_id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.plan_id"]),
    )
    op.create_index("idx_paper_trades_status", "paper_trades", ["status", "symbol"])

    op.create_table(
        "llm_calls",
        sa.Column("call_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("model", sa.String, nullable=False),
        sa.Column("mock", sa.Boolean, nullable=False),
        sa.Column("symbol", sa.String, nullable=True),
        sa.Column("plan_id", UUID(as_uuid=True), nullable=True),
        sa.Column("setup_id", UUID(as_uuid=True), nullable=True),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric, nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("parse_ok", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("request", JSONB, nullable=True),
        sa.Column("response", JSONB, nullable=True),
        sa.PrimaryKeyConstraint("call_id"),
    )
    op.create_index("idx_llm_calls_kind_time", "llm_calls", ["kind", "created_at"])


def downgrade() -> None:
    op.drop_table("paper_trades")
    op.drop_table("llm_calls")
    op.drop_table("setups")
    op.drop_table("plans")

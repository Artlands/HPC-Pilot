"""audit operation log

Revision ID: 0002_audit_operation_log
Revises: 0001_initial_state_schema
Create Date: 2026-05-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_audit_operation_log"
down_revision = "0001_initial_state_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("agent_run_id", sa.String(length=64), nullable=True),
        sa.Column("tool", sa.String(length=255), nullable=False),
        sa.Column("risk", sa.String(length=32), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("decision", sa.String(length=255), nullable=False),
        sa.Column("diff_summary", sa.Text(), nullable=True),
        sa.Column("result_status", sa.String(length=64), nullable=True),
        sa.Column("config_commit", sa.String(length=128), nullable=True),
        sa.Column("revert_argv", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_actor", "audit_events", ["actor"])
    op.create_index("ix_audit_events_result_status", "audit_events", ["result_status"])
    op.create_index("ix_audit_events_tool", "audit_events", ["tool"])
    op.create_index("ix_audit_events_ts", "audit_events", ["ts"])

    op.create_table(
        "audit_commands",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("audit_id", sa.String(length=64), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("argv", sa.JSON(), nullable=False),
        sa.Column("rc", sa.Integer(), nullable=False),
        sa.Column("duration_s", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["audit_id"], ["audit_events.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_commands_audit_id", "audit_commands", ["audit_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_commands_audit_id", table_name="audit_commands")
    op.drop_table("audit_commands")
    op.drop_index("ix_audit_events_ts", table_name="audit_events")
    op.drop_index("ix_audit_events_tool", table_name="audit_events")
    op.drop_index("ix_audit_events_result_status", table_name="audit_events")
    op.drop_index("ix_audit_events_actor", table_name="audit_events")
    op.drop_table("audit_events")

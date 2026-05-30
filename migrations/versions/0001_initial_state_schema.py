"""initial state schema

Revision ID: 0001_initial_state_schema
Revises:
Create Date: 2026-05-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial_state_schema"
down_revision = None
branch_labels = None
depends_on = None


node_role = sa.Enum(
    "LOGIN",
    "COMPUTE_CPU",
    "COMPUTE_GPU",
    "CONTROLLER",
    name="noderole",
)
node_state = sa.Enum(
    "UNKNOWN",
    "PROVISIONING",
    "UP",
    "DRAINED",
    "DOWN",
    "MAINT",
    name="nodestate",
)
image_status = sa.Enum(
    "BUILDING",
    "READY",
    "FAILED",
    "DEPRECATED",
    name="imagestatus",
)


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("parent", sa.String(length=128), nullable=True),
        sa.Column("organization", sa.String(length=128), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("base_os", sa.String(length=128), nullable=False),
        sa.Column("kind", node_role, nullable=False),
        sa.Column("spec_hash", sa.String(length=64), nullable=False),
        sa.Column("cuda_version", sa.String(length=32), nullable=True),
        sa.Column("driver_version", sa.String(length=32), nullable=True),
        sa.Column("kernel_version", sa.String(length=64), nullable=True),
        sa.Column("status", image_status, nullable=False),
        sa.Column("built_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "partitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("max_time_min", sa.Integer(), nullable=True),
        sa.Column("default_qos", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "qos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("max_wall_min", sa.Integer(), nullable=True),
        sa.Column("max_jobs_pu", sa.Integer(), nullable=True),
        sa.Column("max_tres", sa.String(length=255), nullable=True),
        sa.Column("grp_tres", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "user_assocs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user", sa.String(length=128), nullable=False),
        sa.Column("account", sa.String(length=128), nullable=False),
        sa.Column("qos_list", sa.String(length=512), nullable=False),
        sa.Column("default_qos", sa.String(length=128), nullable=True),
        sa.Column("fairshare", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user", "account", name="uq_user_account"),
    )
    op.create_index("ix_user_assocs_user", "user_assocs", ["user"])
    op.create_table(
        "nodes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("mac", sa.String(length=32), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("role", node_role, nullable=False),
        sa.Column("state", node_state, nullable=False),
        sa.Column("image_id", sa.Integer(), nullable=True),
        sa.Column("profile", sa.String(length=128), nullable=True),
        sa.Column("gpu_count", sa.Integer(), nullable=False),
        sa.Column("gpu_model", sa.String(length=128), nullable=True),
        sa.Column("cpu_count", sa.Integer(), nullable=False),
        sa.Column("mem_mb", sa.Integer(), nullable=False),
        sa.Column("features", sa.String(length=512), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["image_id"], ["images.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hostname"),
    )
    op.create_index("ix_nodes_hostname", "nodes", ["hostname"])
    op.create_table(
        "partition_members",
        sa.Column("partition_id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"]),
        sa.ForeignKeyConstraint(["partition_id"], ["partitions.id"]),
        sa.PrimaryKeyConstraint("partition_id", "node_id"),
    )


def downgrade() -> None:
    op.drop_table("partition_members")
    op.drop_index("ix_nodes_hostname", table_name="nodes")
    op.drop_table("nodes")
    op.drop_index("ix_user_assocs_user", table_name="user_assocs")
    op.drop_table("user_assocs")
    op.drop_table("qos")
    op.drop_table("partitions")
    op.drop_table("images")
    op.drop_table("accounts")

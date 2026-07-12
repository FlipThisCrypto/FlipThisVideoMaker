"""Add persistent worker heartbeat records.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workers",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("instance_id", sa.String(length=36), nullable=False),
        sa.Column("assignment", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("current_job_id", sa.String(length=36), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["current_job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instance_id"),
    )
    op.create_index(op.f("ix_workers_assignment"), "workers", ["assignment"], unique=False)
    op.create_index(
        op.f("ix_workers_last_heartbeat_at"),
        "workers",
        ["last_heartbeat_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_workers_last_heartbeat_at"), table_name="workers")
    op.drop_index(op.f("ix_workers_assignment"), table_name="workers")
    op.drop_table("workers")

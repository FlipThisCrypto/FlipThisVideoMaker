"""Add durable worker ownership leases to Jobs.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("claimed_by_worker_id", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("claimed_by_instance_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("lease_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index(
            op.f("ix_jobs_claimed_by_worker_id"),
            ["claimed_by_worker_id"],
            unique=False,
        )
        batch.create_index(
            op.f("ix_jobs_claimed_by_instance_id"),
            ["claimed_by_instance_id"],
            unique=False,
        )
        batch.create_index(
            op.f("ix_jobs_lease_expires_at"),
            ["lease_expires_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index(op.f("ix_jobs_lease_expires_at"))
        batch.drop_index(op.f("ix_jobs_claimed_by_instance_id"))
        batch.drop_index(op.f("ix_jobs_claimed_by_worker_id"))
        batch.drop_column("lease_expires_at")
        batch.drop_column("lease_heartbeat_at")
        batch.drop_column("claimed_by_instance_id")
        batch.drop_column("claimed_by_worker_id")

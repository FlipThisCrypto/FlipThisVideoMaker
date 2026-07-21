"""Add playback-aware chain replenishment state.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("video_chains") as batch:
        batch.add_column(sa.Column("automation_config", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("replenishment_job_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("playback_position_seconds", sa.Float(), nullable=True))
        batch.add_column(
            sa.Column("playback_updated_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_foreign_key(
            "fk_video_chains_replenishment_job_id_jobs",
            "jobs",
            ["replenishment_job_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint(
            "uq_video_chains_replenishment_job_id", ["replenishment_job_id"]
        )
    op.execute(
        sa.text(
            "UPDATE video_chains SET automation_config = '{}', "
            "playback_position_seconds = 0 WHERE automation_config IS NULL"
        )
    )
    with op.batch_alter_table("video_chains") as batch:
        batch.alter_column("automation_config", existing_type=sa.JSON(), nullable=False)
        batch.alter_column("playback_position_seconds", existing_type=sa.Float(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("video_chains") as batch:
        batch.drop_constraint("uq_video_chains_replenishment_job_id", type_="unique")
        batch.drop_constraint("fk_video_chains_replenishment_job_id_jobs", type_="foreignkey")
        batch.drop_column("playback_updated_at")
        batch.drop_column("playback_position_seconds")
        batch.drop_column("replenishment_job_id")
        batch.drop_column("automation_config")

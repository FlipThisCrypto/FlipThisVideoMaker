"""Add durable first/last-frame video chains.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "video_chains",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("continuation_mode", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("active_lineage_version", sa.Integer(), nullable=False),
        sa.Column("buffer_target_seconds", sa.Float(), nullable=False),
        sa.Column("playlist_asset_id", sa.String(length=36), nullable=True),
        sa.Column("assembled_asset_id", sa.String(length=36), nullable=True),
        sa.Column("stream_state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["assembled_asset_id"], ["assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["playlist_asset_id"], ["assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_video_chains_project_id"), "video_chains", ["project_id"])
    op.create_index(op.f("ix_video_chains_state"), "video_chains", ["state"])
    op.create_table(
        "video_chain_clips",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("chain_id", sa.String(length=36), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("lineage_version", sa.Integer(), nullable=False),
        sa.Column("predecessor_clip_id", sa.String(length=36), nullable=True),
        sa.Column("planned_start_frame_asset_id", sa.String(length=36), nullable=False),
        sa.Column("target_end_frame_asset_id", sa.String(length=36), nullable=False),
        sa.Column("actual_start_frame_asset_id", sa.String(length=36), nullable=True),
        sa.Column("actual_last_frame_asset_id", sa.String(length=36), nullable=True),
        sa.Column("native_video_asset_id", sa.String(length=36), nullable=True),
        sa.Column("delivery_video_asset_id", sa.String(length=36), nullable=True),
        sa.Column("qa_report_asset_id", sa.String(length=36), nullable=True),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("request_snapshot", sa.JSON(), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("result_snapshot", sa.JSON(), nullable=False),
        sa.Column("provider_job_id", sa.String(length=200), nullable=True),
        sa.Column("provider_warnings", sa.JSON(), nullable=False),
        sa.Column("failure_info", sa.JSON(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actual_last_frame_asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["actual_start_frame_asset_id"], ["assets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["chain_id"], ["video_chains.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["delivery_video_asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["native_video_asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["planned_start_frame_asset_id"], ["assets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["predecessor_clip_id"], ["video_chain_clips.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["qa_report_asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_end_frame_asset_id"], ["assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
        sa.UniqueConstraint(
            "chain_id",
            "lineage_version",
            "sequence_number",
            "revision",
            name="uq_chain_clip_position_revision",
        ),
        sa.UniqueConstraint(
            "predecessor_clip_id",
            "lineage_version",
            name="uq_chain_clip_successor_per_lineage",
        ),
    )
    op.create_index(op.f("ix_video_chain_clips_chain_id"), "video_chain_clips", ["chain_id"])
    op.create_index(
        op.f("ix_video_chain_clips_predecessor_clip_id"),
        "video_chain_clips",
        ["predecessor_clip_id"],
    )
    op.create_index(op.f("ix_video_chain_clips_state"), "video_chain_clips", ["state"])


def downgrade() -> None:
    op.drop_index(op.f("ix_video_chain_clips_state"), table_name="video_chain_clips")
    op.drop_index(
        op.f("ix_video_chain_clips_predecessor_clip_id"),
        table_name="video_chain_clips",
    )
    op.drop_index(op.f("ix_video_chain_clips_chain_id"), table_name="video_chain_clips")
    op.drop_table("video_chain_clips")
    op.drop_index(op.f("ix_video_chains_state"), table_name="video_chains")
    op.drop_index(op.f("ix_video_chains_project_id"), table_name="video_chains")
    op.drop_table("video_chains")

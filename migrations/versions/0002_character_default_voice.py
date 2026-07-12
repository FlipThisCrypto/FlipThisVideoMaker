"""Add the default voice-profile reference to characters.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "characters", sa.Column("default_voice_profile_id", sa.String(length=36), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("characters", "default_voice_profile_id")

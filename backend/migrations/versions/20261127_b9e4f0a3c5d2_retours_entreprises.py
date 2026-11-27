"""Retours sur les entreprises (J'aime / Pas intéressé) et embeddings à recalculer (texte entier de l'offre)

Revision ID: b9e4f0a3c5d2
Revises: a8d3e9f2b4c1
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9e4f0a3c5d2"
down_revision: str | None = "a8d3e9f2b4c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sentiment", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "company_id"),
    )
    op.create_index("ix_company_feedback_user_id", "company_feedback", ["user_id"])
    op.create_index("ix_company_feedback_company_id", "company_feedback", ["company_id"])
    # embeddings.py lit désormais tout le texte (par morceaux) : les anciens vecteurs sont recalculés par le worker
    op.execute("UPDATE jobs SET embedded_at = NULL")
    op.execute("UPDATE user_profiles SET embedding_version = NULL")


def downgrade() -> None:
    op.drop_table("company_feedback")

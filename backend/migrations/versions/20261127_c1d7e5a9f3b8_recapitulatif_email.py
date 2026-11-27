"""Récapitulatif des nouvelles offres par e-mail : fréquence, dernier envoi, jeton de désabonnement

Revision ID: c1d7e5a9f3b8
Revises: b9e4f0a3c5d2
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1d7e5a9f3b8"
down_revision: str | None = "b9e4f0a3c5d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("digest_frequency", sa.String(length=10), server_default="weekly",
                                     nullable=False))
    op.add_column("users", sa.Column("digest_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("email_token", sa.String(length=64), nullable=True))
    op.create_unique_constraint("uq_users_email_token", "users", ["email_token"])


def downgrade() -> None:
    op.drop_constraint("uq_users_email_token", "users", type_="unique")
    op.drop_column("users", "email_token")
    op.drop_column("users", "digest_sent_at")
    op.drop_column("users", "digest_frequency")

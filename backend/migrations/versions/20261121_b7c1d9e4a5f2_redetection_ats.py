"""redetection ats

Revision ID: b7c1d9e4a5f2
Revises: 8d2e4b6a1f03
Create Date: 2026-11-21 10:00:00.000000

Migration de données : la détection de l'ATS a été améliorée (nom sans suffixe générique reconnu dans les
offres, liens d'ATS lus dans les pages carrières). Les entreprises sans ATS sont redétectées à la prochaine
collecte, sans attendre la semaine entre deux tentatives.
"""

from collections.abc import Sequence

from alembic import op


revision: str = 'b7c1d9e4a5f2'
down_revision: str | None = '8d2e4b6a1f03'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE companies SET ats_probed_at = NULL WHERE ats IS NULL OR ats_slug IS NULL")


def downgrade() -> None:
    pass  # rien à restaurer : la date de la dernière tentative est recalculée à la prochaine collecte

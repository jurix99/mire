"""pgvector preselection

Revision ID: 8d2e4b6a1f03
Revises: 3f7a1c9e2b54
Create Date: 2026-11-20 12:00:00.000000

Extension vector (pgvector) : image pgvector/pgvector en local, paramètre azure.extensions = VECTOR sur Azure.
384 = dimension du modèle d'embeddings (models.EMBEDDING_DIM).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector


revision: str = '8d2e4b6a1f03'
down_revision: str | None = '3f7a1c9e2b54'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.add_column('jobs', sa.Column('embedding', Vector(384), nullable=True))
    op.add_column('jobs', sa.Column('embedded_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('user_profiles', sa.Column('embedding', Vector(384), nullable=True))
    op.add_column('user_profiles', sa.Column('embedding_version', sa.Integer(), nullable=True))
    op.add_column('job_assessments', sa.Column('similarity', sa.Float(), nullable=True))
    # Recherche des plus proches voisins par distance cosinus (ORDER BY embedding <=> :vecteur)
    op.create_index('ix_jobs_embedding_hnsw', 'jobs', ['embedding'], unique=False, postgresql_using='hnsw',
                    postgresql_ops={'embedding': 'vector_cosine_ops'})


def downgrade() -> None:
    op.drop_index('ix_jobs_embedding_hnsw', table_name='jobs', postgresql_using='hnsw',
                  postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_column('job_assessments', 'similarity')
    op.drop_column('user_profiles', 'embedding_version')
    op.drop_column('user_profiles', 'embedding')
    op.drop_column('jobs', 'embedded_at')
    op.drop_column('jobs', 'embedding')
    # L'extension vector est laissée en place (d'autres objets peuvent en dépendre)

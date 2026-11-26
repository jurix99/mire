"""candidature

Revision ID: a8d3e9f2b4c1
Revises: f7c2d8e1a9b3
Create Date: 2026-11-26 09:10:00.000000

Préparation des candidatures (page Candidature) :
- application_forms : formulaire de candidature lu sur l'ATS, une ligne par offre (partagé) ;
- application_drafts : candidature préparée (réponses, CV adapté, lettre), une ligne par process ;
- generated_files : PDF générés (CV, lettre), exportés et supprimés avec le compte ;
- application_generations : journal des générations Gemini (quotas par jour, tokens) ;
- application_events.snapshot : instantané de ce qui a été envoyé (« J'ai envoyé ma candidature »).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a8d3e9f2b4c1'
down_revision: str | None = 'f7c2d8e1a9b3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    ]


def upgrade() -> None:
    op.create_table('application_forms',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('job_id', sa.Integer(), nullable=False),
                    sa.Column('ats', sa.String(length=30), nullable=True),
                    sa.Column('apply_url', sa.String(length=1000), nullable=True),
                    sa.Column('fields', JSON, nullable=False),
                    sa.Column('source', sa.String(length=20), nullable=False),
                    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
                    sa.Column('error', sa.Text(), nullable=True),
                    *_timestamps(),
                    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
                    sa.PrimaryKeyConstraint('id'),
                    sa.UniqueConstraint('job_id'))

    op.create_table('application_drafts',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('application_id', sa.Integer(), nullable=False),
                    sa.Column('form_snapshot', JSON, nullable=False),
                    sa.Column('answers', JSON, nullable=False),
                    sa.Column('cv', JSON, nullable=True),
                    sa.Column('cv_text', sa.Text(), nullable=True),
                    sa.Column('cover_letter', sa.Text(), nullable=True),
                    sa.Column('want_letter', sa.Boolean(), server_default='false', nullable=False),
                    sa.Column('language', sa.String(length=5), nullable=False),
                    sa.Column('status', sa.String(length=20), nullable=False),
                    sa.Column('warnings', JSON, nullable=False),
                    sa.Column('generated_at', sa.DateTime(timezone=True), nullable=True),
                    sa.Column('model', sa.String(length=60), nullable=True),
                    sa.Column('tokens_in', sa.Integer(), server_default='0', nullable=False),
                    sa.Column('tokens_out', sa.Integer(), server_default='0', nullable=False),
                    sa.Column('error', sa.Text(), nullable=True),
                    *_timestamps(),
                    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
                    sa.PrimaryKeyConstraint('id'),
                    sa.UniqueConstraint('application_id'))

    op.create_table('generated_files',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('user_id', sa.Integer(), nullable=False),
                    sa.Column('application_id', sa.Integer(), nullable=False),
                    sa.Column('kind', sa.String(length=10), nullable=False),
                    sa.Column('filename', sa.String(length=255), nullable=False),
                    sa.Column('content', sa.LargeBinary(), nullable=False),
                    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                              nullable=False),
                    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
                    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
                    sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_generated_files_user_id', 'generated_files', ['user_id'])
    op.create_index('ix_generated_files_application_id', 'generated_files', ['application_id'])

    op.create_table('application_generations',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('user_id', sa.Integer(), nullable=False),
                    sa.Column('application_id', sa.Integer(), nullable=True),
                    sa.Column('kind', sa.String(length=20), nullable=False),
                    sa.Column('model', sa.String(length=60), nullable=True),
                    sa.Column('tokens_in', sa.Integer(), server_default='0', nullable=False),
                    sa.Column('tokens_out', sa.Integer(), server_default='0', nullable=False),
                    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                              nullable=False),
                    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
                    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='SET NULL'),
                    sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_application_generations_user_id', 'application_generations', ['user_id'])
    op.create_index('ix_application_generations_application_id', 'application_generations', ['application_id'])
    op.create_index('ix_application_generations_created_at', 'application_generations', ['created_at'])

    op.add_column('application_events', sa.Column('snapshot', JSON, nullable=True))


def downgrade() -> None:
    op.drop_column('application_events', 'snapshot')
    op.drop_table('application_generations')
    op.drop_table('generated_files')
    op.drop_table('application_drafts')
    op.drop_table('application_forms')

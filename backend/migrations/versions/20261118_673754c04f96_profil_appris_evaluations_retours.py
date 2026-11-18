"""profil appris evaluations retours

Revision ID: 673754c04f96
Revises: b55188a9954b
Create Date: 2026-11-18 20:37:27.368561
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '673754c04f96'
down_revision: str | None = 'b55188a9954b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nouveau statut posé par l'utilisateur (autogenerate ne détecte pas les valeurs d'enum).
    # PostgreSQL >= 12 accepte ADD VALUE dans une transaction (la valeur n'est pas utilisée ici).
    op.execute("ALTER TYPE process_status ADD VALUE IF NOT EXISTS 'pas_interesse' BEFORE 'abandonne'")

    op.create_table('job_assessments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('score', sa.Numeric(precision=4, scale=1), nullable=True),
    sa.Column('criteria', postgresql.JSONB(), nullable=False),
    sa.Column('verdict', sa.String(length=20), nullable=False),
    sa.Column('exclusion', sa.Text(), nullable=True),
    sa.Column('why', sa.Text(), nullable=True),
    sa.Column('skills_have', postgresql.JSONB(), nullable=False),
    sa.Column('skills_missing', postgresql.JSONB(), nullable=False),
    sa.Column('profile_version', sa.Integer(), nullable=False),
    sa.Column('model', sa.String(length=60), nullable=True),
    sa.Column('manual_score', sa.Numeric(precision=4, scale=1), nullable=True),
    sa.Column('assessed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'job_id')
    )
    op.create_index(op.f('ix_job_assessments_job_id'), 'job_assessments', ['job_id'], unique=False)
    op.create_index(op.f('ix_job_assessments_user_id'), 'job_assessments', ['user_id'], unique=False)
    op.create_table('job_feedback',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('sentiment', sa.String(length=10), nullable=False),
    sa.Column('reasons', postgresql.JSONB(), nullable=False),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('synthesized_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_job_feedback_job_id'), 'job_feedback', ['job_id'], unique=False)
    op.create_index(op.f('ix_job_feedback_user_id'), 'job_feedback', ['user_id'], unique=False)
    op.add_column('profile_imports', sa.Column('kind', sa.String(length=20), server_default='import', nullable=False))
    op.add_column('profile_imports', sa.Column('proposals', postgresql.JSONB(), nullable=True))
    op.add_column('profile_imports', sa.Column('extra', postgresql.JSONB(), nullable=True))
    op.alter_column('profile_imports', 'data',
               existing_type=postgresql.JSONB(astext_type=sa.Text()),
               nullable=True)
    op.add_column('user_profiles', sa.Column('positioning', sa.Text(), nullable=True))
    op.add_column('user_profiles', sa.Column('excluded_companies', postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column('user_profiles', sa.Column('reference_jobs', postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column('user_profiles', sa.Column('scoring', postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.add_column('user_profiles', sa.Column('version', sa.Integer(), server_default='1', nullable=False))


def downgrade() -> None:
    op.drop_column('user_profiles', 'version')
    op.drop_column('user_profiles', 'scoring')
    op.drop_column('user_profiles', 'reference_jobs')
    op.drop_column('user_profiles', 'excluded_companies')
    op.drop_column('user_profiles', 'positioning')
    op.alter_column('profile_imports', 'data',
               existing_type=postgresql.JSONB(astext_type=sa.Text()),
               nullable=False)
    op.drop_column('profile_imports', 'extra')
    op.drop_column('profile_imports', 'proposals')
    op.drop_column('profile_imports', 'kind')
    op.drop_index(op.f('ix_job_feedback_user_id'), table_name='job_feedback')
    op.drop_index(op.f('ix_job_feedback_job_id'), table_name='job_feedback')
    op.drop_table('job_feedback')
    op.drop_index(op.f('ix_job_assessments_user_id'), table_name='job_assessments')
    op.drop_index(op.f('ix_job_assessments_job_id'), table_name='job_assessments')
    op.drop_table('job_assessments')
    # La valeur 'pas_interesse' de l'enum process_status n'est pas retirée (PostgreSQL ne le permet pas
    # simplement) : sans effet sur l'ancien code.

"""classement des offres

Revision ID: c4e8f1a2b3d6
Revises: b7c1d9e4a5f2
Create Date: 2026-11-21 14:00:00.000000

Liste fermée (taxonomy.py) : tables de référence role_families, skill_families, skills, chargées ici par
taxonomy.sync (et ensuite par `python -m app.jobs taxonomy`) ; compétences des offres (job_skills) ; libellés
hors liste (taxonomy_misses) ; colonnes de classement sur jobs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c4e8f1a2b3d6'
down_revision: str | None = 'b7c1d9e4a5f2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.create_table('role_families',
                    sa.Column('id', sa.String(length=60), nullable=False),
                    sa.Column('label', sa.String(length=120), nullable=False),
                    sa.Column('domain', sa.String(length=30), nullable=False),
                    sa.Column('position', sa.Integer(), nullable=False),
                    sa.Column('title_keywords', JSON, nullable=False),
                    sa.PrimaryKeyConstraint('id'))
    op.create_table('skill_families',
                    sa.Column('id', sa.String(length=60), nullable=False),
                    sa.Column('label', sa.String(length=120), nullable=False),
                    sa.Column('group', sa.String(length=20), nullable=False),
                    sa.Column('position', sa.Integer(), nullable=False),
                    sa.PrimaryKeyConstraint('id'))
    op.create_table('skills',
                    sa.Column('id', sa.String(length=60), nullable=False),
                    sa.Column('label', sa.String(length=120), nullable=False),
                    sa.Column('family_id', sa.String(length=60), nullable=False),
                    sa.Column('domains', JSON, nullable=False),
                    sa.Column('synonyms', JSON, nullable=False),
                    sa.Column('position', sa.Integer(), nullable=False),
                    sa.ForeignKeyConstraint(['family_id'], ['skill_families.id']),
                    sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_skills_family_id', 'skills', ['family_id'])
    op.create_table('job_skills',
                    sa.Column('job_id', sa.Integer(), nullable=False),
                    sa.Column('skill_id', sa.String(length=60), nullable=False),
                    sa.Column('required', sa.Boolean(), server_default='true', nullable=False),
                    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
                    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], ondelete='CASCADE'),
                    sa.PrimaryKeyConstraint('job_id', 'skill_id'))
    op.create_index('ix_job_skills_skill_id', 'job_skills', ['skill_id'])
    op.create_table('taxonomy_misses',
                    sa.Column('kind', sa.String(length=20), nullable=False),
                    sa.Column('label', sa.String(length=120), nullable=False),
                    sa.Column('count', sa.Integer(), nullable=False),
                    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
                    sa.PrimaryKeyConstraint('kind', 'label'))
    op.add_column('jobs', sa.Column('role_family', sa.String(length=60), nullable=True))
    op.add_column('jobs', sa.Column('seniority_level', sa.String(length=20), nullable=True))
    op.add_column('jobs', sa.Column('contract_type', sa.String(length=20), nullable=True))
    op.add_column('jobs', sa.Column('salary_min_keur', sa.Integer(), nullable=True))
    op.add_column('jobs', sa.Column('salary_max_keur', sa.Integer(), nullable=True))
    op.add_column('jobs', sa.Column('salary_kind', sa.String(length=10), nullable=True))
    op.add_column('jobs', sa.Column('market_regions', JSON, nullable=True))
    op.add_column('jobs', sa.Column('product_sector', sa.String(length=40), nullable=True))
    op.add_column('jobs', sa.Column('classified_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('jobs', sa.Column('taxonomy_version', sa.Integer(), nullable=True))
    op.create_index('ix_jobs_role_family', 'jobs', ['role_family'])
    op.create_foreign_key('fk_jobs_role_family', 'jobs', 'role_families', ['role_family'], ['id'], ondelete='SET NULL')

    # Chargement de la liste fermée (même code que `python -m app.jobs taxonomy`)
    from app import taxonomy
    taxonomy.sync(op.get_bind())


def downgrade() -> None:
    op.drop_constraint('fk_jobs_role_family', 'jobs', type_='foreignkey')
    op.drop_index('ix_jobs_role_family', table_name='jobs')
    for column in ('taxonomy_version', 'classified_at', 'product_sector', 'market_regions', 'salary_kind',
                   'salary_max_keur', 'salary_min_keur', 'contract_type', 'seniority_level', 'role_family'):
        op.drop_column('jobs', column)
    op.drop_table('taxonomy_misses')
    op.drop_index('ix_job_skills_skill_id', table_name='job_skills')
    op.drop_table('job_skills')
    op.drop_index('ix_skills_family_id', table_name='skills')
    op.drop_table('skills')
    op.drop_table('skill_families')
    op.drop_table('role_families')

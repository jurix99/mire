"""radar sources passages ats

Revision ID: 32210cd06290
Revises: 673754c04f96
Create Date: 2026-11-19 00:15:50.519493
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '32210cd06290'
down_revision: str | None = '673754c04f96'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('search_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('jobs_seen', sa.Integer(), nullable=False),
    sa.Column('jobs_added', sa.Integer(), nullable=False),
    sa.Column('jobs_duplicates', sa.Integer(), nullable=False),
    sa.Column('jobs_closed', sa.Integer(), nullable=False),
    sa.Column('companies_checked', sa.Integer(), nullable=False),
    sa.Column('sources_added', sa.Integer(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('sources',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('query', sa.Text(), nullable=True),
    sa.Column('active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('added_by', sa.String(length=20), nullable=False),
    sa.Column('added_by_user_id', sa.Integer(), nullable=True),
    sa.Column('jobs_found', sa.Integer(), server_default='0', nullable=False),
    sa.Column('auto_readable', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['added_by_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.add_column('companies', sa.Column('ats', sa.String(length=30), nullable=True))
    op.add_column('companies', sa.Column('ats_slug', sa.String(length=120), nullable=True))
    op.add_column('companies', sa.Column('careers_url', sa.String(length=1000), nullable=True))
    op.add_column('companies', sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('companies', sa.Column('no_relevant_jobs_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('companies', sa.Column('headcount', sa.String(length=40), nullable=True))
    op.add_column('jobs', sa.Column('url_key', sa.String(length=1000), nullable=True))
    op.add_column('jobs', sa.Column('title_key', sa.String(length=300), nullable=True))
    op.add_column('jobs', sa.Column('source_id', sa.Integer(), nullable=True))
    op.add_column('jobs', sa.Column('ats_job_id', sa.String(length=200), nullable=True))
    op.add_column('jobs', sa.Column('role_type', sa.String(length=60), nullable=True))
    op.add_column('jobs', sa.Column('work_mode', sa.String(length=20), nullable=True))
    op.add_column('jobs', sa.Column('travel', sa.String(length=60), nullable=True))
    op.add_column('jobs', sa.Column('salary', sa.String(length=120), nullable=True))
    op.add_column('jobs', sa.Column('published_on', sa.Date(), nullable=True))
    op.add_column('jobs', sa.Column('verified_open_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('jobs', sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_jobs_source_id'), 'jobs', ['source_id'], unique=False)
    op.create_index(op.f('ix_jobs_title_key'), 'jobs', ['title_key'], unique=False)

    # Clés de dédoublonnage des offres existantes (avant la contrainte d'unicité).
    # Si deux offres existantes ont la même URL normalisée, seule la première garde la clé.
    from app.dedup import normalize_url, title_key

    conn = op.get_bind()
    seen: set[str] = set()
    for job_id, url, title in conn.execute(sa.text("SELECT id, url, title FROM jobs ORDER BY id")).all():
        key = normalize_url(url)
        if key in seen:
            key = None
        elif key:
            seen.add(key)
        conn.execute(sa.text("UPDATE jobs SET url_key = :u, title_key = :t WHERE id = :i"),
                     {"u": key, "t": title_key(title), "i": job_id})

    op.create_unique_constraint('uq_jobs_url_key', 'jobs', ['url_key'])
    op.create_foreign_key('fk_jobs_source_id_sources', 'jobs', 'sources', ['source_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    # WARNING: constraint name is None; this directive will fail as
    # rendered.  Add a name, or use a naming convention; see
    # https://alembic.sqlalchemy.org/en/latest/naming.html
    op.drop_constraint('fk_jobs_source_id_sources', 'jobs', type_='foreignkey')
    # WARNING: constraint name is None; this directive will fail as
    # rendered.  Add a name, or use a naming convention; see
    # https://alembic.sqlalchemy.org/en/latest/naming.html
    op.drop_constraint('uq_jobs_url_key', 'jobs', type_='unique')
    op.drop_index(op.f('ix_jobs_title_key'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_source_id'), table_name='jobs')
    op.drop_column('jobs', 'closed_at')
    op.drop_column('jobs', 'verified_open_at')
    op.drop_column('jobs', 'published_on')
    op.drop_column('jobs', 'salary')
    op.drop_column('jobs', 'travel')
    op.drop_column('jobs', 'work_mode')
    op.drop_column('jobs', 'role_type')
    op.drop_column('jobs', 'ats_job_id')
    op.drop_column('jobs', 'source_id')
    op.drop_column('jobs', 'title_key')
    op.drop_column('jobs', 'url_key')
    op.drop_column('companies', 'headcount')
    op.drop_column('companies', 'no_relevant_jobs_at')
    op.drop_column('companies', 'last_checked_at')
    op.drop_column('companies', 'careers_url')
    op.drop_column('companies', 'ats_slug')
    op.drop_column('companies', 'ats')
    op.drop_table('sources')
    op.drop_table('search_runs')

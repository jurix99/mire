"""essai gratuit

Revision ID: e6a1b2c3d4f5
Revises: d5f9a3b4c7e1
Create Date: 2026-11-25 10:00:00.000000

Essai gratuit de la landing (trial.py) : liens LinkedIn déposés sans compte et startups proposées (trial_leads).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e6a1b2c3d4f5'
down_revision: str | None = 'd5f9a3b4c7e1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    op.create_table('trial_leads',
                    sa.Column('id', sa.Integer(), nullable=False),
                    sa.Column('linkedin_url', sa.String(length=300), nullable=False),
                    sa.Column('linkedin_slug', sa.String(length=120), nullable=False),
                    sa.Column('wish', sa.String(length=300), nullable=True),
                    sa.Column('profile_summary', sa.Text(), nullable=True),
                    sa.Column('understood', sa.String(length=300), nullable=True),
                    sa.Column('results', JSON, nullable=True),
                    sa.Column('ip_hash', sa.String(length=64), nullable=False),
                    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                              nullable=False),
                    sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_trial_leads_linkedin_slug', 'trial_leads', ['linkedin_slug'])
    op.create_index('ix_trial_leads_ip_hash', 'trial_leads', ['ip_hash'])
    op.create_index('ix_trial_leads_created_at', 'trial_leads', ['created_at'])


def downgrade() -> None:
    op.drop_table('trial_leads')

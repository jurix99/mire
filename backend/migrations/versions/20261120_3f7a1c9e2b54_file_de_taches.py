"""file de taches

Revision ID: 3f7a1c9e2b54
Revises: 5c3e8b1f2a47
Create Date: 2026-11-20 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = '3f7a1c9e2b54'
down_revision: str | None = '5c3e8b1f2a47'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('tasks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('run_after', sa.DateTime(timezone=True), nullable=False),
    sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('dedup_key', sa.String(length=120), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    # Index partiels : seules les tâches en attente sont cherchées (worker, règle KEDA) ; une seule tâche en
    # attente par clé de dédoublonnage
    op.create_index('ix_tasks_queued_run_after', 'tasks', ['run_after'], unique=False,
                    postgresql_where=sa.text("status = 'queued'"))
    op.create_index('uq_tasks_queued_dedup_key', 'tasks', ['dedup_key'], unique=True,
                    postgresql_where=sa.text("status = 'queued'"))


def downgrade() -> None:
    op.drop_index('uq_tasks_queued_dedup_key', table_name='tasks', postgresql_where=sa.text("status = 'queued'"))
    op.drop_index('ix_tasks_queued_run_after', table_name='tasks', postgresql_where=sa.text("status = 'queued'"))
    op.drop_table('tasks')

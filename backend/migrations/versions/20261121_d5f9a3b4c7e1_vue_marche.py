"""vue marche

Revision ID: d5f9a3b4c7e1
Revises: c4e8f1a2b3d6
Create Date: 2026-11-21 15:00:00.000000

Vue matérialisée mv_market_jobs : une ligne de faits par offre classée (analyses du marché, market.py),
rafraîchie à la fin du traitement quotidien (REFRESH MATERIALIZED VIEW CONCURRENTLY : index unique requis).
Même logique que market.refresh_facts (utilisé sous SQLite, dans les tests).
"""

from collections.abc import Sequence

from alembic import op

revision: str = 'd5f9a3b4c7e1'
down_revision: str | None = 'c4e8f1a2b3d6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE MATERIALIZED VIEW mv_market_jobs AS
        SELECT j.id AS job_id,
               j.company_id,
               COALESCE(j.published_on, (j.created_at AT TIME ZONE 'UTC')::date) AS ref_date,
               (j.closed_at AT TIME ZONE 'UTC')::date AS closed_on,
               j.role_family,
               rf.domain,
               j.seniority_level,
               j.contract_type,
               j.salary_min_keur,
               j.salary_max_keur,
               j.salary_kind,
               j.work_mode,
               j.market_regions,
               j.product_sector,
               c.funding_stage,
               c.funding_announced_on,
               c.investors
        FROM jobs j
        JOIN companies c ON c.id = j.company_id
        LEFT JOIN role_families rf ON rf.id = j.role_family
        WHERE j.classified_at IS NOT NULL
        WITH DATA
    """)
    op.execute("CREATE UNIQUE INDEX ux_mv_market_jobs_job_id ON mv_market_jobs (job_id)")
    op.execute("CREATE INDEX ix_mv_market_jobs_company_id ON mv_market_jobs (company_id)")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_market_jobs")

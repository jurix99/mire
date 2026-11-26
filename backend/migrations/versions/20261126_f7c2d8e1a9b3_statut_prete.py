"""statut prete

Revision ID: f7c2d8e1a9b3
Revises: e6a1b2c3d4f5
Create Date: 2026-11-26 09:00:00.000000

Nouveau statut de process « Prête à envoyer » (prete), entre « À regarder » (a_postuler) et « Postulé » (postule).

PostgreSQL : ALTER TYPE … ADD VALUE ne peut pas être utilisé dans la transaction qui l'ajoute ; la commande tourne
donc hors transaction (autocommit_block) et aucune migration de cette exécution n'utilise la valeur avant le commit.
IF NOT EXISTS : rejouable sans erreur. SQLite (tests) : l'enum est stocké en texte (VARCHAR, sans contrainte),
rien à faire.
"""

from collections.abc import Sequence

from alembic import op

revision: str = 'f7c2d8e1a9b3'
down_revision: str | None = 'e6a1b2c3d4f5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE process_status ADD VALUE IF NOT EXISTS 'prete' AFTER 'a_postuler'")


def downgrade() -> None:
    # PostgreSQL ne sait pas retirer une valeur d'un enum : les process « prête » repassent « à regarder » et la
    # valeur reste dans le type (sans effet).
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("UPDATE applications SET status = 'a_postuler' WHERE status = 'prete'")
    op.execute("UPDATE application_events SET to_status = 'a_postuler' WHERE to_status = 'prete'")
    op.execute("UPDATE application_events SET from_status = 'a_postuler' WHERE from_status = 'prete'")

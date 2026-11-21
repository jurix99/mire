from logging.config import fileConfig

from alembic import context

from app import models  # noqa: F401  (enregistre les tables dans Base.metadata)
from app.db import Base, engine

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    """Vues matérialisées (ex. mv_market_jobs) : écrites à la main dans les migrations, pas autogénérées."""
    return not (type_ == "table" and getattr(obj, "info", {}).get("is_view"))


def run_migrations_offline() -> None:
    context.configure(url=engine.url, target_metadata=target_metadata, literal_binds=True,
                      include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

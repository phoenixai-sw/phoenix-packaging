from logging.config import fileConfig

from alembic import context

from services.api.config import Settings
from services.api.database import Base, build_database
from services.api import models  # noqa: F401 — registers metadata
from services.api import feature_models  # noqa: F401
from services.api.billing import models as billing_models  # noqa: F401

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata
settings = Settings()
if settings.environment in {"staging", "production"}:
    # Fail before opening a DB connection or running any destructive migration.
    settings.validate()


def run_migrations_offline():
    url = settings.database_url.replace("postgres://", "postgresql://", 1)
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine, _ = build_database(settings)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()

"""Ambiente Alembic. Usa a URL administrativa (superuser) — migrations criam extensão/RLS."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.core.db import Base

# Garante que os modelos sejam importados e registrados no metadata.
from app.modules.benchmark import models as _benchmark_models  # noqa: F401
from app.modules.catalog import models as _catalog_models  # noqa: F401
from app.modules.dashboard import models as _dashboard_models  # noqa: F401
from app.modules.debt import models as _debt_models  # noqa: F401
from app.modules.health_edu import models as _health_edu_models  # noqa: F401
from app.modules.indicators import models as _indicators_models  # noqa: F401
from app.modules.ingestion import models as _ingestion_models  # noqa: F401
from app.modules.limits import models as _limits_models  # noqa: F401
from app.modules.tenancy import models as _tenancy_models  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_admin_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_admin_url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

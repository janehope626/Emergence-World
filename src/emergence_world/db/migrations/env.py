"""Alembic environment for portable synchronous migrations."""

from __future__ import annotations

from logging.config import fileConfig
from os import environ

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection

from emergence_world.db import models as database_models
from emergence_world.db.base import Base

del database_models  # Import registers all mapped tables with Base.metadata.

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

if database_url := environ.get("DATABASE_URL"):
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = Base.metadata


def configure_context(connection: Connection | None = None) -> None:
    if connection is None:
        context.configure(
            url=config.get_main_option("sqlalchemy.url"),
            target_metadata=target_metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            compare_type=True,
        )
    else:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )


def run_migrations_offline() -> None:
    configure_context()
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with engine.connect() as connection:
        configure_context(connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

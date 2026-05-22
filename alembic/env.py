from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import create_engine, pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from app.core.config import get_settings
from app.db import models  # noqa: F401
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_database_url() -> str:
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return env_url
    config_url = config.get_main_option("sqlalchemy.url")
    if config_url:
        return config_url
    try:
        return get_settings().sqlalchemy_url
    except Exception as err:
        raise RuntimeError("Unable to resolve database URL for Alembic migration context.") from err


def run_migrations_offline() -> None:
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = get_database_url()
    if "+asyncpg" in url or "+aiosqlite" in url:
        import asyncio

        async def run_async_migrations() -> None:
            connectable = create_async_engine(url, poolclass=pool.NullPool)
            async with connectable.connect() as connection:
                await connection.run_sync(do_run_migrations)
            await connectable.dispose()

        asyncio.run(run_async_migrations())
        return

    connectable = create_engine(url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        do_run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

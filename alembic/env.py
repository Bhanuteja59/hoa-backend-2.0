# backend/alembic/env.py
from __future__ import annotations

from alembic import context
from sqlalchemy import pool
from sqlalchemy import create_engine   # <-- new import for sync engine
import sys
import os

sys.path.append(os.getcwd())

from app.core.config import settings
from app.db.models import Base

config = context.config
target_metadata = Base.metadata

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=settings.SYNC_DATABASE_URL,   # <-- use SYNC url
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = create_engine(         # <-- SYNC engine, not async
        settings.SYNC_DATABASE_URL,
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

"""
Alembic environment configuration for async SQLAlchemy.
"""
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from alembic import context

# Import the Base from your app
from app.database import Base
from app.config import settings

# Import all models to register them with Base.metadata
from app.apps.gestion_usuarios.models import *
from app.apps.gestion_catalogo.models import *
from app.apps.gestion_ventas.models import *
from app.apps.servicios_intelientes.models import *

# this is the Alembic Config object
config = context.config

# Set sqlalchemy.url from settings if not already set
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata for autogenerate support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    # Configurar SSL para proveedores cloud como Supabase
    connect_args = {"statement_cache_size": 0}
    if settings.DB_SSL:
        import ssl
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        connect_args["ssl"] = ssl_context
    
    # Usamos create_async_engine en lugar de async_engine_from_config
    # para poder pasar connect_args con statement_cache_size=0 y ssl
    connectable = create_async_engine(
        settings.DATABASE_URL,
        poolclass=pool.NullPool,          # mantenemos NullPool para migraciones
        connect_args=connect_args,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    import asyncio
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
"""
Configuración de la base de datos con SQLAlchemy async.
Manejo de sesiones y creación del motor de base de datos.
"""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.config import settings

# Crear el motor de base de datos asíncrono
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    connect_args={"statement_cache_size": 0},
)

# Crear el factory de sesiones
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Base para los modelos
Base = declarative_base()


async def get_db():
    """
    Dependencia para obtener la sesión de base de datos.
    Yields la sesión y la cierra al finalizar.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Inicializa la base de datos creando todas las tablas."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_db():
    """Elimina todas las tablas de la base de datos."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
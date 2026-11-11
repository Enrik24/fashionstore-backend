"""
Configuración de la base de datos con SQLAlchemy async.
Manejo de sesiones y creación del motor de base de datos.
"""
import ssl

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.config import settings

# Configurar connect_args según si se necesita SSL
def build_connect_args() -> dict:
    """Construye los `connect_args` del motor (statement_cache_size + SSL).

    Se comparte con `alembic/env.py` para que las migraciones se conecten con
    exactamente el mismo modo SSL que la aplicación.

    El modo SSL se aplica por `connect_args` y NO por la URL: asyncpg no
    reconoce `sslmode=...` (habla `ssl`) y SQLAlchemy le pasa cualquier
    parámetro de la query tal cual, así que `?sslmode=require` en DATABASE_URL
    revienta con "TypeError: connect() got an unexpected keyword argument
    'sslmode'". Ver app/config.py -> Settings.db_sslmode.
    """
    connect_args = {"statement_cache_size": 0}
    _modo_ssl = settings.db_sslmode

    if _modo_ssl in ("verify-ca", "verify-full"):
        # Verificación real del certificado: necesita el CA del proveedor
        # (Railway lo entrega como `root.crt`).
        _ca = settings.DB_SSLROOTCERT
        if not _ca:
            raise RuntimeError(
                f"DB_SSLMODE={_modo_ssl} requiere DB_SSLROOTCERT con la ruta del "
                "certificado CA (en Railway se descarga como root.crt)."
            )
        _contexto = ssl.create_default_context(cafile=_ca)
        _contexto.check_hostname = _modo_ssl == "verify-full"
        _contexto.verify_mode = ssl.CERT_REQUIRED
        connect_args["ssl"] = _contexto
    elif _modo_ssl:
        # asyncpg entiende los nombres de libpq: disable|allow|prefer|require.
        # "require" cifra el canal sin verificar el certificado del servidor.
        connect_args["ssl"] = _modo_ssl
    elif settings.DB_SSL:
        # Modo legacy: contexto TLS sin verificación del certificado.
        _contexto = ssl.create_default_context()
        _contexto.check_hostname = False
        _contexto.verify_mode = ssl.CERT_NONE
        connect_args["ssl"] = _contexto

    return connect_args


connect_args = build_connect_args()

# Crear el motor de base de datos asíncrono
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    connect_args=connect_args,
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


# [EXAMEN-2] Registra el scoping multitenant de SQLAlchemy (ver app/tenant.py).
# Se importa al final para evitar el ciclo database -> tenant -> database.
import app.tenant_events  # noqa: E402,F401
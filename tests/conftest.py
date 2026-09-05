"""
Configuración de pytest para los tests de integración y unitarios.
"""
import pytest
import pytest_asyncio
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from httpx import AsyncClient, ASGITransport

from app.database import Base, get_db
from app.config import settings
from main import app
from app.apps.gestion_usuarios.services import DatosInicialesService
from app.apps.gestion_catalogo.services import DatosInicialesCatalogoService


# Motor de base de datos para tests con NullPool para evitar conflictos de conexiones en asyncpg
TEST_DATABASE_URL = f"{settings.DATABASE_URL}_test"

engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    future=True,
    poolclass=NullPool
)

TestingSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_database():
    """Crea las tablas y los datos iniciales para la suite de tests."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    
    async with TestingSessionLocal() as session:
        try:
            await DatosInicialesService.crear_datos_iniciales(session)
            await DatosInicialesCatalogoService.crear_datos_iniciales(session)
        except Exception as e:
            print(f"Error inicializando datos en test: {e}")
    
    yield
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Proporciona una sesión de base de datos para tests."""
    async with TestingSessionLocal() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Proporciona un cliente HTTP para tests con la sesión de DB sobreescrita."""
    async def override_get_db():
        async with TestingSessionLocal() as session:
            try:
                yield session
            finally:
                await session.close()
    
    app.dependency_overrides[get_db] = override_get_db
    
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    
    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def admin_auth_tokens(client: AsyncClient):
    """Obtiene tokens del administrador por defecto."""
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "correo": "admin@fashionstore.com",
            "contrasena": "Admin123!"
        }
    )
    assert response.status_code == 200, f"Error en login de admin: {response.text}"
    return response.json()


@pytest_asyncio.fixture(scope="function")
async def admin_headers(admin_auth_tokens: dict):
    """Headers con Bearer token para el administrador."""
    token = admin_auth_tokens["access_token"]
    return {"Authorization": f"Bearer {token}"}
"""
Configuración de pytest para los tests de integración y unitarios.
"""
import asyncio
import sys
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

# [EXAMEN-2] P7: en Windows, el ProactorEventLoop deja transportes de asyncpg
# cuyo `__del__` se ejecuta con el loop ya cerrado y provoca una violación de
# acceso (código 0xC0000005) al terminar la suite, aunque todas las pruebas
# pasen. El SelectorEventLoop no tiene ese problema y asyncpg lo soporta.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest
import pytest_asyncio
from typing import AsyncGenerator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from httpx import AsyncClient, ASGITransport

from app.database import Base, get_db
from app.config import settings
from main import app
from app.apps.gestion_usuarios.services import DatosInicialesService
from app.apps.gestion_catalogo.services import DatosInicialesCatalogoService
# [EXAMEN-2] Fase 1: fixtures multitenant y de sesión offline
from app.apps.gestion_saas.models import Plan, Tenant
from app.tenant import TENANT_DEMO_SLUG, clear_tenant_context

# [EXAMEN-2] Fase 2: catálogo, clientes, pagos y Stripe simulado
from app.apps.gestion_catalogo.models import (
    Categoria,
    Color,
    EstadoProducto,
    Inventario,
    Producto,
    Sucursal,
    Talla,
    VarianteProducto,
)
from app.apps.gestion_usuarios.models import Cliente
from app.apps.pagos_diferidos.stripe_pago_service import StripePagoService
from app.apps.pagos_diferidos.webhook_service import WebhookService

SUPERADMIN_CORREO = "superadmin@fashionstore.com"
SUPERADMIN_CONTRASENA = "SuperAdmin123!"
ADMIN_CORREO = "admin@fashionstore.com"
ADMIN_CONTRASENA = "Admin123!"
CONTRASENA_TENANT_NUEVO = "Secreta123!"


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


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
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


# ============================================================
# [EXAMEN-2] Fixtures de la Fase 1 (SaaS multitenant + offline)
# ============================================================

@pytest.fixture(autouse=True)
def limpiar_contexto_tenant():
    """Evita que el tenant de una petición se filtre al resto del test."""
    clear_tenant_context()
    yield
    clear_tenant_context()


@pytest_asyncio.fixture(scope="function")
async def superadmin_auth_tokens(client: AsyncClient):
    """Tokens del Super Administrador de la plataforma (sin tenant)."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"correo": SUPERADMIN_CORREO, "contrasena": SUPERADMIN_CONTRASENA},
    )
    assert response.status_code == 200, f"Error en login de superadmin: {response.text}"
    return response.json()


@pytest_asyncio.fixture(scope="function")
async def superadmin_headers(superadmin_auth_tokens: dict):
    """Headers con Bearer token del Super Administrador de plataforma."""
    return {"Authorization": f"Bearer {superadmin_auth_tokens['access_token']}"}


@pytest_asyncio.fixture(scope="function")
async def demo_tenant(db_session: AsyncSession) -> Tenant:
    """Tenant demo que hereda los datos del Examen 1."""
    result = await db_session.execute(select(Tenant).where(Tenant.slug == TENANT_DEMO_SLUG))
    tenant = result.scalar_one_or_none()
    assert tenant is not None, "El seed debe crear el tenant demo"
    return tenant


@pytest_asyncio.fixture(scope="function")
async def plan_basico(db_session: AsyncSession) -> Plan:
    result = await db_session.execute(select(Plan).where(Plan.nombre == "Básico"))
    plan = result.scalar_one_or_none()
    assert plan is not None, "El seed debe crear el plan Básico"
    return plan


@pytest_asyncio.fixture(scope="function")
async def plan_profesional(db_session: AsyncSession) -> Plan:
    result = await db_session.execute(select(Plan).where(Plan.nombre == "Profesional"))
    plan = result.scalar_one_or_none()
    assert plan is not None, "El seed debe crear el plan Profesional"
    return plan


@pytest_asyncio.fixture(scope="function")
async def tenant_nuevo(client: AsyncClient, superadmin_headers: dict):
    """Fábrica de tenants: alta por API + login del administrador.

    Devuelve un diccionario con `tenant`, `id`, `payload`, `tokens` y
    `headers` (los del administrador del tenant recién creado).
    """
    creados: list[dict] = []

    async def _crear(plan_id: int | None = None, finalizar: bool = True, nombre: str = "Empresa"):
        sufijo = uuid.uuid4().hex[:8]
        payload = {
            "razon_social": f"{nombre} {sufijo}",
            "nit": f"NIT{sufijo}",
            "direccion_fiscal": "Calle Falsa 123",
            "correo_contacto": f"contacto{sufijo}@example.com",
            "telefono": "77777777",
            "slug": f"empresa-{sufijo}",
            "plan_id": plan_id,
            "admin_nombre": "Ada",
            "admin_apellido": sufijo,
            "admin_correo": f"admin{sufijo}@example.com",
            "admin_contrasena": CONTRASENA_TENANT_NUEVO,
        }
        respuesta = await client.post(
            "/api/v1/saas/tenants", json=payload, headers=superadmin_headers
        )
        assert respuesta.status_code == 201, respuesta.text
        tenant = respuesta.json()

        login = await client.post(
            "/api/v1/auth/login",
            json={"correo": payload["admin_correo"], "contrasena": CONTRASENA_TENANT_NUEVO},
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        if finalizar:
            fin = await client.post("/api/v1/saas/configuracion/finalizar", headers=headers)
            assert fin.status_code == 200, fin.text

        datos = {
            "tenant": tenant,
            "id": tenant["id"],
            "payload": payload,
            "tokens": login.json(),
            "headers": headers,
        }
        creados.append(datos)
        return datos

    return _crear


# ============================================================
# [EXAMEN-2] Fixtures de la Fase 2 (operación offline, carrito y pagos)
# ============================================================

@pytest.fixture(autouse=True)
def limpiar_estado_stripe():
    """Cada prueba arranca en modo Stripe simulado y sin estado previo.

    La suite nunca debe depender de la red ni de una clave real: se vacía
    `STRIPE_API_KEY` durante la prueba y se restaura al terminar.
    """
    clave_original = settings.STRIPE_API_KEY
    settings.STRIPE_API_KEY = ""
    StripePagoService.limpiar_simulados()
    try:
        yield
    finally:
        StripePagoService.limpiar_simulados()
        settings.STRIPE_API_KEY = clave_original


@pytest_asyncio.fixture(scope="function")
async def catalogo_tenant(db_session: AsyncSession):
    """Fábrica de catálogo mínimo por tenant (producto, variante e inventario)."""

    async def _crear(
        tenant: dict,
        *,
        precio: str = "100.00",
        stock: int = 10,
        activo: bool = True,
        nombre: str | None = None,
        stock_minimo: int = 0,
    ) -> dict:
        sufijo = uuid.uuid4().hex[:8]
        tenant_id = int(tenant["id"])

        sucursal = (await db_session.execute(
            select(Sucursal)
            .where(Sucursal.tenant_id == tenant_id, Sucursal.deleted_at.is_(None))
            .order_by(Sucursal.id.asc())
        )).scalars().first()
        assert sucursal is not None, "El tenant debe tener una sucursal"

        categoria = Categoria(
            nombre=f"Categoría {sufijo}",
            tenant_id=tenant_id,
        )
        color = Color(nombre=f"Color {sufijo}", codigo_hex="#123456", tenant_id=tenant_id)
        talla = Talla(valor=f"T{sufijo[:3]}", tipo="UNICA", tenant_id=tenant_id)
        db_session.add_all([categoria, color, talla])
        await db_session.flush()

        producto = Producto(
            sku=f"SKU-{sufijo}",
            nombre=nombre or f"Producto {sufijo}",
            descripcion="Producto de prueba",
            precio=Decimal(precio),
            costo_compra=Decimal("50.00"),
            estado=EstadoProducto.ACTIVO if activo else EstadoProducto.INACTIVO,
            categoria_id=categoria.id,
            tenant_id=tenant_id,
        )
        db_session.add(producto)
        await db_session.flush()

        variante = VarianteProducto(
            producto_id=producto.id,
            talla_id=talla.id,
            color_id=color.id,
            sku_variante=f"{producto.sku}-{sufijo[:4]}",
            precio_variante=Decimal(precio),
            tenant_id=tenant_id,
        )
        db_session.add(variante)
        await db_session.flush()

        inventario = Inventario(
            variante_producto_id=variante.id,
            sucursal_id=sucursal.id,
            cantidad=stock,
            cantidad_reservada=0,
            stock_minimo=stock_minimo,
            tenant_id=tenant_id,
        )
        db_session.add(inventario)
        await db_session.commit()

        return {
            "tenant_id": tenant_id,
            "sucursal_id": sucursal.id,
            "categoria_id": categoria.id,
            "producto_id": producto.id,
            "variante_id": variante.id,
            "color_id": color.id,
            "talla_id": talla.id,
            "sku": producto.sku,
            "precio": Decimal(precio),
            "stock": stock,
        }

    return _crear


@pytest_asyncio.fixture(scope="function")
async def cliente_tenant(db_session: AsyncSession):
    """Fábrica de clientes del tenant (sin usuario asociado)."""

    async def _crear(tenant: dict, *, nit_ci: str | None = None) -> dict:
        sufijo = uuid.uuid4().hex[:8]
        cliente = Cliente(
            nit_ci=nit_ci or f"CI{sufijo}",
            telefono="70000000",
            correo=f"cliente{sufijo}@example.com",
            direccion_envio="Av. Siempre Viva 742",
            tenant_id=int(tenant["id"]),
        )
        db_session.add(cliente)
        await db_session.commit()
        await db_session.refresh(cliente)
        return {"id": cliente.id, "tenant_id": cliente.tenant_id, "nit_ci": cliente.nit_ci}

    return _crear


@pytest_asyncio.fixture(scope="function")
async def dispositivo_offline(client: AsyncClient):
    """Registra un dispositivo, configura el PIN y abre sesión offline.

    Devuelve `id`, `token`, `headers` (token de propósito offline) y la
    respuesta completa de la sesión.
    """

    async def _crear(tenant: dict, *, nombre: str = "POS", pin: str = "135790") -> dict:
        dispositivo = await client.post(
            "/api/v1/offline/dispositivos",
            json={"plataforma": "ANDROID", "nombre": nombre},
            headers=tenant["headers"],
        )
        assert dispositivo.status_code == 201, dispositivo.text

        configurado = await client.post(
            "/api/v1/offline/sesion/pin",
            json={
                "contrasena_actual": tenant["payload"]["admin_contrasena"],
                "pin": pin,
            },
            headers=tenant["headers"],
        )
        assert configurado.status_code == 200, configurado.text

        sesion = await client.post(
            "/api/v1/offline/sesion/iniciar",
            json={
                "dispositivo_id": dispositivo.json()["id"],
                "pin": pin,
                "tenant_id": int(tenant["id"]),
            },
        )
        assert sesion.status_code == 200, sesion.text
        token = sesion.json()["token_offline"]

        return {
            "id": dispositivo.json()["id"],
            "token": token,
            "headers": {"Authorization": f"Bearer {token}"},
            "sesion": sesion.json(),
        }

    return _crear


@pytest.fixture(scope="function")
def firmar_webhook():
    """Genera la cabecera `stripe-signature` para un payload dado."""
    return WebhookService.generar_firma


def operacion_payload(
    *,
    uuid_operacion: str,
    tipo_operacion: str,
    payload: dict,
    secuencia_local: int = 1,
    fecha_registro_local: datetime | None = None,
    dispositivo_id: str | None = None,
    sucursal_id: int | None = None,
) -> dict:
    """Construye un ítem de lote de sincronización con valores por defecto."""
    return {
        "uuid_operacion": uuid_operacion,
        "tipo_operacion": tipo_operacion,
        "payload": payload,
        "secuencia_local": secuencia_local,
        "fecha_registro_local": (
            fecha_registro_local or datetime.utcnow() - timedelta(minutes=1)
        ).isoformat(),
        "dispositivo_id": dispositivo_id,
        "sucursal_id": sucursal_id,
    }

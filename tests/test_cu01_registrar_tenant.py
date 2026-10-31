"""[EXAMEN-2] CU01 — Alta de tenant (plan 9.1.10).

Verifica el alta transaccional (tenant + entorno + administrador +
suscripción), las validaciones de unicidad y el estado inicial.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.apps.gestion_saas.models import EstadoTenant, Suscripcion, Tenant
from app.apps.gestion_usuarios.models import Administrador, Rol, Usuario, UsuarioRol


def _payload(sufijo: str | None = None, **overrides) -> dict:
    sufijo = sufijo or uuid.uuid4().hex[:8]
    datos = {
        "razon_social": f"Moda Andina {sufijo}",
        "nit": f"NIT-{sufijo}",
        "direccion_fiscal": "Av. Arce #123, La Paz",
        "correo_contacto": f"contacto{sufijo}@modaandina.com",
        "telefono": "71234567",
        "slug": f"moda-andina-{sufijo}",
        "moneda": "BOB",
        "zona_horaria": "America/La_Paz",
        "admin_nombre": "Lucía",
        "admin_apellido": "Quispe",
        "admin_correo": f"admin{sufijo}@modaandina.com",
        "admin_contrasena": "Secreta123!",
    }
    datos.update(overrides)
    return datos


@pytest.mark.asyncio
async def test_alta_correcta_crea_entorno_admin_y_suscripcion(
    client: AsyncClient, superadmin_headers: dict, db_session, plan_profesional
):
    datos = _payload(plan_id=plan_profesional.id)

    respuesta = await client.post(
        "/api/v1/saas/tenants", json=datos, headers=superadmin_headers
    )

    assert respuesta.status_code == 201, respuesta.text
    tenant = respuesta.json()
    assert tenant["estado"] == EstadoTenant.CONFIGURACION_INICIAL.value
    assert tenant["slug"] == datos["slug"]
    assert tenant["nit"] == datos["nit"]

    # Entorno, administrador y suscripción creados en la misma transacción
    detalle = await client.get(
        f"/api/v1/saas/tenants/{tenant['id']}", headers=superadmin_headers
    )
    assert detalle.status_code == 200, detalle.text
    cuerpo = detalle.json()
    assert cuerpo["uso"]["sucursales_actuales"] == 1  # sucursal matriz
    assert cuerpo["uso"]["usuarios_actuales"] == 1
    assert cuerpo["suscripcion"]["plan_id"] == plan_profesional.id
    assert cuerpo["suscripcion"]["estado"] == "ACTIVA"

    # El administrador existe, pertenece al tenant y conserva el rol
    resultado = await db_session.execute(
        select(Usuario).where(Usuario.correo == datos["admin_correo"])
    )
    admin = resultado.scalar_one()
    assert admin.tenant_id == tenant["id"]

    resultado = await db_session.execute(
        select(func.count(Administrador.id)).where(Administrador.tenant_id == tenant["id"])
    )
    assert resultado.scalar_one() == 1

    resultado = await db_session.execute(
        select(Rol.nombre)
        .join(UsuarioRol, Rol.id == UsuarioRol.rol_id)
        .where(
            UsuarioRol.usuario_id == admin.id,
            UsuarioRol.tenant_id == tenant["id"],
        )
    )
    assert resultado.scalars().all() == ["Administrador"]


@pytest.mark.asyncio
async def test_login_del_administrador_pide_cambiar_contrasena(
    client: AsyncClient, superadmin_headers: dict
):
    datos = _payload()
    tenant = (
        await client.post("/api/v1/saas/tenants", json=datos, headers=superadmin_headers)
    ).json()

    login = await client.post(
        "/api/v1/auth/login",
        json={"correo": datos["admin_correo"], "contrasena": datos["admin_contrasena"]},
    )

    assert login.status_code == 200, login.text
    cuerpo = login.json()
    assert cuerpo["tenant_id"] == tenant["id"]
    assert cuerpo["es_super_admin"] is False
    assert cuerpo["roles"] == ["Administrador"]
    assert cuerpo["debe_cambiar_contrasena"] is True
    assert cuerpo["tenant"]["id"] == tenant["id"]
    assert cuerpo["suscripcion"]["estado"] == "ACTIVA"


@pytest.mark.asyncio
async def test_nit_duplicado_es_rechazado(
    client: AsyncClient, superadmin_headers: dict
):
    datos = _payload()
    primera = await client.post(
        "/api/v1/saas/tenants", json=datos, headers=superadmin_headers
    )
    assert primera.status_code == 201, primera.text

    repetido = _payload(nit=datos["nit"])
    respuesta = await client.post(
        "/api/v1/saas/tenants", json=repetido, headers=superadmin_headers
    )

    assert respuesta.status_code == 409
    assert "NIT" in respuesta.json()["detail"]


@pytest.mark.asyncio
async def test_correo_de_contacto_duplicado_es_rechazado(
    client: AsyncClient, superadmin_headers: dict
):
    datos = _payload()
    assert (
        await client.post("/api/v1/saas/tenants", json=datos, headers=superadmin_headers)
    ).status_code == 201

    repetido = _payload(correo_contacto=datos["correo_contacto"])
    respuesta = await client.post(
        "/api/v1/saas/tenants", json=repetido, headers=superadmin_headers
    )

    assert respuesta.status_code == 409
    assert "correo" in respuesta.json()["detail"].lower()


@pytest.mark.asyncio
async def test_correo_del_administrador_duplicado_es_rechazado(
    client: AsyncClient, superadmin_headers: dict
):
    datos = _payload()
    assert (
        await client.post("/api/v1/saas/tenants", json=datos, headers=superadmin_headers)
    ).status_code == 201

    repetido = _payload(admin_correo=datos["admin_correo"])
    respuesta = await client.post(
        "/api/v1/saas/tenants", json=repetido, headers=superadmin_headers
    )

    assert respuesta.status_code == 409
    assert "administrador" in respuesta.json()["detail"].lower()


@pytest.mark.asyncio
async def test_slug_repetido_se_desambigua(
    client: AsyncClient, superadmin_headers: dict
):
    datos = _payload()
    primero = (
        await client.post("/api/v1/saas/tenants", json=datos, headers=superadmin_headers)
    ).json()

    segundo = _payload(slug=datos["slug"])
    respuesta = await client.post(
        "/api/v1/saas/tenants", json=segundo, headers=superadmin_headers
    )

    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["slug"] == f"{datos['slug']}-2"
    assert respuesta.json()["id"] != primero["id"]


@pytest.mark.asyncio
async def test_plan_inexistente_impide_el_alta(
    client: AsyncClient, superadmin_headers: dict
):
    respuesta = await client.post(
        "/api/v1/saas/tenants",
        json=_payload(plan_id=999_999),
        headers=superadmin_headers,
    )

    assert respuesta.status_code == 400
    assert "plan" in respuesta.json()["detail"].lower()


@pytest.mark.asyncio
async def test_fallo_de_envio_de_correo_devuelve_credenciales_temporales(
    client: AsyncClient, superadmin_headers: dict
):
    """Sin proveedor SMTP configurado el alta no se revierte (plan, CU01).

    Las credenciales se reemiten y el administrador puede entrar con la
    contraseña temporal devuelta.
    """
    datos = _payload()
    tenant = (
        await client.post("/api/v1/saas/tenants", json=datos, headers=superadmin_headers)
    ).json()

    reenvio = await client.post(
        f"/api/v1/saas/tenants/{tenant['id']}/reenviar-credenciales",
        headers=superadmin_headers,
    )

    assert reenvio.status_code == 200, reenvio.text
    cuerpo = reenvio.json()
    assert cuerpo["correo"] == datos["admin_correo"]
    assert cuerpo["contrasena_temporal"]
    assert cuerpo["contrasena_temporal"] != datos["admin_contrasena"]

    login = await client.post(
        "/api/v1/auth/login",
        json={
            "correo": cuerpo["correo"],
            "contrasena": cuerpo["contrasena_temporal"],
        },
    )
    assert login.status_code == 200, login.text
    assert login.json()["debe_cambiar_contrasena"] is True


@pytest.mark.asyncio
async def test_solo_el_super_administrador_puede_dar_de_alta_tenants(
    client: AsyncClient, admin_headers: dict
):
    respuesta = await client.post(
        "/api/v1/saas/tenants", json=_payload(), headers=admin_headers
    )

    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "FUNCIONALIDAD_NO_DISPONIBLE"


@pytest.mark.asyncio
async def test_el_tenant_demo_no_se_duplica_en_el_alta(
    client: AsyncClient, superadmin_headers: dict, demo_tenant: Tenant
):
    respuesta = await client.post(
        "/api/v1/saas/tenants",
        json=_payload(nit=demo_tenant.nit),
        headers=superadmin_headers,
    )

    assert respuesta.status_code == 409

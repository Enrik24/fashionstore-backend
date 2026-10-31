"""[EXAMEN-2] Autenticación multitenant (plan, 9.1.10).

Token con tenant, token sin tenant (Super Administrador), tenant suspendido,
refresh cruzado entre tenants y operación del Super Administrador con la
cabecera `X-Tenant-ID` (plan, 4.2).
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.apps.gestion_usuarios.models import Usuario
from app.security import create_access_token, create_refresh_token, decode_token

ADMIN_CORREO = "admin@fashionstore.com"
ADMIN_CONTRASENA = "Admin123!"


@pytest.mark.asyncio
async def test_token_de_tenant_incluye_tenant_y_roles(
    client: AsyncClient, demo_tenant
):
    respuesta = await client.post(
        "/api/v1/auth/login",
        json={"correo": ADMIN_CORREO, "contrasena": ADMIN_CONTRASENA},
    )

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["tenant_id"] == demo_tenant.id
    assert cuerpo["es_super_admin"] is False
    assert "Administrador" in cuerpo["roles"]
    assert cuerpo["tenant"]["slug"] == demo_tenant.slug
    assert cuerpo["suscripcion"]["estado"] == "ACTIVA"

    payload = decode_token(cuerpo["access_token"])
    assert payload["tenant_id"] == demo_tenant.id
    assert payload["es_super_admin"] is False
    assert "Administrador" in payload["roles"]
    assert payload["type"] == "access"

    refresh_payload = decode_token(cuerpo["refresh_token"])
    assert refresh_payload["tenant_id"] == demo_tenant.id
    assert refresh_payload["type"] == "refresh"


@pytest.mark.asyncio
async def test_token_de_superadmin_no_tiene_tenant(client: AsyncClient):
    respuesta = await client.post(
        "/api/v1/auth/login",
        json={"correo": "superadmin@fashionstore.com", "contrasena": "SuperAdmin123!"},
    )

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["tenant_id"] is None
    assert cuerpo["es_super_admin"] is True
    assert "Super Administrador" in cuerpo["roles"]
    assert cuerpo["tenant"] is None
    assert cuerpo["suscripcion"] is None

    payload = decode_token(cuerpo["access_token"])
    assert payload["tenant_id"] is None
    assert payload["es_super_admin"] is True


@pytest.mark.asyncio
async def test_superadmin_opera_sobre_un_tenant_con_x_tenant_id(
    client: AsyncClient, superadmin_headers: dict, demo_tenant
):
    sin_cabecera = await client.get(
        "/api/v1/saas/configuracion", headers=superadmin_headers
    )
    assert sin_cabecera.status_code == 404, sin_cabecera.text
    assert sin_cabecera.json()["codigo"] == "RECURSO_NO_ENCONTRADO"

    con_cabecera = await client.get(
        "/api/v1/saas/configuracion",
        headers={**superadmin_headers, "X-Tenant-ID": str(demo_tenant.id)},
    )
    assert con_cabecera.status_code == 200, con_cabecera.text
    assert con_cabecera.json()["tenant"]["id"] == demo_tenant.id

    inexistente = await client.get(
        "/api/v1/saas/configuracion",
        headers={**superadmin_headers, "X-Tenant-ID": "999999"},
    )
    assert inexistente.status_code == 404
    assert inexistente.json()["codigo"] == "RECURSO_NO_ENCONTRADO"

    invalida = await client.get(
        "/api/v1/saas/configuracion",
        headers={**superadmin_headers, "X-Tenant-ID": "no-es-un-numero"},
    )
    assert invalida.status_code == 400


@pytest.mark.asyncio
async def test_usuario_de_tenant_no_accede_a_endpoints_de_plataforma(
    client: AsyncClient, admin_headers: dict
):
    listado = await client.get("/api/v1/saas/tenants", headers=admin_headers)
    planes = await client.get("/api/v1/saas/planes", headers=admin_headers)

    assert listado.status_code == 403, listado.text
    assert listado.json()["codigo"] == "FUNCIONALIDAD_NO_DISPONIBLE"
    assert planes.status_code == 403, planes.text
    assert planes.json()["codigo"] == "FUNCIONALIDAD_NO_DISPONIBLE"


@pytest.mark.asyncio
async def test_superadmin_no_es_afectado_por_el_tenant_del_token(
    client: AsyncClient, superadmin_headers: dict
):
    respuesta = await client.get(
        "/api/v1/saas/tenants",
        params={"q": "fashionstore-demo"},
        headers=superadmin_headers,
    )

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["total"] == 1
    assert cuerpo["items"][0]["slug"] == "fashionstore-demo"


@pytest.mark.asyncio
async def test_token_con_tenant_manipulado_es_rechazado(
    client: AsyncClient, demo_tenant, db_session
):
    resultado = await db_session.execute(
        select(Usuario).where(Usuario.correo == ADMIN_CORREO)
    )
    usuario = resultado.scalar_one()

    # El mismo usuario, pero con un tenant distinto al suyo en los claims.
    token = create_access_token(
        {
            "sub": str(usuario.id),
            "correo": usuario.correo,
            "tenant_id": demo_tenant.id + 1,
            "es_super_admin": False,
            "roles": ["Administrador"],
        }
    )

    respuesta = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert respuesta.status_code == 401, respuesta.text
    assert "otro tenant" in respuesta.json()["detail"]


@pytest.mark.asyncio
async def test_token_invalido_o_manipulado_es_rechazado(client: AsyncClient):
    respuesta = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer token.invalido.firma"}
    )
    assert respuesta.status_code == 401

    sin_token = await client.get("/api/v1/auth/me")
    assert sin_token.status_code in (401, 403)


@pytest.mark.asyncio
async def test_tenant_suspendido_no_emite_tokens_y_bloquea_los_vigentes(
    client: AsyncClient, superadmin_headers: dict, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)
    token_anterior = tenant["tokens"]["access_token"]

    suspension = await client.patch(
        f"/api/v1/saas/tenants/{tenant['id']}/estado",
        json={"estado": "SUSPENDIDO", "motivo": "Falta de pago"},
        headers=superadmin_headers,
    )
    assert suspension.status_code == 200, suspension.text
    assert suspension.json()["estado"] == "SUSPENDIDO"

    login = await client.post(
        "/api/v1/auth/login",
        json={
            "correo": tenant["payload"]["admin_correo"],
            "contrasena": tenant["payload"]["admin_contrasena"],
        },
    )
    assert login.status_code == 402, login.text
    assert login.json()["codigo"] == "SUSCRIPCION_REQUERIDA"

    operacion = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token_anterior}"},
    )
    assert operacion.status_code == 403, operacion.text
    assert "suspendida" in operacion.json()["detail"]

    refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tenant["tokens"]["refresh_token"]}
    )
    assert refresh.status_code == 401, refresh.text


@pytest.mark.asyncio
async def test_refresh_cruzado_entre_tenants_es_rechazado(
    client: AsyncClient, tenant_nuevo, plan_basico, demo_tenant
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)

    payload = decode_token(tenant["tokens"]["access_token"])
    usuario_id = int(payload["sub"])
    assert payload["tenant_id"] == tenant["id"]

    # Token de refresco firmado, pero con el tenant de otro cliente.
    cruzado = create_refresh_token(
        {
            "sub": str(usuario_id),
            "correo": tenant["payload"]["admin_correo"],
            "tenant_id": demo_tenant.id,
            "es_super_admin": False,
            "roles": ["Administrador"],
        }
    )

    respuesta = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": cruzado}
    )

    assert respuesta.status_code == 401, respuesta.text
    assert "otro tenant" in respuesta.json()["detail"]


@pytest.mark.asyncio
async def test_refresh_propio_conserva_el_tenant(
    client: AsyncClient, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)

    respuesta = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tenant["tokens"]["refresh_token"]}
    )

    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["tenant_id"] == tenant["id"]
    assert cuerpo["es_super_admin"] is False
    assert decode_token(cuerpo["access_token"])["tenant_id"] == tenant["id"]
    assert decode_token(cuerpo["refresh_token"])["type"] == "refresh"


@pytest.mark.asyncio
async def test_un_token_de_acceso_no_sirve_para_refrescar(
    client: AsyncClient, admin_auth_tokens: dict
):
    respuesta = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": admin_auth_tokens["access_token"]},
    )

    assert respuesta.status_code == 401, respuesta.text

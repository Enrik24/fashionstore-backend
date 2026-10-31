"""[EXAMEN-2] CU03 — Gestión de suscripciones (plan 9.1.10).

Asignación, cambio de plan, renovación, suspensión, reactivación,
cancelación, límite de sucursales y vencimiento (RN-002 / RN-004).
"""
from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.apps.gestion_saas.models import EstadoSuscripcion, Suscripcion


async def _suscripcion(cliente: AsyncClient, headers: dict, tenant_id: int) -> dict:
    detalle = await cliente.get(f"/api/v1/saas/tenants/{tenant_id}", headers=headers)
    assert detalle.status_code == 200, detalle.text
    return detalle.json()["suscripcion"]


@pytest.mark.asyncio
async def test_asignar_suscripcion_a_un_tenant_sin_suscripcion_activa(
    client: AsyncClient, superadmin_headers: dict, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)
    suscripcion = await _suscripcion(client, superadmin_headers, tenant["id"])

    cancelacion = await client.post(
        f"/api/v1/saas/suscripciones/{suscripcion['id']}/cancelar",
        headers=superadmin_headers,
    )
    assert cancelacion.status_code == 200, cancelacion.text

    asignacion = await client.post(
        "/api/v1/saas/suscripciones",
        json={"tenant_id": tenant["id"], "plan_id": plan_basico.id},
        headers=superadmin_headers,
    )

    assert asignacion.status_code == 201, asignacion.text
    cuerpo = asignacion.json()
    assert cuerpo["tenant_id"] == tenant["id"]
    assert cuerpo["estado"] == EstadoSuscripcion.ACTIVA.value
    assert cuerpo["fecha_vencimiento"] == (date.today() + timedelta(days=30)).isoformat()


@pytest.mark.asyncio
async def test_no_se_pueden_tener_dos_suscripciones_activas(
    client: AsyncClient, superadmin_headers: dict, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)

    respuesta = await client.post(
        "/api/v1/saas/suscripciones",
        json={"tenant_id": tenant["id"], "plan_id": plan_basico.id},
        headers=superadmin_headers,
    )

    assert respuesta.status_code == 409
    assert "suscripción activa" in respuesta.json()["detail"].lower()


@pytest.mark.asyncio
async def test_cambio_de_plan(
    client: AsyncClient,
    superadmin_headers: dict,
    tenant_nuevo,
    plan_basico,
    plan_profesional,
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)
    suscripcion = await _suscripcion(client, superadmin_headers, tenant["id"])

    respuesta = await client.put(
        f"/api/v1/saas/suscripciones/{suscripcion['id']}/plan",
        json={"plan_id": plan_profesional.id},
        headers=superadmin_headers,
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["plan_id"] == plan_profesional.id

    # El cambio se refleja en el plan del tenant, sin tocar el de otros
    mi_plan = await client.get("/api/v1/saas/mi-plan/funcionalidades", headers=tenant["headers"])
    assert mi_plan.json()["plan"] == plan_profesional.nombre
    assert mi_plan.json()["reportes_ia"] is True


@pytest.mark.asyncio
async def test_renovacion_extiende_treinta_dias(
    client: AsyncClient, superadmin_headers: dict, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)
    suscripcion = await _suscripcion(client, superadmin_headers, tenant["id"])
    vencimiento_previo = date.fromisoformat(suscripcion["fecha_vencimiento"])

    respuesta = await client.post(
        f"/api/v1/saas/suscripciones/{suscripcion['id']}/renovar",
        headers=superadmin_headers,
    )

    assert respuesta.status_code == 200, respuesta.text
    nuevo = date.fromisoformat(respuesta.json()["fecha_vencimiento"])
    assert nuevo == max(date.today(), vencimiento_previo) + timedelta(days=30)


@pytest.mark.asyncio
async def test_suspension_bloquea_al_tenant(
    client: AsyncClient, superadmin_headers: dict, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)
    suscripcion = await _suscripcion(client, superadmin_headers, tenant["id"])

    respuesta = await client.post(
        f"/api/v1/saas/suscripciones/{suscripcion['id']}/suspender",
        headers=superadmin_headers,
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["estado"] == EstadoSuscripcion.SUSPENDIDA.value

    # El tenant queda suspendido y el token vigente deja de operar (RN-004)
    acceso = await client.get("/api/v1/saas/configuracion", headers=tenant["headers"])
    assert acceso.status_code == 403
    assert "suspendida" in acceso.json()["detail"].lower()

    # Y tampoco puede emitir tokens nuevos
    login = await client.post(
        "/api/v1/auth/login",
        json={
            "correo": tenant["payload"]["admin_correo"],
            "contrasena": tenant["payload"]["admin_contrasena"],
        },
    )
    assert login.status_code == 402


@pytest.mark.asyncio
async def test_reactivacion_devuelve_al_tenant_a_operativo(
    client: AsyncClient, superadmin_headers: dict, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)
    suscripcion = await _suscripcion(client, superadmin_headers, tenant["id"])
    await client.post(
        f"/api/v1/saas/suscripciones/{suscripcion['id']}/suspender",
        headers=superadmin_headers,
    )

    respuesta = await client.post(
        f"/api/v1/saas/suscripciones/{suscripcion['id']}/reactivar",
        headers=superadmin_headers,
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["estado"] == EstadoSuscripcion.ACTIVA.value

    detalle = await client.get(
        f"/api/v1/saas/tenants/{tenant['id']}", headers=superadmin_headers
    )
    assert detalle.json()["estado"] == "OPERATIVO"
    assert (
        await client.get("/api/v1/saas/configuracion", headers=tenant["headers"])
    ).status_code == 200


@pytest.mark.asyncio
async def test_cancelacion_deja_al_tenant_cancelado(
    client: AsyncClient, superadmin_headers: dict, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)
    suscripcion = await _suscripcion(client, superadmin_headers, tenant["id"])

    respuesta = await client.post(
        f"/api/v1/saas/suscripciones/{suscripcion['id']}/cancelar",
        headers=superadmin_headers,
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["estado"] == EstadoSuscripcion.CANCELADA.value

    detalle = await client.get(
        f"/api/v1/saas/tenants/{tenant['id']}", headers=superadmin_headers
    )
    assert detalle.json()["estado"] == "CANCELADO"
    assert detalle.json()["fecha_baja"] is not None


@pytest.mark.asyncio
async def test_limite_de_sucursales_del_plan(
    client: AsyncClient, superadmin_headers: dict, tenant_nuevo, plan_basico
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)  # matriz = 1 sucursal
    assert plan_basico.max_sucursales == 2

    segunda = await client.post(
        "/api/v1/saas/configuracion/sucursales",
        json={"nombre": "Sucursal 2", "direccion": "Calle 2", "telefono": "70000001"},
        headers=tenant["headers"],
    )
    assert segunda.status_code == 201, segunda.text

    tercera = await client.post(
        "/api/v1/saas/configuracion/sucursales",
        json={"nombre": "Sucursal 3", "direccion": "Calle 3", "telefono": "70000002"},
        headers=tenant["headers"],
    )

    assert tercera.status_code == 409, tercera.text
    assert tercera.json()["codigo"] == "LIMITE_PLAN_EXCEDIDO"


@pytest.mark.asyncio
async def test_suscripcion_vencida_bloquea_el_acceso(
    client: AsyncClient,
    superadmin_headers: dict,
    tenant_nuevo,
    plan_basico,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)
    suscripcion = await _suscripcion(client, superadmin_headers, tenant["id"])

    resultado = await db_session.execute(
        select(Suscripcion).where(Suscripcion.id == suscripcion["id"])
    )
    registro = resultado.scalar_one()
    registro.fecha_inicio = date.today() - timedelta(days=60)
    registro.fecha_vencimiento = date.today() - timedelta(days=1)
    await db_session.commit()

    login = await client.post(
        "/api/v1/auth/login",
        json={
            "correo": tenant["payload"]["admin_correo"],
            "contrasena": tenant["payload"]["admin_contrasena"],
        },
    )

    assert login.status_code == 402, login.text
    assert login.json()["codigo"] == "SUSCRIPCION_REQUERIDA"


@pytest.mark.asyncio
async def test_suscripcion_que_vence_hoy_sigue_vigente(
    client: AsyncClient, superadmin_headers: dict, tenant_nuevo, plan_basico, db_session
):
    tenant = await tenant_nuevo(plan_id=plan_basico.id)
    suscripcion = await _suscripcion(client, superadmin_headers, tenant["id"])

    resultado = await db_session.execute(
        select(Suscripcion).where(Suscripcion.id == suscripcion["id"])
    )
    registro = resultado.scalar_one()
    registro.fecha_inicio = date.today() - timedelta(days=60)
    registro.fecha_vencimiento = date.today()  # límite exacto: aún vale
    await db_session.commit()

    login = await client.post(
        "/api/v1/auth/login",
        json={
            "correo": tenant["payload"]["admin_correo"],
            "contrasena": tenant["payload"]["admin_contrasena"],
        },
    )

    assert login.status_code == 200, login.text

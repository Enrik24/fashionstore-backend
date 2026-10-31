"""[EXAMEN-2] CU02 — Configuración de planes (plan 9.1.10).

Cubre el CRUD de planes, la unicidad de nombre, la coherencia de límites,
el impacto de un downgrade y la protección de planes con suscripciones.
"""
import uuid

import pytest
from httpx import AsyncClient

from app.apps.gestion_saas.models import Plan


def _plan(**overrides) -> dict:
    sufijo = uuid.uuid4().hex[:8]
    datos = {
        "nombre": f"Plan {sufijo}",
        "descripcion": "Plan de prueba",
        "precio_mensual": 150.0,
        "max_sucursales": 3,
        "permite_reportes_ia": False,
        "permite_pago_diferido": True,
        "estado": "ACTIVO",
        "orden": 50,
        "soporte_prioritario": False,
    }
    datos.update(overrides)
    return datos


@pytest.mark.asyncio
async def test_crear_y_consultar_plan(client: AsyncClient, superadmin_headers: dict):
    datos = _plan()

    creacion = await client.post(
        "/api/v1/saas/planes", json=datos, headers=superadmin_headers
    )

    assert creacion.status_code == 201, creacion.text
    plan = creacion.json()
    assert plan["nombre"] == datos["nombre"]
    assert plan["max_sucursales"] == 3

    detalle = await client.get(
        f"/api/v1/saas/planes/{plan['id']}", headers=superadmin_headers
    )
    assert detalle.status_code == 200
    assert detalle.json()["id"] == plan["id"]
    assert detalle.json()["suscripciones_activas"] == 0


@pytest.mark.asyncio
async def test_nombre_de_plan_es_unico(client: AsyncClient, superadmin_headers: dict):
    datos = _plan()
    assert (
        await client.post("/api/v1/saas/planes", json=datos, headers=superadmin_headers)
    ).status_code == 201

    respuesta = await client.post(
        "/api/v1/saas/planes",
        json=_plan(nombre=datos["nombre"]),
        headers=superadmin_headers,
    )

    assert respuesta.status_code == 409
    assert "nombre" in respuesta.json()["detail"].lower()


@pytest.mark.asyncio
async def test_limites_incoherentes_son_rechazados(
    client: AsyncClient, superadmin_headers: dict
):
    sin_sucursales = await client.post(
        "/api/v1/saas/planes",
        json=_plan(max_sucursales=0),
        headers=superadmin_headers,
    )
    precio_negativo = await client.post(
        "/api/v1/saas/planes",
        json=_plan(precio_mensual=-10),
        headers=superadmin_headers,
    )

    assert sin_sucursales.status_code == 422
    assert precio_negativo.status_code == 422


@pytest.mark.asyncio
async def test_actualizar_plan_cambia_limites_y_funcionalidades(
    client: AsyncClient, superadmin_headers: dict
):
    plan = (
        await client.post(
            "/api/v1/saas/planes", json=_plan(), headers=superadmin_headers
        )
    ).json()

    respuesta = await client.put(
        f"/api/v1/saas/planes/{plan['id']}",
        json={"max_sucursales": 7, "permite_reportes_ia": True},
        headers=superadmin_headers,
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["max_sucursales"] == 7
    assert respuesta.json()["permite_reportes_ia"] is True


@pytest.mark.asyncio
async def test_no_se_desactiva_un_plan_con_suscripciones_activas(
    client: AsyncClient, superadmin_headers: dict, plan_profesional: Plan
):
    respuesta = await client.put(
        f"/api/v1/saas/planes/{plan_profesional.id}",
        json={"estado": "INACTIVO"},
        headers=superadmin_headers,
    )

    assert respuesta.status_code == 409, respuesta.text
    assert "suscripciones" in respuesta.json()["detail"].lower()


@pytest.mark.asyncio
async def test_no_se_elimina_un_plan_con_suscripciones(
    client: AsyncClient, superadmin_headers: dict, tenant_nuevo, plan_basico: Plan
):
    # Un tenant con el plan Básico impide eliminarlo (integridad comercial).
    await tenant_nuevo(plan_id=plan_basico.id)

    respuesta = await client.delete(
        f"/api/v1/saas/planes/{plan_basico.id}", headers=superadmin_headers
    )

    assert respuesta.status_code == 409
    assert "suscripciones" in respuesta.json()["detail"].lower()


@pytest.mark.asyncio
async def test_eliminar_plan_sin_suscripciones(
    client: AsyncClient, superadmin_headers: dict
):
    plan = (
        await client.post(
            "/api/v1/saas/planes", json=_plan(), headers=superadmin_headers
        )
    ).json()

    respuesta = await client.delete(
        f"/api/v1/saas/planes/{plan['id']}", headers=superadmin_headers
    )

    assert respuesta.status_code == 200
    assert respuesta.json()["eliminado"] is True
    assert (
        await client.get(f"/api/v1/saas/planes/{plan['id']}", headers=superadmin_headers)
    ).status_code == 404


@pytest.mark.asyncio
async def test_impacto_de_downgrade_detecta_tenants_afectados(
    client: AsyncClient,
    superadmin_headers: dict,
    tenant_nuevo,
    plan_profesional: Plan,
    plan_basico: Plan,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)

    for nombre in ("Sucursal Sur", "Sucursal Norte"):
        creacion = await client.post(
            "/api/v1/saas/configuracion/sucursales",
            json={
                "nombre": nombre,
                "direccion": "Calle 1 #100",
                "telefono": "70000000",
            },
            headers=tenant["headers"],
        )
        assert creacion.status_code == 201, creacion.text

    impacto = await client.get(
        f"/api/v1/saas/planes/{plan_basico.id}/impacto", headers=superadmin_headers
    )

    assert impacto.status_code == 200, impacto.text
    cuerpo = impacto.json()
    assert cuerpo["max_sucursales"] == plan_basico.max_sucursales
    afectados = {item["tenant_id"] for item in cuerpo["tenants_afectados"]}
    assert tenant["id"] in afectados


@pytest.mark.asyncio
async def test_downgrade_por_encima_del_limite_es_bloqueado(
    client: AsyncClient,
    superadmin_headers: dict,
    tenant_nuevo,
    plan_profesional: Plan,
    plan_basico: Plan,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    for nombre in ("Sucursal Sur", "Sucursal Norte"):
        assert (
            await client.post(
                "/api/v1/saas/configuracion/sucursales",
                json={"nombre": nombre, "direccion": "Calle 1", "telefono": "70000000"},
                headers=tenant["headers"],
            )
        ).status_code == 201

    detalle = (
        await client.get(
            f"/api/v1/saas/tenants/{tenant['id']}", headers=superadmin_headers
        )
    ).json()

    respuesta = await client.put(
        f"/api/v1/saas/suscripciones/{detalle['suscripcion']['id']}/plan",
        json={"plan_id": plan_basico.id},
        headers=superadmin_headers,
    )

    assert respuesta.status_code == 409, respuesta.text
    assert respuesta.json()["codigo"] == "LIMITE_PLAN_EXCEDIDO"

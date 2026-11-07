"""[EXAMEN-2] Fase A — tipo de tela y composición (RF-033 / RN-021)."""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.apps.gestion_catalogo.models import Producto


def _sufijo() -> str:
    return uuid.uuid4().hex[:8]


@pytest.mark.asyncio
async def test_crud_tipo_tela_y_aislamiento(
    client: AsyncClient, tenant_nuevo, plan_profesional, db_session
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    otro = await tenant_nuevo(plan_id=plan_profesional.id)

    creado = await client.post(
        "/api/v1/tipos-tela/",
        json={"nombre": "Algodón", "descripcion": "Fibra natural"},
        headers=tenant["headers"],
    )
    assert creado.status_code == 201, creado.text
    tipo = creado.json()
    assert tipo["tenant_id"] == int(tenant["id"])
    assert tipo["estado"] == "ACTIVO"

    duplicado = await client.post(
        "/api/v1/tipos-tela/",
        json={"nombre": " algodón "},
        headers=tenant["headers"],
    )
    assert duplicado.status_code == 409, duplicado.text

    # Mismo nombre en otro tenant: no hay conflicto.
    ajeno = await client.post(
        "/api/v1/tipos-tela/",
        json={"nombre": "Algodón"},
        headers=otro["headers"],
    )
    assert ajeno.status_code == 201, ajeno.text

    listado = await client.get("/api/v1/tipos-tela/", headers=tenant["headers"])
    assert listado.status_code == 200, listado.text
    ids = [item["id"] for item in listado.json()]
    assert tipo["id"] in ids
    assert ajeno.json()["id"] not in ids

    detalle = await client.get(
        f"/api/v1/tipos-tela/{tipo['id']}", headers=tenant["headers"]
    )
    assert detalle.status_code == 200

    actualizado = await client.put(
        f"/api/v1/tipos-tela/{tipo['id']}",
        json={"cuidados_lavado": "Lavar a 30°C"},
        headers=tenant["headers"],
    )
    assert actualizado.status_code == 200
    assert actualizado.json()["cuidados_lavado"] == "Lavar a 30°C"


@pytest.mark.asyncio
async def test_producto_con_tipo_de_tela_y_filtro(
    client: AsyncClient, tenant_nuevo, plan_profesional, db_session
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    tipo = (await client.post(
        "/api/v1/tipos-tela/",
        json={"nombre": "Denim"},
        headers=tenant["headers"],
    )).json()

    sufijo = _sufijo()
    producto = await client.post(
        "/api/v1/productos/",
        json={
            "sku": f"SKU-{sufijo}",
            "nombre": f"Jean {sufijo}",
            "precio": "150.00",
            "tipo_tela_id": tipo["id"],
            "composicion": "98% algodón, 2% elastano",
        },
        headers=tenant["headers"],
    )
    assert producto.status_code in (200, 201), producto.text
    cuerpo = producto.json()
    assert cuerpo["tipo_tela_id"] == tipo["id"]
    assert cuerpo["composicion"] == "98% algodón, 2% elastano"
    assert cuerpo["tipo_tela"]["nombre"] == "Denim"

    filtrado = await client.get(
        f"/api/v1/productos/?tipo_tela_id={tipo['id']}", headers=tenant["headers"]
    )
    assert filtrado.status_code == 200, filtrado.text
    assert [item["id"] for item in filtrado.json()] == [cuerpo["id"]]

    por_composicion = await client.get(
        "/api/v1/productos/?composicion=elastano", headers=tenant["headers"]
    )
    assert por_composicion.status_code == 200
    assert cuerpo["id"] in [item["id"] for item in por_composicion.json()]


@pytest.mark.asyncio
async def test_producto_con_tela_de_otro_tenant_es_rechazado(
    client: AsyncClient, tenant_nuevo, plan_profesional
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    otro = await tenant_nuevo(plan_id=plan_profesional.id)
    tipo_ajeno = (await client.post(
        "/api/v1/tipos-tela/",
        json={"nombre": "Lana"},
        headers=otro["headers"],
    )).json()

    respuesta = await client.post(
        "/api/v1/productos/",
        json={
            "sku": f"SKU-{_sufijo()}",
            "nombre": "Prenda ajena",
            "precio": "80.00",
            "tipo_tela_id": tipo_ajeno["id"],
        },
        headers=tenant["headers"],
    )
    assert respuesta.status_code in (400, 422), respuesta.text


@pytest.mark.asyncio
async def test_baja_logica_conserva_la_referencia_del_producto(
    client: AsyncClient, tenant_nuevo, plan_profesional, db_session
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    tipo = (await client.post(
        "/api/v1/tipos-tela/",
        json={"nombre": "Lino"},
        headers=tenant["headers"],
    )).json()
    producto = (await client.post(
        "/api/v1/productos/",
        json={
            "sku": f"SKU-{_sufijo()}",
            "nombre": "Camisa de lino",
            "precio": "200.00",
            "tipo_tela_id": tipo["id"],
        },
        headers=tenant["headers"],
    )).json()

    borrado = await client.delete(
        f"/api/v1/tipos-tela/{tipo['id']}", headers=tenant["headers"]
    )
    assert borrado.status_code == 200, borrado.text

    listado = await client.get("/api/v1/tipos-tela/", headers=tenant["headers"])
    assert tipo["id"] not in [item["id"] for item in listado.json()]

    fila = (await db_session.execute(
        select(Producto).where(Producto.id == producto["id"])
    )).scalar_one()
    assert fila.tipo_tela_id == tipo["id"]

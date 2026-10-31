"""[EXAMEN-2] CU14 — Traspasos entre sucursales (plan, 9.3.8 y 9.3.11).

Cubre la creación con una y varias líneas, el stock insuficiente por línea, la
recepción total, la recepción parcial con merma, el rechazo por línea, la
cancelación, las sucursales de otro tenant, la regla RN-013 y el PDF.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.apps.gestion_catalogo.models import Inventario, MovimientoInventario, TipoMovimiento
from app.apps.gestion_comercial_logistica.models import Traspaso
from tests._fase3_helpers import crear_sucursal


async def _crear_traspaso(
    client: AsyncClient,
    tenant: dict,
    origen_id: int,
    destino_id: int,
    lineas: list[tuple[int, int]],
    *,
    motivo: str = "Reabastecimiento",
):
    return await client.post(
        "/api/v1/traspasos",
        json={
            "sucursal_origen_id": int(origen_id),
            "sucursal_destino_id": int(destino_id),
            "motivo": motivo,
            "detalles": [
                {"variante_producto_id": int(variante_id), "cantidad": int(cantidad)}
                for variante_id, cantidad in lineas
            ],
        },
        headers=tenant["headers"],
    )


async def _stock(db_session, tenant, sucursal_id: int, variante_id: int) -> int:
    inventario = (await db_session.execute(
        select(Inventario).where(
            Inventario.tenant_id == int(tenant["id"]),
            Inventario.sucursal_id == int(sucursal_id),
            Inventario.variante_producto_id == int(variante_id),
        )
    )).scalar_one_or_none()
    return int(inventario.cantidad) if inventario else 0


def _movimientos(db_session, tenant, tipo: TipoMovimiento):
    return db_session.execute(
        select(MovimientoInventario).where(
            MovimientoInventario.tenant_id == int(tenant["id"]),
            MovimientoInventario.tipo == tipo,
        )
    )


@pytest.mark.asyncio
async def test_creacion_con_una_y_varias_lineas(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    variante_a = await catalogo_tenant(tenant, precio="100.00", stock=50)
    variante_b = await catalogo_tenant(tenant, precio="120.00", stock=40)
    destino = await crear_sucursal(db_session, tenant)

    origen_id = variante_a["sucursal_id"]

    # Una sola línea.
    simple = await _crear_traspaso(
        client, tenant, origen_id, destino["id"], [(variante_a["variante_id"], 5)]
    )
    assert simple.status_code == 201, simple.text
    cuerpo = simple.json()
    assert cuerpo["estado"] == "EN_TRANSITO"
    assert cuerpo["cantidad_pendiente"] == 5
    assert cuerpo["sucursal_origen_id"] == origen_id
    assert cuerpo["sucursal_destino_id"] == destino["id"]
    assert cuerpo["detalles"][0]["cantidad"] == 5
    assert cuerpo["detalles"][0]["cantidad_recibida"] == 0
    assert cuerpo["detalles"][0]["cantidad_merma"] == 0
    assert cuerpo["fecha_envio"] is not None

    assert await _stock(
        db_session, tenant, origen_id, variante_a["variante_id"]
    ) == 45
    assert await _stock(
        db_session, tenant, destino["id"], variante_a["variante_id"]
    ) == 0

    # Varias líneas.
    multiple = await _crear_traspaso(
        client,
        tenant,
        origen_id,
        destino["id"],
        [
            (variante_a["variante_id"], 3),
            (variante_b["variante_id"], 4),
        ],
    )
    assert multiple.status_code == 201, multiple.text
    cuerpo_multiple = multiple.json()
    assert cuerpo_multiple["cantidad_pendiente"] == 7
    assert len(cuerpo_multiple["detalles"]) == 2
    assert await _stock(
        db_session, tenant, origen_id, variante_a["variante_id"]
    ) == 42
    assert await _stock(
        db_session, tenant, origen_id, variante_b["variante_id"]
    ) == 36

    salidas = (await _movimientos(
        db_session, tenant, TipoMovimiento.TRASPASO_SALIDA
    )).scalars().all()
    cantidades = sorted(mov.cantidad for mov in salidas)
    assert cantidades == [-5, -4, -3]

    listado = await client.get(
        "/api/v1/traspasos?estado=EN_TRANSITO", headers=tenant["headers"]
    )
    assert listado.status_code == 200, listado.text
    assert len(listado.json()["traspasos"]) == 2


@pytest.mark.asyncio
async def test_stock_insuficiente_en_una_linea_no_modifica_nada(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    variante_a = await catalogo_tenant(tenant, stock=10)
    variante_b = await catalogo_tenant(tenant, stock=2)
    destino = await crear_sucursal(db_session, tenant)
    origen_id = variante_a["sucursal_id"]

    respuesta = await _crear_traspaso(
        client,
        tenant,
        origen_id,
        destino["id"],
        [
            (variante_a["variante_id"], 5),
            (variante_b["variante_id"], 10),
        ],
    )
    assert respuesta.status_code == 400, respuesta.text
    assert "stock insuficiente" in respuesta.json()["detail"].lower()

    # La validación previa evita descontar la línea que sí alcanzaba.
    assert await _stock(
        db_session, tenant, origen_id, variante_a["variante_id"]
    ) == 10
    assert await _stock(
        db_session, tenant, origen_id, variante_b["variante_id"]
    ) == 2
    total = (await db_session.execute(
        select(func.count(Traspaso.id)).where(
            Traspaso.tenant_id == int(tenant["id"])
        )
    )).scalar_one()
    assert total == 0


@pytest.mark.asyncio
async def test_recepcion_total_y_recepcion_duplicada(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    variante = await catalogo_tenant(tenant, stock=20)
    destino = await crear_sucursal(db_session, tenant)
    origen_id = variante["sucursal_id"]

    creado = await _crear_traspaso(
        client, tenant, origen_id, destino["id"], [(variante["variante_id"], 5)]
    )
    assert creado.status_code == 201, creado.text
    traspaso_id = creado.json()["id"]

    recibido = await client.post(
        f"/api/v1/traspasos/{traspaso_id}/recibir",
        json={},
        headers=tenant["headers"],
    )
    assert recibido.status_code == 200, recibido.text
    cuerpo = recibido.json()
    assert cuerpo["estado"] == "RECIBIDO"
    assert cuerpo["cantidad_pendiente"] == 0
    assert cuerpo["detalles"][0]["cantidad_recibida"] == 5
    assert cuerpo["detalles"][0]["cantidad_merma"] == 0
    assert cuerpo["fecha_recepcion"] is not None

    assert await _stock(
        db_session, tenant, destino["id"], variante["variante_id"]
    ) == 5
    entradas = (await _movimientos(
        db_session, tenant, TipoMovimiento.TRASPASO_ENTRADA
    )).scalars().all()
    assert [mov.cantidad for mov in entradas] == [5]

    duplicada = await client.post(
        f"/api/v1/traspasos/{traspaso_id}/recibir",
        json={},
        headers=tenant["headers"],
    )
    assert duplicada.status_code == 409, duplicada.text


@pytest.mark.asyncio
async def test_recepcion_parcial_con_merma_cumple_rn_013(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    variante = await catalogo_tenant(tenant, stock=20)
    destino = await crear_sucursal(db_session, tenant)

    creado = await _crear_traspaso(
        client,
        tenant,
        variante["sucursal_id"],
        destino["id"],
        [(variante["variante_id"], 6)],
    )
    assert creado.status_code == 201, creado.text
    traspaso = creado.json()
    detalle_id = traspaso["detalles"][0]["id"]

    recibido = await client.post(
        f"/api/v1/traspasos/{traspaso['id']}/recibir",
        json={
            "detalles": [
                {
                    "detalle_id": detalle_id,
                    "cantidad_recibida": 4,
                    "cantidad_merma": 2,
                    "observaciones": "Dos prendas manchadas",
                }
            ]
        },
        headers=tenant["headers"],
    )
    assert recibido.status_code == 200, recibido.text
    cuerpo = recibido.json()
    assert cuerpo["estado"] == "RECHAZADO_PARCIAL"
    detalle = cuerpo["detalles"][0]
    assert detalle["cantidad_recibida"] == 4
    assert detalle["cantidad_merma"] == 2
    assert detalle["cantidad_recibida"] + detalle["cantidad_merma"] == detalle["cantidad"]

    assert await _stock(
        db_session, tenant, destino["id"], variante["variante_id"]
    ) == 4
    mermas = (await _movimientos(
        db_session, tenant, TipoMovimiento.MERMA
    )).scalars().all()
    assert [mov.cantidad for mov in mermas] == [2]
    assert "manchadas" in (mermas[0].motivo or "")


@pytest.mark.asyncio
async def test_recepcion_que_viola_rn_013_es_rechazada(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    variante = await catalogo_tenant(tenant, stock=20)
    destino = await crear_sucursal(db_session, tenant)

    creado = await _crear_traspaso(
        client,
        tenant,
        variante["sucursal_id"],
        destino["id"],
        [(variante["variante_id"], 6)],
    )
    assert creado.status_code == 201, creado.text
    traspaso = creado.json()

    respuesta = await client.post(
        f"/api/v1/traspasos/{traspaso['id']}/recibir",
        json={
            "detalles": [
                {
                    "detalle_id": traspaso["detalles"][0]["id"],
                    "cantidad_recibida": 3,
                    "cantidad_merma": 1,
                }
            ]
        },
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 400, respuesta.text
    assert "rn-013" in respuesta.json()["detail"].lower()

    estado = await client.get(
        f"/api/v1/traspasos/{traspaso['id']}", headers=tenant["headers"]
    )
    assert estado.status_code == 200, estado.text
    assert estado.json()["estado"] == "EN_TRANSITO"
    assert await _stock(
        db_session, tenant, destino["id"], variante["variante_id"]
    ) == 0


@pytest.mark.asyncio
async def test_rechazo_por_linea_devuelve_solo_la_linea_rechazada(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    variante_a = await catalogo_tenant(tenant, stock=20)
    variante_b = await catalogo_tenant(tenant, stock=20)
    destino = await crear_sucursal(db_session, tenant)
    origen_id = variante_a["sucursal_id"]

    creado = await _crear_traspaso(
        client,
        tenant,
        origen_id,
        destino["id"],
        [
            (variante_a["variante_id"], 3),
            (variante_b["variante_id"], 4),
        ],
    )
    assert creado.status_code == 201, creado.text
    traspaso = creado.json()
    detalle_a = next(
        item for item in traspaso["detalles"]
        if item["variante_producto_id"] == variante_a["variante_id"]
    )

    rechazo = await client.post(
        f"/api/v1/traspasos/{traspaso['id']}/rechazar",
        json={
            "motivo": "Prenda dañada en el transporte",
            "detalles": [{"detalle_id": detalle_a["id"]}],
        },
        headers=tenant["headers"],
    )
    assert rechazo.status_code == 200, rechazo.text
    cuerpo = rechazo.json()
    assert cuerpo["estado"] == "RECHAZADO_PARCIAL"

    por_variante = {
        item["variante_producto_id"]: item for item in cuerpo["detalles"]
    }
    rechazada = por_variante[variante_a["variante_id"]]
    pendiente = por_variante[variante_b["variante_id"]]
    assert rechazada["cantidad_merma"] == 3
    assert rechazada["cantidad_recibida"] == 0
    assert pendiente["cantidad_merma"] == 0
    assert pendiente["cantidad_pendiente"] == 4

    # Solo se repone el stock de la línea rechazada.
    assert await _stock(
        db_session, tenant, origen_id, variante_a["variante_id"]
    ) == 20
    assert await _stock(
        db_session, tenant, origen_id, variante_b["variante_id"]
    ) == 16


@pytest.mark.asyncio
async def test_rechazo_total_y_cancelacion_restauran_stock(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    variante = await catalogo_tenant(tenant, stock=20)
    destino = await crear_sucursal(db_session, tenant)
    origen_id = variante["sucursal_id"]

    rechazado = await _crear_traspaso(
        client, tenant, origen_id, destino["id"], [(variante["variante_id"], 5)]
    )
    assert rechazado.status_code == 201, rechazado.text
    assert await _stock(
        db_session, tenant, origen_id, variante["variante_id"]
    ) == 15

    respuesta = await client.post(
        f"/api/v1/traspasos/{rechazado.json()['id']}/rechazar",
        json={"motivo": "Lote incompleto", "devolver_origen": True},
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["estado"] == "CANCELADO"
    assert await _stock(
        db_session, tenant, origen_id, variante["variante_id"]
    ) == 20
    assert await _stock(
        db_session, tenant, destino["id"], variante["variante_id"]
    ) == 0

    cancelado = await _crear_traspaso(
        client, tenant, origen_id, destino["id"], [(variante["variante_id"], 4)]
    )
    assert cancelado.status_code == 201, cancelado.text
    assert await _stock(
        db_session, tenant, origen_id, variante["variante_id"]
    ) == 16

    cancelacion = await client.post(
        f"/api/v1/traspasos/{cancelado.json()['id']}/cancelar",
        headers=tenant["headers"],
    )
    assert cancelacion.status_code == 200, cancelacion.text
    cuerpo = cancelacion.json()
    assert cuerpo["estado"] == "CANCELADO"
    assert cuerpo["detalles"][0]["cantidad_recibida"] == 0
    assert cuerpo["detalles"][0]["cantidad_merma"] == 0
    assert await _stock(
        db_session, tenant, origen_id, variante["variante_id"]
    ) == 20

    repetida = await client.post(
        f"/api/v1/traspasos/{cancelado.json()['id']}/cancelar",
        headers=tenant["headers"],
    )
    assert repetida.status_code == 409, repetida.text


@pytest.mark.asyncio
async def test_sucursales_de_otro_tenant_y_sucursal_repetida(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
):
    tenant_a = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Traspaso A")
    tenant_b = await tenant_nuevo(plan_id=plan_profesional.id, nombre="Traspaso B")
    variante_a = await catalogo_tenant(tenant_a, stock=10)
    variante_b = await catalogo_tenant(tenant_b, stock=10)
    origen_id = variante_a["sucursal_id"]

    ajeno = await _crear_traspaso(
        client,
        tenant_a,
        origen_id,
        variante_b["sucursal_id"],
        [(variante_a["variante_id"], 2)],
    )
    assert ajeno.status_code == 404, ajeno.text

    repetida = await _crear_traspaso(
        client,
        tenant_a,
        origen_id,
        origen_id,
        [(variante_a["variante_id"], 2)],
    )
    assert repetida.status_code == 400, repetida.text
    assert "distintas" in repetida.json()["detail"].lower()
    assert await _stock(
        db_session, tenant_a, origen_id, variante_a["variante_id"]
    ) == 10


@pytest.mark.asyncio
async def test_pdf_del_traspaso(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    variante = await catalogo_tenant(tenant, stock=10)
    destino = await crear_sucursal(db_session, tenant)

    creado = await _crear_traspaso(
        client,
        tenant,
        variante["sucursal_id"],
        destino["id"],
        [(variante["variante_id"], 2)],
    )
    assert creado.status_code == 201, creado.text

    pdf = await client.get(
        f"/api/v1/traspasos/{creado.json()['id']}/pdf", headers=tenant["headers"]
    )
    assert pdf.status_code == 200, pdf.text
    assert pdf.content.startswith(b"%PDF")
    assert "application/pdf" in pdf.headers["content-type"]
    assert "traspaso_" in pdf.headers["content-disposition"]

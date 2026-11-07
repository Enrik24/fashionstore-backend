"""[EXAMEN-2] Fase C — reposición separada de devolución (CU18 / RF-031)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.apps.gestion_catalogo.models import (
    Color,
    Inventario,
    MovimientoInventario,
    Talla,
    TipoMovimiento,
    VarianteProducto,
)
from app.apps.gestion_ventas.models import (
    EstadoSolicitudDevolucion,
    SolicitudDevolucion,
    TipoSolicitudDevolucion,
)
from tests._fase3_helpers import crear_venta, fijar_inventario


def _sufijo() -> str:
    return uuid.uuid4().hex[:6]


async def _variante_hermana(
    db_session,
    tenant: dict,
    catalogo: dict,
    *,
    precio: str = "100.00",
    stock: int = 5,
    talla_valor: str = "L",
    orden: int = 40,
) -> int:
    """Crea otra variante del mismo producto (otra talla/color)."""
    tenant_id = int(tenant["id"])
    sufijo = _sufijo()
    color = Color(nombre=f"Color {sufijo}", codigo_hex="#334455", tenant_id=tenant_id)
    talla = Talla(valor=f"{talla_valor}{sufijo[:2]}", tipo="ropa", tenant_id=tenant_id, orden=orden)
    db_session.add_all([color, talla])
    await db_session.flush()
    variante = VarianteProducto(
        producto_id=catalogo["producto_id"],
        talla_id=talla.id,
        color_id=color.id,
        sku_variante=f"VAR-{sufijo}",
        precio_variante=Decimal(precio),
        tenant_id=tenant_id,
    )
    db_session.add(variante)
    await db_session.commit()
    await db_session.refresh(variante)
    await fijar_inventario(db_session, tenant, catalogo["sucursal_id"], variante.id, stock)
    return variante.id


async def _stock(db_session, tenant: dict, sucursal_id: int, variante_id: int) -> int:
    inventario = (await db_session.execute(
        select(Inventario).where(
            Inventario.tenant_id == int(tenant["id"]),
            Inventario.sucursal_id == int(sucursal_id),
            Inventario.variante_producto_id == int(variante_id),
        )
    )).scalar_one_or_none()
    return int(inventario.cantidad) if inventario else 0


def _payload(venta: dict, variante_nueva: int, **extra) -> dict:
    cuerpo = {
        "orden_id": venta["orden_id"],
        "motivo": "TALLA_INCORRECTA",
        "items": [
            {
                "detalle_orden_id": venta["detalle_id"],
                "cantidad": 1,
                "variante_cambio_id": variante_nueva,
            }
        ],
    }
    cuerpo.update(extra)
    return cuerpo


@pytest.mark.asyncio
async def test_reposicion_mueve_inventario_y_es_sin_diferencia(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=10)
    cliente = await cliente_tenant(tenant)
    venta = await crear_venta(db_session, tenant, catalogo, cliente["id"], precio="100.00")
    nueva = await _variante_hermana(db_session, tenant, catalogo, precio="100.00", stock=5)

    respuesta = await client.post(
        "/api/v1/devoluciones/reposicion",
        json=_payload(venta, nueva),
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()

    assert cuerpo["tipo"] == "REPOSICION"
    assert cuerpo["numero_reposicion"].startswith("REP-")
    assert cuerpo["nota_reposicion_url"].endswith("/nota-reposicion")
    assert cuerpo["tipo_diferencia"] == "SIN_DIFERENCIA"
    assert cuerpo["estado"] == "COMPLETADA"
    assert cuerpo["nota_devolucion_url"] is None

    assert await _stock(db_session, tenant, catalogo["sucursal_id"], catalogo["variante_id"]) == 11
    assert await _stock(db_session, tenant, catalogo["sucursal_id"], nueva) == 4

    movimientos = (await db_session.execute(
        select(MovimientoInventario.tipo).where(
            MovimientoInventario.tenant_id == int(tenant["id"]),
            MovimientoInventario.tipo.in_([
                TipoMovimiento.REPOSICION_ENTRADA,
                TipoMovimiento.REPOSICION_SALIDA,
            ]),
        )
    )).scalars().all()
    assert TipoMovimiento.REPOSICION_ENTRADA in movimientos
    assert TipoMovimiento.REPOSICION_SALIDA in movimientos


@pytest.mark.asyncio
async def test_reposicion_clasifica_las_tres_diferencias(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=10)
    cliente = await cliente_tenant(tenant)

    casos = [
        ("150.00", "COBRO", 50.0),
        ("80.00", "SALDO_FAVOR", 20.0),
        ("100.00", "SIN_DIFERENCIA", 0.0),
    ]
    for precio, tipo_esperado, monto in casos:
        venta = await crear_venta(db_session, tenant, catalogo, cliente["id"], precio="100.00")
        nueva = await _variante_hermana(db_session, tenant, catalogo, precio=precio, stock=5)
        respuesta = await client.post(
            "/api/v1/devoluciones/reposicion",
            json=_payload(venta, nueva),
            headers=tenant["headers"],
        )
        assert respuesta.status_code == 200, respuesta.text
        cuerpo = respuesta.json()
        assert cuerpo["tipo_diferencia"] == tipo_esperado, cuerpo
        assert cuerpo["diferencia_precio"] == pytest.approx(
            float(precio) - 100.0, abs=0.01
        )
        if tipo_esperado == "COBRO":
            assert cuerpo["monto_pagado_diferencia"] == pytest.approx(monto)
        if tipo_esperado == "SALDO_FAVOR":
            assert cuerpo["saldo_favor"] == pytest.approx(monto)


@pytest.mark.asyncio
async def test_reposicion_sin_stock_no_mueve_inventario(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=10)
    cliente = await cliente_tenant(tenant)
    venta = await crear_venta(db_session, tenant, catalogo, cliente["id"], precio="100.00")
    sin_stock = await _variante_hermana(db_session, tenant, catalogo, stock=0)

    respuesta = await client.post(
        "/api/v1/devoluciones/reposicion",
        json=_payload(venta, sin_stock),
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 400, respuesta.text
    assert await _stock(db_session, tenant, catalogo["sucursal_id"], catalogo["variante_id"]) == 10
    assert await _stock(db_session, tenant, catalogo["sucursal_id"], sin_stock) == 0


@pytest.mark.asyncio
async def test_reposicion_rechaza_variante_de_otro_producto(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=10)
    otro_catalogo = await catalogo_tenant(tenant, precio="120.00", stock=10)
    cliente = await cliente_tenant(tenant)
    venta = await crear_venta(db_session, tenant, catalogo, cliente["id"], precio="100.00")

    respuesta = await client.post(
        "/api/v1/devoluciones/reposicion",
        json=_payload(venta, otro_catalogo["variante_id"]),
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 400, respuesta.text
    assert "mismo producto" in respuesta.text


@pytest.mark.asyncio
async def test_reposicion_fuera_de_plazo_es_rechazada(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=10)
    cliente = await cliente_tenant(tenant)
    venta = await crear_venta(
        db_session,
        tenant,
        catalogo,
        cliente["id"],
        precio="100.00",
        fecha=datetime.now(timezone.utc) - timedelta(days=30),
        limite_dias=7,
    )
    nueva = await _variante_hermana(db_session, tenant, catalogo, stock=5)

    respuesta = await client.post(
        "/api/v1/devoluciones/reposicion",
        json=_payload(venta, nueva),
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 400
    assert "plazo" in respuesta.text.lower()


@pytest.mark.asyncio
async def test_nota_de_reposicion_pdf_y_listado(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=10)
    cliente = await cliente_tenant(tenant)
    venta = await crear_venta(db_session, tenant, catalogo, cliente["id"], precio="100.00")
    nueva = await _variante_hermana(db_session, tenant, catalogo, precio="120.00", stock=5)

    creada = await client.post(
        "/api/v1/devoluciones/reposicion",
        json=_payload(venta, nueva),
        headers=tenant["headers"],
    )
    solicitud_id = creada.json()["id"]

    pdf = await client.get(
        f"/api/v1/devoluciones/{solicitud_id}/nota-reposicion",
        headers=tenant["headers"],
    )
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    assert "nota_reposicion_" in pdf.headers["content-disposition"]

    listado = await client.get(
        "/api/v1/devoluciones/reposiciones", headers=tenant["headers"]
    )
    assert listado.status_code == 200
    assert [item["id"] for item in listado.json()["reposiciones"]] == [solicitud_id]


@pytest.mark.asyncio
async def test_reposicion_offline_idempotente(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=10)
    cliente = await cliente_tenant(tenant)
    venta = await crear_venta(db_session, tenant, catalogo, cliente["id"], precio="100.00")
    nueva = await _variante_hermana(db_session, tenant, catalogo, stock=5)
    uuid_offline = str(uuid.uuid4())

    primera = await client.post(
        "/api/v1/devoluciones/reposicion",
        json=_payload(venta, nueva, uuid_offline=uuid_offline),
        headers=tenant["headers"],
    )
    segunda = await client.post(
        "/api/v1/devoluciones/reposicion",
        json=_payload(venta, nueva, uuid_offline=uuid_offline),
        headers=tenant["headers"],
    )
    assert primera.status_code == 200, primera.text
    assert segunda.status_code == 200, segunda.text
    assert primera.json()["id"] == segunda.json()["id"]
    assert segunda.json()["idempotente"] is True

    total = (await db_session.execute(
        select(func.count(SolicitudDevolucion.id)).where(
            SolicitudDevolucion.tenant_id == int(tenant["id"]),
            SolicitudDevolucion.uuid_offline == uuid.UUID(uuid_offline),
        )
    )).scalar_one()
    assert total == 1
    assert await _stock(db_session, tenant, catalogo["sucursal_id"], nueva) == 4
    assert await _stock(db_session, tenant, catalogo["sucursal_id"], catalogo["variante_id"]) == 11


@pytest.mark.asyncio
async def test_devolucion_y_reposicion_pueden_convivir_en_la_misma_orden(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=10)
    cliente = await cliente_tenant(tenant)
    venta = await crear_venta(
        db_session, tenant, catalogo, cliente["id"], cantidad=2, precio="100.00"
    )
    nueva = await _variante_hermana(db_session, tenant, catalogo, precio="110.00", stock=5)

    devolucion = await client.post(
        "/api/v1/devoluciones/cambio",
        json={
            "orden_id": venta["orden_id"],
            "tipo": "DEVOLUCION",
            "motivo": "DEFECTO_FABRICA",
            "items": [{"detalle_orden_id": venta["detalle_id"], "cantidad": 1}],
        },
        headers=tenant["headers"],
    )
    assert devolucion.status_code == 200, devolucion.text
    assert devolucion.json()["tipo"] == "DEVOLUCION"
    assert devolucion.json()["numero_reposicion"] is None

    reposicion = await client.post(
        "/api/v1/devoluciones/reposicion",
        json=_payload(venta, nueva),
        headers=tenant["headers"],
    )
    assert reposicion.status_code == 200, reposicion.text
    assert reposicion.json()["tipo"] == "REPOSICION"

    filas = (await db_session.execute(
        select(SolicitudDevolucion.tipo).where(
            SolicitudDevolucion.tenant_id == int(tenant["id"]),
            SolicitudDevolucion.orden_id == venta["orden_id"],
        )
    )).scalars().all()
    assert TipoSolicitudDevolucion.DEVOLUCION in filas
    assert TipoSolicitudDevolucion.REPOSICION in filas

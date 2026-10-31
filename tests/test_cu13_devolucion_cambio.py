"""[EXAMEN-2] CU13 — Devoluciones y cambios (plan, 9.3.9 y 9.3.11).

Cubre el plazo RN-012, el cambio por una prenda más cara y más barata, el
stock insuficiente, la devolución/cambio offline con UUID idempotente, la venta
anulada al sincronizar y la nota PDF.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.apps.gestion_catalogo.models import Inventario, MovimientoInventario, TipoMovimiento
from app.apps.gestion_comercial_logistica.handlers import manejar_devolucion_cambio
from app.apps.gestion_comercial_logistica.cambio_service import CambioDevolucionService
from app.apps.gestion_ventas.models import (
    Comprobante,
    EstadoOrden,
    EstadoSincronizacion,
    EstadoSolicitudDevolucion,
    SolicitudDevolucion,
)
from app.apps.operaciones_offline.registro_manejadores import obtener_manejador
from tests._fase3_helpers import crear_venta


async def _stock(db_session, tenant, sucursal_id: int, variante_id: int) -> int:
    inventario = (await db_session.execute(
        select(Inventario).where(
            Inventario.tenant_id == int(tenant["id"]),
            Inventario.sucursal_id == int(sucursal_id),
            Inventario.variante_producto_id == int(variante_id),
        )
    )).scalar_one_or_none()
    return int(inventario.cantidad) if inventario else 0


async def _cambio_payload(
    venta: dict, variante_nueva: int | None = None, *, cantidad: int = 1
) -> dict:
    return {
        "orden_id": venta["orden_id"],
        "tipo": "CAMBIO" if variante_nueva else "DEVOLUCION",
        "motivo": "TALLA_INCORRECTA" if variante_nueva else "DEFECTO_FABRICA",
        "items": [
            {
                "detalle_orden_id": venta["detalle_id"],
                "cantidad": cantidad,
                "variante_cambio_id": variante_nueva,
            }
        ],
    }


@pytest.mark.asyncio
async def test_cambio_dentro_de_plazo_por_prenda_mas_cara(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    original = await catalogo_tenant(tenant, precio="100.00", stock=10)
    nueva = await catalogo_tenant(tenant, precio="150.00", stock=5)
    cliente = await cliente_tenant(tenant)
    venta = await crear_venta(
        db_session, tenant, original, cliente["id"], cantidad=1, precio="100.00"
    )

    buscar = await client.get(
        f"/api/v1/devoluciones/venta/{venta['numero_comprobante']}",
        headers=tenant["headers"],
    )
    assert buscar.status_code == 200, buscar.text
    assert buscar.json()["dentro_plazo"] is True

    respuesta = await client.post(
        "/api/v1/devoluciones/cambio",
        json=await _cambio_payload(venta, nueva["variante_id"]),
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()

    assert cuerpo["estado"] == "COMPLETADA"
    assert cuerpo["tipo"] == "CAMBIO"
    assert cuerpo["diferencia_precio"] == pytest.approx(50.0)
    assert cuerpo["tipo_diferencia"] == "COBRO"
    assert cuerpo["monto_pagado_diferencia"] == pytest.approx(50.0)
    assert cuerpo["saldo_favor"] == pytest.approx(0.0)
    assert cuerpo["es_offline"] is False
    assert cuerpo["detalles"][0]["variante_cambio_id"] == nueva["variante_id"]
    assert cuerpo["nota_devolucion_url"] == (
        f"/api/v1/devoluciones/{cuerpo['id']}/nota"
    )

    # La prenda devuelta se reintegra y la nueva se descuenta del stock.
    assert await _stock(
        db_session, tenant, original["sucursal_id"], original["variante_id"]
    ) == 11
    assert await _stock(
        db_session, tenant, nueva["sucursal_id"], nueva["variante_id"]
    ) == 4

    historial = await client.get(
        "/api/v1/devoluciones/historial", headers=tenant["headers"]
    )
    assert historial.status_code == 200, historial.text
    assert [item["id"] for item in historial.json()["devoluciones"]] == [cuerpo["id"]]

    nota = await client.get(
        f"/api/v1/devoluciones/{cuerpo['id']}/nota", headers=tenant["headers"]
    )
    assert nota.status_code == 200, nota.text
    assert nota.content.startswith(b"%PDF")
    assert "application/pdf" in nota.headers["content-type"]


@pytest.mark.asyncio
async def test_cambio_por_prenda_mas_barata_genera_saldo_a_favor(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    original = await catalogo_tenant(tenant, precio="120.00", stock=10)
    nueva = await catalogo_tenant(tenant, precio="80.00", stock=10)
    cliente = await cliente_tenant(tenant)
    venta = await crear_venta(
        db_session, tenant, original, cliente["id"], cantidad=1, precio="120.00"
    )

    respuesta = await client.post(
        "/api/v1/devoluciones/cambio",
        json=await _cambio_payload(venta, nueva["variante_id"]),
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()

    assert cuerpo["estado"] == "COMPLETADA"
    assert cuerpo["diferencia_precio"] == pytest.approx(-40.0)
    assert cuerpo["tipo_diferencia"] == "SALDO_FAVOR"
    assert cuerpo["monto_pagado_diferencia"] == pytest.approx(0.0)
    assert cuerpo["saldo_favor"] == pytest.approx(40.0)


@pytest.mark.asyncio
async def test_cambio_fuera_de_plazo_es_rechazado(
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
    vencida = await crear_venta(
        db_session,
        tenant,
        catalogo,
        cliente["id"],
        fecha=datetime.now(timezone.utc) - timedelta(days=30),
        numero_orden="VENCIDA-CU13",
    )

    respuesta = await client.post(
        "/api/v1/devoluciones/cambio",
        json=await _cambio_payload(vencida),
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 400, respuesta.text
    assert "plazo" in respuesta.json()["detail"].lower()

    validar = await client.get(
        f"/api/v1/devoluciones/validar-plazo/{vencida['orden_id']}",
        headers=tenant["headers"],
    )
    assert validar.status_code == 400, validar.text

    total = (await db_session.execute(
        select(func.count(SolicitudDevolucion.id)).where(
            SolicitudDevolucion.tenant_id == int(tenant["id"])
        )
    )).scalar_one()
    assert total == 0


@pytest.mark.asyncio
async def test_cambio_con_stock_insuficiente_no_toca_inventario(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    original = await catalogo_tenant(tenant, precio="100.00", stock=10)
    agotada = await catalogo_tenant(tenant, precio="150.00", stock=0)
    cliente = await cliente_tenant(tenant)
    venta = await crear_venta(
        db_session, tenant, original, cliente["id"], cantidad=1, precio="100.00"
    )

    respuesta = await client.post(
        "/api/v1/devoluciones/cambio",
        json=await _cambio_payload(venta, agotada["variante_id"]),
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 400, respuesta.text
    assert "stock" in respuesta.json()["detail"].lower()

    assert await _stock(
        db_session, tenant, original["sucursal_id"], original["variante_id"]
    ) == 10
    assert await _stock(
        db_session, tenant, agotada["sucursal_id"], agotada["variante_id"]
    ) == 0


@pytest.mark.asyncio
async def test_confirmar_cambio_cierra_operacion_y_actualiza_comprobante(
    client: AsyncClient,
    db_session,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    original = await catalogo_tenant(tenant, precio="100.00", stock=10)
    nueva = await catalogo_tenant(tenant, precio="130.00", stock=10)
    cliente = await cliente_tenant(tenant)
    venta = await crear_venta(
        db_session, tenant, original, cliente["id"], cantidad=1, precio="100.00"
    )

    respuesta = await client.post(
        "/api/v1/devoluciones/cambio",
        json=await _cambio_payload(venta, nueva["variante_id"]),
        headers=tenant["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    solicitud_id = respuesta.json()["id"]

    confirmado = await CambioDevolucionService.confirmar_cambio(
        db_session, int(tenant["id"]), solicitud_id
    )
    await db_session.commit()

    assert confirmado["id"] == solicitud_id
    assert confirmado["estado"] == "COMPLETADA"
    assert confirmado["nota_devolucion_url"] == (
        f"/api/v1/devoluciones/{solicitud_id}/nota"
    )
    assert [detalle["estado_item"] for detalle in confirmado["detalles"]] == [
        "ENTREGADO"
    ]

    comprobante = await db_session.get(Comprobante, venta["comprobante_id"])
    assert comprobante.archivo_pdf == confirmado["nota_devolucion_url"]

    # Confirmar de nuevo no duplica ni cambia el resultado.
    repetido = await CambioDevolucionService.confirmar_cambio(
        db_session, int(tenant["id"]), solicitud_id
    )
    await db_session.commit()
    assert repetido["id"] == solicitud_id
    assert repetido["estado"] == "COMPLETADA"


@pytest.mark.asyncio
async def test_devolucion_offline_es_idempotente_por_uuid(
    db_session, tenant_nuevo, plan_profesional, catalogo_tenant, cliente_tenant
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="90.00", stock=10)
    cliente = await cliente_tenant(tenant)
    venta = await crear_venta(
        db_session, tenant, catalogo, cliente["id"], cantidad=1, precio="90.00"
    )

    manejador = obtener_manejador("DEVOLUCION_CAMBIO")
    assert callable(manejador)

    operacion = SimpleNamespace(
        tenant_id=int(tenant["id"]),
        usuario_id=None,
        uuid_operacion=uuid4(),
        payload_json=await _cambio_payload(venta),
    )
    contexto = {"tenant_id": int(tenant["id"])}

    primera = await manejar_devolucion_cambio(db_session, operacion, contexto)
    await db_session.commit()
    assert primera["estado"] == "PENDIENTE_REEMBOLSO"
    assert primera["es_offline"] is True
    assert primera["uuid_offline"] == str(operacion.uuid_operacion)
    assert primera["idempotente"] is False
    assert primera["monto_reembolso"] == pytest.approx(90.0)

    segunda = await manejar_devolucion_cambio(db_session, operacion, contexto)
    await db_session.commit()
    assert segunda["id"] == primera["id"]
    assert segunda["idempotente"] is True

    total = (await db_session.execute(
        select(func.count(SolicitudDevolucion.id)).where(
            SolicitudDevolucion.tenant_id == int(tenant["id"])
        )
    )).scalar_one()
    assert total == 1

    pendientes = await db_session.execute(
        select(MovimientoInventario).where(
            MovimientoInventario.tenant_id == int(tenant["id"]),
            MovimientoInventario.tipo == TipoMovimiento.DEVOLUCION,
        )
    )
    assert pendientes.scalars().all()


@pytest.mark.asyncio
async def test_venta_anulada_al_sincronizar_queda_en_conflicto(
    db_session, tenant_nuevo, plan_profesional, catalogo_tenant, cliente_tenant
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=10)
    cliente = await cliente_tenant(tenant)
    anulada = await crear_venta(
        db_session,
        tenant,
        catalogo,
        cliente["id"],
        numero_orden="ANULADA-CU13",
        estado=EstadoOrden.CANCELADO,
    )

    operacion = SimpleNamespace(
        tenant_id=int(tenant["id"]),
        usuario_id=None,
        uuid_operacion=uuid4(),
        payload_json={
            "orden_id": anulada["orden_id"],
            "tipo": "DEVOLUCION",
            "motivo": "DEFECTO_FABRICA",
            "items": [
                {
                    "detalle_orden_id": anulada["detalle_id"],
                    "cantidad": 1,
                }
            ],
        },
    )
    resultado = await manejar_devolucion_cambio(
        db_session, operacion, {"tenant_id": int(tenant["id"])}
    )
    await db_session.commit()

    assert resultado["estado"] == "ERROR_VENTA_INVALIDA"
    solicitud = (await db_session.execute(
        select(SolicitudDevolucion).where(
            SolicitudDevolucion.tenant_id == int(tenant["id"]),
            SolicitudDevolucion.uuid_offline == operacion.uuid_operacion,
        )
    )).scalar_one()
    assert solicitud.estado == EstadoSolicitudDevolucion.ERROR_VENTA_INVALIDA
    assert solicitud.estado_sincronizacion == EstadoSincronizacion.CONFLICTO
    assert solicitud.orden_id == anulada["orden_id"]
    assert "anulada" in (solicitud.motivo_rechazo or "").lower()

    pendientes = await manejar_devolucion_cambio(
        db_session, operacion, {"tenant_id": int(tenant["id"])}
    )
    await db_session.commit()
    assert pendientes["solicitud_id"] == solicitud.id
    assert pendientes["estado"] == "ERROR_VENTA_INVALIDA"
    assert pendientes["idempotente"] is True

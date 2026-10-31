"""[EXAMEN-2] CU07 — Venta de contado creada offline (plan 9.2.10).

Verifica la creación de la venta en efectivo, el recálculo central de totales,
el descuento de inventario, el comprobante emitido, el rechazo de medios de
pago electrónicos (RN-008) y el rechazo de productos inactivos.
"""
from decimal import Decimal
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select

from app.apps.gestion_catalogo.models import Inventario, Producto
from app.apps.gestion_ventas.models import (
    Comprobante,
    DetalleOrden,
    EstadoOrden,
    Orden,
    VentaPresencial,
)
from app.apps.operaciones_offline.models import (
    EstadoSincronizacion,
    OperacionOffline,
)
from tests.conftest import operacion_payload


def _venta_payload(catalogo: dict, cantidad: int = 1, **extra) -> dict:
    cuerpo = {
        "sucursal_id": catalogo["sucursal_id"],
        "metodo_pago": "EFECTIVO",
        "detalles": [
            {
                "variante_producto_id": catalogo["variante_id"],
                "cantidad": cantidad,
                "precio_unitario_local": float(catalogo["precio"]),
            }
        ],
    }
    cuerpo.update(extra)
    return cuerpo


async def _sincronizar(client: AsyncClient, dispositivo: dict, operaciones: list[dict]):
    respuesta = await client.post(
        "/api/v1/offline/sync",
        json={"operaciones": operaciones},
        headers=dispositivo["headers"],
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


async def test_venta_contado_crea_orden_pagada_y_comprobante(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    cliente_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="150.00", stock=10)
    cliente = await cliente_tenant(tenant)
    dispositivo = await dispositivo_offline(tenant)

    uuid_operacion = str(uuid4())
    lote = await _sincronizar(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=uuid_operacion,
                tipo_operacion="VENTA_CONTADO",
                payload=_venta_payload(catalogo, cantidad=2, cliente_id=cliente["id"]),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
    )
    resultado = lote["resultados"][0]
    assert resultado["estado"] == "CONFIRMADA", resultado
    datos = resultado["datos_resultado"]

    assert datos["numero_orden"]
    assert datos["comprobante_numero"]
    assert datos["metodo_pago"] == "EFECTIVO"
    assert Decimal(str(datos["subtotal"])) == Decimal("300.00")
    assert Decimal(str(datos["descuento"])) == Decimal("0.00")
    assert Decimal(str(datos["total"])) == Decimal("300.00")
    assert datos["items"] == 1

    orden = await db_session.get(Orden, datos["orden_id"])
    assert orden is not None
    assert orden.tenant_id == tenant["id"]
    assert orden.estado == EstadoOrden.PAGADO
    assert orden.es_offline is True
    assert orden.estado_sincronizacion == EstadoSincronizacion.CONFIRMADA
    assert orden.total == Decimal("300.00")

    detalles = (await db_session.execute(
        select(DetalleOrden).where(DetalleOrden.orden_id == orden.id)
    )).scalars().all()
    assert len(detalles) == 1
    assert detalles[0].cantidad == 2
    assert detalles[0].precio_unitario == Decimal("150.00")
    assert detalles[0].precio_unitario_local == Decimal("150.00")

    comprobante = (await db_session.execute(
        select(Comprobante).where(Comprobante.orden_id == orden.id)
    )).scalar_one()
    assert comprobante.tenant_id == tenant["id"]
    assert comprobante.es_temporal is False

    venta = (await db_session.execute(
        select(VentaPresencial).where(VentaPresencial.orden_id == orden.id)
    )).scalar_one()
    assert venta.tenant_id == tenant["id"]
    assert venta.metodo_pago.value == "EFECTIVO"
    assert venta.es_offline is True
    assert venta.uuid_offline is not None
    assert str(venta.uuid_offline) == uuid_operacion

    inventario = (await db_session.execute(
        select(Inventario).where(Inventario.variante_producto_id == catalogo["variante_id"])
    )).scalar_one()
    assert inventario.cantidad == 8
    assert inventario.cantidad_disponible == 8
    assert inventario.cantidad_reservada == 0

    operacion = (await db_session.execute(
        select(OperacionOffline).where(OperacionOffline.uuid_operacion == uuid_operacion)
    )).scalar_one()
    assert operacion.estado_sincronizacion == EstadoSincronizacion.CONFIRMADA


async def test_venta_aplica_descuento_local_y_recalcula_el_total(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=5)
    dispositivo = await dispositivo_offline(tenant)

    lote = await _sincronizar(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=str(uuid4()),
                tipo_operacion="VENTA_CONTADO",
                payload=_venta_payload(catalogo, cantidad=2, descuento="25.00"),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
    )
    datos = lote["resultados"][0]["datos_resultado"]
    assert Decimal(str(datos["subtotal"])) == Decimal("200.00")
    assert Decimal(str(datos["descuento"])) == Decimal("25.00")
    assert Decimal(str(datos["total"])) == Decimal("175.00")

    orden = await db_session.get(Orden, datos["orden_id"])
    assert orden.total == Decimal("175.00")
    assert orden.descuentos == Decimal("25.00")


async def test_el_total_se_calcula_con_el_precio_central_no_con_el_local(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, precio="100.00", stock=5)
    dispositivo = await dispositivo_offline(tenant)

    lote = await _sincronizar(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=str(uuid4()),
                tipo_operacion="VENTA_CONTADO",
                payload=_venta_payload(catalogo, cantidad=1, precio_unitario_local="80.00"),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
    )
    datos = lote["resultados"][0]["datos_resultado"]
    # El servidor manda: el total usa el precio central (100), no el local (80).
    assert Decimal(str(datos["total"])) == Decimal("100.00")


async def test_venta_con_pago_electronico_es_rechazada(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=5)
    dispositivo = await dispositivo_offline(tenant)

    lote = await _sincronizar(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=str(uuid4()),
                tipo_operacion="VENTA_CONTADO",
                payload=_venta_payload(catalogo, metodo_pago="TARJETA"),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
    )
    resultado = lote["resultados"][0]
    assert resultado["estado"] == "RECHAZADA"
    assert "efectivo" in resultado["motivo"].lower()
    assert resultado["datos_resultado"]["metodo_pago"] == "TARJETA"

    ordenes = (await db_session.execute(
        select(Orden).where(Orden.tenant_id == tenant["id"])
    )).scalars().all()
    assert ordenes == []

    inventario = (await db_session.execute(
        select(Inventario).where(Inventario.variante_producto_id == catalogo["variante_id"])
    )).scalar_one()
    assert inventario.cantidad == 5


async def test_venta_de_producto_inactivo_es_rechazada(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=5, activo=False)
    dispositivo = await dispositivo_offline(tenant)

    lote = await _sincronizar(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=str(uuid4()),
                tipo_operacion="VENTA_CONTADO",
                payload=_venta_payload(catalogo),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
    )
    resultado = lote["resultados"][0]
    assert resultado["estado"] == "RECHAZADA"
    assert "activo" in resultado["motivo"].lower()

    producto = await db_session.get(Producto, catalogo["producto_id"])
    assert producto.estado.value == "INACTIVO"

    inventario = (await db_session.execute(
        select(Inventario).where(Inventario.variante_producto_id == catalogo["variante_id"])
    )).scalar_one()
    assert inventario.cantidad == 5

    ordenes = (await db_session.execute(
        select(Orden).where(Orden.tenant_id == tenant["id"])
    )).scalars().all()
    assert ordenes == []


async def test_venta_sin_stock_no_deja_datos_parciales(
    client: AsyncClient,
    tenant_nuevo,
    plan_profesional,
    catalogo_tenant,
    dispositivo_offline,
    db_session,
):
    tenant = await tenant_nuevo(plan_id=plan_profesional.id)
    catalogo = await catalogo_tenant(tenant, stock=1)
    dispositivo = await dispositivo_offline(tenant)

    lote = await _sincronizar(
        client,
        dispositivo,
        [
            operacion_payload(
                uuid_operacion=str(uuid4()),
                tipo_operacion="VENTA_CONTADO",
                payload=_venta_payload(catalogo, cantidad=3),
                sucursal_id=catalogo["sucursal_id"],
            )
        ],
    )
    resultado = lote["resultados"][0]
    assert resultado["estado"] == "RECHAZADA"

    ordenes = (await db_session.execute(
        select(Orden).where(Orden.tenant_id == tenant["id"])
    )).scalars().all()
    assert ordenes == []

    inventario = (await db_session.execute(
        select(Inventario).where(Inventario.variante_producto_id == catalogo["variante_id"])
    )).scalar_one()
    assert inventario.cantidad == 1
    assert inventario.cantidad_reservada == 0

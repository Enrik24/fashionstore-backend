"""[EXAMEN-2] Utilidades directas a base de datos para las pruebas de Fase 3.

Las pruebas de CU10–CU14 necesitan ventas, sucursales e inventario que no se
crean con un endpoint simple. Estas fábricas escriben directo con la sesión de
prueba y confirman al final para que las peticiones HTTP (que abren otra
sesión) vean los datos.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.analitica_avanzada.datos_sinteticos_service import (
    DatosSinteticosService,
)
from app.apps.gestion_catalogo.models import Ciudad, Inventario, Sucursal
from app.apps.gestion_ventas.models import (
    Comprobante,
    DetalleOrden,
    EstadoOrden,
    Orden,
    TipoComprobante,
    TipoOrden,
)


def _sufijo() -> str:
    return uuid.uuid4().hex[:8]


async def crear_sucursal(
    db: AsyncSession, tenant: dict, nombre: str | None = None
) -> dict:
    """Crea una sucursal adicional del tenant reutilizando una ciudad del seed."""
    tenant_id = int(tenant["id"])
    ciudad = (await db.execute(select(Ciudad).order_by(Ciudad.id.asc()))).scalars().first()
    assert ciudad is not None, "El seed debe crear al menos una ciudad"

    sucursal = Sucursal(
        nombre=nombre or f"Sucursal {_sufijo()}",
        direccion="Av. Secundaria 456",
        telefono="70000002",
        ciudad_id=ciudad.id,
        tenant_id=tenant_id,
    )
    db.add(sucursal)
    await db.commit()
    await db.refresh(sucursal)
    return {
        "id": sucursal.id,
        "tenant_id": tenant_id,
        "nombre": sucursal.nombre,
        "ciudad_id": ciudad.id,
    }


async def fijar_inventario(
    db: AsyncSession,
    tenant: dict,
    sucursal_id: int,
    variante_id: int,
    cantidad: int,
    *,
    stock_minimo: int = 0,
) -> Inventario:
    """Asegura una fila de inventario con la cantidad indicada."""
    tenant_id = int(tenant["id"])
    inventario = (await db.execute(
        select(Inventario).where(
            Inventario.tenant_id == tenant_id,
            Inventario.variante_producto_id == int(variante_id),
            Inventario.sucursal_id == int(sucursal_id),
        )
    )).scalar_one_or_none()
    if inventario is None:
        inventario = Inventario(
            variante_producto_id=int(variante_id),
            sucursal_id=int(sucursal_id),
            cantidad=int(cantidad),
            cantidad_reservada=0,
            cantidad_vendida=0,
            stock_minimo=int(stock_minimo),
            tenant_id=tenant_id,
        )
        db.add(inventario)
    else:
        inventario.cantidad = int(cantidad)
        inventario.cantidad_reservada = 0
        inventario.stock_minimo = int(stock_minimo)
    await db.commit()
    await db.refresh(inventario)
    return inventario


async def crear_venta(
    db: AsyncSession,
    tenant: dict,
    catalogo: dict,
    cliente_id: int | None,
    *,
    variante_id: int | None = None,
    cantidad: int = 1,
    precio: Decimal | str | None = None,
    fecha: datetime | None = None,
    estado: EstadoOrden = EstadoOrden.PAGADO,
    numero_orden: str | None = None,
    sucursal_id: int | None = None,
    limite_dias: int = 7,
) -> dict[str, Any]:
    """Crea una orden pagada con detalle y comprobante para probar CU10–CU13."""
    tenant_id = int(tenant["id"])
    fecha = fecha or datetime.now(timezone.utc)
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=timezone.utc)
    precio_unitario = Decimal(str(precio if precio is not None else catalogo["precio"]))
    total = (precio_unitario * int(cantidad)).quantize(Decimal("0.01"))
    numero_orden = numero_orden or f"TEST-{_sufijo()}"

    orden = Orden(
        numero_orden=numero_orden,
        cliente_id=cliente_id,
        sucursal_id=int(sucursal_id or catalogo["sucursal_id"]),
        fecha=fecha,
        tipo=TipoOrden.PRESENCIAL,
        estado=estado,
        total=total,
        impuestos=Decimal("0.00"),
        descuentos=Decimal("0.00"),
        tenant_id=tenant_id,
        fecha_limite_cambio=fecha + timedelta(days=int(limite_dias)),
    )
    db.add(orden)
    await db.flush()

    detalle = DetalleOrden(
        orden_id=orden.id,
        variante_producto_id=int(variante_id or catalogo["variante_id"]),
        cantidad=int(cantidad),
        precio_unitario=precio_unitario,
        subtotal=total,
        tenant_id=tenant_id,
    )
    db.add(detalle)
    await db.flush()

    numero_comprobante = f"COMP-{_sufijo()}"
    comprobante = Comprobante(
        numero=numero_comprobante,
        tipo=TipoComprobante.RECIBO,
        monto_total=total,
        orden_id=orden.id,
        tenant_id=tenant_id,
    )
    db.add(comprobante)
    await db.commit()
    await db.refresh(orden)
    await db.refresh(detalle)

    return {
        "orden_id": orden.id,
        "numero_orden": orden.numero_orden,
        "detalle_id": detalle.id,
        "variante_id": detalle.variante_producto_id,
        "cantidad": detalle.cantidad,
        "precio_unitario": float(precio_unitario),
        "total": float(total),
        "fecha": fecha,
        "fecha_limite_cambio": orden.fecha_limite_cambio,
        "numero_comprobante": numero_comprobante,
        "comprobante_id": comprobante.id,
    }


async def sembrar_sinteticos(
    db: AsyncSession,
    tenant: dict,
    *,
    cantidad_clientes: int = 15,
    cantidad_ventas: int = 120,
    dias: int = 120,
    semilla: int = 2026,
) -> dict:
    """Genera y siembra un dataset sintético; devuelve los resúmenes."""
    tenant_id = int(tenant["id"])
    generado = await DatosSinteticosService.generar_dataset(
        db,
        tenant_id,
        {
            "cantidad_clientes": cantidad_clientes,
            "cantidad_ventas": cantidad_ventas,
            "dias": dias,
            "semilla": semilla,
        },
    )
    sembrado = await DatosSinteticosService.sembrar_dataset(
        db, tenant_id, generado["dataset_id"]
    )
    await db.commit()
    return {"dataset": generado, "siembra": sembrado}

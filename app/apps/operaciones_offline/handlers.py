"""
[EXAMEN-2] Manejadores del Paquete 2 — RESERVA, VENTA_CONTADO y CARRITO.

Cada función recibe `(db, operacion, contexto)` y devuelve el `datos_resultado`
que se guarda en `OperacionOffline.resultado_json`. Los errores de negocio se
expresan con `OperacionRechazada`/`AceptacionRequerida`; el motor de
sincronización decide el estado final y registra el conflicto.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.exceptions import OperacionRechazada, ValidationException
from app.apps.gestion_catalogo.models import Sucursal, VarianteProducto
from app.apps.gestion_usuarios.models import Cliente
from app.apps.gestion_ventas.models import (
    Carrito,
    DetalleOrden,
    DetalleReserva,
    EstadoCarrito,
    EstadoDetalleReserva,
    EstadoOrden,
    EstadoReserva,
    ItemCarrito,
    MetodoPagoPresencial,
    Orden,
    Reserva,
    TipoComprobante,
    TipoOrden,
    VentaPresencial,
)
from app.apps.gestion_ventas.services import (
    InventarioVentasService,
    OrdenService,
    ReservaService,
)
from app.apps.operaciones_offline.conflicto_service import ConflictoService
from app.apps.operaciones_offline.models import (
    EstadoSincronizacion,
    OrigenOperacion,
    TipoConflicto,
)
from app.apps.operaciones_offline.registro_manejadores import registrar_manejador


def _moneda(valor) -> Decimal:
    if valor is None:
        return Decimal("0.00")
    return Decimal(str(valor)).quantize(Decimal("0.01"))


async def _cargar_variante(
    db: AsyncSession, tenant_id: int, variante_id: int
) -> VarianteProducto:
    result = await db.execute(
        select(VarianteProducto)
        .options(selectinload(VarianteProducto.producto))
        .where(
            VarianteProducto.id == variante_id,
            VarianteProducto.tenant_id == tenant_id,
            VarianteProducto.deleted_at.is_(None),
        )
    )
    variante = result.scalar_one_or_none()
    if variante is None:
        raise OperacionRechazada(
            f"La variante #{variante_id} no existe en el tenant",
            tipo_conflicto=TipoConflicto.VARIANTE_INEXISTENTE.value,
            estrategia="RECHAZO",
            datos={"variante_producto_id": variante_id},
        )
    if variante.producto is None or variante.producto.deleted_at is not None:
        raise OperacionRechazada(
            f"La variante #{variante_id} no tiene producto vigente",
            tipo_conflicto=TipoConflicto.VARIANTE_INEXISTENTE.value,
            estrategia="RECHAZO",
            datos={"variante_producto_id": variante_id},
        )
    estado_producto = getattr(variante.producto.estado, "value", variante.producto.estado)
    if estado_producto != "ACTIVO":
        raise OperacionRechazada(
            f"El producto de la variante #{variante_id} no está activo",
            tipo_conflicto=TipoConflicto.PRODUCTO_INACTIVO.value,
            estrategia="RECHAZO",
            datos={"variante_producto_id": variante_id, "producto_id": variante.producto_id},
        )
    return variante


def _precio_central(variante: VarianteProducto) -> Decimal:
    if variante.precio_variante is not None:
        return _moneda(variante.precio_variante)
    return _moneda(variante.producto.precio if variante.producto else 0)


def _detalles(payload: dict) -> list[dict]:
    detalles = payload.get("detalles") or payload.get("items") or []
    if not isinstance(detalles, list) or not detalles:
        raise ValidationException("La operación no contiene detalles")
    normalizados = []
    totales_por_variante: dict[int, int] = {}
    for detalle in detalles:
        if not isinstance(detalle, dict):
            raise ValidationException("Detalle inválido")
        variante_id = detalle.get("variante_producto_id") or detalle.get("variante_id")
        cantidad = int(detalle.get("cantidad") or 0)
        if variante_id is None or cantidad <= 0:
            raise ValidationException("Cada detalle requiere variante y cantidad positiva")
        variante_id = int(variante_id)
        totales_por_variante[variante_id] = totales_por_variante.get(variante_id, 0) + cantidad
        normalizados.append({**detalle, "variante_producto_id": variante_id, "cantidad": cantidad})
    return normalizados


async def _validar_stock(
    db: AsyncSession, tenant_id: int, sucursal_id: int, detalles: list[dict]
) -> None:
    conflictos = await ConflictoService.detectar_stock(db, tenant_id, sucursal_id, detalles)
    if conflictos:
        raise OperacionRechazada(
            "Stock insuficiente en el servidor central",
            tipo_conflicto=TipoConflicto.STOCK_INSUFICIENTE.value,
            estrategia="RECHAZO",
            datos={"faltantes": conflictos},
        )


async def _validar_cliente(
    db: AsyncSession, tenant_id: int, cliente_id
) -> int:
    if cliente_id is None:
        raise ValidationException("La operación requiere cliente_id")
    result = await db.execute(
        select(Cliente).where(
            Cliente.id == int(cliente_id),
            Cliente.tenant_id == tenant_id,
            Cliente.deleted_at.is_(None),
        )
    )
    if result.scalar_one_or_none() is None:
        raise OperacionRechazada(
            f"El cliente #{cliente_id} no existe en el tenant",
            tipo_conflicto=TipoConflicto.PRODUCTO_INACTIVO.value,
            estrategia="RECHAZO",
            datos={"cliente_id": cliente_id},
        )
    return int(cliente_id)


async def _validar_sucursal(
    db: AsyncSession, tenant_id: int, sucursal_id
) -> int:
    if sucursal_id is None:
        raise ValidationException("La operación requiere sucursal_id")
    result = await db.execute(
        select(Sucursal).where(
            Sucursal.id == int(sucursal_id),
            Sucursal.tenant_id == tenant_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise OperacionRechazada(
            f"La sucursal #{sucursal_id} no pertenece al tenant",
            tipo_conflicto=TipoConflicto.SUCURSAL_INVALIDA.value,
            estrategia="RECHAZO",
            datos={"sucursal_id": sucursal_id},
        )
    return int(sucursal_id)


# ============================================================
# RESERVA (CU06)
# ============================================================

async def manejar_reserva(
    db: AsyncSession, operacion, contexto: dict | None = None
) -> dict:
    payload = operacion.payload_json or {}
    tenant_id = operacion.tenant_id
    sucursal_id = await _validar_sucursal(
        db, tenant_id, payload.get("sucursal_id") or operacion.sucursal_id
    )
    cliente_id = await _validar_cliente(db, tenant_id, payload.get("cliente_id"))
    detalles = _detalles(payload)

    # Consolida cantidades por variante y valida contra el stock central.
    totales: dict[int, int] = {}
    for detalle in detalles:
        totales[detalle["variante_producto_id"]] = (
            totales.get(detalle["variante_producto_id"], 0) + detalle["cantidad"]
        )
    await _validar_stock(
        db,
        tenant_id,
        sucursal_id,
        [{"variante_producto_id": v, "cantidad": c} for v, c in totales.items()],
    )
    for variante_id, cantidad in totales.items():
        await _cargar_variante(db, tenant_id, variante_id)

    fecha_reserva = operacion.fecha_registro_local or datetime.utcnow()
    if payload.get("fecha_reserva"):
        try:
            fecha_reserva = datetime.fromisoformat(str(payload["fecha_reserva"]).replace("Z", "+00:00"))
            if fecha_reserva.tzinfo is not None:
                fecha_reserva = fecha_reserva.replace(tzinfo=None)
        except ValueError:
            raise ValidationException("fecha_reserva inválida")

    horario = payload.get("horario_aproximado")
    if isinstance(horario, str):
        try:
            horario = time.fromisoformat(horario)
        except ValueError:
            raise ValidationException("horario_aproximado inválido")

    reserva = Reserva(
        numero_reserva=ReservaService.generar_numero_reserva(),
        cliente_id=cliente_id,
        sucursal_id=sucursal_id,
        fecha_reserva=fecha_reserva,
        horario_aproximado=horario,
        estado=EstadoReserva.PENDIENTE,
        notas=payload.get("notas"),
        tenant_id=tenant_id,
        uuid_reserva=uuid4(),
        es_offline=True,
        estado_sincronizacion=EstadoSincronizacion.CONFIRMADA,
    )
    db.add(reserva)
    await db.flush()

    for detalle in detalles:
        db.add(DetalleReserva(
            reserva_id=reserva.id,
            variante_producto_id=detalle["variante_producto_id"],
            cantidad=detalle["cantidad"],
            estado=EstadoDetalleReserva.PENDIENTE,
            tenant_id=tenant_id,
        ))
        await InventarioVentasService.reservar_stock(
            db,
            detalle["variante_producto_id"],
            sucursal_id,
            detalle["cantidad"],
            usuario_id=operacion.usuario_id,
        )

    await db.flush()
    return {
        "reserva_id": reserva.id,
        "numero_reserva": reserva.numero_reserva,
        "uuid_reserva": str(reserva.uuid_reserva),
        "sucursal_id": sucursal_id,
        "cliente_id": cliente_id,
        "detalles": len(detalles),
        "estado": reserva.estado.value,
    }


# ============================================================
# VENTA_CONTADO (CU07)
# ============================================================

async def manejar_venta_contado(
    db: AsyncSession, operacion, contexto: dict | None = None
) -> dict:
    payload = operacion.payload_json or {}
    tenant_id = operacion.tenant_id
    sucursal_id = await _validar_sucursal(
        db, tenant_id, payload.get("sucursal_id") or operacion.sucursal_id
    )

    metodo = str(payload.get("metodo_pago") or "EFECTIVO").upper()
    if metodo != MetodoPagoPresencial.EFECTIVO.value:
        # RN-008: los cobros electrónicos no se ejecutan sin conexión.
        raise OperacionRechazada(
            "Una venta offline solo admite pago en efectivo",
            tipo_conflicto=TipoConflicto.VENTA_ORIGEN_INVALIDA.value,
            estrategia="RECHAZO",
            datos={"metodo_pago": metodo},
        )

    detalles = _detalles(payload)
    cliente_id = payload.get("cliente_id")
    if cliente_id is not None:
        cliente_id = await _validar_cliente(db, tenant_id, cliente_id)

    totales: dict[int, int] = {}
    for detalle in detalles:
        totales[detalle["variante_producto_id"]] = (
            totales.get(detalle["variante_producto_id"], 0) + detalle["cantidad"]
        )
    await _validar_stock(
        db,
        tenant_id,
        sucursal_id,
        [{"variante_producto_id": v, "cantidad": c} for v, c in totales.items()],
    )

    items: list[dict] = []
    for detalle in detalles:
        variante = await _cargar_variante(db, tenant_id, detalle["variante_producto_id"])
        precio = _precio_central(variante)
        subtotal = _moneda(precio * detalle["cantidad"])
        items.append({
            "variante_producto_id": variante.id,
            "cantidad": detalle["cantidad"],
            "precio_unitario": precio,
            "precio_unitario_local": (
                _moneda(detalle.get("precio_unitario_local"))
                if detalle.get("precio_unitario_local") is not None else None
            ),
            "subtotal": subtotal,
        })

    subtotal = sum((item["subtotal"] for item in items), Decimal("0.00"))
    # El descuento local solo se acepta si viene informado; el total central
    # siempre se recalcula (RN-007/RN-014).
    descuento = _moneda(payload.get("descuento") or 0)
    if descuento < 0 or descuento > subtotal:
        descuento = Decimal("0.00")
    total = _moneda(max(Decimal("0.00"), subtotal - descuento))

    numero_orden = OrdenService.generar_numero_orden()
    orden = Orden(
        numero_orden=numero_orden,
        cliente_id=cliente_id,
        sucursal_id=sucursal_id,
        tipo=TipoOrden.PRESENCIAL,
        estado=EstadoOrden.PAGADO,
        total=total,
        impuestos=Decimal("0.00"),
        descuentos=descuento,
        tenant_id=tenant_id,
        uuid_orden=uuid4(),
        origen=OrigenOperacion.OFFLINE,
        es_offline=True,
        estado_sincronizacion=EstadoSincronizacion.CONFIRMADA,
        fecha_limite_cambio=datetime.utcnow() + timedelta(days=settings.PLAZO_CAMBIO_DIAS),
        moneda=settings.STRIPE_MONEDA.upper(),
    )
    db.add(orden)
    await db.flush()

    for item in items:
        db.add(DetalleOrden(
            orden_id=orden.id,
            variante_producto_id=item["variante_producto_id"],
            cantidad=item["cantidad"],
            precio_unitario=item["precio_unitario"],
            subtotal=item["subtotal"],
            tenant_id=tenant_id,
            precio_unitario_local=item["precio_unitario_local"],
            precio_unitario_servidor=item["precio_unitario"],
        ))
        await InventarioVentasService.descontar_stock_venta(
            db,
            item["variante_producto_id"],
            sucursal_id,
            item["cantidad"],
            usuario_id=operacion.usuario_id,
            motivo="Venta de contado offline sincronizada",
        )

    comprobante = await OrdenService.generar_comprobante(
        db, orden.id, TipoComprobante.TICKET
    )
    comprobante.uuid_offline = operacion.uuid_operacion
    comprobante.es_temporal = False

    venta = VentaPresencial(
        orden_id=orden.id,
        sucursal_id=sucursal_id,
        cliente_id=cliente_id,
        fecha=operacion.fecha_registro_local or datetime.utcnow(),
        metodo_pago=MetodoPagoPresencial.EFECTIVO,
        tenant_id=tenant_id,
        es_offline=True,
        uuid_offline=operacion.uuid_operacion,
        estado_sincronizacion=EstadoSincronizacion.CONFIRMADA,
    )
    db.add(venta)
    await db.flush()

    return {
        "orden_id": orden.id,
        "numero_orden": orden.numero_orden,
        "venta_id": venta.id,
        "comprobante_id": comprobante.id,
        "comprobante_numero": comprobante.numero,
        "subtotal": float(subtotal),
        "descuento": float(descuento),
        "total": float(total),
        "items": len(items),
        "metodo_pago": MetodoPagoPresencial.EFECTIVO.value,
    }


# ============================================================
# CARRITO (CU15 desde el lote)
# ============================================================

async def manejar_carrito(
    db: AsyncSession, operacion, contexto: dict | None = None
) -> dict:
    payload = operacion.payload_json or {}
    tenant_id = operacion.tenant_id
    uuid_carrito = payload.get("uuid_carrito") or payload.get("uuid")
    try:
        uuid_carrito = UUID(str(uuid_carrito))
    except (TypeError, ValueError):
        raise ValidationException("La operación requiere uuid_carrito válido")

    cliente_id = payload.get("cliente_id")
    if cliente_id is not None:
        cliente_id = await _validar_cliente(db, tenant_id, cliente_id)

    result = await db.execute(
        select(Carrito)
        .options(selectinload(Carrito.items))
        .where(Carrito.uuid_carrito == uuid_carrito)
    )
    carrito = result.scalar_one_or_none()
    carrito_nuevo = False
    if carrito is None:
        # Evita chocar con el índice parcial de "un carrito activo por
        # dispositivo/cliente": si existe uno, se reutiliza y se le asigna el
        # uuid del dispositivo.
        query = select(Carrito).options(selectinload(Carrito.items)).where(
            Carrito.estado == EstadoCarrito.ACTIVO,
            Carrito.dispositivo_id == operacion.dispositivo_id,
        )
        if cliente_id is not None:
            query = query.where(Carrito.cliente_id == cliente_id)
        carrito = (await db.execute(query)).scalars().first()
        if carrito is None:
            carrito = Carrito(
                cliente_id=cliente_id,
                estado=EstadoCarrito.ACTIVO,
                tenant_id=tenant_id,
                dispositivo_id=operacion.dispositivo_id,
                es_offline=True,
                estado_sincronizacion=EstadoSincronizacion.CONFIRMADA,
            )
            db.add(carrito)
            carrito_nuevo = True
        carrito.uuid_carrito = uuid_carrito
    else:
        if carrito.estado != EstadoCarrito.ACTIVO:
            raise OperacionRechazada(
                "El carrito ya fue convertido o abandonado",
                datos={"uuid_carrito": str(uuid_carrito), "estado": carrito.estado.value},
            )
        carrito.dispositivo_id = operacion.dispositivo_id
        carrito.es_offline = True
        carrito.estado_sincronizacion = EstadoSincronizacion.CONFIRMADA

    await db.flush()

    if not carrito_nuevo:
        for item in list(carrito.items or []):
            await db.delete(item)
        await db.flush()

    detalles = _detalles(payload)
    total = Decimal("0.00")
    for detalle in detalles:
        variante = await _cargar_variante(db, tenant_id, detalle["variante_producto_id"])
        precio = _precio_central(variante)
        subtotal = _moneda(precio * detalle["cantidad"])
        total += subtotal
        db.add(ItemCarrito(
            carrito_id=carrito.id,
            variante_producto_id=variante.id,
            cantidad=detalle["cantidad"],
            precio_unitario=precio,
            subtotal=subtotal,
            tenant_id=tenant_id,
            precio_unitario_local=(
                _moneda(detalle.get("precio_unitario_local"))
                if detalle.get("precio_unitario_local") is not None else None
            ),
            stock_local_conocido=detalle.get("stock_local_conocido"),
        ))
    carrito.descuento_aplicado = Decimal("0.00")
    await db.flush()

    return {
        "carrito_id": carrito.id,
        "uuid_carrito": str(uuid_carrito),
        "items": len(detalles),
        "total": float(_moneda(total)),
        "requiere_aceptacion": False,
    }


def registrar_handlers() -> None:
    """Registra los tipos propios del Paquete 2 (plan 4.7)."""
    from app.apps.operaciones_offline.models import TipoOperacionOffline

    registrar_manejador(TipoOperacionOffline.RESERVA.value, manejar_reserva)
    registrar_manejador(TipoOperacionOffline.VENTA_CONTADO.value, manejar_venta_contado)
    registrar_manejador(TipoOperacionOffline.CARRITO.value, manejar_carrito)

"""
[EXAMEN-2] Manejadores del Paquete 5 — ORDEN_PAGO_DIFERIDO y PAGO_DIFERIDO.

El motor de sincronización del Paquete 2 nunca importa este paquete: es este
módulo el que se registra en el registro de manejadores (plan 4.7). Los
manejadores no confirman el cobro por sí mismos; el `PaymentIntent` queda en
`REQUIERE_ACCION_CLIENTE` y la confirmación llega por webhook firmado o por
verificación directa del intent (RN-015/RN-018).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.exceptions import (
    OperacionRechazada,
    PagoDuplicado,
    PagoNoEncontrado,
    ValidationException,
)
from app.apps.gestion_ventas.models import (
    Carrito,
    DetalleOrden,
    EstadoCarrito,
    EstadoOrden,
    EstadoTransaccion,
    ItemCarrito,
    MetodoPagoDigital,
    Orden,
    TransaccionPago,
)
from app.apps.operaciones_offline.models import (
    EstadoPagoDiferido,
    EstadoSincronizacion,
    OrigenOperacion,
    TipoConflicto,
    TipoOperacionOffline,
)
from app.apps.operaciones_offline.registro_manejadores import registrar_manejador
from app.apps.pagos_diferidos.carrito_service import CarritoOfflineService
from app.apps.pagos_diferidos.models import PagoDiferido
from app.apps.pagos_diferidos.orden_diferida_service import OrdenDiferidaService
from app.apps.pagos_diferidos.stripe_pago_service import (
    StripeErrorConfiguracion,
    StripeErrorPago,
    StripeErrorTransitorio,
    StripePagoService,
)


def _uuid(valor, campo: str) -> UUID:
    try:
        return UUID(str(valor))
    except (TypeError, ValueError):
        raise ValidationException(f"{campo} inválido")


def _d(valor) -> Decimal:
    return Decimal(str(valor if valor is not None else 0)).quantize(Decimal("0.01"))


async def _cargar_carrito(
    db: AsyncSession, tenant_id: int, uuid_valor: UUID
) -> Optional[Carrito]:
    result = await db.execute(
        select(Carrito)
        .options(
            selectinload(Carrito.items)
            .selectinload(ItemCarrito.variante_producto)
        )
        .where(
            Carrito.uuid_carrito == uuid_valor,
            Carrito.tenant_id == tenant_id,
        )
    )
    return result.scalar_one_or_none()


async def _crear_carrito_desde_payload(
    db: AsyncSession,
    operacion,
    uuid_valor: UUID,
    payload: dict,
) -> Carrito:
    """Reconstruye el carrito del dispositivo sin hacer commit.

    La operación `CARRITO` normalmente ya persistió el carrito; si el lote
    trae directamente `ORDEN_PAGO_DIFERIDO`, este método garantiza que la
    orden se cree a partir del estado local recibido.
    """
    items_payload = payload.get("items") or payload.get("detalles") or []
    if not isinstance(items_payload, list) or not items_payload:
        raise ValidationException("La operación requiere items del carrito")

    carrito = await CarritoOfflineService._resolver_carrito(
        db,
        operacion.tenant_id,
        uuid_valor,
        payload.get("cliente_id"),
        operacion.dispositivo_id,
    )
    carrito.es_offline = True
    carrito.estado_sincronizacion = EstadoSincronizacion.CONFIRMADA
    await db.flush()

    for item in list(carrito.items or []):
        await db.delete(item)
    await db.flush()

    procesados = await CarritoOfflineService._variantes_y_precios(
        db, operacion.tenant_id, items_payload
    )
    for item in procesados:
        db.add(ItemCarrito(
            carrito_id=carrito.id,
            variante_producto_id=item["variante_producto_id"],
            cantidad=item["cantidad"],
            precio_unitario=item["precio_unitario"],
            subtotal=item["subtotal"],
            tenant_id=operacion.tenant_id,
            precio_unitario_local=item["precio_unitario_local"],
            stock_local_conocido=item["stock_local_conocido"],
        ))
    await db.flush()
    return await _cargar_carrito(db, operacion.tenant_id, uuid_valor)


# ============================================================
# ORDEN_PAGO_DIFERIDO (CU15 -> CU16)
# ============================================================

async def manejar_orden_pago_diferido(
    db: AsyncSession, operacion, contexto: dict | None = None
) -> dict:
    payload = operacion.payload_json or {}
    tenant_id = operacion.tenant_id

    uuid_raw = payload.get("uuid_carrito") or payload.get("uuid")
    if not uuid_raw:
        raise ValidationException("La operación requiere uuid_carrito")
    uuid_valor = _uuid(uuid_raw, "uuid_carrito")

    carrito = await _cargar_carrito(db, tenant_id, uuid_valor)
    if carrito is None:
        carrito = await _crear_carrito_desde_payload(
            db, operacion, uuid_valor, payload
        )
    if carrito is None:
        raise ValidationException("No se pudo reconstruir el carrito")
    if carrito.estado != EstadoCarrito.ACTIVO:
        raise ValidationException(
            f"El carrito está en estado {carrito.estado.value} y no puede convertirse"
        )

    sucursal_id = await CarritoOfflineService.resolver_sucursal(
        db,
        tenant_id,
        payload.get("sucursal_id") or operacion.sucursal_id,
    )
    if payload.get("cliente_id") is not None and carrito.cliente_id is None:
        carrito.cliente_id = int(payload["cliente_id"])

    resultado = await OrdenDiferidaService.crear_orden_desde_carrito(
        db,
        carrito,
        tenant_id,
        acepta_nuevo_total=bool(payload.get("acepta_nuevo_total")),
        direccion_envio=payload.get("direccion_envio"),
        sucursal_id=sucursal_id,
    )

    # [EXAMEN-2] CU16/RN-015: la orden sola no habilita el cobro. Con el
    # `uuid_pago` del payload se crea también el PagoDiferido + PaymentIntent
    # (mismo contrato que la operación PAGO_DIFERIDO), de modo que tras la
    # primera sincronización el móvil ya puede abrir el PaymentSheet sin
    # depender de un reintento del POST directo (que nunca se reenviaba).
    pago_info: Optional[dict] = None
    uuid_pago_raw = payload.get("uuid_pago")
    if uuid_pago_raw:
        uuid_pago = _uuid(uuid_pago_raw, "uuid_pago")
        orden_creada = await db.get(Orden, int(resultado["orden_id"]))
        if orden_creada is None:
            raise ValidationException("La orden creada no es recuperable")
        existente = await _buscar_pago_global(db, uuid_pago)
        if existente is not None:
            # RN-015: mismo uuid_pago → mismo cobro, sin segundo PaymentIntent.
            pago_info = _serializar_pago(existente, orden_creada, duplicado=True)
        else:
            pago_info = await _crear_pago_para_orden(
                db,
                tenant_id,
                operacion,
                uuid_pago,
                orden_creada,
                moneda=payload.get("moneda"),
            )

    return {
        **resultado,
        "uuid_carrito": str(uuid_valor),
        "uuid_pago": payload.get("uuid_pago"),
        "requiere_pago": True,
        **({"pago": pago_info} if pago_info is not None else {}),
    }


# ============================================================
# PAGO_DIFERIDO (CU16)
# ============================================================

async def _buscar_pago_global(db: AsyncSession, uuid_valor: UUID) -> Optional[PagoDiferido]:
    result = await db.execute(
        select(PagoDiferido)
        .where(PagoDiferido.uuid_pago == uuid_valor)
        .execution_options(ignorar_tenant=True)
    )
    return result.scalar_one_or_none()


async def _resolver_orden(
    db: AsyncSession, tenant_id: int, payload: dict
) -> Orden:
    orden = None
    if payload.get("orden_id") is not None:
        result = await db.execute(
            select(Orden)
            .options(selectinload(Orden.detalles))
            .where(
                Orden.id == int(payload["orden_id"]),
                Orden.tenant_id == tenant_id,
            )
        )
        orden = result.scalar_one_or_none()
    elif payload.get("uuid_orden"):
        uuid_orden = _uuid(payload["uuid_orden"], "uuid_orden")
        result = await db.execute(
            select(Orden)
            .options(selectinload(Orden.detalles))
            .where(
                Orden.uuid_orden == uuid_orden,
                Orden.tenant_id == tenant_id,
            )
        )
        orden = result.scalar_one_or_none()
    if orden is None:
        raise PagoNoEncontrado("Orden no encontrada para el pago diferido")
    return orden


def _serializar_pago(pago: PagoDiferido, orden: Orden, duplicado: bool = False) -> dict:
    return {
        "pago_id": pago.id,
        "uuid_pago": str(pago.uuid_pago),
        "orden_id": orden.id,
        "numero_orden": orden.numero_orden,
        "uuid_orden": str(orden.uuid_orden) if orden.uuid_orden else None,
        "monto": float(pago.monto),
        "moneda": pago.moneda,
        "estado": pago.estado.value if hasattr(pago.estado, "value") else str(pago.estado),
        "stripe_payment_intent_id": pago.stripe_payment_intent_id,
        "client_secret": pago.client_secret,
        "idempotency_key": pago.idempotency_key,
        "requiere_accion": pago.estado == EstadoPagoDiferido.REQUIERE_ACCION_CLIENTE,
        "simulado": StripePagoService.modo_simulado(),
        "duplicado": duplicado,
    }


async def _crear_pago_para_orden(
    db: AsyncSession,
    tenant_id: int,
    operacion,
    uuid_valor: UUID,
    orden: Orden,
    moneda: Optional[str] = None,
) -> dict:
    """[EXAMEN-2] CU16/RN-015: crea el PagoDiferido + PaymentIntent de una orden.

    Compartido por la operación `PAGO_DIFERIDO` y por `ORDEN_PAGO_DIFERIDO`
    (que así completa orden + pago en una sola pasada de sincronización).
    La confirmación nunca ocurre aquí: el intent queda en
    `REQUIERE_ACCION_CLIENTE` y llega por webhook o verificación directa.
    """
    if orden.estado == EstadoOrden.PAGADO:
        raise PagoDuplicado("La orden ya está pagada")
    if orden.estado in {EstadoOrden.CANCELADO, EstadoOrden.PAGO_VENCIDO}:
        raise OperacionRechazada(
            "La orden no admite un nuevo pago",
            tipo_conflicto=TipoConflicto.ORDEN_EXPIRADA.value,
            estrategia="RECHAZO",
            datos={"estado_orden": orden.estado.value},
        )

    ahora = datetime.utcnow()
    expira = orden.expira_en or orden.fecha_limite_pago
    if expira is not None:
        if getattr(expira, "tzinfo", None) is not None:
            expira = expira.replace(tzinfo=None)
        if expira < ahora:
            raise OperacionRechazada(
                "La orden expiró antes del pago",
                tipo_conflicto=TipoConflicto.ORDEN_EXPIRADA.value,
                estrategia="RECHAZO",
                datos={"expira_en": expira.isoformat()},
            )

    pago = PagoDiferido(
        tenant_id=tenant_id,
        orden_id=orden.id,
        uuid_pago=uuid_valor,
        monto=_d(orden.total),
        moneda=(moneda or orden.moneda or settings.STRIPE_MONEDA).upper()[:3],
        estado=EstadoPagoDiferido.EN_PROCESO,
        idempotency_key=str(uuid_valor),
        intentos=1,
        fecha_expiracion=expira,
        fecha_ultimo_intento=ahora,
        operacion_offline_id=operacion.id,
    )
    db.add(pago)
    await db.flush()

    transaccion = TransaccionPago(
        orden_id=orden.id,
        monto=pago.monto,
        moneda=pago.moneda,
        metodo_pago=MetodoPagoDigital.STRIPE,
        estado=EstadoTransaccion.PENDIENTE,
        tenant_id=tenant_id,
        origen=OrigenOperacion.OFFLINE,
        idempotency_key=str(uuid_valor),
        intentos=1,
        fecha_expiracion=expira,
    )
    db.add(transaccion)
    await db.flush()

    try:
        intent = StripePagoService.crear_payment_intent(
            uuid_pago=str(uuid_valor),
            monto=pago.monto,
            moneda=pago.moneda,
            metadata={
                "tenant_id": tenant_id,
                "orden_id": orden.id,
                "uuid_pago": str(uuid_valor),
            },
            idempotency_key=str(uuid_valor),
        )
        pago.stripe_payment_intent_id = intent["id"]
        pago.client_secret = intent.get("client_secret")
        pago.estado = EstadoPagoDiferido.REQUIERE_ACCION_CLIENTE
        transaccion.stripe_payment_intent_id = intent["id"]
        transaccion.datos_respuesta = intent
    except StripeErrorTransitorio as exc:
        pago.estado = EstadoPagoDiferido.EN_COLA
        pago.ultimo_error = str(exc)
        transaccion.datos_respuesta = {"error": str(exc), "reintentar": True}
    except (StripeErrorConfiguracion, StripeErrorPago) as exc:
        pago.estado = EstadoPagoDiferido.ERROR_CRITICO
        pago.ultimo_error = str(exc)
        transaccion.estado = EstadoTransaccion.RECHAZADO
        transaccion.datos_respuesta = {"error": str(exc), "reintentar": False}

    await db.flush()
    return _serializar_pago(pago, orden)


async def manejar_pago_diferido(
    db: AsyncSession, operacion, contexto: dict | None = None
) -> dict:
    payload = operacion.payload_json or {}
    tenant_id = operacion.tenant_id
    uuid_valor = _uuid(payload.get("uuid_pago"), "uuid_pago")

    existente = await _buscar_pago_global(db, uuid_valor)
    if existente is not None:
        if existente.tenant_id != tenant_id:
            raise PagoNoEncontrado("Pago diferido no encontrado")
        orden = await _resolver_orden(
            db, tenant_id, {"orden_id": existente.orden_id}
        )
        # RN-015: mismo uuid_pago → mismo cobro, sin segundo PaymentIntent.
        return _serializar_pago(existente, orden, duplicado=True)

    orden = await _resolver_orden(db, tenant_id, payload)
    return await _crear_pago_para_orden(
        db,
        tenant_id,
        operacion,
        uuid_valor,
        orden,
        moneda=payload.get("moneda"),
    )


def registrar_handlers() -> None:
    """Registra los tipos propios del Paquete 5 (plan 4.7)."""
    registrar_manejador(
        TipoOperacionOffline.ORDEN_PAGO_DIFERIDO.value,
        manejar_orden_pago_diferido,
    )
    registrar_manejador(
        TipoOperacionOffline.PAGO_DIFERIDO.value,
        manejar_pago_diferido,
    )

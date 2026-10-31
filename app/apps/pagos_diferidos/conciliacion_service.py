"""
[EXAMEN-2] ConciliacionService — cruza el webhook con la orden y el stock (CU16).

Aplica una sola vez cada evento de Stripe. Si la orden ya no es cobrable
(expirada, cancelada, tenant suspendido) dispara el reembolso (RN-018) y deja
la trazabilidad en `LogConflicto`.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import PagoNoEncontrado
from app.apps.gestion_saas.models import EstadoTenant, Tenant
from app.apps.gestion_ventas.models import (
    Comprobante,
    EstadoOrden,
    EstadoTransaccion,
    MetodoPagoDigital,
    Orden,
    TipoComprobante,
    TransaccionPago,
)
from app.apps.gestion_ventas.services import InventarioVentasService
from app.apps.operaciones_offline.models import (
    EstadoPagoDiferido,
    EstadoSincronizacion,
    LogConflicto,
    OperacionOffline,
    TipoConflicto,
)
from app.apps.pagos_diferidos.models import EventoStripe, PagoDiferido
from app.apps.pagos_diferidos.reembolso_service import ReembolsoService


def _valor(obj) -> str:
    return str(getattr(obj, "value", obj))


class ConciliacionService:

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    @staticmethod
    async def obtener_pago_por_intent(
        db: AsyncSession, payment_intent_id: str
    ) -> Optional[PagoDiferido]:
        if not payment_intent_id:
            return None
        result = await db.execute(
            select(PagoDiferido)
            .where(PagoDiferido.stripe_payment_intent_id == payment_intent_id)
            .execution_options(ignorar_tenant=True)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def obtener_pago(
        db: AsyncSession, uuid_pago, tenant_id: Optional[int] = None
    ) -> PagoDiferido:
        from app.apps.pagos_diferidos.pago_service import PagoDiferidoService

        return await PagoDiferidoService.obtener_pago(db, uuid_pago, tenant_id)

    @staticmethod
    async def _emitir_comprobante(
        db: AsyncSession, orden: Orden, pago: PagoDiferido
    ) -> Comprobante:
        result = await db.execute(
            select(Comprobante).where(Comprobante.orden_id == orden.id)
        )
        comprobante = result.scalar_one_or_none()
        if comprobante is None:
            comprobante = Comprobante(
                numero=f"FAC-{datetime.now().strftime('%Y%m')}-{orden.id:06d}",
                tipo=TipoComprobante.FACTURA,
                monto_total=orden.total,
                archivo_pdf=f"/api/v1/ordenes/{orden.id}/comprobante/pdf",
                orden_id=orden.id,
                tenant_id=pago.tenant_id,
                uuid_offline=None,
                es_temporal=False,
            )
            db.add(comprobante)
        else:
            comprobante.es_temporal = False
            comprobante.uuid_offline = None
            comprobante.monto_total = orden.total
        await db.flush()
        return comprobante

    @staticmethod
    async def _transaccion_confirmada(
        db: AsyncSession, pago: PagoDiferido, datos_stripe: Optional[dict] = None
    ) -> TransaccionPago:
        result = await db.execute(
            select(TransaccionPago)
            .where(TransaccionPago.orden_id == pago.orden_id)
            .order_by(TransaccionPago.id.asc())
        )
        transaccion = result.scalars().first()
        if transaccion is None:
            transaccion = TransaccionPago(
                orden_id=pago.orden_id,
                monto=pago.monto,
                moneda=pago.moneda,
                metodo_pago=MetodoPagoDigital.STRIPE,
                estado=EstadoTransaccion.PENDIENTE,
                tenant_id=pago.tenant_id,
                stripe_payment_intent_id=pago.stripe_payment_intent_id,
                idempotency_key=pago.idempotency_key,
            )
            db.add(transaccion)
        transaccion.stripe_payment_intent_id = pago.stripe_payment_intent_id
        transaccion.idempotency_key = pago.idempotency_key
        transaccion.datos_respuesta = datos_stripe or transaccion.datos_respuesta
        await db.flush()
        return transaccion

    @staticmethod
    async def _consumir_stock_reservado(db: AsyncSession, orden: Orden) -> None:
        if orden.sucursal_id is None:
            return
        for detalle in orden.detalles:
            if not detalle.variante_producto_id:
                continue
            inventario = await InventarioVentasService.obtener_o_crear_inventario(
                db,
                detalle.variante_producto_id,
                orden.sucursal_id,
                for_update=True,
            )
            if (inventario.cantidad_reservada or 0) >= detalle.cantidad:
                await InventarioVentasService.liberar_stock_reserva(
                    db,
                    detalle.variante_producto_id,
                    orden.sucursal_id,
                    detalle.cantidad,
                    comprar=True,
                )

    @staticmethod
    async def _liberar_stock_reservado(db: AsyncSession, orden: Orden) -> None:
        if orden.sucursal_id is None:
            return
        for detalle in orden.detalles:
            if not detalle.variante_producto_id:
                continue
            inventario = await InventarioVentasService.obtener_o_crear_inventario(
                db,
                detalle.variante_producto_id,
                orden.sucursal_id,
                for_update=True,
            )
            cantidad = min(detalle.cantidad, inventario.cantidad_reservada or 0)
            if cantidad > 0:
                await InventarioVentasService.liberar_stock_reserva(
                    db,
                    detalle.variante_producto_id,
                    orden.sucursal_id,
                    cantidad,
                    comprar=False,
                )

    @staticmethod
    async def registrar_conflicto_pago(
        db: AsyncSession,
        pago: PagoDiferido,
        tipo_conflicto: str,
        detalle: Optional[dict] = None,
    ) -> Optional[LogConflicto]:
        if not pago.operacion_offline_id:
            return None
        result = await db.execute(
            select(OperacionOffline)
            .where(OperacionOffline.id == pago.operacion_offline_id)
            .execution_options(ignorar_tenant=True)
        )
        operacion = result.scalar_one_or_none()
        if operacion is None:
            return None
        conflicto = LogConflicto(
            tenant_id=pago.tenant_id,
            operacion_offline_id=operacion.id,
            tipo_conflicto=tipo_conflicto,
            detalle_json=detalle or {},
            resuelto=False,
            estrategia_resolucion="AUTOMATICA" if tipo_conflicto == TipoConflicto.PAGO_DUPLICADO.value else "MANUAL",
        )
        db.add(conflicto)
        await db.flush()
        return conflicto

    # ------------------------------------------------------------------
    # Confirmación
    # ------------------------------------------------------------------
    @classmethod
    async def confirmar_pago(
        cls,
        db: AsyncSession,
        pago: PagoDiferido,
        datos_stripe: Optional[dict] = None,
        evento: Optional[EventoStripe] = None,
    ) -> dict:
        if pago.estado == EstadoPagoDiferido.CONFIRMADO:
            return {"estado": pago.estado.value, "duplicado": True}

        result = await db.execute(
            select(Orden)
            .options(selectinload(Orden.detalles))
            .where(Orden.id == pago.orden_id)
            .execution_options(ignorar_tenant=True)
        )
        orden = result.scalar_one_or_none()
        if orden is None:
            raise PagoNoEncontrado("La orden del pago no existe")

        tenant_result = await db.execute(
            select(Tenant)
            .where(Tenant.id == pago.tenant_id)
            .execution_options(ignorar_tenant=True)
        )
        tenant = tenant_result.scalar_one_or_none()

        ahora = datetime.utcnow()
        expira = orden.expira_en or orden.fecha_limite_pago
        if expira is not None and getattr(expira, "tzinfo", None) is not None:
            expira = expira.replace(tzinfo=None)
        expirada = expira is not None and expira < ahora
        rechazada = orden.estado in {
            EstadoOrden.CANCELADO,
            EstadoOrden.PAGO_VENCIDO,
        }

        # RN-017: tenant suspendido → la orden queda pendiente de regularización
        if tenant is not None and tenant.estado != EstadoTenant.OPERATIVO:
            orden.estado = EstadoOrden.PENDIENTE_REGULARIZACION
            await cls.registrar_conflicto_pago(
                db,
                pago,
                TipoConflicto.TENANT_SUSPENDIDO.value,
                {"estado_tenant": _valor(tenant.estado)},
            )
            await ReembolsoService.reembolsar_por_orden_rechazada(
                db, pago, "Tenant suspendido: cobro regularizado"
            )
            if evento is not None:
                evento.procesado = True
                evento.fecha_procesamiento = ahora
                evento.error_detalle = None
            await db.flush()
            return {"estado": pago.estado.value, "reembolsado": True, "motivo": "TENANT_SUSPENDIDO"}

        # RN-018: pago capturado con orden expirada/cancelada → reembolso
        if expirada or rechazada:
            orden.estado = EstadoOrden.PAGO_VENCIDO if expirada else orden.estado
            await cls._liberar_stock_reservado(db, orden)
            await cls.registrar_conflicto_pago(
                db,
                pago,
                TipoConflicto.ORDEN_EXPIRADA.value,
                {
                    "orden_id": orden.id,
                    "expira_en": expira.isoformat() if expira else None,
                    "motivo": "Pago capturado después de vencer la orden",
                },
            )
            await ReembolsoService.reembolsar_por_orden_rechazada(
                db, pago, "Orden expirada o cancelada después del cobro"
            )
            if evento is not None:
                evento.procesado = True
                evento.fecha_procesamiento = ahora
                evento.error_detalle = None
            await db.flush()
            return {"estado": pago.estado.value, "reembolsado": True, "motivo": "ORDEN_EXPIRADA"}

        # Confirmación normal
        pago.estado = EstadoPagoDiferido.CONFIRMADO
        pago.fecha_ultimo_intento = ahora
        pago.ultimo_error = None
        orden.estado = EstadoOrden.PAGADO

        await cls._consumir_stock_reservado(db, orden)
        transaccion = await cls._transaccion_confirmada(db, pago, datos_stripe)
        transaccion.estado = EstadoTransaccion.CONFIRMADO
        comprobante = await cls._emitir_comprobante(db, orden, pago)
        if evento is not None:
            evento.pago_diferido_id = pago.id
            evento.procesado = True
            evento.fecha_procesamiento = ahora
            evento.error_detalle = None
        await db.flush()

        from app.services.websocket_manager import manager

        await manager.difundir_canal(f"pagos:{pago.tenant_id}", {
            "evento": "pago_confirmado",
            "uuid_pago": str(pago.uuid_pago),
            "orden_id": orden.id,
            "estado": pago.estado.value,
        })
        return {
            "estado": pago.estado.value,
            "orden_id": orden.id,
            "numero_orden": orden.numero_orden,
            "comprobante_id": comprobante.id,
            "comprobante_numero": comprobante.numero,
            "transaccion_id": transaccion.id,
        }

    # ------------------------------------------------------------------
    # Aplicación de eventos
    # ------------------------------------------------------------------
    @classmethod
    async def aplicar_evento(cls, db: AsyncSession, evento: EventoStripe) -> dict:
        payload = evento.payload_json or {}
        tipo = evento.tipo
        objeto = (payload.get("data") or {}).get("object") or {}
        payment_intent_id = (
            objeto.get("id")
            if tipo.startswith("payment_intent.")
            else objeto.get("payment_intent")
        )
        pago = None
        if evento.pago_diferido_id:
            pago = await db.get(PagoDiferido, evento.pago_diferido_id)
        if pago is None and payment_intent_id:
            pago = await cls.obtener_pago_por_intent(db, payment_intent_id)
        if pago is None and objeto.get("metadata", {}).get("uuid_pago"):
            from app.apps.pagos_diferidos.pago_service import PagoDiferidoService
            pago = await PagoDiferidoService.buscar_por_uuid(db, objeto["metadata"]["uuid_pago"])

        if pago is None:
            # Evento fuera de orden: queda registrado y se reintenta cuando se
            # cree el PagoDiferido (plan 9.2.10).
            evento.error_detalle = "PagoDiferido no encontrado; evento pendiente de conciliación"
            await db.flush()
            return {"procesado": False, "motivo": "PAGO_NO_ENCONTRADO"}

        evento.pago_diferido_id = pago.id

        if tipo == "payment_intent.succeeded":
            resultado = await cls.confirmar_pago(db, pago, datos_stripe=objeto, evento=evento)
        elif tipo == "payment_intent.payment_failed":
            error = (objeto.get("last_payment_error") or {}).get("message") or "Cobro rechazado"
            pago.estado = EstadoPagoDiferido.RECHAZADO
            pago.ultimo_error = error
            pago.motivo_rechazo = error
            pago.intentos = (pago.intentos or 0) + 1
            await cls._transaccion_confirmada(db, pago, objeto)
            result = await db.execute(
                select(TransaccionPago).where(TransaccionPago.orden_id == pago.orden_id)
            )
            for transaccion in result.scalars().all():
                transaccion.estado = EstadoTransaccion.RECHAZADO
            evento.procesado = True
            evento.fecha_procesamiento = datetime.utcnow()
            resultado = {"estado": pago.estado.value, "motivo": error}
        elif tipo == "payment_intent.canceled":
            pago.estado = EstadoPagoDiferido.CANCELADO
            result_orden = await db.execute(
                select(Orden)
                .options(selectinload(Orden.detalles))
                .where(Orden.id == pago.orden_id)
                .execution_options(ignorar_tenant=True)
            )
            orden = result_orden.scalar_one_or_none()
            if orden is not None:
                orden.estado = EstadoOrden.CANCELADO
                await cls._liberar_stock_reservado(db, orden)
            evento.procesado = True
            evento.fecha_procesamiento = datetime.utcnow()
            resultado = {"estado": pago.estado.value}
        elif tipo == "charge.refunded":
            pago.reembolso_id = objeto.get("id")
            evento.procesado = True
            evento.fecha_procesamiento = datetime.utcnow()
            resultado = {"estado": "REEMBOLSADO", "reembolso_id": pago.reembolso_id}
        elif tipo in {"payment_intent.requires_action", "payment_intent.amount_capturable_updated"}:
            pago.estado = EstadoPagoDiferido.REQUIERE_ACCION_CLIENTE
            evento.procesado = True
            evento.fecha_procesamiento = datetime.utcnow()
            resultado = {"estado": pago.estado.value}
        else:
            evento.procesado = True
            evento.fecha_procesamiento = datetime.utcnow()
            resultado = {"estado": pago.estado.value, "ignorado": True}

        await db.flush()
        return resultado

    # ------------------------------------------------------------------
    # Reconciliación de eventos fuera de orden
    # ------------------------------------------------------------------
    @classmethod
    async def reconciliar_eventos_pendientes(
        cls, db: AsyncSession, pago: PagoDiferido
    ) -> int:
        if not pago.stripe_payment_intent_id:
            return 0
        result = await db.execute(
            select(EventoStripe)
            .where(EventoStripe.procesado.is_(False))
            .execution_options(ignorar_tenant=True)
        )
        procesados = 0
        for evento in result.scalars().all():
            objeto = ((evento.payload_json or {}).get("data") or {}).get("object") or {}
            candidatos = {
                str(objeto.get("id") or ""),
                str(objeto.get("payment_intent") or ""),
                str((objeto.get("metadata") or {}).get("uuid_pago") or ""),
            }
            if pago.stripe_payment_intent_id in candidatos or str(pago.uuid_pago) in candidatos:
                await cls.aplicar_evento(db, evento)
                procesados += 1
        if procesados:
            await db.commit()
        return procesados

    @staticmethod
    async def verificar_orden_cobrable(db: AsyncSession, orden: Orden) -> list[dict]:
        conflictos = []
        if orden.estado in {EstadoOrden.CANCELADO, EstadoOrden.PAGO_VENCIDO}:
            conflictos.append({
                "tipo_conflicto": TipoConflicto.ORDEN_EXPIRADA.value,
                "orden_id": orden.id,
                "estado": _valor(orden.estado),
            })
        expira = orden.expira_en or orden.fecha_limite_pago
        if expira is not None:
            if getattr(expira, "tzinfo", None) is not None:
                expira = expira.replace(tzinfo=None)
            if expira < datetime.utcnow():
                conflictos.append({
                    "tipo_conflicto": TipoConflicto.ORDEN_EXPIRADA.value,
                    "orden_id": orden.id,
                    "expira_en": expira.isoformat(),
                })
        return conflictos

"""
[EXAMEN-2] PagoDiferidoService — cola e idempotencia del cobro (CU16).

`uuid_pago` es a la vez clave idempotente del negocio (RN-015) y
`Idempotency-Key` de Stripe: reenviarlo devuelve el estado actual sin crear un
segundo `PaymentIntent`.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.exceptions import (
    ConflictException,
    NotFoundException,
    OperacionRechazada,
    PagoDuplicado,
    PagoNoConfirmado,
    ValidationException,
)
from app.services.websocket_manager import manager
from app.apps.gestion_ventas.models import (
    EstadoOrden,
    EstadoTransaccion,
    MetodoPagoDigital,
    Orden,
    TransaccionPago,
)
from app.apps.operaciones_offline.idempotencia_service import IdempotenciaService
from app.apps.operaciones_offline.models import (
    EstadoPagoDiferido,
    OrigenOperacion,
    TipoConflicto,
)
from app.apps.pagos_diferidos.carrito_service import CarritoOfflineService
from app.apps.pagos_diferidos.conciliacion_service import ConciliacionService
from app.apps.pagos_diferidos.models import PagoDiferido
from app.apps.pagos_diferidos.schemas import PagoDiferidoRequest
from app.apps.pagos_diferidos.stripe_pago_service import (
    StripeErrorConfiguracion,
    StripeErrorPago,
    StripeErrorTransitorio,
    StripePagoService,
)

ESTADOS_PENDIENTES = {
    EstadoPagoDiferido.PENDIENTE_ENVIO,
    EstadoPagoDiferido.EN_COLA,
    EstadoPagoDiferido.EN_PROCESO,
    EstadoPagoDiferido.REQUIERE_ACCION_CLIENTE,
}


def _uuid(valor) -> UUID:
    try:
        return UUID(str(valor))
    except (TypeError, ValueError):
        raise ValidationException("uuid_pago inválido")


class PagoDiferidoService:

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------
    @staticmethod
    async def buscar_por_uuid(db: AsyncSession, uuid_pago) -> Optional[PagoDiferido]:
        uuid_valor = _uuid(uuid_pago)
        result = await db.execute(
            select(PagoDiferido)
            .options(selectinload(PagoDiferido.eventos))
            .where(PagoDiferido.uuid_pago == uuid_valor)
            .execution_options(ignorar_tenant=True)
        )
        return result.scalar_one_or_none()

    @classmethod
    async def obtener_pago(
        cls, db: AsyncSession, uuid_pago, tenant_id: Optional[int] = None
    ) -> PagoDiferido:
        pago = await cls.buscar_por_uuid(db, uuid_pago)
        if pago is None or (tenant_id is not None and pago.tenant_id != tenant_id):
            raise NotFoundException("Pago diferido no encontrado")
        return pago

    @staticmethod
    async def _numero_orden(db: AsyncSession, orden_id: int) -> Optional[str]:
        result = await db.execute(
            select(Orden.numero_orden)
            .where(Orden.id == orden_id)
            .execution_options(ignorar_tenant=True)
        )
        return result.scalar_one_or_none()

    @classmethod
    async def serializar(cls, db: AsyncSession, pago: PagoDiferido, duplicado=False) -> dict:
        return {
            "id": pago.id,
            "uuid_pago": str(pago.uuid_pago),
            "orden_id": pago.orden_id,
            "numero_orden": await cls._numero_orden(db, pago.orden_id),
            "monto": pago.monto,
            "moneda": pago.moneda,
            "estado": pago.estado.value if hasattr(pago.estado, "value") else str(pago.estado),
            "stripe_payment_intent_id": pago.stripe_payment_intent_id,
            "client_secret": pago.client_secret,
            "requiere_accion": pago.estado == EstadoPagoDiferido.REQUIERE_ACCION_CLIENTE,
            "idempotency_key": pago.idempotency_key,
            "intentos": pago.intentos or 0,
            "fecha_creacion": pago.fecha_creacion,
            "fecha_expiracion": pago.fecha_expiracion,
            "fecha_ultimo_intento": pago.fecha_ultimo_intento,
            "ultimo_error": pago.ultimo_error,
            "motivo_rechazo": pago.motivo_rechazo,
            "reembolso_id": pago.reembolso_id,
            "simulado": StripePagoService.modo_simulado(),
            "duplicado": duplicado,
        }

    @staticmethod
    async def resolver_orden(
        db: AsyncSession,
        tenant_id: int,
        datos: PagoDiferidoRequest,
    ) -> Orden:
        if datos.uuid_carrito and not (datos.orden_id or datos.uuid_orden):
            conversion = await CarritoOfflineService.convertir_carrito(
                db,
                datos.uuid_carrito,
                tenant_id,
                acepta_nuevo_total=datos.acepta_nuevo_total,
            )
            orden_id = conversion["orden_id"]
        elif datos.orden_id is not None:
            orden_id = datos.orden_id
        elif datos.uuid_orden:
            try:
                uuid_orden = UUID(str(datos.uuid_orden))
            except (TypeError, ValueError):
                raise ValidationException("uuid_orden inválido")
            result = await db.execute(
                select(Orden.id).where(
                    Orden.uuid_orden == uuid_orden,
                    Orden.tenant_id == tenant_id,
                )
            )
            orden_id = result.scalar_one_or_none()
            if orden_id is None:
                raise NotFoundException("Orden no encontrada")
        else:
            raise ValidationException("Se requiere uuid_orden, orden_id o uuid_carrito")

        result = await db.execute(
            select(Orden)
            .options(selectinload(Orden.detalles))
            .where(Orden.id == orden_id, Orden.tenant_id == tenant_id)
        )
        orden = result.scalar_one_or_none()
        if orden is None:
            raise NotFoundException("Orden no encontrada")
        return orden

    # ------------------------------------------------------------------
    # Registro del pago
    # ------------------------------------------------------------------
    @classmethod
    async def registrar_pago(
        cls,
        db: AsyncSession,
        tenant_id: int,
        usuario_id: int,
        datos: PagoDiferidoRequest,
    ) -> dict:
        uuid_valor = _uuid(datos.uuid_pago)
        existente = await cls.buscar_por_uuid(db, uuid_valor)
        if existente is not None:
            if existente.tenant_id != tenant_id:
                raise NotFoundException("Pago diferido no encontrado")
            # RN-015: mismo uuid_pago → mismo estado, sin segundo cobro.
            return await cls.serializar(db, existente, duplicado=True)

        orden = await cls.resolver_orden(db, tenant_id, datos)

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
                orden.estado = EstadoOrden.PAGO_VENCIDO
                await ConciliacionService._liberar_stock_reservado(db, orden)
                await db.commit()
                raise OperacionRechazada(
                    "La orden expiró antes del pago",
                    tipo_conflicto=TipoConflicto.ORDEN_EXPIRADA.value,
                    estrategia="RECHAZO",
                    datos={"expira_en": expira.isoformat()},
                )

        expiracion = expira or (
            ahora + timedelta(hours=settings.PAGO_DIFERIDO_VIGENCIA_HORAS)
        )
        operacion = await IdempotenciaService.buscar_operacion(db, uuid_valor)

        pago = PagoDiferido(
            tenant_id=tenant_id,
            orden_id=orden.id,
            uuid_pago=uuid_valor,
            monto=Decimal(str(orden.total)).quantize(Decimal("0.01")),
            moneda=(datos.moneda or orden.moneda or settings.STRIPE_MONEDA).upper()[:3],
            estado=EstadoPagoDiferido.EN_PROCESO,
            idempotency_key=str(uuid_valor),
            intentos=1,
            fecha_expiracion=expiracion,
            fecha_ultimo_intento=ahora,
            operacion_offline_id=operacion.id if operacion is not None else None,
            motivo_rechazo=None,
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
            fecha_expiracion=expiracion,
        )
        db.add(transaccion)
        await db.flush()

        metadata = {
            "tenant_id": tenant_id,
            "orden_id": orden.id,
            "uuid_pago": str(uuid_valor),
            **{k: str(v) for k, v in (datos.metadata or {}).items()},
        }
        try:
            intent = StripePagoService.crear_payment_intent(
                uuid_pago=str(uuid_valor),
                monto=pago.monto,
                moneda=pago.moneda,
                metadata=metadata,
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
            transaccion.estado = EstadoTransaccion.PENDIENTE
            transaccion.datos_respuesta = {"error": str(exc), "reintentar": True}
        except StripeErrorConfiguracion as exc:
            pago.estado = EstadoPagoDiferido.ERROR_CRITICO
            pago.ultimo_error = str(exc)
            transaccion.estado = EstadoTransaccion.RECHAZADO
            transaccion.datos_respuesta = {"error": str(exc), "reintentar": False}
        except StripeErrorPago as exc:
            pago.estado = EstadoPagoDiferido.RECHAZADO
            pago.ultimo_error = str(exc)
            pago.motivo_rechazo = str(exc)
            transaccion.estado = EstadoTransaccion.RECHAZADO
            transaccion.datos_respuesta = {"error": str(exc)}

        await db.commit()
        await db.refresh(pago)

        # Evento fuera de orden: `payment_intent.succeeded` pudo llegar antes.
        if pago.stripe_payment_intent_id:
            await ConciliacionService.reconciliar_eventos_pendientes(db, pago)
            await db.refresh(pago)

        await manager.difundir_canal(f"pagos:{tenant_id}", {
            "evento": "pago_registrado",
            "uuid_pago": str(pago.uuid_pago),
            "estado": pago.estado.value,
        })
        return await cls.serializar(db, pago, duplicado=False)

    # ------------------------------------------------------------------
    # Confirmación / reintento / cancelación
    # ------------------------------------------------------------------
    @classmethod
    async def confirmar_pago(
        cls, db: AsyncSession, uuid_pago, tenant_id: int
    ) -> dict:
        pago = await cls.obtener_pago(db, uuid_pago, tenant_id)
        if pago.estado == EstadoPagoDiferido.CONFIRMADO:
            return await cls.serializar(db, pago, duplicado=True)
        if not pago.stripe_payment_intent_id:
            raise PagoNoConfirmado("El pago no tiene PaymentIntent")

        intent = StripePagoService.obtener_payment_intent(pago.stripe_payment_intent_id)
        estado_stripe = (intent.get("status") or "").lower()
        if estado_stripe == "succeeded":
            # Se confía en el PaymentIntent verificado, nunca en el cliente.
            await ConciliacionService.confirmar_pago(db, pago, datos_stripe=intent)
            await db.commit()
            await db.refresh(pago)
            return await cls.serializar(db, pago)
        if estado_stripe in {"requires_action", "requires_confirmation", "requires_payment_method"}:
            pago.estado = EstadoPagoDiferido.REQUIERE_ACCION_CLIENTE
            pago.ultimo_error = "Stripe aún requiere la confirmación del cliente"
            await db.commit()
            raise PagoNoConfirmado("El pago aún requiere acción del cliente")
        raise PagoNoConfirmado(f"El pago está en estado {estado_stripe or 'desconocido'}")

    @classmethod
    async def reintentar_pago(
        cls, db: AsyncSession, uuid_pago, tenant_id: int
    ) -> dict:
        pago = await cls.obtener_pago(db, uuid_pago, tenant_id)
        if pago.estado == EstadoPagoDiferido.CONFIRMADO:
            return await cls.serializar(db, pago, duplicado=True)
        if pago.estado not in {
            EstadoPagoDiferido.RECHAZADO,
            EstadoPagoDiferido.ERROR_CRITICO,
            EstadoPagoDiferido.EN_COLA,
        }:
            raise ConflictException(
                f"El pago está en estado {pago.estado.value} y no es reintentable"
            )
        if (pago.intentos or 0) >= settings.PAGO_DIFERIDO_MAX_REINTENTOS:
            pago.estado = EstadoPagoDiferido.ERROR_CRITICO
            pago.ultimo_error = "Superó el tope de reintentos"
            await db.commit()
            raise ConflictException("El pago superó el número máximo de reintentos")

        # Si el intento anterior ya cobró, no se crea otro cobro.
        if pago.stripe_payment_intent_id:
            intent = StripePagoService.obtener_payment_intent(pago.stripe_payment_intent_id)
            if (intent.get("status") or "").lower() == "succeeded":
                await ConciliacionService.confirmar_pago(db, pago, datos_stripe=intent)
                await db.commit()
                return await cls.serializar(db, pago)

        pago.intentos = (pago.intentos or 0) + 1
        pago.estado = EstadoPagoDiferido.EN_PROCESO
        pago.fecha_ultimo_intento = datetime.utcnow()
        try:
            intent = StripePagoService.crear_payment_intent(
                uuid_pago=str(pago.uuid_pago),
                monto=pago.monto,
                moneda=pago.moneda,
                metadata={
                    "tenant_id": pago.tenant_id,
                    "orden_id": pago.orden_id,
                    "uuid_pago": str(pago.uuid_pago),
                    "reintento": pago.intentos,
                },
                idempotency_key=f"{pago.uuid_pago}:r{pago.intentos}",
            )
            pago.stripe_payment_intent_id = intent["id"]
            pago.client_secret = intent.get("client_secret")
            pago.estado = EstadoPagoDiferido.REQUIERE_ACCION_CLIENTE
            pago.ultimo_error = None
        except StripeErrorTransitorio as exc:
            pago.estado = EstadoPagoDiferido.EN_COLA
            pago.ultimo_error = str(exc)
        except (StripeErrorConfiguracion, StripeErrorPago) as exc:
            pago.estado = EstadoPagoDiferido.ERROR_CRITICO
            pago.ultimo_error = str(exc)
        await db.commit()
        await db.refresh(pago)
        return await cls.serializar(db, pago)

    @classmethod
    async def cancelar_pago(
        cls, db: AsyncSession, uuid_pago, tenant_id: int, motivo: Optional[str] = None
    ) -> dict:
        pago = await cls.obtener_pago(db, uuid_pago, tenant_id)
        if pago.estado == EstadoPagoDiferido.CONFIRMADO:
            raise ConflictException("Un pago confirmado no se cancela; use el reembolso")
        if pago.estado == EstadoPagoDiferido.CANCELADO:
            return await cls.serializar(db, pago, duplicado=True)

        result = await db.execute(
            select(Orden)
            .options(selectinload(Orden.detalles))
            .where(Orden.id == pago.orden_id)
            .execution_options(ignorar_tenant=True)
        )
        orden = result.scalar_one_or_none()
        if orden is not None:
            orden.estado = EstadoOrden.CANCELADO
            await ConciliacionService._liberar_stock_reservado(db, orden)

        if pago.stripe_payment_intent_id:
            StripePagoService.cancelar_payment_intent(pago.stripe_payment_intent_id)
        pago.estado = EstadoPagoDiferido.CANCELADO
        pago.motivo_rechazo = motivo or "Cancelado por el usuario"
        result = await db.execute(
            select(TransaccionPago).where(TransaccionPago.orden_id == pago.orden_id)
        )
        for transaccion in result.scalars().all():
            transaccion.estado = EstadoTransaccion.CANCELADO
        await db.commit()
        await db.refresh(pago)
        return await cls.serializar(db, pago)

    # ------------------------------------------------------------------
    # Listado y expiración
    # ------------------------------------------------------------------
    @staticmethod
    async def listar_pagos(
        db: AsyncSession,
        tenant_id: int,
        estado: Optional[str] = None,
        desde: Optional[datetime] = None,
        hasta: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[PagoDiferido]:
        query = select(PagoDiferido).where(PagoDiferido.tenant_id == tenant_id)
        if estado:
            query = query.where(PagoDiferido.estado == estado.upper())
        if desde:
            query = query.where(PagoDiferido.fecha_creacion >= desde)
        if hasta:
            query = query.where(PagoDiferido.fecha_creacion <= hasta)
        query = (
            query.order_by(PagoDiferido.fecha_creacion.desc())
            .offset(max(0, skip))
            .limit(max(1, min(limit, 200)))
        )
        return list((await db.execute(query)).scalars().all())

    @staticmethod
    async def contar_pendientes(db: AsyncSession, tenant_id: int) -> int:
        result = await db.execute(
            select(func.count(PagoDiferido.id)).where(
                PagoDiferido.tenant_id == tenant_id,
                PagoDiferido.estado.in_(list(ESTADOS_PENDIENTES)),
            )
        )
        return result.scalar_one() or 0

    @classmethod
    async def expirar_pagos(cls, db: AsyncSession) -> dict:
        """Job de expiración: pagos vencidos, órdenes y stock liberado."""
        ahora = datetime.utcnow()
        pagos_vencidos = 0
        ordenes_vencidas = 0

        result = await db.execute(
            select(PagoDiferido)
            .where(
                PagoDiferido.estado.in_(list(ESTADOS_PENDIENTES)),
                PagoDiferido.fecha_expiracion < ahora,
            )
            .execution_options(ignorar_tenant=True)
        )
        for pago in result.scalars().all():
            pago.estado = EstadoPagoDiferido.EXPIRADO
            pago.motivo_rechazo = "Pago vencido"
            if pago.stripe_payment_intent_id:
                StripePagoService.cancelar_payment_intent(pago.stripe_payment_intent_id)
            orden_result = await db.execute(
                select(Orden)
                .options(selectinload(Orden.detalles))
                .where(Orden.id == pago.orden_id)
                .execution_options(ignorar_tenant=True)
            )
            orden = orden_result.scalar_one_or_none()
            if orden is not None and orden.estado != EstadoOrden.PAGADO:
                orden.estado = EstadoOrden.PAGO_VENCIDO
                await ConciliacionService._liberar_stock_reservado(db, orden)
            trans_result = await db.execute(
                select(TransaccionPago).where(TransaccionPago.orden_id == pago.orden_id)
            )
            for transaccion in trans_result.scalars().all():
                transaccion.estado = EstadoTransaccion.EXPIRADO
            pagos_vencidos += 1
            await manager.difundir_canal(f"pagos:{pago.tenant_id}", {
                "evento": "pago_expirado",
                "uuid_pago": str(pago.uuid_pago),
                "estado": pago.estado.value,
            })

        # Órdenes pendientes de pago diferido sin PagoDiferido asociado.
        result = await db.execute(
            select(Orden)
            .options(selectinload(Orden.detalles))
            .where(
                Orden.estado == EstadoOrden.PENDIENTE_PAGO_DIFERIDO,
                Orden.expira_en.is_not(None),
                Orden.expira_en < ahora,
            )
            .execution_options(ignorar_tenant=True)
        )
        for orden in result.scalars().all():
            orden.estado = EstadoOrden.PAGO_VENCIDO
            await ConciliacionService._liberar_stock_reservado(db, orden)
            ordenes_vencidas += 1

        if pagos_vencidos or ordenes_vencidas:
            await db.commit()
        return {"pagos_expirados": pagos_vencidos, "ordenes_vencidas": ordenes_vencidas}

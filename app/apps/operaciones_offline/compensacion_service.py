"""[EXAMEN-2] Fase E — compensación de ventas offline rechazadas (RF-032 / RN-020).

Cuando una venta que el cliente **ya pagó** en la sucursal se rechaza al
sincronizar (por stock insuficiente, producto inactivo, etc.), se crea **una**
compensación trazable. La unique por `operacion_offline_id` garantiza que el
reintento de sincronización no duplique el dinero.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictException, NotFoundException, ValidationException
from app.apps.operaciones_offline.models import (
    Compensacion,
    EstadoCompensacion,
    TipoCompensacion,
    TipoOperacionOffline,
)

logger = logging.getLogger(__name__)


def _decimal(valor) -> Decimal:
    return Decimal(str(valor if valor is not None else 0)).quantize(Decimal("0.01"))


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class CompensacionService:
    """Alta, confirmación, reintento y consulta de compensaciones."""

    @staticmethod
    def _venta_pagada(payload: dict) -> bool:
        metodo = str(payload.get("metodo_pago") or "").upper()
        if metodo in {"EFECTIVO", "TARJETA", "TARJETA_DEBITO", "TARJETA_CREDITO", "QR"}:
            return True
        if payload.get("pago_confirmado") is True:
            return True
        try:
            return _decimal(payload.get("monto_recibido")) > 0
        except Exception:
            return False

    @staticmethod
    def _tipo_para(payload: dict) -> TipoCompensacion:
        metodo = str(payload.get("metodo_pago") or "EFECTIVO").upper()
        if metodo == "EFECTIVO":
            return TipoCompensacion.REEMBOLSO_EFECTIVO
        if metodo in {"TARJETA", "TARJETA_DEBITO", "TARJETA_CREDITO", "QR"}:
            if payload.get("stripe_payment_intent_id") or payload.get("pago_diferido_id"):
                return TipoCompensacion.REEMBOLSO_TARJETA
            return TipoCompensacion.REEMBOLSO_TARJETA
        return TipoCompensacion.NOTA_CREDITO

    @staticmethod
    async def crear_para_operacion(
        db: AsyncSession,
        operacion,
        conflicto: dict | None = None,
        monto=None,
        motivo: str | None = None,
    ) -> Compensacion | None:
        existente = (await db.execute(
            select(Compensacion).where(
                Compensacion.operacion_offline_id == operacion.id
            )
        )).scalar_one_or_none()
        if existente is not None:
            return existente

        payload = dict(operacion.payload_json or {})
        if not CompensacionService._venta_pagada(payload):
            # Sin evidencia de pago solo queda el conflicto, como antes.
            return None

        tipo_operacion = (
            operacion.tipo_operacion.value
            if hasattr(operacion.tipo_operacion, "value")
            else str(operacion.tipo_operacion)
        )
        if tipo_operacion not in {
            TipoOperacionOffline.VENTA_CONTADO.value,
            TipoOperacionOffline.DEVOLUCION_CAMBIO.value,
            TipoOperacionOffline.REPOSICION.value,
        }:
            return None

        monto_decimal = _decimal(
            monto
            if monto is not None
            else (payload.get("total") or payload.get("monto_total") or 0)
        )
        if monto_decimal <= 0:
            return None

        compensacion = Compensacion(
            tenant_id=operacion.tenant_id,
            operacion_offline_id=operacion.id,
            orden_id=payload.get("orden_id"),
            cliente_id=payload.get("cliente_id"),
            tipo=CompensacionService._tipo_para(payload),
            monto=monto_decimal,
            moneda=str(payload.get("moneda") or "BOB").upper()[:3],
            estado=EstadoCompensacion.PENDIENTE,
            motivo=(motivo or "Venta offline rechazada")[:200],
        )
        db.add(compensacion)
        await db.flush()

        # Reembolso automático por pasarela cuando existe el pago diferido.
        if compensacion.tipo == TipoCompensacion.REEMBOLSO_TARJETA:
            try:
                from app.apps.pagos_diferidos.models import PagoDiferido
                from app.apps.pagos_diferidos.reembolso_service import ReembolsoService

                pago = None
                if compensacion.orden_id:
                    pago = (await db.execute(
                        select(PagoDiferido)
                        .where(
                            PagoDiferido.orden_id == compensacion.orden_id,
                            PagoDiferido.tenant_id == operacion.tenant_id,
                        )
                        .order_by(PagoDiferido.id.desc())
                    )).scalars().first()
                if pago is not None and pago.stripe_payment_intent_id:
                    resultado = await ReembolsoService.reembolsar(
                        db,
                        pago,
                        monto=compensacion.monto,
                        motivo=compensacion.motivo or "Venta offline rechazada",
                        usuario_id=None,
                    )
                    compensacion.referencia_externa = resultado.get("reembolso_id") or resultado.get("refund_id")
                    compensacion.estado = EstadoCompensacion.COMPLETADA
                    compensacion.fecha_resolucion = _ahora()
            except Exception as exc:
                logger.warning(
                    "Reembolso por pasarela falló para la operación %s: %s",
                    operacion.id,
                    exc,
                )
                compensacion.estado = EstadoCompensacion.FALLIDA
                compensacion.error_detalle = str(exc)[:2000]

        await db.flush()

        # RN-024: la notificación se persiste antes de enviarse.
        try:
            from app.services.notificacion_service import NotificacionService

            await NotificacionService.crear(
                db,
                tenant_id=operacion.tenant_id,
                cliente_id=compensacion.cliente_id,
                usuario_id=operacion.usuario_id,
                tipo="COMPENSACION",
                asunto="Devolución de tu compra",
                mensaje=(
                    f"Tu venta no pudo completarse. Se registró una compensación de "
                    f"{compensacion.monto} {compensacion.moneda} "
                    f"({compensacion.tipo.value})."
                ),
                referencia=("compensacion", compensacion.id),
            )
            compensacion.notificado_cliente = True
        except Exception as exc:  # pragma: no cover - notificación opcional
            logger.warning("No se pudo notificar la compensación %s: %s", compensacion.id, exc)

        await db.flush()
        return compensacion

    @staticmethod
    async def obtener(db: AsyncSession, tenant_id: int, compensacion_id: int) -> Compensacion:
        compensacion = (await db.execute(
            select(Compensacion).where(
                Compensacion.id == compensacion_id,
                Compensacion.tenant_id == tenant_id,
            )
        )).scalar_one_or_none()
        if compensacion is None:
            raise NotFoundException("Compensación no encontrada")
        return compensacion

    @staticmethod
    async def listar(
        db: AsyncSession,
        tenant_id: int,
        estado: str | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Compensacion]:
        query = select(Compensacion).where(Compensacion.tenant_id == tenant_id)
        if estado:
            query = query.where(Compensacion.estado == EstadoCompensacion(estado.upper()))
        if desde:
            query = query.where(Compensacion.fecha_creacion >= desde)
        if hasta:
            query = query.where(Compensacion.fecha_creacion <= hasta)
        query = (
            query.order_by(Compensacion.fecha_creacion.desc(), Compensacion.id.desc())
            .offset(max(0, skip))
            .limit(max(1, min(limit, 200)))
        )
        return list((await db.execute(query)).scalars().all())

    @staticmethod
    async def resumen(db: AsyncSession, tenant_id: int) -> dict:
        por_estado = (await db.execute(
            select(Compensacion.estado, func.count(Compensacion.id), func.sum(Compensacion.monto))
            .where(Compensacion.tenant_id == tenant_id)
            .group_by(Compensacion.estado)
        )).all()
        por_tipo = (await db.execute(
            select(Compensacion.tipo, func.count(Compensacion.id), func.sum(Compensacion.monto))
            .where(Compensacion.tenant_id == tenant_id)
            .group_by(Compensacion.tipo)
        )).all()
        return {
            "tenant_id": tenant_id,
            "por_estado": {
                (estado.value if hasattr(estado, "value") else str(estado)): {
                    "cantidad": int(total or 0),
                    "monto": float(monto or 0),
                }
                for estado, total, monto in por_estado
            },
            "por_tipo": {
                (tipo.value if hasattr(tipo, "value") else str(tipo)): {
                    "cantidad": int(total or 0),
                    "monto": float(monto or 0),
                }
                for tipo, total, monto in por_tipo
            },
        }

    @staticmethod
    async def confirmar_efectivo(
        db: AsyncSession, tenant_id: int, compensacion_id: int, usuario_id: int | None
    ) -> Compensacion:
        compensacion = await CompensacionService.obtener(db, tenant_id, compensacion_id)
        if compensacion.tipo != TipoCompensacion.REEMBOLSO_EFECTIVO:
            raise ValidationException(
                "Solo las compensaciones en efectivo se confirman en caja"
            )
        if compensacion.estado == EstadoCompensacion.COMPLETADA:
            return compensacion
        compensacion.estado = EstadoCompensacion.COMPLETADA
        compensacion.fecha_resolucion = _ahora()
        compensacion.resuelto_por = usuario_id
        await db.commit()
        await db.refresh(compensacion)
        return compensacion

    @staticmethod
    async def reintentar(
        db: AsyncSession, tenant_id: int, compensacion_id: int, usuario_id: int | None = None
    ) -> Compensacion:
        compensacion = await CompensacionService.obtener(db, tenant_id, compensacion_id)
        if compensacion.estado == EstadoCompensacion.COMPLETADA:
            raise ConflictException("La compensación ya está completada")
        if compensacion.tipo == TipoCompensacion.REEMBOLSO_EFECTIVO:
            raise ValidationException(
                "El reembolso en efectivo se confirma en caja, no se reintenta por pasarela"
            )
        try:
            from app.apps.pagos_diferidos.models import PagoDiferido
            from app.apps.pagos_diferidos.reembolso_service import ReembolsoService

            if not compensacion.orden_id:
                raise ValidationException("La compensación no tiene orden asociada")
            pago = (await db.execute(
                select(PagoDiferido)
                .where(
                    PagoDiferido.orden_id == compensacion.orden_id,
                    PagoDiferido.tenant_id == tenant_id,
                )
                .order_by(PagoDiferido.id.desc())
            )).scalars().first()
            if pago is None or not pago.stripe_payment_intent_id:
                raise ValidationException("No hay un pago por pasarela para reembolsar")
            resultado = await ReembolsoService.reembolsar(
                db,
                pago,
                monto=compensacion.monto,
                motivo=compensacion.motivo or "Reintento de compensación",
                usuario_id=usuario_id,
            )
            compensacion.referencia_externa = resultado.get("reembolso_id") or resultado.get("refund_id")
            compensacion.estado = EstadoCompensacion.COMPLETADA
            compensacion.fecha_resolucion = _ahora()
            compensacion.resuelto_por = usuario_id
            compensacion.error_detalle = None
        except Exception as exc:
            compensacion.estado = EstadoCompensacion.FALLIDA
            compensacion.error_detalle = str(exc)[:2000]
        await db.commit()
        await db.refresh(compensacion)
        return compensacion

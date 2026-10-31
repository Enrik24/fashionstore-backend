"""
[EXAMEN-2] ReembolsoService — reembolso total o parcial (RN-018).

Nunca reembolsa dos veces el mismo pago: la `Idempotency-Key` del reembolso
deriva de `uuid_pago` y el resultado se persiste en `PagoDiferido.reembolso_id`
y en `TransaccionPago`.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictException, PagoNoEncontrado
from app.apps.gestion_ventas.models import (
    EstadoOrden,
    EstadoTransaccion,
    TransaccionPago,
)
from app.apps.pagos_diferidos.models import PagoDiferido
from app.apps.pagos_diferidos.stripe_pago_service import (
    StripeErrorPago,
    StripeErrorTransitorio,
    StripePagoService,
)
from app.apps.operaciones_offline.models import EstadoPagoDiferido


class ReembolsoService:

    @staticmethod
    def _monto_valido(monto, total: Decimal) -> Decimal:
        monto = Decimal(str(monto if monto is not None else total))
        if monto <= 0 or monto > total:
            raise ConflictException("El monto del reembolso debe ser mayor que 0 y no superar el pago")
        return monto.quantize(Decimal("0.01"))

    @classmethod
    async def reembolsar(
        cls,
        db: AsyncSession,
        pago: PagoDiferido,
        monto=None,
        motivo: Optional[str] = None,
        usuario_id: Optional[int] = None,
    ) -> dict:
        if pago.reembolso_id:
            # Regla de oro: jamás se reembolsa dos veces el mismo pago.
            return {
                "reembolso_id": pago.reembolso_id,
                "monto": float(pago.monto),
                "estado": "REEMBOLSADO",
                "reutilizado": True,
            }
        if not pago.stripe_payment_intent_id:
            raise PagoNoEncontrado("El pago no tiene PaymentIntent para reembolsar")

        monto_reembolso = cls._monto_valido(monto, Decimal(str(pago.monto)))
        try:
            reembolso = StripePagoService.reembolsar(
                pago.stripe_payment_intent_id,
                monto_reembolso,
                idempotency_key=f"{pago.uuid_pago}:refund",
                motivo=motivo,
            )
        except StripeErrorTransitorio as exc:
            pago.ultimo_error = f"Reembolso transitorio: {exc}"
            await db.flush()
            raise
        except StripeErrorPago as exc:
            pago.ultimo_error = f"Reembolso rechazado: {exc}"
            pago.estado = EstadoPagoDiferido.ERROR_CRITICO
            await db.flush()
            raise ConflictException("Stripe rechazó el reembolso")

        pago.reembolso_id = reembolso.get("id")
        pago.motivo_rechazo = motivo or pago.motivo_rechazo
        pago.estado = EstadoPagoDiferido.RECHAZADO

        result = await db.execute(
            select(TransaccionPago).where(TransaccionPago.orden_id == pago.orden_id)
        )
        transacciones = list(result.scalars().all())
        for transaccion in transacciones:
            transaccion.estado = EstadoTransaccion.REEMBOLSADO
            transaccion.datos_respuesta = {
                **(transaccion.datos_respuesta or {}),
                "reembolso_id": pago.reembolso_id,
                "monto_reembolsado": float(monto_reembolso),
                "motivo": motivo,
                "usuario_id": usuario_id,
                "fecha_reembolso": datetime.utcnow().isoformat(),
            }
        await db.flush()
        return {
            "reembolso_id": pago.reembolso_id,
            "monto": float(monto_reembolso),
            "estado": "REEMBOLSADO",
            "reutilizado": bool(reembolso.get("reutilizado")),
        }

    @classmethod
    async def reembolsar_por_orden_rechazada(
        cls,
        db: AsyncSession,
        pago: PagoDiferido,
        motivo: str,
        monto=None,
    ) -> dict:
        return await cls.reembolsar(db, pago, monto=monto, motivo=motivo)

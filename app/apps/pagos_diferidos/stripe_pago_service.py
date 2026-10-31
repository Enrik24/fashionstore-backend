"""
[EXAMEN-2] StripePagoService — PaymentIntent, Idempotency-Key y Refund (CU16).

Cuando `STRIPE_API_KEY` está vacío se usa un modo simulado determinista que
permite ejecutar toda la suite sin red. En producción se delega en el SDK y
nunca se crea un `PaymentIntent` sin `Idempotency-Key = uuid_pago` (RN-015).
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional
from uuid import uuid4

import stripe

from app.config import settings

logger = logging.getLogger(__name__)

if settings.STRIPE_API_VERSION:
    stripe.api_version = settings.STRIPE_API_VERSION
stripe.api_key = settings.STRIPE_API_KEY

# Estado determinista del modo simulado (pruebas y desarrollo sin red)
_SIMULADOS: dict[str, dict[str, Any]] = {}
_IDEMPOTENCIA: dict[str, str] = {}
_REEMBOLSOS: dict[str, dict[str, Any]] = {}


class StripeErrorConfiguracion(Exception):
    """Error de configuración de Stripe (clave inválida, versión, etc.)."""


class StripeErrorTransitorio(Exception):
    """429/5xx o timeout de Stripe: el cliente debe reintentar."""


class StripeErrorPago(Exception):
    """Tarjeta rechazada u otro error definitivo del cobro."""


class StripePagoService:

    @staticmethod
    def modo_simulado() -> bool:
        return not bool(settings.STRIPE_API_KEY)

    @staticmethod
    def _centavos(monto) -> int:
        return max(1, int(Decimal(str(monto)) * 100))

    @classmethod
    def crear_payment_intent(
        cls,
        *,
        uuid_pago: str,
        monto,
        moneda: Optional[str] = None,
        metadata: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        clave = str(idempotency_key or uuid_pago)
        moneda = (moneda or settings.STRIPE_MONEDA).lower()
        metadata = {str(k): str(v) for k, v in (metadata or {}).items()}

        if cls.modo_simulado():
            existente = _IDEMPOTENCIA.get(clave)
            if existente and existente in _SIMULADOS:
                return dict(_SIMULADOS[existente], reutilizado=True)
            intent_id = f"pi_sim_{uuid4().hex[:24]}"
            datos = {
                "id": intent_id,
                "client_secret": f"{intent_id}_secret_sim",
                "status": "requires_payment_method",
                "amount": cls._centavos(monto),
                "currency": moneda,
                "metadata": metadata,
                "idempotency_key": clave,
                "simulado": True,
            }
            _SIMULADOS[intent_id] = datos
            _IDEMPOTENCIA[clave] = intent_id
            return dict(datos, reutilizado=False)

        if not settings.STRIPE_API_KEY:
            raise StripeErrorConfiguracion("Stripe no está configurado")
        try:
            intent = stripe.PaymentIntent.create(
                amount=cls._centavos(monto),
                currency=moneda,
                automatic_payment_methods={"enabled": True},
                metadata={**metadata, "uuid_pago": str(uuid_pago)},
                idempotency_key=clave,
            )
        except stripe.error.RateLimitError as exc:
            raise StripeErrorTransitorio(str(exc))
        except stripe.error.APIConnectionError as exc:
            raise StripeErrorTransitorio(str(exc))
        except stripe.error.StripeError as exc:
            # Clave inválida, versión incompatible, etc.
            raise StripeErrorConfiguracion(str(exc))
        return {
            "id": intent.id,
            "client_secret": intent.client_secret,
            "status": intent.status,
            "amount": intent.amount,
            "currency": intent.currency,
            "metadata": dict(intent.metadata or {}),
            "idempotency_key": clave,
            "simulado": False,
        }

    @classmethod
    def obtener_payment_intent(cls, payment_intent_id: str) -> dict[str, Any]:
        if cls.modo_simulado():
            datos = _SIMULADOS.get(payment_intent_id)
            if datos is None:
                return {
                    "id": payment_intent_id,
                    "status": "requires_payment_method",
                    "simulado": True,
                    "desconocido": True,
                }
            return dict(datos)
        try:
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        except stripe.error.RateLimitError as exc:
            raise StripeErrorTransitorio(str(exc))
        except stripe.error.APIConnectionError as exc:
            raise StripeErrorTransitorio(str(exc))
        except stripe.error.StripeError as exc:
            raise StripeErrorConfiguracion(str(exc))
        return {
            "id": intent.id,
            "status": intent.status,
            "amount": intent.amount,
            "currency": intent.currency,
            "metadata": dict(intent.metadata or {}),
            "simulado": False,
        }

    @classmethod
    def marcar_confirmado_simulado(cls, payment_intent_id: str) -> dict[str, Any]:
        """Solo pruebas: emula la confirmación que haría Stripe.js."""
        datos = _SIMULADOS.get(payment_intent_id)
        if datos is None:
            datos = {
                "id": payment_intent_id,
                "client_secret": f"{payment_intent_id}_secret_sim",
                "amount": 0,
                "currency": settings.STRIPE_MONEDA.lower(),
                "metadata": {},
                "idempotency_key": payment_intent_id,
                "simulado": True,
            }
            _SIMULADOS[payment_intent_id] = datos
        datos["status"] = "succeeded"
        return dict(datos)

    @classmethod
    def cancelar_payment_intent(cls, payment_intent_id: str) -> dict[str, Any]:
        if cls.modo_simulado():
            datos = _SIMULADOS.get(payment_intent_id)
            if datos is not None:
                datos["status"] = "canceled"
                return dict(datos)
            return {"id": payment_intent_id, "status": "canceled", "simulado": True}
        try:
            intent = stripe.PaymentIntent.cancel(payment_intent_id)
        except stripe.error.StripeError:
            return {"id": payment_intent_id, "status": "canceled"}
        return {"id": intent.id, "status": intent.status, "simulado": False}

    @classmethod
    def reembolsar(
        cls,
        payment_intent_id: str,
        monto=None,
        *,
        idempotency_key: Optional[str] = None,
        motivo: Optional[str] = None,
    ) -> dict[str, Any]:
        clave = str(idempotency_key or payment_intent_id)
        if clave in _REEMBOLSOS:
            return dict(_REEMBOLSOS[clave], reutilizado=True)

        if cls.modo_simulado():
            datos = {
                "id": f"re_sim_{uuid4().hex[:24]}",
                "payment_intent": payment_intent_id,
                "amount": cls._centavos(monto) if monto is not None else None,
                "status": "succeeded",
                "simulado": True,
                "motivo": motivo,
            }
            _REEMBOLSOS[clave] = datos
            return dict(datos, reutilizado=False)

        params: dict[str, Any] = {"payment_intent": payment_intent_id}
        if monto is not None:
            params["amount"] = cls._centavos(monto)
        if motivo:
            params["metadata"] = {"motivo": str(motivo)[:400]}
        try:
            refund = stripe.Refund.create(idempotency_key=clave, **params)
        except stripe.error.RateLimitError as exc:
            raise StripeErrorTransitorio(str(exc))
        except stripe.error.APIConnectionError as exc:
            raise StripeErrorTransitorio(str(exc))
        except stripe.error.StripeError as exc:
            raise StripeErrorPago(str(exc))
        return {
            "id": refund.id,
            "payment_intent": payment_intent_id,
            "amount": refund.amount,
            "status": refund.status,
            "simulado": False,
        }

    @staticmethod
    def limpiar_simulados() -> None:
        _SIMULADOS.clear()
        _IDEMPOTENCIA.clear()
        _REEMBOLSOS.clear()

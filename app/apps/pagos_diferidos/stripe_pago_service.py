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
_SESIONES: dict[str, dict[str, Any]] = {}


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
    def crear_checkout_session(
        cls,
        *,
        uuid_pago: str,
        monto,
        moneda: Optional[str] = None,
        metadata: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
        success_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
    ) -> dict[str, Any]:
        """Checkout Session (mode=payment) para el pago diferido (CU16).

        Devuelve el mismo contrato que `crear_payment_intent` más
        `checkout_session_id`/`checkout_url`. El `id` devuelto es el del
        PaymentIntent subyacente: toda la confirmación, cancelación y
        reembolso sigue operando sobre ese intent (RN-015/RN-018).

        En modo simulado delega en `crear_payment_intent` (sin URL real) para
        que la suite pueda monkeypatchear un único punto de entrada.
        """
        clave = str(idempotency_key or uuid_pago)
        moneda = (moneda or settings.STRIPE_MONEDA).lower()
        metadata = {str(k): str(v) for k, v in (metadata or {}).items()}
        metadata.setdefault("uuid_pago", str(uuid_pago))

        if cls.modo_simulado():
            intent = cls.crear_payment_intent(
                uuid_pago=uuid_pago,
                monto=monto,
                moneda=moneda,
                metadata=metadata,
                idempotency_key=clave,
            )
            # Determinista: el mismo intent (idempotencia) → misma sesión.
            session_id = intent["id"].replace("pi_sim_", "cs_sim_", 1)
            datos = {
                **intent,
                "checkout_session_id": session_id,
                "checkout_url": None,
                "payment_intent": intent["id"],
            }
            _SESIONES[session_id] = datos
            return datos

        base_exito = (success_url or settings.STRIPE_PAGO_DIFERIDO_SUCCESS_URL).rstrip("/")
        base_cancel = (cancel_url or settings.STRIPE_PAGO_DIFERIDO_CANCEL_URL).rstrip("/")
        try:
            session = stripe.checkout.Session.create(
                mode="payment",
                line_items=[{
                    "price_data": {
                        "currency": moneda,
                        "product_data": {"name": f"FashionStore · pedido diferido {uuid_pago}"},
                        "unit_amount": cls._centavos(monto),
                    },
                    "quantity": 1,
                }],
                payment_intent_data={"metadata": metadata},
                metadata=metadata,
                client_reference_id=str(uuid_pago),
                success_url=f"{base_exito}/{uuid_pago}?retorno=exito",
                cancel_url=f"{base_cancel}/{uuid_pago}?retorno=cancelado",
                idempotency_key=clave,
            )
        except stripe.error.RateLimitError as exc:
            raise StripeErrorTransitorio(str(exc))
        except stripe.error.APIConnectionError as exc:
            raise StripeErrorTransitorio(str(exc))
        except stripe.error.StripeError as exc:
            raise StripeErrorConfiguracion(str(exc))
        return {
            "id": session.payment_intent,
            "client_secret": None,
            "status": "requires_payment_method",
            "amount": session.amount_total,
            "currency": session.currency,
            "metadata": dict(session.metadata or {}),
            "idempotency_key": clave,
            "simulado": False,
            "checkout_session_id": session.id,
            "checkout_url": session.url,
            "payment_intent": session.payment_intent,
        }

    @classmethod
    def obtener_checkout_session(cls, session_id: str) -> dict[str, Any]:
        if cls.modo_simulado():
            datos = _SESIONES.get(session_id)
            if datos is None:
                return {"id": session_id, "status": "open", "simulado": True, "desconocido": True}
            intent = _SIMULADOS.get(datos.get("payment_intent") or "")
            if intent is not None and intent.get("status") == "succeeded":
                return dict(datos, status="complete", payment_status="paid")
            return dict(datos, status="open", payment_status="unpaid")
        try:
            session = stripe.checkout.Session.retrieve(session_id)
        except stripe.error.RateLimitError as exc:
            raise StripeErrorTransitorio(str(exc))
        except stripe.error.APIConnectionError as exc:
            raise StripeErrorTransitorio(str(exc))
        except stripe.error.StripeError as exc:
            raise StripeErrorConfiguracion(str(exc))
        return {
            "id": session.id,
            "status": session.status,
            "payment_status": session.payment_status,
            "payment_intent": session.payment_intent,
            "url": session.url,
            "metadata": dict(session.metadata or {}),
            "simulado": False,
        }

    @classmethod
    def expirar_checkout_session(cls, session_id: str) -> dict[str, Any]:
        if cls.modo_simulado():
            datos = _SESIONES.get(session_id)
            intent_id = (datos or {}).get("payment_intent")
            if intent_id:
                cls.cancelar_payment_intent(intent_id)
            return {"id": session_id, "status": "expired", "simulado": True}
        try:
            session = stripe.checkout.Session.expire(session_id)
        except stripe.error.StripeError:
            return {"id": session_id, "status": "expired"}
        return {"id": session.id, "status": session.status, "simulado": False}

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
        _SESIONES.clear()

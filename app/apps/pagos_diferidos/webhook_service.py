"""
[EXAMEN-2] WebhookService — firma, registro único y aplicación de eventos (CU16).

Reglas de oro (plan 9.2.6):
* nunca se procesa un webhook sin registrar su `stripe_event_id`;
* un evento ya procesado responde 200 sin volver a tocar stock/orden/comprobante;
* solo una firma inválida produce 400.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import FirmaWebhookInvalida, ValidationException
from app.apps.pagos_diferidos.models import EventoStripe

logger = logging.getLogger(__name__)

TOLERANCIA_SEGUNDOS = 300


class WebhookService:

    @staticmethod
    def _secreto() -> str:
        return settings.STRIPE_WEBHOOK_SECRET_PAGOS or settings.STRIPE_WEBHOOK_SECRET

    @classmethod
    def validar_firma(cls, payload: bytes, firma: Optional[str]) -> None:
        secreto = cls._secreto()
        if not secreto:
            # Modo simulado sin secreto configurado: no hay red que validar.
            if not settings.STRIPE_API_KEY:
                return
            raise FirmaWebhookInvalida("No hay secreto de webhook configurado")
        if not firma:
            raise FirmaWebhookInvalida()

        partes = {}
        for bloque in firma.split(","):
            if "=" in bloque:
                clave, valor = bloque.split("=", 1)
                partes.setdefault(clave.strip(), []).append(valor.strip())
        timestamps = partes.get("t") or []
        firmas = partes.get("v1") or []
        if not timestamps or not firmas:
            raise FirmaWebhookInvalida()

        try:
            timestamp = int(timestamps[0])
        except ValueError:
            raise FirmaWebhookInvalida()
        if TOLERANCIA_SEGUNDOS and abs(time.time() - timestamp) > TOLERANCIA_SEGUNDOS:
            raise FirmaWebhookInvalida("La firma del webhook expiró")

        firmado = f"{timestamp}.{payload.decode('utf-8')}".encode("utf-8")
        esperado = hmac.new(secreto.encode("utf-8"), firmado, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(esperado, candidata) for candidata in firmas):
            raise FirmaWebhookInvalida()

    @staticmethod
    def generar_firma(payload: bytes, secreto: Optional[str] = None, timestamp: Optional[int] = None) -> str:
        """Utilidad para pruebas y para el modo simulado."""
        secreto = secreto or settings.STRIPE_WEBHOOK_SECRET_PAGOS or settings.STRIPE_WEBHOOK_SECRET
        timestamp = timestamp or int(time.time())
        firmado = f"{timestamp}.{payload.decode('utf-8')}".encode("utf-8")
        firma = hmac.new(secreto.encode("utf-8"), firmado, hashlib.sha256).hexdigest()
        return f"t={timestamp},v1={firma}"

    @classmethod
    async def procesar(
        cls, db: AsyncSession, payload: bytes, firma: Optional[str]
    ) -> dict:
        cls.validar_firma(payload, firma)

        try:
            evento_json = json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValidationException("El cuerpo del webhook no es JSON válido")

        stripe_event_id = evento_json.get("id")
        tipo = evento_json.get("type")
        if not stripe_event_id or not tipo:
            raise ValidationException("El evento no contiene id o type")

        existente = (await db.execute(
            select(EventoStripe)
            .where(EventoStripe.stripe_event_id == stripe_event_id)
            .execution_options(ignorar_tenant=True)
        )).scalar_one_or_none()
        if existente is not None:
            return {
                "recibido": True,
                "evento": stripe_event_id,
                "tipo": tipo,
                "procesado": bool(existente.procesado),
                "duplicado": True,
                "motivo": existente.error_detalle,
            }

        objeto = (evento_json.get("data") or {}).get("object") or {}
        metadata = objeto.get("metadata") or {}
        tenant_id = metadata.get("tenant_id")
        payment_intent_id = (
            objeto.get("id") if tipo.startswith("payment_intent.") else objeto.get("payment_intent")
        )

        # El webhook no tiene JWT: el tenant se deduce del pago o de la metadata.
        evento = EventoStripe(
            tenant_id=int(tenant_id) if tenant_id else None,
            stripe_event_id=stripe_event_id,
            tipo=tipo,
            payload_json=evento_json,
            procesado=False,
        )
        if payment_intent_id:
            from app.apps.pagos_diferidos.conciliacion_service import ConciliacionService

            pago = await ConciliacionService.obtener_pago_por_intent(db, payment_intent_id)
            if pago is not None:
                evento.pago_diferido_id = pago.id
                evento.tenant_id = pago.tenant_id
        db.add(evento)
        # Se confirma el registro antes de aplicar el efecto: si el proceso
        # falla, el evento no se pierde y puede reintentarse.
        await db.commit()
        await db.refresh(evento)

        try:
            from app.apps.pagos_diferidos.conciliacion_service import ConciliacionService

            resultado = await ConciliacionService.aplicar_evento(db, evento)
            await db.commit()
            return {
                "recibido": True,
                "evento": stripe_event_id,
                "tipo": tipo,
                "procesado": bool(evento.procesado),
                "duplicado": False,
                "motivo": None if evento.procesado else resultado.get("motivo"),
            }
        except Exception as exc:  # el webhook siempre debe responder 200
            await db.rollback()
            evento = (await db.execute(
                select(EventoStripe)
                .where(EventoStripe.stripe_event_id == stripe_event_id)
                .execution_options(ignorar_tenant=True)
            )).scalar_one_or_none()
            if evento is not None:
                evento.error_detalle = str(exc)[:1000]
                evento.procesado = False
                await db.commit()
            logger.exception("Error procesando webhook %s", stripe_event_id)
            return {
                "recibido": True,
                "evento": stripe_event_id,
                "tipo": tipo,
                "procesado": False,
                "duplicado": False,
                "motivo": str(exc),
            }

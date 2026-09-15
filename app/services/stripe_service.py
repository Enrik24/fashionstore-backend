"""
Servicio de Integración con Stripe Checkout y Webhooks.
Maneja la creación de sesiones de pago, verificación de webhooks y reembolsos.
"""
import stripe
from decimal import Decimal
from typing import Optional, Dict, Any
from app.config import settings
import logging

logger = logging.getLogger(__name__)

# Configurar clave de API de Stripe
stripe.api_key = settings.STRIPE_API_KEY


class StripeService:
    @staticmethod
    def create_checkout_session(
        orden_id: int,
        numero_orden: str,
        total: Decimal,
        email_usuario: Optional[str] = None,
        detalles: Optional[list] = None,
        success_url: Optional[str] = None,
        cancel_url: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Crea una sesión de Stripe Checkout para pagar una orden.
        """
        stripe.api_key = settings.STRIPE_API_KEY
        
        # URLs de redirección
        s_url = success_url or f"{settings.STRIPE_SUCCESS_URL}?session_id={{CHECKOUT_SESSION_ID}}&orden_id={orden_id}"
        c_url = cancel_url or f"{settings.STRIPE_CANCEL_URL}?orden_id={orden_id}"
        
        line_items = []
        if detalles and len(detalles) > 0:
            for item in detalles:
                # Nombre del producto o variante
                nombre_item = f"Producto #{item.variante_producto_id or 'General'}"
                if hasattr(item, 'variante_producto') and item.variante_producto:
                    if hasattr(item.variante_producto, 'producto') and item.variante_producto.producto:
                        nombre_item = f"{item.variante_producto.producto.nombre}"
                    elif hasattr(item.variante_producto, 'sku_variante'):
                        nombre_item = f"SKU: {item.variante_producto.sku_variante}"
                
                precio_centavos = int(Decimal(str(item.precio_unitario)) * 100)
                line_items.append({
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": nombre_item,
                        },
                        "unit_amount": max(precio_centavos, 50),  # Stripe mínimo ~50 centavos
                    },
                    "quantity": item.cantidad,
                })
        else:
            # Línea genérica con el total de la orden
            total_centavos = int(Decimal(str(total)) * 100)
            line_items.append({
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"Orden {numero_orden}",
                    },
                    "unit_amount": max(total_centavos, 50),
                },
                "quantity": 1,
            })
            
        session_params: Dict[str, Any] = {
            "payment_method_types": ["card"],
            "line_items": line_items,
            "mode": "payment",
            "success_url": s_url,
            "cancel_url": c_url,
            "metadata": {
                "orden_id": str(orden_id),
                "numero_orden": numero_orden
            }
        }
        
        # Pre-llenar email del cliente si está disponible
        if email_usuario:
            session_params["customer_email"] = email_usuario
            
        session = stripe.checkout.Session.create(**session_params)
        
        return {
            "session_id": session.id,
            "checkout_url": session.url
        }

    @staticmethod
    def retrieve_session(session_id: str) -> stripe.checkout.Session:
        """Obtiene los detalles de una sesión de Stripe."""
        stripe.api_key = settings.STRIPE_API_KEY
        return stripe.checkout.Session.retrieve(session_id)

    @staticmethod
    def construct_webhook_event(payload: bytes, sig_header: str, webhook_secret: Optional[str] = None) -> stripe.Event:
        """Verifica la firma y construye el evento del Webhook de Stripe."""
        secret = webhook_secret or settings.STRIPE_WEBHOOK_SECRET
        return stripe.Webhook.construct_event(payload, sig_header, secret)

    @staticmethod
    def refund_payment(payment_intent_id: str, amount_centavos: Optional[int] = None) -> stripe.Refund:
        """Realiza un reembolso de un pago en Stripe."""
        stripe.api_key = settings.STRIPE_API_KEY
        params: Dict[str, Any] = {"payment_intent": payment_intent_id}
        if amount_centavos:
            params["amount"] = amount_centavos
        return stripe.Refund.create(**params)

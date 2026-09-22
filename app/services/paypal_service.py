"""
Servicio de Integración con PayPal REST API v2.
Maneja la autenticación OAuth2, creación de órdenes, captura de pagos y reembolsos.
"""
import httpx
from decimal import Decimal
from typing import Optional, Dict, Any
from app.config import settings
import logging

logger = logging.getLogger(__name__)


class PayPalService:
    @staticmethod
    def _get_base_url() -> str:
        if settings.PAYPAL_MODE.lower() == "live":
            return "https://api-m.paypal.com"
        return "https://api-m.sandbox.paypal.com"

    @classmethod
    async def get_access_token(cls) -> str:
        """Obtiene el token de acceso OAuth2 de PayPal."""
        # Validar que las credenciales estén configuradas
        if not settings.PAYPAL_CLIENT_ID or not settings.PAYPAL_CLIENT_SECRET:
            logger.error("PAYPAL_CLIENT_ID o PAYPAL_CLIENT_SECRET no están configurados")
            raise ValueError("Las credenciales de PayPal no están configuradas. Verifica las variables de entorno PAYPAL_CLIENT_ID y PAYPAL_CLIENT_SECRET.")
        
        url = f"{cls._get_base_url()}/v1/oauth2/token"
        auth = (settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET)
        data = {"grant_type": "client_credentials"}
        headers = {"Accept": "application/json", "Accept-Language": "en_US"}
        
        logger.info(f"Intentando obtener token de PayPal desde: {url}")
        logger.info(f"Modo PayPal: {settings.PAYPAL_MODE}")
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, auth=auth, data=data, headers=headers, timeout=10.0)
                if response.status_code != 200:
                    logger.error(f"Error obteniendo token PayPal (status {response.status_code}): {response.text}")
                    raise ValueError(f"Error autenticando con PayPal: {response.text}")
                token_data = response.json()
                access_token = token_data.get("access_token")
                if not access_token:
                    logger.error(f"No se recibió access_token en la respuesta de PayPal: {token_data}")
                    raise ValueError("No se recibió access_token de PayPal")
                logger.info("Token de PayPal obtenido exitosamente")
                return access_token
        except httpx.TimeoutException as e:
            logger.error(f"Timeout al conectar con PayPal: {str(e)}")
            raise ValueError("Timeout al conectar con PayPal. Intenta nuevamente.")
        except httpx.RequestError as e:
            logger.error(f"Error de conexión con PayPal: {str(e)}")
            raise ValueError(f"Error de conexión con PayPal: {str(e)}")

    @classmethod
    async def create_order(
        cls,
        orden_id: int,
        numero_orden: str,
        total: Decimal,
        moneda: str = "USD",
        return_url: Optional[str] = None,
        cancel_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Crea una orden en PayPal v2 y retorna la URL de aprobación y el order_id.
        """
        logger.info(f"Creando orden PayPal para orden_id={orden_id}, total={total}, moneda={moneda}")
        
        try:
            token = await cls.get_access_token()
        except Exception as e:
            logger.error(f"Error obteniendo token de acceso: {str(e)}")
            raise
            
        url = f"{cls._get_base_url()}/v2/checkout/orders"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        
        ret_url = return_url or f"{settings.STRIPE_SUCCESS_URL}?orden_id={orden_id}&gateway=paypal"
        can_url = cancel_url or f"{settings.STRIPE_CANCEL_URL}?orden_id={orden_id}&gateway=paypal"
        
        logger.info(f"URLs de retorno - success: {ret_url}, cancel: {can_url}")
        
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": str(orden_id),
                    "description": f"Orden FashionStore {numero_orden}",
                    "custom_id": str(orden_id),
                    "amount": {
                        "currency_code": moneda,
                        "value": f"{total:.2f}"
                    }
                }
            ],
            "application_context": {
                "return_url": ret_url,
                "cancel_url": can_url,
                "user_action": "PAY_NOW",
                "brand_name": settings.APP_NAME
            }
        }
        
        logger.info(f"Enviando solicitud a PayPal: {url}")
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, headers=headers, timeout=15.0)
                if response.status_code not in (200, 201):
                    logger.error(f"Error creando orden PayPal (status {response.status_code}): {response.text}")
                    raise ValueError(f"Error al crear orden en PayPal: {response.text}")
                
                data = response.json()
                order_id = data.get("id")
                approve_url = ""
                for link in data.get("links", []):
                    if link.get("rel") == "approve":
                        approve_url = link.get("href")
                        break
                
                logger.info(f"Orden PayPal creada exitosamente: order_id={order_id}")
                        
                return {
                    "order_id": order_id,
                    "approve_url": approve_url,
                    "status": data.get("status"),
                    "raw_response": data
                }
        except httpx.TimeoutException as e:
            logger.error(f"Timeout al crear orden en PayPal: {str(e)}")
            raise ValueError("Timeout al crear orden en PayPal. Intenta nuevamente.")
        except httpx.RequestError as e:
            logger.error(f"Error de conexión al crear orden en PayPal: {str(e)}")
            raise ValueError(f"Error de conexión con PayPal: {str(e)}")

    @classmethod
    async def capture_order(cls, order_id: str) -> Dict[str, Any]:
        """Captura los fondos de una orden aprobada por el comprador en PayPal."""
        token = await cls.get_access_token()
        url = f"{cls._get_base_url()}/v2/checkout/orders/{order_id}/capture"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, timeout=15.0)
            if response.status_code not in (200, 201):
                logger.error(f"Error capturando orden PayPal {order_id}: {response.text}")
                raise ValueError(f"Error al capturar pago en PayPal: {response.text}")
            
            return response.json()

    @classmethod
    async def refund_payment(cls, capture_id: str, amount: Optional[Decimal] = None, currency: str = "USD") -> Dict[str, Any]:
        """Reembolsa una captura de pago en PayPal."""
        token = await cls.get_access_token()
        url = f"{cls._get_base_url()}/v2/payments/captures/{capture_id}/refund"
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        
        payload = {}
        if amount:
            payload["amount"] = {
                "value": f"{amount:.2f}",
                "currency_code": currency
            }
            
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers, timeout=15.0)
            if response.status_code not in (200, 201):
                logger.error(f"Error reembolsando PayPal capture {capture_id}: {response.text}")
                raise ValueError(f"Error al reembolsar pago en PayPal: {response.text}")
            
            return response.json()

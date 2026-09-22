"""
Servicio de Integración con Firebase Cloud Messaging (FCM).
Permite el registro de tokens de dispositivos y el envío de notificaciones push
a clientes y personal de sucursal.
"""
import os
import json
import logging
from typing import List, Optional, Dict, Any
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

import firebase_admin
from firebase_admin import credentials, messaging
from app.config import settings
from app.apps.gestion_usuarios.models import DispositivoFCM, Usuario, EncargadoSucursal, Cajero

logger = logging.getLogger("fashionstore.firebase")

_firebase_app_initialized = False


def _get_firebase_app():
    """Inicializa y retorna la app de Firebase Admin SDK como singleton."""
    global _firebase_app_initialized
    if _firebase_app_initialized:
        return firebase_admin.get_app()

    try:
        cred = None
        # 1. Intentar desde variable JSON en string
        if settings.FIREBASE_CREDENTIALS_JSON:
            try:
                cred_dict = json.loads(settings.FIREBASE_CREDENTIALS_JSON)
                cred = credentials.Certificate(cred_dict)
                logger.info("Firebase inicializado desde FIREBASE_CREDENTIALS_JSON")
            except Exception as e:
                logger.warning(f"No se pudo parsear FIREBASE_CREDENTIALS_JSON: {e}")

        # 2. Intentar desde archivo físico si existe
        if not cred and settings.FIREBASE_CREDENTIALS_PATH:
            path = settings.FIREBASE_CREDENTIALS_PATH
            # Resolver ruta relativa
            if not os.path.isabs(path):
                base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
                alt_path = os.path.join(base_dir, path)
                if os.path.exists(alt_path):
                    path = alt_path

            if os.path.exists(path):
                cred = credentials.Certificate(path)
                logger.info(f"Firebase inicializado desde archivo: {path}")

        if cred:
            app = firebase_admin.initialize_app(cred)
            _firebase_app_initialized = True
            return app
        else:
            logger.warning("No se encontraron credenciales de Firebase válidas. FCM estará en modo silencioso.")
            return None

    except Exception as e:
        logger.error(f"Error al inicializar Firebase Admin SDK: {e}")
        return None


class FirebaseService:
    """Servicio de gestión y despacho de Notificaciones Push vía FCM."""

    @classmethod
    async def registrar_dispositivo(
        cls,
        db: AsyncSession,
        usuario_id: int,
        token: str,
        tipo_dispositivo: str = "web"
    ) -> DispositivoFCM:
        """Registra o actualiza un token FCM para un usuario."""
        if not token:
            raise ValueError("El token FCM no puede estar vacío")

        res = await db.execute(
            select(DispositivoFCM).where(DispositivoFCM.token == token)
        )
        disp = res.scalar_one_or_none()

        if disp:
            disp.usuario_id = usuario_id
            disp.tipo_dispositivo = tipo_dispositivo
            disp.activo = True
        else:
            disp = DispositivoFCM(
                usuario_id=usuario_id,
                token=token,
                tipo_dispositivo=tipo_dispositivo,
                activo=True
            )
            db.add(disp)

        await db.commit()
        await db.refresh(disp)
        logger.info(f"Dispositivo FCM registrado para usuario ID={usuario_id} (tipo={tipo_dispositivo})")
        return disp

    @classmethod
    async def eliminar_dispositivo(
        cls,
        db: AsyncSession,
        token: str
    ) -> bool:
        """Desactiva o elimina un token FCM."""
        res = await db.execute(
            select(DispositivoFCM).where(DispositivoFCM.token == token)
        )
        disp = res.scalar_one_or_none()
        if disp:
            disp.activo = False
            await db.commit()
            return True
        return False

    @classmethod
    async def enviar_multicast(
        cls,
        db: AsyncSession,
        tokens: List[str],
        titulo: str,
        cuerpo: str,
        data: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Envía un mensaje FCM a una lista de tokens de dispositivo."""
        if not tokens:
            return {"exito": 0, "fallo": 0, "mensajes": "Sin tokens"}

        app = _get_firebase_app()
        if not app:
            logger.warning(f"[FCM Mock/Silencioso] Notificación: '{titulo}' - '{cuerpo}' a {len(tokens)} dispositivos")
            return {"exito": len(tokens), "fallo": 0, "modo": "simulado"}

        payload_data = data or {}
        # Asegurarse de que todos los valores de data sean strings
        clean_data = {str(k): str(v) for k, v in payload_data.items()}

        message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title=titulo,
                body=cuerpo,
            ),
            data=clean_data,
            tokens=tokens,
            webpush=messaging.WebpushConfig(
                notification=messaging.WebpushNotification(
                    title=titulo,
                    body=cuerpo,
                    icon="/assets/icons/icon-192x192.png",
                    badge="/favicon.ico"
                ),
                fcm_options=messaging.WebpushFCMOptions(
                    link=clean_data.get("url", "/profile/reservations")
                )
            )
        )

        try:
            response = messaging.send_each_for_multicast(message)
            logger.info(
                f"FCM Multicast enviado: {response.success_count} éxitos, {response.failure_count} fallos de {len(tokens)} tokens"
            )

            # Limpiar tokens inválidos si los hubo
            if response.failure_count > 0:
                tokens_a_desactivar = []
                for idx, resp in enumerate(response.responses):
                    if not resp.success:
                        err_code = getattr(resp.exception, "code", None) or str(resp.exception)
                        if "registration-token-not-registered" in str(err_code).lower() or "invalid" in str(err_code).lower():
                            tokens_a_desactivar.append(tokens[idx])

                if tokens_a_desactivar:
                    for t in tokens_a_desactivar:
                        res = await db.execute(select(DispositivoFCM).where(DispositivoFCM.token == t))
                        d = res.scalar_one_or_none()
                        if d:
                            d.activo = False
                    await db.commit()

            return {
                "exito": response.success_count,
                "fallo": response.failure_count,
            }
        except Exception as e:
            logger.error(f"Error al despachar FCM Multicast: {e}")
            return {"exito": 0, "fallo": len(tokens), "error": str(e)}

    @classmethod
    async def enviar_notificacion_usuario(
        cls,
        db: AsyncSession,
        usuario_id: int,
        titulo: str,
        cuerpo: str,
        data: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Envía notificación push a todos los dispositivos registrados y activos de un usuario."""
        res = await db.execute(
            select(DispositivoFCM.token).where(
                and_(
                    DispositivoFCM.usuario_id == usuario_id,
                    DispositivoFCM.activo == True
                )
            )
        )
        tokens = list(res.scalars().all())
        if not tokens:
            logger.info(f"Usuario {usuario_id} no tiene dispositivos FCM activos registrados.")
            return {"exito": 0, "fallo": 0, "detalle": "Sin tokens activos"}

        return await cls.enviar_multicast(db, tokens, titulo, cuerpo, data)

    @classmethod
    async def enviar_notificacion_sucursal(
        cls,
        db: AsyncSession,
        sucursal_id: int,
        titulo: str,
        cuerpo: str,
        data: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Notifica al personal asignado a la sucursal (Encargados y Cajeros)."""
        # 1. Obtener IDs de usuarios encargados de la sucursal
        res_enc = await db.execute(
            select(EncargadoSucursal.usuario_id).where(EncargadoSucursal.sucursal_id == sucursal_id)
        )
        usuario_ids = set(res_enc.scalars().all())

        # 2. Obtener IDs de usuarios cajeros de la sucursal
        res_caj = await db.execute(
            select(Cajero.usuario_id).where(Cajero.sucursal_id == sucursal_id)
        )
        for uid in res_caj.scalars().all():
            usuario_ids.add(uid)

        if not usuario_ids:
            logger.info(f"No hay personal asignado a la sucursal ID={sucursal_id} para notificar.")
            return {"exito": 0, "fallo": 0, "detalle": "Sin personal en sucursal"}

        # 3. Obtener tokens de todos estos usuarios
        res_tokens = await db.execute(
            select(DispositivoFCM.token).where(
                and_(
                    DispositivoFCM.usuario_id.in_(usuario_ids),
                    DispositivoFCM.activo == True
                )
            )
        )
        tokens = list(res_tokens.scalars().all())
        if not tokens:
            logger.info(f"Personal de sucursal {sucursal_id} no tiene dispositivos FCM registrados.")
            return {"exito": 0, "fallo": 0, "detalle": "Sin tokens de personal"}

        return await cls.enviar_multicast(db, tokens, titulo, cuerpo, data)

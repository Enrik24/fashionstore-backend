"""
[EXAMEN-2] IdempotenciaService — control del ciclo de vida del UUID (CU06-CU08).

Reglas:
* RN-005: un `uuid_operacion` repetido devuelve el resultado previo.
* RN-015: `uuid_pago` es la clave idempotente del cobro.
* La tabla `claves_idempotencia` protege los POST que crean recursos y los
  webhooks que no son de Stripe.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import ValidationException
from app.apps.operaciones_offline.models import (
    ClaveIdempotencia,
    EstadoSincronizacion,
    OperacionOffline,
)


class IdempotenciaService:

    @staticmethod
    def validar_uuid(valor: Any) -> UUID:
        try:
            return UUID(str(valor))
        except (TypeError, ValueError, AttributeError):
            raise ValidationException("El UUID enviado no es válido")

    @staticmethod
    def validar_antiguedad(fecha_local: Optional[datetime]) -> None:
        if fecha_local is None:
            return
        ahora = datetime.utcnow()
        fecha = fecha_local
        if getattr(fecha, "tzinfo", None) is not None:
            fecha = fecha.replace(tzinfo=None)
        if fecha > ahora + timedelta(minutes=5):
            raise ValidationException("La operación tiene una fecha futura")
        if fecha < ahora - timedelta(days=settings.OFFLINE_MAX_DIAS_PENDIENTE):
            raise ValidationException(
                f"La operación supera la antigüedad máxima de {settings.OFFLINE_MAX_DIAS_PENDIENTE} días"
            )

    @staticmethod
    async def buscar_operacion(
        db: AsyncSession, uuid_operacion, incluir_otros_tenants: bool = True
    ) -> Optional[OperacionOffline]:
        uuid_valor = IdempotenciaService.validar_uuid(uuid_operacion)
        query = select(OperacionOffline).where(
            OperacionOffline.uuid_operacion == uuid_valor
        )
        if incluir_otros_tenants:
            # El UUID es único global (RN-005); el filtro de tenant se omite
            # deliberadamente para detectar un UUID reutilizado por otro tenant.
            query = query.execution_options(ignorar_tenant=True)
        result = await db.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def registrar_operacion(
        db: AsyncSession,
        uuid_operacion,
        tenant_id: int,
        sucursal_id: Optional[int],
        tipo_operacion: str,
        payload: dict,
        fecha_local: Optional[datetime],
        usuario_id: int,
        dispositivo_id,
        secuencia_local: int = 0,
    ) -> OperacionOffline:
        operacion = OperacionOffline(
            tenant_id=tenant_id,
            dispositivo_id=IdempotenciaService.validar_uuid(dispositivo_id),
            usuario_id=usuario_id,
            uuid_operacion=IdempotenciaService.validar_uuid(uuid_operacion),
            secuencia_local=int(secuencia_local or 0),
            tipo_operacion=tipo_operacion,
            payload_json=payload or {},
            estado_sincronizacion=EstadoSincronizacion.PENDIENTE,
            fecha_registro_local=fecha_local or datetime.utcnow(),
            fecha_recepcion_servidor=datetime.utcnow(),
            sucursal_id=sucursal_id,
        )
        db.add(operacion)
        await db.flush()
        return operacion

    @staticmethod
    async def marcar_en_proceso(db: AsyncSession, operacion: OperacionOffline) -> OperacionOffline:
        operacion.estado_sincronizacion = EstadoSincronizacion.ENVIADA
        operacion.fecha_ultimo_intento = datetime.utcnow()
        await db.flush()
        return operacion

    @staticmethod
    async def marcar_confirmada(
        db: AsyncSession, operacion: OperacionOffline, resultado: dict
    ) -> OperacionOffline:
        operacion.estado_sincronizacion = EstadoSincronizacion.CONFIRMADA
        operacion.resultado_json = resultado or {}
        operacion.requiere_revision = False
        operacion.error_detalle = None
        operacion.motivo_rechazo = None
        await db.flush()
        return operacion

    @staticmethod
    async def marcar_duplicada(
        db: AsyncSession, operacion: OperacionOffline
    ) -> OperacionOffline:
        # No se cambia el estado original: el resultado previo sigue siendo la
        # evidencia. El estado DUPLICADA se usa solo en la respuesta.
        await db.flush()
        return operacion

    @staticmethod
    async def marcar_rechazada(
        db: AsyncSession, operacion: OperacionOffline, motivo: str
    ) -> OperacionOffline:
        operacion.estado_sincronizacion = EstadoSincronizacion.RECHAZADA
        operacion.motivo_rechazo = motivo
        operacion.resultado_json = {
            "estado": EstadoSincronizacion.RECHAZADA.value,
            "motivo": motivo,
        }
        await db.flush()
        return operacion

    @staticmethod
    async def marcar_conflicto_manual(
        db: AsyncSession,
        operacion: OperacionOffline,
        conflicto_id: Optional[int],
        motivo: Optional[str] = None,
    ) -> OperacionOffline:
        operacion.estado_sincronizacion = EstadoSincronizacion.CONFLICTO
        operacion.requiere_revision = True
        operacion.motivo_rechazo = motivo or "Conflicto pendiente de revisión"
        operacion.resultado_json = {
            "estado": EstadoSincronizacion.CONFLICTO.value,
            "motivo": operacion.motivo_rechazo,
            "conflicto_id": conflicto_id,
        }
        await db.flush()
        return operacion

    @staticmethod
    async def marcar_error_critico(
        db: AsyncSession, operacion: OperacionOffline, motivo: str
    ) -> OperacionOffline:
        operacion.estado_sincronizacion = EstadoSincronizacion.ERROR_CRITICO
        operacion.error_detalle = motivo
        operacion.resultado_json = {
            "estado": EstadoSincronizacion.ERROR_CRITICO.value,
            "motivo": motivo,
            "reintentar": False,
        }
        await db.flush()
        return operacion

    @staticmethod
    async def obtener_resultado(
        db: AsyncSession, uuid_operacion
    ) -> Optional[dict]:
        operacion = await IdempotenciaService.buscar_operacion(db, uuid_operacion)
        return operacion.resultado_json if operacion else None

    # ------------------------------------------------------------------
    # ClaveIdempotencia (POST y webhooks no Stripe)
    # ------------------------------------------------------------------
    @staticmethod
    def calcular_request_hash(payload: Any) -> str:
        normalizado = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(normalizado.encode("utf-8")).hexdigest()

    @staticmethod
    async def obtener_clave(
        db: AsyncSession, tenant_id: int, clave, endpoint: str
    ) -> Optional[ClaveIdempotencia]:
        uuid_clave = IdempotenciaService.validar_uuid(clave)
        result = await db.execute(
            select(ClaveIdempotencia).where(
                ClaveIdempotencia.tenant_id == tenant_id,
                ClaveIdempotencia.clave == uuid_clave,
                ClaveIdempotencia.endpoint == endpoint,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def registrar_clave(
        db: AsyncSession,
        tenant_id: int,
        clave,
        endpoint: str,
        request_hash: str,
        estado: str = "EN_PROCESO",
    ) -> ClaveIdempotencia:
        registro = ClaveIdempotencia(
            tenant_id=tenant_id,
            clave=IdempotenciaService.validar_uuid(clave),
            endpoint=endpoint,
            request_hash=request_hash,
            estado=estado,
            fecha_expiracion=datetime.utcnow() + timedelta(hours=settings.IDEMPOTENCIA_TTL_HORAS),
        )
        db.add(registro)
        await db.flush()
        return registro

    @staticmethod
    async def completar_clave(
        db: AsyncSession, registro: ClaveIdempotencia, respuesta: Optional[dict] = None
    ) -> ClaveIdempotencia:
        registro.estado = "COMPLETADA"
        registro.respuesta_json = respuesta
        await db.flush()
        return registro

    @staticmethod
    async def marcar_error_clave(
        db: AsyncSession, registro: ClaveIdempotencia, error: str
    ) -> ClaveIdempotencia:
        registro.estado = "ERROR"
        registro.respuesta_json = {"error": error}
        await db.flush()
        return registro

    @staticmethod
    async def limpiar_expiradas(db: AsyncSession) -> int:
        from sqlalchemy import delete

        result = await db.execute(
            delete(ClaveIdempotencia).where(
                ClaveIdempotencia.fecha_expiracion < datetime.utcnow()
            )
        )
        await db.commit()
        return result.rowcount or 0

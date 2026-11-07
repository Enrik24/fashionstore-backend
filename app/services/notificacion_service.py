"""[EXAMEN-2] Fase D — notificaciones al cliente (RF-037 / RN-024).

Cada notificación se **persiste antes de enviarse**: si el envío falla, la fila
queda `FALLIDA` (o `PENDIENTE` cuando el canal no tiene adaptador) y puede
reintentarse. Esto garantiza que el aviso nunca se pierda cuando el cliente no
está conectado.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundException, ValidationException
from app.apps.gestion_usuarios.models import Cliente
from app.apps.gestion_ventas.models import (
    CanalNotificacion,
    EstadoNotificacion,
    NotificacionCliente,
)

logger = logging.getLogger(__name__)


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


class NotificacionService:
    """Registro y envío de notificaciones al cliente."""

    @staticmethod
    async def _resolver_canales(
        db: AsyncSession, tenant_id: int, cliente_id: int | None, canales: Optional[Iterable]
    ) -> list[CanalNotificacion]:
        if canales:
            return [
                c if isinstance(c, CanalNotificacion) else CanalNotificacion(str(c).upper())
                for c in canales
            ]
        cliente = None
        if cliente_id is not None:
            cliente = await db.get(Cliente, cliente_id)
        elegidos: list[CanalNotificacion] = []
        if cliente is not None and getattr(cliente, "correo", None):
            elegidos.append(CanalNotificacion.CORREO)
        if cliente is not None and getattr(cliente, "telefono", None):
            elegidos.append(CanalNotificacion.SMS)
        if not elegidos:
            # Sin datos de contacto: el aviso queda para la sucursal (RN-024).
            elegidos.append(CanalNotificacion.PUSH)
        return elegidos

    @staticmethod
    async def crear(
        db: AsyncSession,
        tenant_id: int,
        cliente_id: int | None,
        usuario_id: int | None,
        tipo: str,
        asunto: str | None,
        mensaje: str,
        canales: Optional[Iterable] = None,
        referencia: tuple[str, int] | None = None,
    ) -> list[NotificacionCliente]:
        canales = await NotificacionService._resolver_canales(
            db, tenant_id, cliente_id, canales
        )
        referencia_tipo, referencia_id = (referencia or (None, None))
        filas: list[NotificacionCliente] = []

        for canal in canales:
            existente = None
            if referencia_id is not None:
                existente = (await db.execute(
                    select(NotificacionCliente).where(
                        NotificacionCliente.tenant_id == tenant_id,
                        NotificacionCliente.tipo == tipo,
                        NotificacionCliente.referencia_tipo == referencia_tipo,
                        NotificacionCliente.referencia_id == referencia_id,
                        NotificacionCliente.canal == canal,
                    )
                )).scalars().first()
            if existente is not None:
                filas.append(existente)
                continue

            fila = NotificacionCliente(
                tenant_id=tenant_id,
                cliente_id=cliente_id,
                usuario_id=usuario_id,
                tipo=tipo,
                canal=canal,
                asunto=asunto,
                mensaje=mensaje,
                estado=EstadoNotificacion.PENDIENTE,
                referencia_tipo=referencia_tipo,
                referencia_id=referencia_id,
            )
            db.add(fila)
            filas.append(fila)

        await db.flush()
        for fila in filas:
            if fila.estado == EstadoNotificacion.PENDIENTE:
                await NotificacionService._enviar(db, fila)
        await db.flush()
        return filas

    @staticmethod
    async def _enviar(db: AsyncSession, fila: NotificacionCliente) -> None:
        if fila.canal == CanalNotificacion.PUSH and fila.usuario_id:
            try:
                from app.services.firebase_service import FirebaseService

                await FirebaseService.enviar_notificacion_usuario(
                    db=db,
                    usuario_id=fila.usuario_id,
                    titulo=fila.asunto or "FashionStore",
                    cuerpo=fila.mensaje,
                    data={
                        "tipo": fila.tipo,
                        "referencia_tipo": fila.referencia_tipo or "",
                        "referencia_id": str(fila.referencia_id or ""),
                    },
                )
                fila.estado = EstadoNotificacion.ENVIADA
                fila.fecha_envio = _ahora()
                fila.error_detalle = None
                return
            except Exception as exc:  # pragma: no cover - depende de Firebase
                logger.warning("Push no enviado (notificación %s): %s", fila.id, exc)
                fila.estado = EstadoNotificacion.FALLIDA
                fila.error_detalle = str(exc)[:500]
                return

        # Correo/SMS no tienen adaptador configurado en el examen: se deja
        # pendiente para que la sucursal lo gestione (RN-024).
        fila.estado = EstadoNotificacion.PENDIENTE
        fila.error_detalle = (
            "Canal sin adaptador configurado; la sucursal puede contactar al cliente"
        )

    @staticmethod
    async def reintentar(db: AsyncSession, notificacion_id: int) -> NotificacionCliente:
        fila = await db.get(NotificacionCliente, notificacion_id)
        if fila is None:
            raise NotFoundException("Notificación no encontrada")
        if fila.estado == EstadoNotificacion.LEIDA:
            raise ValidationException("Una notificación leída no se reenvía")
        fila.estado = EstadoNotificacion.PENDIENTE
        await db.flush()
        await NotificacionService._enviar(db, fila)
        await db.commit()
        await db.refresh(fila)
        return fila

    @staticmethod
    async def listar(
        db: AsyncSession,
        tenant_id: int,
        cliente_id: int | None = None,
        estado: str | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[NotificacionCliente]:
        query = select(NotificacionCliente).where(
            NotificacionCliente.tenant_id == tenant_id
        )
        if cliente_id is not None:
            query = query.where(NotificacionCliente.cliente_id == cliente_id)
        if estado:
            query = query.where(
                NotificacionCliente.estado == EstadoNotificacion(estado.upper())
            )
        query = (
            query.order_by(NotificacionCliente.fecha_creacion.desc())
            .offset(max(0, skip))
            .limit(max(1, min(limit, 200)))
        )
        return list((await db.execute(query)).scalars().all())

    @staticmethod
    async def marcar_leida(
        db: AsyncSession, tenant_id: int, notificacion_id: int
    ) -> NotificacionCliente:
        fila = (await db.execute(
            select(NotificacionCliente).where(
                NotificacionCliente.id == notificacion_id,
                NotificacionCliente.tenant_id == tenant_id,
            )
        )).scalar_one_or_none()
        if fila is None:
            raise NotFoundException("Notificación no encontrada")
        fila.leida = True
        fila.estado = EstadoNotificacion.LEIDA
        await db.commit()
        await db.refresh(fila)
        return fila

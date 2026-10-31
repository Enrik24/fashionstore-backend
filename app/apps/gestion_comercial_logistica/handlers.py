"""[EXAMEN-2] Manejador offline `DEVOLUCION_CAMBIO` (CU13).

El motor de sincronización del Paquete 2 nunca importa este paquete; es este
módulo el que se registra en el registro de manejadores (plan 4.7 y 9.3.9).
El manejador solo hace `flush`: el motor envuelve la operación en un savepoint
y confirma al final.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.apps.gestion_comercial_logistica.cambio_service import CambioDevolucionService
from app.apps.operaciones_offline.models import TipoOperacionOffline
from app.apps.operaciones_offline.registro_manejadores import registrar_manejador


async def manejar_devolucion_cambio(
    db: AsyncSession, operacion, contexto: dict | None = None
) -> dict:
    """Procesa una devolución/cambio creada sin conexión."""
    return await CambioDevolucionService.procesar_cambio_offline(
        db, operacion, contexto
    )


def registrar_handlers() -> None:
    """Registra el tipo propio del Paquete 4 (plan 4.7)."""
    registrar_manejador(
        TipoOperacionOffline.DEVOLUCION_CAMBIO.value,
        manejar_devolucion_cambio,
    )

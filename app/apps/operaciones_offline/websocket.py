"""WebSocket helpers del Paquete 2 (canal de sincronización, CU08)."""
from fastapi import WebSocket

from app.services.websocket_manager import manager


def canal_sync(tenant_id: int, sucursal_id: int | None = None) -> str:
    if sucursal_id is None:
        return f"sync:{tenant_id}"
    return f"sync:{tenant_id}:{sucursal_id}"


async def conectar_sync(websocket: WebSocket, tenant_id: int, sucursal_id: int | None = None) -> str:
    canal = canal_sync(tenant_id, sucursal_id)
    await manager.conectar_canal(websocket, canal)
    return canal


async def publicar_sync(tenant_id: int, sucursal_id: int | None, evento: dict) -> None:
    canal = canal_sync(tenant_id, sucursal_id)
    await manager.difundir_canal(canal, evento)
    # Un cliente suscrito solo al tenant también recibe el evento.
    if sucursal_id is not None:
        await manager.difundir_canal(canal_sync(tenant_id), evento)

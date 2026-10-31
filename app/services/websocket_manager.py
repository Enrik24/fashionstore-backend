"""
Gestor de conexiones WebSocket para actualizaciones en tiempo real.

Conserva el canal por producto del Examen 1 y agrega canales genéricos usados
por la sincronización offline y el pago diferido (plan 9.2.1).
"""
from typing import Dict, List

from fastapi import WebSocket
import logging

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        # canal (str) -> lista de WebSockets conectados
        self.active_connections: Dict[str, List[WebSocket]] = {}

    # --------------------------------------------------------
    # Canales genéricos (Examen 2)
    # --------------------------------------------------------
    async def conectar_canal(self, websocket: WebSocket, canal: str) -> None:
        await websocket.accept()
        self.active_connections.setdefault(str(canal), []).append(websocket)
        logger.info(
            "WebSocket conectado al canal %s. Total: %s",
            canal,
            len(self.active_connections[str(canal)]),
        )

    def desconectar_canal(self, websocket: WebSocket, canal: str) -> None:
        clave = str(canal)
        conexiones = self.active_connections.get(clave)
        if conexiones and websocket in conexiones:
            conexiones.remove(websocket)
        if conexiones is not None and not conexiones:
            self.active_connections.pop(clave, None)
        logger.info("WebSocket desconectado del canal %s", canal)

    async def difundir_canal(self, canal: str, mensaje: dict) -> None:
        conexiones = list(self.active_connections.get(str(canal), []))
        desconectados: List[WebSocket] = []
        for conexion in conexiones:
            try:
                await conexion.send_json(mensaje)
            except Exception as exc:  # pragma: no cover - depende del socket
                logger.warning("Error enviando mensaje WS al canal %s: %s", canal, exc)
                desconectados.append(conexion)
        for conexion in desconectados:
            self.desconectar_canal(conexion, canal)

    # --------------------------------------------------------
    # Compatibilidad con el canal por producto del Examen 1
    # --------------------------------------------------------
    @staticmethod
    def _canal_producto(producto_id: int) -> str:
        return f"producto:{producto_id}"

    async def connect(self, websocket: WebSocket, producto_id: int):
        await self.conectar_canal(websocket, self._canal_producto(producto_id))

    def disconnect(self, websocket: WebSocket, producto_id: int):
        self.desconectar_canal(websocket, self._canal_producto(producto_id))

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        await websocket.send_json(message)

    async def broadcast_to_product(self, producto_id: int, message: dict):
        await self.difundir_canal(self._canal_producto(producto_id), message)


manager = ConnectionManager()

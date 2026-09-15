"""
Gestor de conexiones WebSocket para actualizaciones en tiempo real.
Permite conectar clientes por producto_id y emitir cambios de disponibilidad.
"""
from typing import Dict, List
from fastapi import WebSocket
import logging

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        # Mapea producto_id -> lista de WebSockets conectados
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, producto_id: int):
        await websocket.accept()
        if producto_id not in self.active_connections:
            self.active_connections[producto_id] = []
        self.active_connections[producto_id].append(websocket)
        logger.info(f"WebSocket cliente conectado al producto #{producto_id}. Total: {len(self.active_connections[producto_id])}")

    def disconnect(self, websocket: WebSocket, producto_id: int):
        if producto_id in self.active_connections:
            if websocket in self.active_connections[producto_id]:
                self.active_connections[producto_id].remove(websocket)
            if not self.active_connections[producto_id]:
                del self.active_connections[producto_id]
        logger.info(f"WebSocket cliente desconectado del producto #{producto_id}")

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        await websocket.send_json(message)

    async def broadcast_to_product(self, producto_id: int, message: dict):
        if producto_id in self.active_connections:
            disconnected = []
            for connection in self.active_connections[producto_id]:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    logger.warning(f"Error enviando mensaje WS al cliente: {e}")
                    disconnected.append(connection)
            for dead_conn in disconnected:
                self.disconnect(dead_conn, producto_id)


manager = ConnectionManager()

"""
[EXAMEN-2] Router del Paquete 5 — carrito offline y pago diferido (CU15/CU16).

Las rutas de carrito y pago aceptan token online u offline mediante
`autenticacion_flexible`; el webhook de Stripe no lleva JWT y se autentica con
su firma HMAC (RN-015).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import CarritoNoHabilitado, PagoDiferidoNoDisponible
from app.security import decode_token
from app.services.websocket_manager import manager
from app.tenant import autenticacion_flexible, get_suscripcion_activa
from app.apps.operaciones_offline.models import ConfiguracionOffline
from app.apps.pagos_diferidos.carrito_service import CarritoOfflineService
from app.apps.pagos_diferidos.models import EventoStripe, PagoDiferido
from app.apps.pagos_diferidos.pago_service import PagoDiferidoService
from app.apps.pagos_diferidos.reembolso_service import ReembolsoService
from app.apps.pagos_diferidos.schemas import (
    CarritoOfflineItemsRequest,
    CarritoOfflineRequest,
    ConvertirCarritoRequest,
    PagoDiferidoRequest,
    ReembolsoRequest,
)
from app.apps.pagos_diferidos.webhook_service import WebhookService

router = APIRouter(tags=["Carrito y pago diferido (CU15/CU16)"])
ws_router = APIRouter(tags=["Tiempo real — pagos"])

canal_pagos = lambda tenant_id: f"pagos:{tenant_id}"  # noqa: E731


# ============================================================
# Utilidades de autorización y contexto
# ============================================================

def _tenant_id(contexto: dict) -> int:
    return int(contexto["tenant"].id)


def _dispositivo_id(contexto: dict):
    dispositivo = contexto.get("dispositivo")
    return getattr(dispositivo, "id", None)


async def _verificar_carrito_habilitado(db: AsyncSession, tenant_id: int) -> None:
    result = await db.execute(
        select(ConfiguracionOffline).where(ConfiguracionOffline.tenant_id == tenant_id)
    )
    config = result.scalar_one_or_none()
    if config is not None and not config.habilitar_carrito_offline:
        raise CarritoNoHabilitado()


async def _verificar_pago_diferido(db: AsyncSession, tenant_id: int) -> None:
    suscripcion = await get_suscripcion_activa(db, tenant_id)
    if suscripcion is None or not getattr(suscripcion.plan, "permite_pago_diferido", False):
        raise PagoDiferidoNoDisponible()


# ============================================================
# Carrito offline (CU15)
# ============================================================

@router.post("/api/v1/carrito/offline", status_code=201)
async def registrar_carrito_offline(
    datos: CarritoOfflineRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    await _verificar_carrito_habilitado(db, tenant_id)
    await _verificar_pago_diferido(db, tenant_id)
    return await CarritoOfflineService.registrar_carrito(
        db, tenant_id, datos, dispositivo_id=_dispositivo_id(contexto)
    )


@router.get("/api/v1/carrito/offline/pendientes")
async def listar_carritos_pendientes(
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    return await CarritoOfflineService.listar_pendientes(
        db, tenant_id, dispositivo_id=_dispositivo_id(contexto)
    )


@router.get("/api/v1/carrito/offline/{uuid_carrito}")
async def obtener_carrito_offline(
    uuid_carrito: str,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    carrito = await CarritoOfflineService.obtener_carrito(db, uuid_carrito, tenant_id)
    return await CarritoOfflineService.serializar(db, carrito, tenant_id)


@router.put("/api/v1/carrito/offline/{uuid_carrito}/items")
async def actualizar_items_carrito(
    uuid_carrito: str,
    datos: CarritoOfflineItemsRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    await _verificar_carrito_habilitado(db, tenant_id)
    return await CarritoOfflineService.actualizar_items(
        db,
        uuid_carrito,
        tenant_id,
        datos.items,
        dispositivo_id=_dispositivo_id(contexto),
    )


@router.delete("/api/v1/carrito/offline/{uuid_carrito}/items/{item_id}")
async def quitar_item_carrito(
    uuid_carrito: str,
    item_id: int,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    return await CarritoOfflineService.quitar_item(
        db, uuid_carrito, tenant_id, item_id
    )


@router.post("/api/v1/carrito/offline/{uuid_carrito}/validar")
async def validar_carrito_offline(
    uuid_carrito: str,
    sucursal_id: Optional[int] = Query(None),
    cupon_codigo: Optional[str] = Query(None),
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    return await CarritoOfflineService.validar_carrito(
        db, uuid_carrito, tenant_id, sucursal_id=sucursal_id, cupon_codigo=cupon_codigo
    )


@router.post("/api/v1/carrito/offline/{uuid_carrito}/convertir", status_code=201)
async def convertir_carrito_offline(
    uuid_carrito: str,
    datos: ConvertirCarritoRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    await _verificar_carrito_habilitado(db, tenant_id)
    await _verificar_pago_diferido(db, tenant_id)
    return await CarritoOfflineService.convertir_carrito(
        db,
        uuid_carrito,
        tenant_id,
        acepta_nuevo_total=datos.acepta_nuevo_total,
        direccion_envio=datos.direccion_envio,
        cliente_id=datos.cliente_id,
        sucursal_id=datos.sucursal_id,
    )


# ============================================================
# Pago diferido (CU16)
# ============================================================

@router.post("/api/v1/pagos/diferidos", status_code=201)
async def registrar_pago_diferido(
    datos: PagoDiferidoRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    await _verificar_pago_diferido(db, tenant_id)
    return await PagoDiferidoService.registrar_pago(
        db, tenant_id, contexto["usuario"].id, datos
    )


@router.get("/api/v1/pagos/diferidos")
async def listar_pagos_diferidos(
    estado: Optional[str] = Query(None),
    desde: Optional[datetime] = Query(None),
    hasta: Optional[datetime] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    pagos = await PagoDiferidoService.listar_pagos(
        db, tenant_id, estado=estado, desde=desde, hasta=hasta, skip=skip, limit=limit
    )
    return [await PagoDiferidoService.serializar(db, p) for p in pagos]


@router.get("/api/v1/pagos/pendientes/count")
async def contar_pagos_pendientes(
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    pendientes = await PagoDiferidoService.contar_pendientes(db, tenant_id)
    return {"tenant_id": tenant_id, "pendientes": pendientes}


@router.get("/api/v1/pagos/diferidos/{uuid_pago}")
async def obtener_pago_diferido(
    uuid_pago: str,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    pago = await PagoDiferidoService.obtener_pago(db, uuid_pago, tenant_id)
    return await PagoDiferidoService.serializar(db, pago)


@router.post("/api/v1/pagos/diferidos/{uuid_pago}/confirmar")
async def confirmar_pago_diferido(
    uuid_pago: str,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    return await PagoDiferidoService.confirmar_pago(db, uuid_pago, tenant_id)


@router.post("/api/v1/pagos/diferidos/{uuid_pago}/reintentar")
async def reintentar_pago_diferido(
    uuid_pago: str,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    return await PagoDiferidoService.reintentar_pago(db, uuid_pago, tenant_id)


@router.post("/api/v1/pagos/diferidos/{uuid_pago}/cancelar")
async def cancelar_pago_diferido(
    uuid_pago: str,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    return await PagoDiferidoService.cancelar_pago(db, uuid_pago, tenant_id)


# ============================================================
# Webhook y auditoría
# ============================================================

@router.post("/api/v1/pagos/webhooks/stripe")
async def webhook_stripe(
    request: Request,
    stripe_signature: Optional[str] = Header(default=None, alias="stripe-signature"),
    db: AsyncSession = Depends(get_db),
):
    """Webhook firmado de pago diferido: sin JWT, idempotente por evento."""
    payload = await request.body()
    return await WebhookService.procesar(db, payload, stripe_signature)


async def _obtener_pago_por_id(
    db: AsyncSession, pago_id: int, tenant_id: int
) -> PagoDiferido:
    result = await db.execute(
        select(PagoDiferido).where(
            PagoDiferido.id == pago_id,
            PagoDiferido.tenant_id == tenant_id,
        )
    )
    pago = result.scalar_one_or_none()
    if pago is None:
        from app.exceptions import PagoNoEncontrado

        raise PagoNoEncontrado()
    return pago


@router.post("/api/v1/pagos/{pago_id}/reembolsar")
async def reembolsar_pago(
    pago_id: int,
    datos: ReembolsoRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    pago = await _obtener_pago_por_id(db, pago_id, tenant_id)
    resultado = await ReembolsoService.reembolsar(
        db,
        pago,
        monto=datos.monto,
        motivo=datos.motivo,
        usuario_id=contexto["usuario"].id,
    )
    await db.commit()
    return resultado


@router.get("/api/v1/pagos/{pago_id}/eventos")
async def listar_eventos_pago(
    pago_id: int,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(contexto)
    pago = await _obtener_pago_por_id(db, pago_id, tenant_id)
    result = await db.execute(
        select(EventoStripe)
        .where(
            EventoStripe.pago_diferido_id == pago.id,
            EventoStripe.tenant_id == tenant_id,
        )
        .order_by(EventoStripe.fecha_recepcion.asc())
    )
    return [
        {
            "id": evento.id,
            "stripe_event_id": evento.stripe_event_id,
            "tipo": evento.tipo,
            "procesado": evento.procesado,
            "fecha_recepcion": evento.fecha_recepcion,
            "fecha_procesamiento": evento.fecha_procesamiento,
            "pago_diferido_id": evento.pago_diferido_id,
            "error_detalle": evento.error_detalle,
        }
        for evento in result.scalars().all()
    ]


# ============================================================
# WebSocket de pagos
# ============================================================

@ws_router.websocket("/api/v1/ws/pagos")
async def websocket_pagos(
    websocket: WebSocket,
    tenant_id: int = Query(...),
    token: Optional[str] = Query(None),
):
    if token:
        try:
            payload = decode_token(token)
            tenant_token = payload.get("tenant_id")
            if tenant_token is not None and int(tenant_token) != int(tenant_id):
                await websocket.close(code=1008, reason="Tenant ajeno")
                return
        except Exception:
            await websocket.close(code=1008, reason="Token inválido")
            return

    canal = canal_pagos(tenant_id)
    await manager.conectar_canal(websocket, canal)
    try:
        await websocket.send_json({
            "evento": "conectado",
            "canal": canal,
            "tenant_id": tenant_id,
        })
        while True:
            # Se mantiene abierto; los mensajes del cliente son ignorados.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.desconectar_canal(websocket, canal)
    except Exception:
        manager.desconectar_canal(websocket, canal)

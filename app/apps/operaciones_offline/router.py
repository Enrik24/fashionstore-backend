"""
[EXAMEN-2] Router del Paquete 2 — sesión offline (CU17, Fase 1) y motor de
operación offline (CU05-CU09, Fase 2).
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import (
    DispositivoRevocado,
    NotFoundException,
    ValidationException,
)
from app.security import decode_token, get_current_user
from app.services.websocket_manager import manager
from app.tenant import (
    autenticacion_flexible,
    get_current_tenant,
    require_sesion_offline_valida,
)
from app.apps.gestion_saas.schemas import ConfiguracionOfflineResponse
from app.apps.operaciones_offline.conflicto_service import ConflictoService
from app.apps.operaciones_offline.models import (
    DispositivoSincronizacion,
    OperacionOffline,
)
from app.apps.operaciones_offline.paquete_service import PaqueteOfflineService
from app.apps.operaciones_offline.schemas import (
    CierreSesionOfflineResponse,
    DescartarConflictoRequest,
    DescargaRegistrarRequest,
    DispositivoRegistroRequest,
    DispositivoResponse,
    EstadoSesionOfflineResponse,
    LoteSyncRequest,
    PinConfigurarRequest,
    PinConfigurarResponse,
    RenovarSesionRequest,
    ResolverConflictoRequest,
    RevocacionRequest,
    SesionOfflineRequest,
    SesionOfflineResponse,
)
from app.apps.operaciones_offline.services import (
    ConfiguracionOfflineService,
    DispositivoService,
    SesionOfflineService,
)
from app.apps.operaciones_offline.sincronizacion_service import SincronizacionService

router = APIRouter(prefix="/api/v1/offline", tags=["Operación Offline (CU05-CU09/CU17)"])
ws_router = APIRouter(tags=["Tiempo real — sincronización"])


def _serializar_dispositivo(dispositivo: DispositivoSincronizacion) -> dict:
    return {
        "id": str(dispositivo.id),
        "tenant_id": dispositivo.tenant_id,
        "usuario_id": dispositivo.usuario_id,
        "plataforma": dispositivo.plataforma,
        "nombre": dispositivo.nombre,
        "activo": dispositivo.activo,
        "ultima_conexion": dispositivo.ultima_conexion,
        "fecha_registro": dispositivo.fecha_registro,
    }


@router.post("/dispositivos", response_model=DispositivoResponse, status_code=201)
async def registrar_dispositivo(
    datos: DispositivoRegistroRequest,
    current_user=Depends(get_current_user),
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    dispositivo = await DispositivoService.registrar_dispositivo(
        db, tenant.id, current_user.id, datos
    )
    return _serializar_dispositivo(dispositivo)


@router.get("/dispositivos", response_model=list[DispositivoResponse])
async def listar_dispositivos(
    current_user=Depends(get_current_user),
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    dispositivos = await DispositivoService.listar_dispositivos(db, current_user.id, tenant.id)
    return [_serializar_dispositivo(d) for d in dispositivos]


@router.post("/dispositivos/{dispositivo_id}/revocar", response_model=DispositivoResponse)
async def revocar_dispositivo(
    dispositivo_id: str,
    datos: RevocacionRequest | None = None,
    current_user=Depends(get_current_user),
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    dispositivo = await DispositivoService.revocar_dispositivo(
        db, dispositivo_id, current_user.id, tenant.id,
        datos.motivo if datos else None,
    )
    return _serializar_dispositivo(dispositivo)


@router.post("/dispositivos/{dispositivo_id}/reactivar", response_model=DispositivoResponse)
async def reactivar_dispositivo(
    dispositivo_id: str,
    current_user=Depends(get_current_user),
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    dispositivo = await DispositivoService.reactivar_dispositivo(db, dispositivo_id, tenant.id)
    return _serializar_dispositivo(dispositivo)


@router.post("/sesion/pin", response_model=PinConfigurarResponse)
async def configurar_pin(
    datos: PinConfigurarRequest,
    current_user=Depends(get_current_user),
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    return await SesionOfflineService.configurar_pin(db, tenant.id, current_user.id, datos)


@router.delete("/sesion/pin")
async def eliminar_pin(
    current_user=Depends(get_current_user),
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    return await SesionOfflineService.eliminar_pin(db, tenant.id, current_user.id)


@router.post("/sesion/iniciar", response_model=SesionOfflineResponse)
async def iniciar_sesion_offline(
    datos: SesionOfflineRequest,
    db: AsyncSession = Depends(get_db),
):
    """Valida el PIN sin red y emite un token de propósito `offline`."""
    tenant_id = datos.tenant_id
    if tenant_id is None:
        try:
            device_uuid = UUID(datos.dispositivo_id)
        except (TypeError, ValueError):
            raise ValidationException("dispositivo_id inválido")
        result = await db.execute(
            select(DispositivoSincronizacion).where(DispositivoSincronizacion.id == device_uuid)
        )
        dispositivo = result.scalar_one_or_none()
        if dispositivo is None:
            raise NotFoundException("Dispositivo no encontrado")
        tenant_id = dispositivo.tenant_id

    return await SesionOfflineService.iniciar_sesion_offline(
        db, tenant_id, datos.dispositivo_id, datos.pin, datos.usuario_id
    )


@router.post("/sesion/renovar", response_model=SesionOfflineResponse)
async def renovar_sesion_offline(
    datos: RenovarSesionRequest,
    db: AsyncSession = Depends(get_db),
):
    return await SesionOfflineService.renovar_sesion_offline(db, datos.token_offline)


@router.post("/sesion/cerrar", response_model=CierreSesionOfflineResponse)
async def cerrar_sesion_offline(
    contexto=Depends(require_sesion_offline_valida),
    db: AsyncSession = Depends(get_db),
):
    return await SesionOfflineService.cerrar_sesion_offline(db, contexto["payload"])


@router.get("/sesion/estado", response_model=EstadoSesionOfflineResponse)
async def estado_sesion_offline(
    x_device_id: str | None = Header(default=None, alias="X-Device-ID"),
    current_user=Depends(get_current_user),
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    return await SesionOfflineService.obtener_estado_sesion(
        db, current_user.id, tenant.id, x_device_id
    )


@router.get("/configuracion", response_model=ConfiguracionOfflineResponse)
async def obtener_configuracion_offline(
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    return await ConfiguracionOfflineService.obtener_configuracion(db, tenant.id)


# ============================================================
# [EXAMEN-2] Fase 2 — descarga offline (CU05)
# ============================================================

async def _sucursal_para(
    db: AsyncSession, tenant_id: int, sucursal_id: Optional[int]
) -> Optional[int]:
    if sucursal_id is None:
        from app.apps.gestion_catalogo.models import Sucursal

        return (await db.execute(
            select(Sucursal.id)
            .where(Sucursal.tenant_id == tenant_id, Sucursal.deleted_at.is_(None))
            .order_by(Sucursal.id.asc())
            .limit(1)
        )).scalar_one_or_none()
    await PaqueteOfflineService.validar_sucursal_del_tenant(db, tenant_id, sucursal_id)
    return sucursal_id


async def _dispositivo_para(
    db: AsyncSession, contexto: dict, dispositivo_id: Optional[str] = None
):
    dispositivo = contexto.get("dispositivo")
    if dispositivo is not None:
        return dispositivo
    raw = dispositivo_id
    if not raw:
        raise ValidationException(
            "Se requiere X-Device-ID o un token offline con dispositivo"
        )
    try:
        device_uuid = UUID(str(raw))
    except (TypeError, ValueError):
        raise ValidationException("dispositivo_id inválido")
    result = await db.execute(
        select(DispositivoSincronizacion).where(
            DispositivoSincronizacion.id == device_uuid,
            DispositivoSincronizacion.tenant_id == int(contexto["tenant"].id),
        )
    )
    dispositivo = result.scalar_one_or_none()
    if dispositivo is None:
        raise DispositivoRevocado("Dispositivo no registrado")
    if not dispositivo.activo or dispositivo.revocado_en is not None:
        raise DispositivoRevocado()
    return dispositivo


@router.get("/version")
async def obtener_version_paquete(
    sucursal_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user),
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    sucursal = await _sucursal_para(db, tenant.id, sucursal_id)
    version = await PaqueteOfflineService.calcular_version_paquete(
        db, tenant.id, sucursal
    )
    actualizado = await PaqueteOfflineService._max_actualizacion(
        db, tenant.id, sucursal
    )
    return {
        "tenant_id": tenant.id,
        "sucursal_id": sucursal,
        "version": version,
        "hash_paquete": version,
        "fecha_corte": PaqueteOfflineService.obtener_fecha_corte(tenant.id, sucursal),
        "actualizado_en": actualizado,
    }


@router.get("/paquete")
async def obtener_paquete_offline(
    sucursal_id: Optional[int] = Query(None),
    dispositivo_id: Optional[str] = Query(None),
    current_user=Depends(get_current_user),
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    sucursal = await _sucursal_para(db, tenant.id, sucursal_id)
    return await PaqueteOfflineService.generar_paquete(
        db,
        tenant.id,
        sucursal,
        current_user.id,
        dispositivo_id=dispositivo_id,
    )


@router.get("/catalogo")
async def obtener_catalogo_incremental(
    desde_version: Optional[str] = Query(None),
    sucursal_id: Optional[int] = Query(None),
    current_user=Depends(get_current_user),
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    sucursal = await _sucursal_para(db, tenant.id, sucursal_id)
    return await PaqueteOfflineService.obtener_delta(
        db, tenant.id, sucursal, desde_version
    )


@router.get("/stock")
async def obtener_stock_offline(
    sucursal_id: Optional[int] = Query(None),
    actualizado_desde: Optional[datetime] = Query(None),
    current_user=Depends(get_current_user),
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    sucursal = await _sucursal_para(db, tenant.id, sucursal_id)
    if sucursal is None:
        return []
    return await PaqueteOfflineService.obtener_stock_sucursal(
        db, tenant.id, sucursal, desde=actualizado_desde
    )


@router.get("/clientes")
async def obtener_clientes_offline(
    current_user=Depends(get_current_user),
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    config = await ConfiguracionOfflineService.obtener_configuracion(db, tenant.id)
    if not config.incluir_clientes:
        return []
    return await PaqueteOfflineService.obtener_clientes_frecuentes(
        db, tenant.id, config.max_clientes
    )


@router.get("/promociones")
async def obtener_promociones_offline(
    current_user=Depends(get_current_user),
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    return await PaqueteOfflineService.obtener_promociones_vigentes(db, tenant.id)


@router.post("/descargas/registrar")
async def registrar_descarga_offline(
    datos: DescargaRegistrarRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    await _dispositivo_para(db, contexto, datos.dispositivo_id)
    return await PaqueteOfflineService.registrar_descarga(
        db, int(contexto["tenant"].id), contexto["usuario"].id, datos
    )


# ============================================================
# [EXAMEN-2] Fase 2 — sincronización (CU06-CU08)
# ============================================================

def _serializar_operacion(operacion: OperacionOffline) -> dict:
    return {
        "uuid_operacion": str(operacion.uuid_operacion),
        "tipo_operacion": (
            operacion.tipo_operacion.value
            if hasattr(operacion.tipo_operacion, "value")
            else str(operacion.tipo_operacion)
        ),
        "estado_sincronizacion": (
            operacion.estado_sincronizacion.value
            if hasattr(operacion.estado_sincronizacion, "value")
            else str(operacion.estado_sincronizacion)
        ),
        "requiere_revision": bool(operacion.requiere_revision),
        "intentos": operacion.intentos or 0,
        "motivo_rechazo": operacion.motivo_rechazo,
        "error_detalle": operacion.error_detalle,
        "resultado_json": operacion.resultado_json,
        "secuencia_local": operacion.secuencia_local,
        "sucursal_id": operacion.sucursal_id,
        "dispositivo_id": str(operacion.dispositivo_id) if operacion.dispositivo_id else None,
        "fecha_registro_local": operacion.fecha_registro_local,
        "fecha_recepcion_servidor": operacion.fecha_recepcion_servidor,
        "fecha_ultimo_intento": operacion.fecha_ultimo_intento,
    }


@router.post("/sync")
async def sincronizar_lote(
    lote: LoteSyncRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = int(contexto["tenant"].id)
    dispositivo = await _dispositivo_para(db, contexto, lote.dispositivo_id)
    return await SincronizacionService.procesar_lote(
        db,
        tenant_id,
        contexto["usuario"].id,
        dispositivo.id,
        lote,
    )


@router.get("/sync/{lote_id}")
async def estado_lote(
    lote_id: str,
    contexto=Depends(autenticacion_flexible),
):
    # El procesamiento es síncrono: cuando el POST responde, el lote terminó.
    return {"lote_id": lote_id, "estado": "PROCESADO", "asincrono": False}


@router.get("/operaciones")
async def listar_operaciones_offline(
    estado: Optional[str] = Query(None),
    tipo: Optional[str] = Query(None),
    desde: Optional[datetime] = Query(None),
    hasta: Optional[datetime] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    operaciones = await SincronizacionService.listar_operaciones(
        db,
        int(contexto["tenant"].id),
        estado=estado,
        tipo=tipo,
        desde=desde,
        hasta=hasta,
        skip=skip,
        limit=limit,
    )
    return [_serializar_operacion(o) for o in operaciones]


@router.get("/operaciones/{uuid_operacion}")
async def obtener_operacion_offline(
    uuid_operacion: str,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    operacion = await SincronizacionService.obtener_operacion(
        db, uuid_operacion, int(contexto["tenant"].id)
    )
    return _serializar_operacion(operacion)


@router.post("/operaciones/{uuid_operacion}/reintentar")
async def reintentar_operacion_offline(
    uuid_operacion: str,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    operacion = await SincronizacionService.obtener_operacion(
        db, uuid_operacion, int(contexto["tenant"].id)
    )
    return await SincronizacionService.reintentar_operacion(
        db, operacion.uuid_operacion, contexto["usuario"].id
    )


@router.get("/resumen")
async def resumen_sincronizacion(
    desde: Optional[datetime] = Query(None),
    hasta: Optional[datetime] = Query(None),
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    return await SincronizacionService.resumen(
        db, int(contexto["tenant"].id), desde=desde, hasta=hasta
    )


# ============================================================
# [EXAMEN-2] Fase 2 — conflictos (CU09)
# ============================================================

def _serializar_conflicto(conflicto) -> dict:
    operacion = getattr(conflicto, "operacion", None)
    return {
        "id": conflicto.id,
        "operacion_offline_id": conflicto.operacion_offline_id,
        "uuid_operacion": str(operacion.uuid_operacion) if operacion else None,
        "tipo_conflicto": conflicto.tipo_conflicto,
        "resuelto": bool(conflicto.resuelto),
        "resolucion": conflicto.resolucion,
        "estrategia_resolucion": conflicto.estrategia_resolucion,
        "observaciones": conflicto.observaciones,
        "detalle_json": conflicto.detalle_json,
        "fecha": conflicto.fecha,
        "fecha_resolucion": conflicto.fecha_resolucion,
        "resuelto_por": conflicto.resuelto_por,
    }


@router.get("/conflictos")
async def listar_conflictos(
    resuelto: Optional[bool] = Query(None),
    tipo: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    conflictos = await ConflictoService.listar_conflictos(
        db,
        int(contexto["tenant"].id),
        resuelto=resuelto,
        tipo=tipo,
        skip=skip,
        limit=limit,
    )
    return [_serializar_conflicto(c) for c in conflictos]


@router.get("/conflictos/pendientes/count")
async def contar_conflictos_pendientes(
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = int(contexto["tenant"].id)
    total = await ConflictoService.contar_pendientes(db, tenant_id)
    return {"tenant_id": tenant_id, "pendientes": total}


@router.get("/conflictos/{conflicto_id}")
async def obtener_conflicto(
    conflicto_id: int,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    conflicto = await ConflictoService.obtener_conflicto(
        db, conflicto_id, int(contexto["tenant"].id)
    )
    return _serializar_conflicto(conflicto)


@router.post("/conflictos/{conflicto_id}/resolver")
async def resolver_conflicto(
    conflicto_id: int,
    datos: ResolverConflictoRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    conflicto = await ConflictoService.resolver_manualmente(
        db,
        conflicto_id,
        int(contexto["tenant"].id),
        datos.decision,
        datos.observacion,
        contexto["usuario"].id,
    )
    return _serializar_conflicto(conflicto)


@router.post("/conflictos/{conflicto_id}/descartar")
async def descartar_conflicto(
    conflicto_id: int,
    datos: DescartarConflictoRequest | None = None,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    conflicto = await ConflictoService.descartar_conflicto(
        db,
        conflicto_id,
        int(contexto["tenant"].id),
        contexto["usuario"].id,
        datos.motivo if datos else None,
    )
    return _serializar_conflicto(conflicto)


# ============================================================
# [EXAMEN-2] Fase E — Compensaciones de ventas rechazadas (RF-032)
# ============================================================

def _serializar_compensacion(compensacion) -> dict:
    return {
        "id": compensacion.id,
        "tenant_id": compensacion.tenant_id,
        "operacion_offline_id": compensacion.operacion_offline_id,
        "orden_id": compensacion.orden_id,
        "cliente_id": compensacion.cliente_id,
        "tipo": compensacion.tipo.value if hasattr(compensacion.tipo, "value") else compensacion.tipo,
        "monto": float(compensacion.monto or 0),
        "moneda": compensacion.moneda,
        "estado": compensacion.estado.value if hasattr(compensacion.estado, "value") else compensacion.estado,
        "motivo": compensacion.motivo,
        "referencia_externa": compensacion.referencia_externa,
        "notificado_cliente": compensacion.notificado_cliente,
        "error_detalle": compensacion.error_detalle,
        "fecha_creacion": compensacion.fecha_creacion.isoformat() if compensacion.fecha_creacion else None,
        "fecha_resolucion": compensacion.fecha_resolucion.isoformat() if compensacion.fecha_resolucion else None,
        "resuelto_por": compensacion.resuelto_por,
    }


@router.get("/compensaciones/resumen")
async def resumen_compensaciones(
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    from app.apps.operaciones_offline.compensacion_service import CompensacionService

    return await CompensacionService.resumen(db, int(contexto["tenant"].id))


@router.get("/compensaciones")
async def listar_compensaciones(
    estado: Optional[str] = Query(None),
    desde: Optional[datetime] = Query(None),
    hasta: Optional[datetime] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    from app.apps.operaciones_offline.compensacion_service import CompensacionService

    filas = await CompensacionService.listar(
        db, int(contexto["tenant"].id), estado=estado,
        desde=desde, hasta=hasta, skip=skip, limit=limit,
    )
    return {"compensaciones": [_serializar_compensacion(f) for f in filas]}


@router.get("/compensaciones/{compensacion_id}")
async def obtener_compensacion(
    compensacion_id: int,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    from app.apps.operaciones_offline.compensacion_service import CompensacionService

    compensacion = await CompensacionService.obtener(
        db, int(contexto["tenant"].id), compensacion_id
    )
    return _serializar_compensacion(compensacion)


@router.post("/compensaciones/{compensacion_id}/confirmar-efectivo")
async def confirmar_compensacion_efectivo(
    compensacion_id: int,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    from app.apps.operaciones_offline.compensacion_service import CompensacionService

    compensacion = await CompensacionService.confirmar_efectivo(
        db,
        int(contexto["tenant"].id),
        compensacion_id,
        getattr(contexto.get("usuario"), "id", None),
    )
    return _serializar_compensacion(compensacion)


@router.post("/compensaciones/{compensacion_id}/reintentar")
async def reintentar_compensacion(
    compensacion_id: int,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    from app.apps.operaciones_offline.compensacion_service import CompensacionService

    compensacion = await CompensacionService.reintentar(
        db,
        int(contexto["tenant"].id),
        compensacion_id,
        getattr(contexto.get("usuario"), "id", None),
    )
    return _serializar_compensacion(compensacion)


# ============================================================
# WebSocket de sincronización
# ============================================================

@ws_router.websocket("/api/v1/ws/sync")
async def websocket_sync(
    websocket: WebSocket,
    tenant_id: int = Query(...),
    sucursal_id: Optional[int] = Query(None),
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

    canal = f"sync:{tenant_id}:{sucursal_id}" if sucursal_id is not None else f"sync:{tenant_id}"
    await manager.conectar_canal(websocket, canal)
    try:
        await websocket.send_json({
            "evento": "conectado",
            "canal": canal,
            "tenant_id": tenant_id,
            "sucursal_id": sucursal_id,
        })
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.desconectar_canal(websocket, canal)
    except Exception:
        manager.desconectar_canal(websocket, canal)


"""[EXAMEN-2] Router del Paquete 4 — devoluciones/cambios y traspasos.

CU13 reutiliza los modelos de `gestion_ventas` y CU14 usa `Traspaso` y
`DetalleTraspaso`. Las operaciones de escritura se confirman aquí; los
servicios solo hacen `flush` para que el motor offline pueda envolverlas en un
savepoint.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.tenant import autenticacion_flexible
from app.apps.gestion_comercial_logistica.cambio_service import CambioDevolucionService
from app.apps.gestion_comercial_logistica.schemas import (
    CambioRequest,
    RechazarDevolucionRequest,
    RecibirTraspasoRequest,
    RechazarTraspasoRequest,
    TraspasoCreateRequest,
)
from app.apps.gestion_comercial_logistica.traspaso_service import TraspasoService

router = APIRouter(prefix="/api/v1", tags=["Devoluciones y traspasos (CU13/CU14)"])


def _tenant_id(contexto) -> int:
    return int(contexto["tenant"].id)


def _usuario(contexto):
    return contexto.get("usuario")


def _serializar_orden(orden) -> dict:
    return {
        "id": orden.id,
        "numero_orden": orden.numero_orden,
        "cliente_id": orden.cliente_id,
        "sucursal_id": orden.sucursal_id,
        "fecha": orden.fecha.isoformat() if orden.fecha else None,
        "estado": orden.estado.value if hasattr(orden.estado, "value") else orden.estado,
        "tipo": orden.tipo.value if hasattr(orden.tipo, "value") else orden.tipo,
        "total": float(orden.total or 0),
        "fecha_limite_cambio": (
            orden.fecha_limite_cambio.isoformat() if orden.fecha_limite_cambio else None
        ),
        "detalles": [
            {
                "id": detalle.id,
                "variante_producto_id": detalle.variante_producto_id,
                "cantidad": detalle.cantidad,
                "precio_unitario": float(detalle.precio_unitario or 0),
                "subtotal": float(detalle.subtotal or 0),
            }
            for detalle in (orden.detalles or [])
        ],
    }


# ======================================================================
# CU13 — Devoluciones y cambios
# ======================================================================

@router.post("/devoluciones/cambio")
async def registrar_cambio(
    datos: CambioRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    resultado = await CambioDevolucionService.procesar_cambio(
        db,
        _tenant_id(contexto),
        getattr(_usuario(contexto), "id", None),
        datos.model_dump(),
        es_offline=datos.uuid_offline is not None,
    )
    await db.commit()
    return resultado


@router.get("/devoluciones/venta/{numero_comprobante}")
async def buscar_venta_para_devolucion(
    numero_comprobante: str,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    orden = await CambioDevolucionService.buscar_venta(
        db, _tenant_id(contexto), numero_comprobante=numero_comprobante
    )
    try:
        limite = CambioDevolucionService.validar_plazo(orden)
        dentro_plazo = True
    except Exception:
        limite = orden.fecha_limite_cambio
        dentro_plazo = False
    return {
        "venta": _serializar_orden(orden),
        "dentro_plazo": dentro_plazo,
        "fecha_limite_cambio": limite.isoformat() if limite else None,
    }


@router.get("/devoluciones/validar-plazo/{orden_id}")
async def validar_plazo_devolucion(
    orden_id: int,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    orden = await CambioDevolucionService._obtener_orden(
        db, orden_id, _tenant_id(contexto)
    )
    limite = CambioDevolucionService.validar_plazo(orden)
    return {
        "orden_id": orden.id,
        "dentro_plazo": True,
        "fecha_limite_cambio": limite.isoformat(),
        "plazo_dias": 7,
    }


@router.get("/devoluciones/pendientes-offline")
async def listar_devoluciones_pendientes(
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    return {
        "pendientes": await CambioDevolucionService.listar_pendientes_offline(
            db, _tenant_id(contexto)
        )
    }


@router.get("/devoluciones/historial")
async def historial_devoluciones(
    tipo: str | None = Query(None),
    estado: str | None = Query(None),
    desde: datetime | None = Query(None),
    hasta: datetime | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    return {
        "devoluciones": await CambioDevolucionService.consultar_historial(
            db,
            _tenant_id(contexto),
            tipo=tipo,
            estado=estado,
            desde=desde,
            hasta=hasta,
            skip=skip,
            limit=limit,
        )
    }


@router.post("/devoluciones/{solicitud_id}/aprobar")
async def aprobar_devolucion(
    solicitud_id: int,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    resultado = await CambioDevolucionService.aprobar_solicitud(
        db, _tenant_id(contexto), solicitud_id, getattr(_usuario(contexto), "id", None)
    )
    await db.commit()
    return resultado


@router.post("/devoluciones/{solicitud_id}/rechazar")
async def rechazar_devolucion(
    solicitud_id: int,
    datos: RechazarDevolucionRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    resultado = await CambioDevolucionService.rechazar_solicitud(
        db,
        _tenant_id(contexto),
        solicitud_id,
        getattr(_usuario(contexto), "id", None),
        datos.motivo,
    )
    await db.commit()
    return resultado


@router.get("/devoluciones/{solicitud_id}/nota")
async def nota_devolucion(
    solicitud_id: int,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    contenido, nombre = await CambioDevolucionService.generar_nota_devolucion(
        db, _tenant_id(contexto), solicitud_id
    )
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


# ======================================================================
# CU14 — Traspasos entre sucursales
# ======================================================================

@router.post("/traspasos", status_code=201)
async def crear_traspaso(
    datos: TraspasoCreateRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    resultado = await TraspasoService.crear_traspaso(
        db,
        _tenant_id(contexto),
        getattr(_usuario(contexto), "id", None),
        datos.model_dump(),
    )
    await db.commit()
    return resultado


@router.get("/traspasos")
async def listar_traspasos(
    estado: str | None = Query(None),
    sucursal_id: int | None = Query(None),
    producto_id: int | None = Query(None),
    desde: datetime | None = Query(None),
    hasta: datetime | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    return {
        "traspasos": await TraspasoService.listar_traspasos(
            db,
            _tenant_id(contexto),
            estado=estado,
            sucursal_id=sucursal_id,
            producto_id=producto_id,
            desde=desde,
            hasta=hasta,
            skip=skip,
            limit=limit,
        )
    }


@router.get("/traspasos/sugerencias")
async def sugerencias_traspaso(
    producto_id: int | None = Query(None),
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    return await TraspasoService.sugerir_traspaso(
        db, _tenant_id(contexto), producto_id=producto_id
    )


@router.get("/traspasos/producto/{producto_id}")
async def historial_producto_traspasos(
    producto_id: int,
    limit: int = Query(50, ge=1, le=200),
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    return await TraspasoService.historial_producto(
        db, producto_id, _tenant_id(contexto), limit=limit
    )


@router.get("/traspasos/{traspaso_id}")
async def obtener_traspaso(
    traspaso_id: int,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    return await TraspasoService.obtener_traspaso(
        db, traspaso_id, _tenant_id(contexto)
    )


@router.post("/traspasos/{traspaso_id}/recibir")
async def recibir_traspaso(
    traspaso_id: int,
    datos: RecibirTraspasoRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    resultado = await TraspasoService.recibir_traspaso(
        db,
        traspaso_id,
        _tenant_id(contexto),
        getattr(_usuario(contexto), "id", None),
        cantidades_recibidas=(
            [item.model_dump() for item in datos.detalles] if datos.detalles else None
        ),
        observaciones=datos.observaciones,
    )
    await db.commit()
    return resultado


@router.post("/traspasos/{traspaso_id}/rechazar")
async def rechazar_traspaso(
    traspaso_id: int,
    datos: RechazarTraspasoRequest,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    resultado = await TraspasoService.rechazar_traspaso(
        db,
        traspaso_id,
        _tenant_id(contexto),
        getattr(_usuario(contexto), "id", None),
        motivo=datos.motivo,
        devolver_origen=datos.devolver_origen,
        detalles=(
            [item.model_dump() for item in datos.detalles] if datos.detalles else None
        ),
    )
    await db.commit()
    return resultado


@router.post("/traspasos/{traspaso_id}/cancelar")
async def cancelar_traspaso(
    traspaso_id: int,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    resultado = await TraspasoService.cancelar_traspaso(
        db, traspaso_id, _tenant_id(contexto), getattr(_usuario(contexto), "id", None)
    )
    await db.commit()
    return resultado


@router.get("/traspasos/{traspaso_id}/pdf")
async def pdf_traspaso(
    traspaso_id: int,
    contexto=Depends(autenticacion_flexible),
    db: AsyncSession = Depends(get_db),
):
    contenido, nombre = await TraspasoService.generar_pdf_traspaso(
        db, traspaso_id, _tenant_id(contexto)
    )
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )

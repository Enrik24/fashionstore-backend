"""
Router de FastAPI para Gestión de Marketing y Promociones (CU24).
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional

from app.database import get_db
from app.security import require_role
from app.apps.gestion_usuarios.models import Usuario
from app.apps.gestion_usuarios.services import BitacoraService
from app.apps.gestion_marketing.models import EstadoPromocion, TipoPromocion
from app.apps.gestion_marketing.schemas import (
    PromocionCreate,
    PromocionUpdate,
    PromocionEstadoUpdate,
    PromocionResponse,
    PromocionPublicaResponse
)
from app.apps.gestion_marketing.services import PromocionService


router = APIRouter(prefix="/api/v1", tags=["Promociones (CU24)"])


# ==============================================================================
# ENDPOINT PÚBLICO (CATÁLOGO)
# ==============================================================================

@router.get("/public/promociones/activas", response_model=List[PromocionPublicaResponse], name="obtener_promociones_activas_publicas")
async def obtener_promociones_activas_publicas(
    sucursal_id: Optional[int] = Query(None, description="Filtrar por sucursal específica"),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene el catálogo público de promociones comerciales activas y vigentes."""
    return await PromocionService.obtener_promociones_activas(db, sucursal_id=sucursal_id)


# ==============================================================================
# ENDPOINTS ADMINISTRACIÓN (ADMIN)
# ==============================================================================

@router.post("/promociones/", response_model=PromocionResponse, status_code=status.HTTP_201_CREATED, name="crear_promocion")
async def crear_promocion(
    datos: PromocionCreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea una nueva promoción comercial (CU24)."""
    resultado = await PromocionService.crear(db, datos, creado_por_id=current_user.id)
    
    await BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_PROMOCION",
        usuario_id=current_user.id,
        ip_address=request.client.host if request.client else None,
        modulo="Promociones",
        detalles=f"Promoción '{resultado.nombre}' (ID: {resultado.id}) creada con tipo {resultado.tipo} y estado {resultado.estado}."
    )
    return resultado


@router.get("/promociones/", response_model=List[PromocionResponse], name="listar_promociones")
async def listar_promociones(
    estado: Optional[EstadoPromocion] = Query(None, description="Filtrar por estado"),
    tipo: Optional[TipoPromocion] = Query(None, description="Filtrar por tipo"),
    solo_vigentes: bool = Query(False, description="Filtrar únicamente las vigentes hoy"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Lista las promociones registradas con filtros avanzados."""
    return await PromocionService.listar(
        db=db,
        estado=estado,
        tipo=tipo,
        solo_vigentes=solo_vigentes,
        skip=skip,
        limit=limit
    )


@router.get("/promociones/{promocion_id}", response_model=PromocionResponse, name="obtener_promocion")
async def obtener_promocion(
    promocion_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene el detalle completo de una promoción comercial."""
    return await PromocionService.obtener_response(db, promocion_id)


@router.put("/promociones/{promocion_id}", response_model=PromocionResponse, name="actualizar_promocion")
async def actualizar_promocion(
    promocion_id: int,
    datos: PromocionUpdate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza los datos y aplicabilidad de una promoción."""
    antes = BitacoraService.foto(await PromocionService.obtener(db, promocion_id))
    resultado = await PromocionService.actualizar(db, promocion_id, datos)

    await BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_PROMOCION",
        usuario_id=current_user.id,
        ip_address=request.client.host if request.client else None,
        modulo="Promociones",
        detalles=f"Promoción '{resultado.nombre}' (ID: {promocion_id}) actualizada.",
        registro_id=promocion_id,
        valores_anteriores=antes,
        valores_nuevos=BitacoraService.foto(await PromocionService.obtener(db, promocion_id))
    )
    return resultado


@router.patch("/promociones/{promocion_id}/estado", response_model=PromocionResponse, name="cambiar_estado_promocion")
async def cambiar_estado_promocion(
    promocion_id: int,
    datos: PromocionEstadoUpdate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Cambia el estado de una promoción (valida solapamiento si se pasa a ACTIVA)."""
    antes = BitacoraService.foto(await PromocionService.obtener(db, promocion_id))
    resultado = await PromocionService.cambiar_estado(db, promocion_id, datos.estado)

    await BitacoraService.registrar_evento(
        db=db,
        accion="CAMBIAR_ESTADO_PROMOCION",
        usuario_id=current_user.id,
        ip_address=request.client.host if request.client else None,
        modulo="Promociones",
        detalles=f"Promoción (ID: {promocion_id}) cambió de estado a {datos.estado}.",
        registro_id=promocion_id,
        valores_anteriores=antes,
        valores_nuevos=BitacoraService.foto(await PromocionService.obtener(db, promocion_id))
    )
    return resultado


@router.delete("/promociones/{promocion_id}", name="eliminar_promocion")
async def eliminar_promocion(
    promocion_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina o desactiva una promoción comercial."""
    resultado = await PromocionService.eliminar(db, promocion_id)

    await BitacoraService.registrar_evento(
        db=db,
        accion="ELIMINAR_PROMOCION",
        usuario_id=current_user.id,
        ip_address=request.client.host if request.client else None,
        modulo="Promociones",
        detalles=f"Operación de eliminación ejecutada para promoción ID: {promocion_id}."
    )
    return resultado

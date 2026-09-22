"""
Router - Gestión de Catálogo, Productos e Inventario
Contiene todos los endpoints de la App 2.
"""
from fastapi import APIRouter, Depends, Query, UploadFile, File, HTTPException, status, WebSocket, WebSocketDisconnect, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List, Optional
from datetime import datetime
from decimal import Decimal

from app.database import get_db, AsyncSessionLocal
from app.security import get_current_user, require_role, get_client_ip
from app.apps.gestion_usuarios.models import Usuario
from app.apps.gestion_usuarios.services import BitacoraService
from app.services.cloudinary_service import CloudinaryService
from app.services.websocket_manager import manager
from app.apps.gestion_catalogo.models import VarianteProducto, Producto
from app.apps.gestion_catalogo import services as catalogo_services
from app.apps.gestion_catalogo.schemas import (
    # Ciudad
    CiudadCreate, CiudadUpdate, CiudadResponse,
    # Sucursal
    SucursalCreate, SucursalUpdate, SucursalResponse, SucursalConCiudadResponse,
    # Categoría
    CategoriaCreate, CategoriaUpdate, CategoriaResponse,
    # Talla y Color
    TallaCreate, TallaUpdate, TallaResponse, ColorCreate, ColorUpdate, ColorResponse,
    # Temporada
    TemporadaCreate, TemporadaUpdate, TemporadaResponse,
    # Colección
    ColeccionCreate, ColeccionUpdate, ColeccionResponse,
    # Proveedor
    ProveedorCreate, ProveedorUpdate, ProveedorResponse,
    # Producto
    ProductoCreate, ProductoUpdate, ProductoResponse, ProductoDetalleResponse,
    # Variante
    VarianteProductoCreate, VarianteProductoResponse, VarianteProductoDetalleResponse,
    AgregarVarianteRequest,
    # Inventario
    InventarioCreate, InventarioUpdate, InventarioResponse, InventarioDetalleResponse,
    # Movimiento
    MovimientoInventarioCreate, MovimientoInventarioResponse,
    # Disponibilidad
    DisponibilidadProductoResponse, DisponibilidadResponse, StockVarianteSucursalResponse,
    # Búsqueda y Filtros
    ProductoFilter, ProductoBusquedaResponse,
    # Requests
    StockPorSucursalRequest, AsociarColeccionRequest,
    # Favoritos (CU25)
    FavoritoResponse, FavoritoIdsResponse, MoverFavoritoCarritoRequest,
    # Valoraciones (CU26)
    ValoracionCreate, ValoracionUpdate, ValoracionResponse, PuedeValorarResponse,
    # Recepciones por proveedor (Opción A)
    RecepcionCreate, RecepcionResponse,
)
from app.apps.gestion_ventas.dependencies import obtener_cliente_actual

# Crear router
router = APIRouter(prefix="/api/v1", tags=["Gestión de Catálogo, Productos e Inventario"])


# ============================================
# Endpoints de Ciudad
# ============================================

@router.get("/ciudades/", response_model=List[CiudadResponse], name="list_ciudades")
async def listar_ciudades(
    db: AsyncSession = Depends(get_db)
):
    """Lista todas las ciudades."""
    return await catalogo_services.CiudadService.get_all(db)


@router.post("/ciudades/", response_model=CiudadResponse, name="create_ciudad")
async def crear_ciudad(
    datos: CiudadCreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea una nueva ciudad."""
    ciudad = await catalogo_services.CiudadService.create(db, datos)
    await BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_CIUDAD",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Sucursales",
        detalles=f"Ciudad creada: {ciudad.nombre} (ID: {ciudad.id})"
    )
    return ciudad


@router.get("/ciudades/{ciudad_id}", response_model=CiudadResponse, name="get_ciudad")
async def obtener_ciudad(
    ciudad_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Obtiene una ciudad por su ID."""
    return await catalogo_services.CiudadService.get(db, ciudad_id)


@router.put("/ciudades/{ciudad_id}", response_model=CiudadResponse, name="update_ciudad")
async def actualizar_ciudad(
    ciudad_id: int,
    datos: CiudadUpdate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza una ciudad."""
    antes = BitacoraService.foto(await catalogo_services.CiudadService.get(db, ciudad_id))
    ciudad = await catalogo_services.CiudadService.update(db, ciudad_id, datos)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_CIUDAD",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Sucursales",
        detalles=f"Ciudad actualizada: ID {ciudad_id} ({ciudad.nombre})",
        registro_id=ciudad_id,
        valores_anteriores=antes,
        valores_nuevos=BitacoraService.foto(ciudad)
    )
    return ciudad


@router.delete("/ciudades/{ciudad_id}", name="delete_ciudad")
async def eliminar_ciudad(
    ciudad_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina una ciudad."""
    await catalogo_services.CiudadService.delete(db, ciudad_id)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ELIMINAR_CIUDAD",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Sucursales",
        detalles=f"Ciudad eliminada: ID {ciudad_id}"
    )
    return {"message": "Ciudad eliminada exitosamente"}


# ============================================
# Endpoints de Sucursal
# ============================================

@router.get("/sucursales/", response_model=List[SucursalConCiudadResponse], name="list_sucursales")
async def listar_sucursales(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    ciudad_id: Optional[int] = None,
    estado: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Lista todas las sucursales."""
    sucursales, total = await catalogo_services.SucursalService.get_all(db, skip, limit, ciudad_id, estado)
    return sucursales


@router.post("/sucursales/", response_model=SucursalResponse, name="create_sucursal")
async def crear_sucursal(
    datos: SucursalCreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea una nueva sucursal."""
    sucursal = await catalogo_services.SucursalService.create(db, datos)
    await BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_SUCURSAL",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Sucursales",
        detalles=f"Sucursal creada: {sucursal.nombre} (ID: {sucursal.id})"
    )
    return sucursal


@router.get("/sucursales/{sucursal_id}", response_model=SucursalConCiudadResponse, name="get_sucursal")
async def obtener_sucursal(
    sucursal_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Obtiene una sucursal por su ID."""
    return await catalogo_services.SucursalService.get(db, sucursal_id)


@router.put("/sucursales/{sucursal_id}", response_model=SucursalResponse, name="update_sucursal")
async def actualizar_sucursal(
    sucursal_id: int,
    datos: SucursalUpdate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza una sucursal."""
    antes = BitacoraService.foto(await catalogo_services.SucursalService.get(db, sucursal_id))
    sucursal = await catalogo_services.SucursalService.update(db, sucursal_id, datos)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_SUCURSAL",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Sucursales",
        detalles=f"Sucursal actualizada: ID {sucursal_id} ({sucursal.nombre})",
        registro_id=sucursal_id,
        valores_anteriores=antes,
        valores_nuevos=BitacoraService.foto(sucursal)
    )
    return sucursal


@router.delete("/sucursales/{sucursal_id}", name="delete_sucursal")
async def eliminar_sucursal(
    sucursal_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina una sucursal."""
    await catalogo_services.SucursalService.delete(db, sucursal_id)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ELIMINAR_SUCURSAL",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Sucursales",
        detalles=f"Sucursal eliminada: ID {sucursal_id}"
    )
    return {"message": "Sucursal eliminada exitosamente"}


@router.get("/sucursales/{sucursal_id}/productos", name="get_productos_sucursal")
async def productos_por_sucursal(
    sucursal_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene los productos disponibles en una sucursal."""
    inventarios, total = await catalogo_services.InventarioService.get_inventario_sucursal(
        db, sucursal_id, skip, limit
    )
    return inventarios


# ============================================
# Endpoints de Categoría
# ============================================

@router.get("/categorias/", response_model=List[CategoriaResponse], name="list_categorias")
async def listar_categorias(
    db: AsyncSession = Depends(get_db)
):
    """Lista todas las categorías."""
    return await catalogo_services.CategoriaService.get_all(db)


@router.post("/categorias/", response_model=CategoriaResponse, name="create_categoria")
async def crear_categoria(
    datos: CategoriaCreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea una nueva categoría."""
    categoria = await catalogo_services.CategoriaService.create(db, datos)
    await BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_CATEGORIA",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Categorías",
        detalles=f"Categoría creada: {categoria.nombre} (ID: {categoria.id})"
    )
    return categoria


@router.get("/categorias/{categoria_id}", response_model=CategoriaResponse, name="get_categoria")
async def obtener_categoria(
    categoria_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene una categoría por su ID."""
    return await catalogo_services.CategoriaService.get(db, categoria_id)


@router.put("/categorias/{categoria_id}", response_model=CategoriaResponse, name="update_categoria")
async def actualizar_categoria(
    categoria_id: int,
    datos: CategoriaUpdate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza una categoría."""
    antes = BitacoraService.foto(await catalogo_services.CategoriaService.get(db, categoria_id))
    categoria = await catalogo_services.CategoriaService.update(db, categoria_id, datos)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_CATEGORIA",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Categorías",
        detalles=f"Categoría actualizada: ID {categoria_id} ({categoria.nombre})",
        registro_id=categoria_id,
        valores_anteriores=antes,
        valores_nuevos=BitacoraService.foto(categoria)
    )
    return categoria


@router.delete("/categorias/{categoria_id}", name="delete_categoria")
async def eliminar_categoria(
    categoria_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina una categoría."""
    await catalogo_services.CategoriaService.delete(db, categoria_id)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ELIMINAR_CATEGORIA",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Categorías",
        detalles=f"Categoría eliminada: ID {categoria_id}"
    )
    return {"message": "Categoría eliminada exitosamente"}


@router.get("/categorias/{categoria_id}/productos", response_model=List[ProductoResponse], name="get_productos_categoria")
async def productos_por_categoria(
    categoria_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene los productos pertenecientes a una categoría."""
    productos, total = await catalogo_services.ProductoService.get_all(
        db, skip=skip, limit=limit, categoria_id=categoria_id
    )
    return productos


# ============================================
# Endpoints de Talla
# ============================================

@router.get("/tallas/", response_model=List[TallaResponse], name="list_tallas")
async def listar_tallas(
    tipo: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Lista todas las tallas."""
    return await catalogo_services.TallaService.get_all(db, tipo)


@router.post("/tallas/", response_model=TallaResponse, name="create_talla")
async def crear_talla(
    datos: TallaCreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea una nueva talla."""
    talla = await catalogo_services.TallaService.create(db, datos)
    await BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_TALLA",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Productos",
        detalles=f"Talla creada: {talla.valor} ({talla.tipo})"
    )
    return talla


@router.put("/tallas/{talla_id}", response_model=TallaResponse, name="update_talla")
async def actualizar_talla(
    talla_id: int,
    datos: TallaUpdate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza una talla."""
    antes = BitacoraService.foto(await catalogo_services.TallaService.get(db, talla_id))
    talla = await catalogo_services.TallaService.update(db, talla_id, datos)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_TALLA",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Productos",
        detalles=f"Talla actualizada: ID {talla_id} ({talla.valor})",
        registro_id=talla_id,
        valores_anteriores=antes,
        valores_nuevos=BitacoraService.foto(talla)
    )
    return talla


@router.delete("/tallas/{talla_id}", name="delete_talla")
async def eliminar_talla(
    talla_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina una talla (las variantes quedan como 'Sin talla')."""
    await catalogo_services.TallaService.delete(db, talla_id)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ELIMINAR_TALLA",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Productos",
        detalles=f"Talla eliminada: ID {talla_id}"
    )
    return {"message": "Talla eliminada exitosamente"}


# ============================================
# Endpoints de Color
# ============================================

@router.get("/colores/", response_model=List[ColorResponse], name="list_colores")
async def listar_colores(
    db: AsyncSession = Depends(get_db)
):
    """Lista todos los colores."""
    return await catalogo_services.ColorService.get_all(db)


@router.post("/colores/", response_model=ColorResponse, name="create_color")
async def crear_color(
    datos: ColorCreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea un nuevo color."""
    color = await catalogo_services.ColorService.create(db, datos)
    await BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_COLOR",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Productos",
        detalles=f"Color creado: {color.nombre} ({color.codigo_hex})"
    )
    return color


@router.put("/colores/{color_id}", response_model=ColorResponse, name="update_color")
async def actualizar_color(
    color_id: int,
    datos: ColorUpdate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza un color."""
    antes = BitacoraService.foto(await catalogo_services.ColorService.get(db, color_id))
    color = await catalogo_services.ColorService.update(db, color_id, datos)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_COLOR",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Productos",
        detalles=f"Color actualizado: ID {color_id} ({color.nombre})",
        registro_id=color_id,
        valores_anteriores=antes,
        valores_nuevos=BitacoraService.foto(color)
    )
    return color


@router.delete("/colores/{color_id}", name="delete_color")
async def eliminar_color(
    color_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina un color (bloqueado si tiene variantes asociadas)."""
    await catalogo_services.ColorService.delete(db, color_id)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ELIMINAR_COLOR",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Productos",
        detalles=f"Color eliminado: ID {color_id}"
    )
    return {"message": "Color eliminado exitosamente"}


# ============================================
# Endpoints de Temporada
# ============================================

@router.get("/temporadas/", response_model=List[TemporadaResponse], name="list_temporadas")
async def listar_temporadas(
    db: AsyncSession = Depends(get_db)
):
    """Lista todas las temporadas."""
    return await catalogo_services.TemporadaService.get_all(db)


@router.post("/temporadas/", response_model=TemporadaResponse, name="create_temporada")
async def crear_temporada(
    datos: TemporadaCreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea una nueva temporada."""
    temporada = await catalogo_services.TemporadaService.create(db, datos)
    await BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_TEMPORADA",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Temporadas",
        detalles=f"Temporada creada: {temporada.nombre} (ID: {temporada.id})"
    )
    return temporada


@router.get("/temporadas/{temporada_id}", response_model=TemporadaResponse, name="get_temporada")
async def obtener_temporada(
    temporada_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene una temporada por su ID."""
    return await catalogo_services.TemporadaService.get(db, temporada_id)


@router.put("/temporadas/{temporada_id}", response_model=TemporadaResponse, name="update_temporada")
async def actualizar_temporada(
    temporada_id: int,
    datos: TemporadaUpdate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza una temporada."""
    antes = BitacoraService.foto(await catalogo_services.TemporadaService.get(db, temporada_id))
    temporada = await catalogo_services.TemporadaService.update(db, temporada_id, datos)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_TEMPORADA",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Temporadas",
        detalles=f"Temporada actualizada: ID {temporada_id} ({temporada.nombre})",
        registro_id=temporada_id,
        valores_anteriores=antes,
        valores_nuevos=BitacoraService.foto(temporada)
    )
    return temporada


@router.delete("/temporadas/{temporada_id}", name="delete_temporada")
async def eliminar_temporada(
    temporada_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina una temporada."""
    await catalogo_services.TemporadaService.delete(db, temporada_id)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ELIMINAR_TEMPORADA",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Temporadas",
        detalles=f"Temporada eliminada: ID {temporada_id}"
    )
    return {"message": "Temporada eliminada exitosamente"}


@router.get("/temporadas/{temporada_id}/productos", response_model=List[ProductoResponse], name="get_productos_temporada")
async def productos_por_temporada(
    temporada_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene los productos pertenecientes a una temporada."""
    productos, total = await catalogo_services.ProductoService.get_all(
        db, skip=skip, limit=limit, temporada_id=temporada_id
    )
    return productos


# ============================================
# Endpoints de Colección
# ============================================

@router.get("/colecciones/", response_model=List[ColeccionResponse], name="list_colecciones")
async def listar_colecciones(
    temporada_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    """Lista todas las colecciones."""
    return await catalogo_services.ColeccionService.get_all(db, temporada_id)


@router.post("/colecciones/", response_model=ColeccionResponse, name="create_coleccion")
async def crear_coleccion(
    datos: ColeccionCreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea una nueva colección."""
    coleccion = await catalogo_services.ColeccionService.create(db, datos)
    await BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_COLECCION",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Colecciones",
        detalles=f"Colección creada: {coleccion.nombre} (ID: {coleccion.id})"
    )
    return coleccion


@router.get("/colecciones/{coleccion_id}", response_model=ColeccionResponse, name="get_coleccion")
async def obtener_coleccion(
    coleccion_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene una colección por su ID."""
    return await catalogo_services.ColeccionService.get(db, coleccion_id)


@router.put("/colecciones/{coleccion_id}", response_model=ColeccionResponse, name="update_coleccion")
async def actualizar_coleccion(
    coleccion_id: int,
    datos: ColeccionUpdate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza una colección."""
    antes = BitacoraService.foto(await catalogo_services.ColeccionService.get(db, coleccion_id))
    coleccion = await catalogo_services.ColeccionService.update(db, coleccion_id, datos)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_COLECCION",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Colecciones",
        detalles=f"Colección actualizada: ID {coleccion_id} ({coleccion.nombre})",
        registro_id=coleccion_id,
        valores_anteriores=antes,
        valores_nuevos=BitacoraService.foto(coleccion)
    )
    return coleccion


@router.delete("/colecciones/{coleccion_id}", name="delete_coleccion")
async def eliminar_coleccion(
    coleccion_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina una colección."""
    await catalogo_services.ColeccionService.delete(db, coleccion_id)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ELIMINAR_COLECCION",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Colecciones",
        detalles=f"Colección eliminada: ID {coleccion_id}"
    )
    return {"message": "Colección eliminada exitosamente"}


@router.get("/colecciones/{coleccion_id}/productos", response_model=List[ProductoResponse], name="get_productos_coleccion")
async def productos_por_coleccion(
    coleccion_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Obtiene los productos asociados a una colección."""
    from app.apps.gestion_catalogo.models import Coleccion, Producto
    res = await db.execute(
        select(Coleccion).options(
            selectinload(Coleccion.productos).selectinload(Producto.categoria),
            selectinload(Coleccion.productos).selectinload(Producto.temporada),
            selectinload(Coleccion.productos).selectinload(Producto.proveedor),
            selectinload(Coleccion.productos).selectinload(Producto.variantes)
        ).where(Coleccion.id == coleccion_id)
    )
    col = res.scalar_one_or_none()
    if not col:
        raise HTTPException(status_code=404, detail="Colección no encontrada")
    return col.productos


@router.get("/productos/{producto_id}/colecciones", response_model=List[ColeccionResponse], name="list_colecciones_producto")
async def colecciones_de_producto(
    producto_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Lista las colecciones a las que pertenece un producto."""
    return await catalogo_services.ColeccionService.get_colecciones_de_producto(db, producto_id)


@router.post("/productos/{producto_id}/colecciones", name="asociar_producto_coleccion", status_code=status.HTTP_201_CREATED)
async def asociar_producto_coleccion(
    producto_id: int,
    datos: AsociarColeccionRequest,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Asocia un producto a una colección."""
    await catalogo_services.ColeccionService.associate_producto(db, producto_id, datos.coleccion_id)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ASOCIAR_PRODUCTO_COLECCION",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Colecciones",
        detalles=f"Producto ID {producto_id} asociado a colección ID {datos.coleccion_id}"
    )
    return {"message": "Producto asociado a la colección exitosamente"}


@router.delete("/productos/{producto_id}/colecciones/{coleccion_id}", name="quitar_producto_coleccion")
async def quitar_producto_coleccion(
    producto_id: int,
    coleccion_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Quita un producto de una colección."""
    await catalogo_services.ColeccionService.disassociate_producto(db, producto_id, coleccion_id)
    await BitacoraService.registrar_evento(
        db=db,
        accion="QUITAR_PRODUCTO_COLECCION",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Colecciones",
        detalles=f"Producto ID {producto_id} quitado de colección ID {coleccion_id}"
    )
    return {"message": "Producto quitado de la colección exitosamente"}


# ============================================
# Endpoints de Proveedor
# ============================================

@router.get("/proveedores/", response_model=List[ProveedorResponse], name="list_proveedores")
async def listar_proveedores(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Lista todos los proveedores."""
    proveedores, total = await catalogo_services.ProveedorService.get_all(db, skip, limit)
    return proveedores


@router.post("/proveedores/", response_model=ProveedorResponse, name="create_proveedor")
async def crear_proveedor(
    datos: ProveedorCreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea un nuevo proveedor."""
    proveedor = await catalogo_services.ProveedorService.create(db, datos)
    await BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_PROVEEDOR",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Proveedores",
        detalles=f"Proveedor creado: {proveedor.nombre} (ID: {proveedor.id})"
    )
    return proveedor


@router.get("/proveedores/{proveedor_id}", response_model=ProveedorResponse, name="get_proveedor")
async def obtener_proveedor(
    proveedor_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene un proveedor por su ID."""
    return await catalogo_services.ProveedorService.get(db, proveedor_id)


@router.put("/proveedores/{proveedor_id}", response_model=ProveedorResponse, name="update_proveedor")
async def actualizar_proveedor(
    proveedor_id: int,
    datos: ProveedorUpdate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza un proveedor."""
    antes = BitacoraService.foto(await catalogo_services.ProveedorService.get(db, proveedor_id))
    proveedor = await catalogo_services.ProveedorService.update(db, proveedor_id, datos)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_PROVEEDOR",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Proveedores",
        detalles=f"Proveedor actualizado: ID {proveedor_id} ({proveedor.nombre})",
        registro_id=proveedor_id,
        valores_anteriores=antes,
        valores_nuevos=BitacoraService.foto(proveedor)
    )
    return proveedor


@router.delete("/proveedores/{proveedor_id}", name="delete_proveedor")
async def eliminar_proveedor(
    proveedor_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina un proveedor."""
    await catalogo_services.ProveedorService.delete(db, proveedor_id)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ELIMINAR_PROVEEDOR",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Proveedores",
        detalles=f"Proveedor eliminado: ID {proveedor_id}"
    )
    return {"message": "Proveedor eliminado exitosamente"}


# ============================================
# Endpoints de Recepción por Proveedor (Opción A)
# ============================================

@router.post("/recepciones/", response_model=RecepcionResponse, name="create_recepcion")
async def crear_recepcion(
    datos: RecepcionCreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador", "Encargado")),
    db: AsyncSession = Depends(get_db)
):
    """Registra un ingreso de mercadería vinculado a proveedor y sucursal (suma stock + movimiento RECEPCION)."""
    if await _es_encargado(current_user):
        suc_staff = await _obtener_sucursal_staff(db, current_user)
        if suc_staff and suc_staff != datos.sucursal_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tiene permisos para recibir mercadería en otra sucursal"
            )
    rec = await catalogo_services.RecepcionService.create(db, datos, current_user.id)
    await BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_RECEPCION",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Recepciones",
        detalles=f"Recepción {rec.numero} prov.ID {rec.proveedor_id} suc.ID {rec.sucursal_id} total {rec.total_unidades} uds.",
        registro_id=rec.id,
    )
    return rec


@router.get("/recepciones/", response_model=List[RecepcionResponse], name="list_recepciones")
async def listar_recepciones(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    proveedor_id: Optional[int] = None,
    sucursal_id: Optional[int] = None,
    current_user: Usuario = Depends(require_role("Administrador", "Encargado")),
    db: AsyncSession = Depends(get_db)
):
    """Lista recepciones, filtrables por proveedor y sucursal."""
    if await _es_encargado(current_user):
        suc_staff = await _obtener_sucursal_staff(db, current_user)
        if suc_staff:
            sucursal_id = suc_staff
    return await catalogo_services.RecepcionService.list(db, skip, limit, proveedor_id, sucursal_id)


@router.get("/recepciones/{recepcion_id}", response_model=RecepcionResponse, name="get_recepcion")
async def obtener_recepcion(
    recepcion_id: int,
    current_user: Usuario = Depends(require_role("Administrador", "Encargado")),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene una recepción por ID."""
    rec = await catalogo_services.RecepcionService.get(db, recepcion_id)
    if await _es_encargado(current_user):
        suc_staff = await _obtener_sucursal_staff(db, current_user)
        if suc_staff and suc_staff != rec.sucursal_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tiene permisos para ver recepciones de otra sucursal"
            )
    return rec


@router.get("/proveedores/{proveedor_id}/recepciones", response_model=List[RecepcionResponse], name="recepciones_por_proveedor")
async def recepciones_por_proveedor(
    proveedor_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: Usuario = Depends(require_role("Administrador", "Encargado")),
    db: AsyncSession = Depends(get_db)
):
    """Historial de ingresos de un proveedor (trazabilidad RF06)."""
    return await catalogo_services.RecepcionService.list(db, skip, limit, proveedor_id=proveedor_id)


# ============================================
# Endpoints de Producto
# ============================================

@router.get("/productos/", response_model=List[ProductoResponse], name="list_productos")
async def listar_productos(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=1000),
    categoria_id: Optional[int] = None,
    estado: Optional[str] = None,
    genero: Optional[str] = None,
    temporada_id: Optional[int] = None,
    proveedor_id: Optional[int] = None,
    buscar: Optional[str] = None,
    current_user: Usuario = Depends(require_role("Administrador", "Encargado", "Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Lista todos los productos."""
    productos, total = await catalogo_services.ProductoService.get_all(
        db, skip, limit, categoria_id, estado, genero, temporada_id, proveedor_id, buscar
    )
    return productos


@router.post("/productos/", response_model=ProductoResponse, name="create_producto")
async def crear_producto(
    datos: ProductoCreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea un nuevo producto."""
    producto = await catalogo_services.ProductoService.create(db, datos, datos.stock_por_sucursal)
    await BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_PRODUCTO",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Productos",
        detalles=f"Producto creado: {producto.nombre} (ID: {producto.id}, SKU: {producto.sku})"
    )
    return producto


@router.get("/productos/{producto_id}", response_model=ProductoDetalleResponse, name="get_producto")
async def obtener_producto(
    producto_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene un producto por su ID."""
    return await catalogo_services.ProductoService.get(db, producto_id)


@router.put("/productos/{producto_id}", response_model=ProductoResponse, name="update_producto")
async def actualizar_producto(
    producto_id: int,
    datos: ProductoUpdate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza un producto."""
    antes = BitacoraService.foto(await catalogo_services.ProductoService.get(db, producto_id))
    producto = await catalogo_services.ProductoService.update(db, producto_id, datos)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_PRODUCTO",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Productos",
        detalles=f"Producto actualizado: ID {producto_id} ({producto.nombre})",
        registro_id=producto_id,
        valores_anteriores=antes,
        valores_nuevos=BitacoraService.foto(producto)
    )
    return producto


@router.delete("/productos/{producto_id}", name="delete_producto")
async def eliminar_producto(
    producto_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina un producto."""
    await catalogo_services.ProductoService.delete(db, producto_id)
    await BitacoraService.registrar_evento(
        db=db,
        accion="ELIMINAR_PRODUCTO",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Productos",
        detalles=f"Producto eliminado: ID {producto_id}"
    )
    return {"message": "Producto eliminado exitosamente"}


# ============================================
# Endpoints de Variante de Producto
# ============================================

@router.get("/productos/{producto_id}/variantes", response_model=List[VarianteProductoDetalleResponse], name="list_variantes")
async def listar_variantes(
    producto_id: int,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Lista las variantes de un producto."""
    query = (
        select(VarianteProducto)
        .options(
            selectinload(VarianteProducto.talla),
            selectinload(VarianteProducto.color),
            selectinload(VarianteProducto.producto)
        )
        .where(VarianteProducto.producto_id == producto_id)
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/productos/{producto_id}/variantes", response_model=VarianteProductoResponse, name="create_variante")
async def crear_variante(
    producto_id: int,
    datos: AgregarVarianteRequest,
    request: Request,
    cantidad_inicial: int = Query(0, ge=0),
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Agrega una variante a un producto."""
    variante = await catalogo_services.ProductoService.agregar_variante(
        db, producto_id, datos, cantidad_inicial
    )
    await BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_VARIANTE",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Productos",
        detalles=f"Variante creada para producto ID {producto_id}: SKU {variante.sku_variante} (ID: {variante.id}, Stock inicial: {cantidad_inicial})"
    )
    return variante


# ============================================
# Endpoints de Inventario
# ============================================

async def _obtener_sucursal_staff(db: AsyncSession, usuario: Usuario) -> Optional[int]:
    from app.apps.gestion_usuarios.models import EncargadoSucursal, Cajero
    q_enc = select(EncargadoSucursal.sucursal_id).where(EncargadoSucursal.usuario_id == usuario.id)
    res_enc = await db.execute(q_enc)
    suc_id = res_enc.scalar_one_or_none()
    if suc_id:
        return suc_id
    q_caj = select(Cajero.sucursal_id).where(Cajero.usuario_id == usuario.id)
    res_caj = await db.execute(q_caj)
    return res_caj.scalar_one_or_none()


async def _es_encargado(usuario: Usuario) -> bool:
    roles = [r.nombre.lower() for r in (usuario.roles or [])]
    return "administrador" not in roles and any(r in ("encargado", "encargado de sucursal") for r in roles)


@router.get("/inventario/", response_model=List[InventarioDetalleResponse], name="list_inventario")
async def listar_inventario(
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=1000),
    sucursal_id: Optional[int] = None,
    producto_id: Optional[int] = None,
    estado: Optional[str] = None,
    current_user: Usuario = Depends(require_role("Administrador", "Encargado")),
    db: AsyncSession = Depends(get_db)
):
    """Lista el inventario global o el inventario de la sucursal asignada si es Encargado."""
    user_roles = [r.nombre.lower() for r in current_user.roles]
    if "administrador" not in user_roles and any(r in ["encargado", "encargado de sucursal"] for r in user_roles):
        suc_staff = await _obtener_sucursal_staff(db, current_user)
        if not suc_staff:
            return []
        sucursal_id = suc_staff

    inventarios, total = await catalogo_services.InventarioService.get_inventario_global(
        db, skip, limit, sucursal_id, producto_id, estado
    )
    return inventarios


@router.get("/inventario/sucursal/{sucursal_id}", response_model=List[InventarioDetalleResponse], name="inventario_sucursal")
async def inventario_por_sucursal(
    sucursal_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=1000),
    current_user: Usuario = Depends(require_role("Administrador", "Encargado")),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene el inventario de una sucursal específica."""
    user_roles = [r.nombre.lower() for r in current_user.roles]
    if "administrador" not in user_roles and any(r in ["encargado", "encargado de sucursal"] for r in user_roles):
        suc_staff = await _obtener_sucursal_staff(db, current_user)
        if suc_staff != sucursal_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tiene permisos para acceder al inventario de otra sucursal"
            )

    inventarios, total = await catalogo_services.InventarioService.get_inventario_sucursal(
        db, sucursal_id, skip, limit
    )
    return inventarios


@router.put("/inventario/{inventario_id}", response_model=InventarioResponse, name="update_inventario")
async def actualizar_inventario(
    inventario_id: int,
    datos: InventarioUpdate,
    request: Request,
    motivo: str = Query(..., description="Motivo del ajuste"),
    current_user: Usuario = Depends(require_role("Administrador", "Encargado")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza la cantidad de inventario."""
    user_roles = [r.nombre.lower() for r in current_user.roles]
    if "administrador" not in user_roles and any(r in ["encargado", "encargado de sucursal"] for r in user_roles):
        suc_staff = await _obtener_sucursal_staff(db, current_user)
        inv_check = await catalogo_services.InventarioService.get_inventario_por_id(db, inventario_id)
        if not inv_check or inv_check.sucursal_id != suc_staff:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tiene permisos para ajustar el inventario de otra sucursal"
            )

    inv_antes = await catalogo_services.InventarioService.get_inventario_por_id(db, inventario_id)
    antes = BitacoraService.foto(inv_antes)
    inv = await catalogo_services.InventarioService.update_cantidad(
        db, inventario_id, datos.cantidad, current_user.id, motivo
    )
    await BitacoraService.registrar_evento(
        db=db,
        accion="AJUSTE_INVENTARIO",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Inventario",
        detalles=f"Inventario ID {inventario_id} ajustado a cantidad {datos.cantidad}. Motivo: {motivo}",
        registro_id=inventario_id,
        valores_anteriores=antes,
        valores_nuevos=BitacoraService.foto(inv)
    )
    return inv


# ============================================
# Endpoints de Movimiento de Inventario
# ============================================

@router.post("/inventario/movimientos", response_model=MovimientoInventarioResponse, name="create_movimiento")
async def crear_movimiento(
    datos: MovimientoInventarioCreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador", "Encargado")),
    db: AsyncSession = Depends(get_db)
):
    """Registra un movimiento de inventario."""
    user_roles = [r.nombre.lower() for r in current_user.roles]
    if "administrador" not in user_roles and any(r in ["encargado", "encargado de sucursal"] for r in user_roles):
        suc_staff = await _obtener_sucursal_staff(db, current_user)
        inv_check = await catalogo_services.InventarioService.get_inventario_por_id(db, datos.inventario_id)
        if not inv_check or inv_check.sucursal_id != suc_staff:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tiene permisos para registrar movimientos en otra sucursal"
            )

    mov = await catalogo_services.InventarioService.registrar_movimiento(db, datos, current_user.id)
    await BitacoraService.registrar_evento(
        db=db,
        accion="MOVIMIENTO_INVENTARIO",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Inventario",
        detalles=f"Movimiento inventario: Tipo {datos.tipo}, Cantidad {datos.cantidad}, Inventario ID {datos.inventario_id}, Motivo: {datos.motivo}"
    )
    return mov


@router.get("/inventario/movimientos", response_model=List[MovimientoInventarioResponse], name="list_movimientos")
async def listar_movimientos(
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    inventario_id: Optional[int] = None,
    sucursal_id: Optional[int] = None,
    tipo: Optional[str] = None,
    current_user: Usuario = Depends(require_role("Administrador", "Encargado")),
    db: AsyncSession = Depends(get_db)
):
    """Lista los movimientos de inventario."""
    from app.apps.gestion_catalogo.models import TipoMovimiento
    
    user_roles = [r.nombre.lower() for r in current_user.roles]
    if "administrador" not in user_roles and any(r in ["encargado", "encargado de sucursal"] for r in user_roles):
        suc_staff = await _obtener_sucursal_staff(db, current_user)
        if not suc_staff:
            return []
        sucursal_id = suc_staff

    tipo_enum = TipoMovimiento[tipo] if tipo else None
    movimientos, total = await catalogo_services.InventarioService.get_movimientos(
        db, skip, limit, inventario_id, sucursal_id, tipo_enum
    )
    return movimientos


# ============================================
# Endpoints de Catálogo y Disponibilidad (Públicos)
# ============================================

@router.get("/public/catalogo", response_model=ProductoBusquedaResponse, name="public_catalogo")
async def catalogo_publico(
    q: Optional[str] = None,
    genero: Optional[str] = Query(None, description="HOMBRE, MUJER, UNISEX"),
    categoria_id: Optional[int] = None,
    temporada_id: Optional[int] = None,
    coleccion_id: Optional[int] = None,
    talla_id: Optional[int] = None,
    color_id: Optional[int] = None,
    precio_min: Optional[Decimal] = None,
    precio_max: Optional[Decimal] = None,
    ordenar_por: Optional[str] = Query(None, description="precio_asc, precio_desc, nombre, fecha"),
    pagina: int = Query(1, ge=1),
    limite: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene el catálogo público de productos con filtros y paginación."""
    filtros = ProductoFilter(
        q=q,
        genero=genero,
        categoria_id=categoria_id,
        temporada_id=temporada_id,
        coleccion_id=coleccion_id,
        talla_id=talla_id,
        color_id=color_id,
        precio_min=precio_min,
        precio_max=precio_max,
        ordenar_por=ordenar_por,
        pagina=pagina,
        limite=limite
    )
    productos, total = await catalogo_services.ProductoService.search_productos_publicos(db, filtros)
    total_paginas = (total + limite - 1) // limite if total > 0 else 1
    return {
        "items": productos,
        "total": total,
        "pagina": pagina,
        "total_paginas": total_paginas
    }


@router.get("/public/productos/buscar", response_model=ProductoBusquedaResponse, name="public_buscar_productos")
async def buscar_productos_publico(
    q: Optional[str] = None,
    genero: Optional[str] = Query(None, description="HOMBRE, MUJER, UNISEX"),
    categoria_id: Optional[int] = None,
    temporada_id: Optional[int] = None,
    coleccion_id: Optional[int] = None,
    talla_id: Optional[int] = None,
    color_id: Optional[int] = None,
    precio_min: Optional[Decimal] = None,
    precio_max: Optional[Decimal] = None,
    ordenar_por: Optional[str] = None,
    pagina: int = Query(1, ge=1),
    limite: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """Búsqueda avanzada de productos en el catálogo público."""
    filtros = ProductoFilter(
        q=q,
        genero=genero,
        categoria_id=categoria_id,
        temporada_id=temporada_id,
        coleccion_id=coleccion_id,
        talla_id=talla_id,
        color_id=color_id,
        precio_min=precio_min,
        precio_max=precio_max,
        ordenar_por=ordenar_por,
        pagina=pagina,
        limite=limite
    )
    productos, total = await catalogo_services.ProductoService.search_productos_publicos(db, filtros)
    total_paginas = (total + limite - 1) // limite if total > 0 else 1
    return {
        "items": productos,
        "total": total,
        "pagina": pagina,
        "total_paginas": total_paginas
    }


@router.get("/public/productos/populares", response_model=List[ProductoDetalleResponse], name="public_productos_populares")
async def productos_populares_publico(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene los productos destacados / populares del catálogo."""
    return await catalogo_services.ProductoService.get_productos_populares(db, limit)


@router.get("/public/productos/{producto_id}", response_model=ProductoDetalleResponse, name="public_detalle_producto")
async def detalle_producto_publico(
    producto_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Obtiene los detalles públicos de un producto específico."""
    return await catalogo_services.ProductoService.get(db, producto_id)


@router.get("/public/productos/{producto_id}/relacionados", response_model=List[ProductoDetalleResponse], name="public_productos_relacionados")
async def productos_relacionados_publico(
    producto_id: int,
    limit: int = Query(6, ge=1, le=20),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene productos relacionados de la misma categoría/temporada."""
    return await catalogo_services.ProductoService.get_productos_relacionados(db, producto_id, limit)


@router.get("/public/disponibilidad/{producto_id}", response_model=DisponibilidadProductoResponse, name="disponibilidad_producto")
async def disponibilidad_producto(
    producto_id: int,
    talla_id: Optional[int] = None,
    color_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    """Obtiene la disponibilidad de un producto por sucursal (público)."""
    return await catalogo_services.DisponibilidadService.get_disponibilidad_producto(
        db, producto_id, talla_id, color_id
    )


@router.get("/public/disponibilidad/{producto_id}/sucursal/{sucursal_id}", name="disponibilidad_producto_sucursal")
async def disponibilidad_producto_por_sucursal(
    producto_id: int,
    sucursal_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Obtiene la disponibilidad de todas las variantes de un producto en una sucursal específica."""
    return await catalogo_services.DisponibilidadService.get_stock_por_sucursal(
        db, producto_id, sucursal_id
    )


@router.get("/public/stock/{variante_id}", name="disponibilidad_variante_todas_sucursales")
async def stock_variante_todas_sucursales(
    variante_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Obtiene el stock de una variante específica en todas las sucursales."""
    return await catalogo_services.DisponibilidadService.get_disponibilidad_variante(
        db, variante_id
    )


@router.websocket("/ws/disponibilidad/{producto_id}")
async def websocket_disponibilidad(websocket: WebSocket, producto_id: int):
    """
    WebSocket endpoint para consultar y recibir actualizaciones de disponibilidad
    en tiempo real para un producto según la talla y color seleccionados.
    """
    await manager.connect(websocket, producto_id)
    try:
        # Enviar disponibilidad inicial general
        async with AsyncSessionLocal() as db:
            try:
                data = await catalogo_services.DisponibilidadService.get_disponibilidad_producto(
                    db, producto_id, None, None
                )
                await websocket.send_json(data)
            except Exception as e:
                await websocket.send_json({"error": str(e)})

        while True:
            msg = await websocket.receive_json()
            talla_id = msg.get("talla_id") if isinstance(msg, dict) else None
            color_id = msg.get("color_id") if isinstance(msg, dict) else None
            async with AsyncSessionLocal() as db:
                try:
                    data = await catalogo_services.DisponibilidadService.get_disponibilidad_producto(
                        db, producto_id, talla_id, color_id
                    )
                    await websocket.send_json(data)
                except Exception as e:
                    await websocket.send_json({"error": str(e)})
    except WebSocketDisconnect:
        manager.disconnect(websocket, producto_id)
    except Exception:
        manager.disconnect(websocket, producto_id)


# ============================================
# Endpoints de Alertas de Stock
# ============================================

@router.get("/inventario/alertas", response_model=List[InventarioDetalleResponse], name="alertas_stock")
async def alertas_stock(
    current_user: Usuario = Depends(require_role("Administrador", "Encargado")),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene productos con stock bajo el mínimo."""
    return await catalogo_services.InventarioService.get_alertas_stock(db)


# ============================================
# Endpoints de Carga y Gestión de Archivos (Cloudinary)
# ============================================

@router.post("/upload/imagen", name="subir_imagen")
async def subir_imagen(
    request: Request,
    file: UploadFile = File(...),
    folder: str = Query("fashionstore/productos"),
    current_user: Usuario = Depends(require_role("Administrador", "Encargado")),
    db: AsyncSession = Depends(get_db)
):
    """Sube una imagen a Cloudinary y retorna sus URLs y public_id."""
    try:
        resultado = CloudinaryService.upload_image(file.file, folder=folder)
        await BitacoraService.registrar_evento(
            db=db,
            accion="SUBIR_IMAGEN",
            usuario_id=current_user.id,
            ip_address=get_client_ip(request),
            modulo="Multimedia",
            detalles=f"Imagen subida: {resultado.get('public_id')}"
        )
        return resultado
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al subir imagen a Cloudinary: {str(e)}"
        )


@router.delete("/upload/imagen", name="eliminar_imagen")
async def eliminar_imagen(
    request: Request,
    public_id: str = Query(..., description="ID público de Cloudinary de la imagen"),
    current_user: Usuario = Depends(require_role("Administrador", "Encargado")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina una imagen de Cloudinary por su public_id (limpieza de imágenes huérfanas)."""
    try:
        exito = CloudinaryService.delete_image(public_id)
        if exito:
            await BitacoraService.registrar_evento(
                db=db,
                accion="ELIMINAR_IMAGEN",
                usuario_id=current_user.id,
                ip_address=get_client_ip(request),
                modulo="Multimedia",
                detalles=f"Imagen eliminada de Cloudinary: {public_id}"
            )
        return {
            "success": exito,
            "message": "Imagen eliminada de Cloudinary" if exito else "No se pudo eliminar la imagen",
            "public_id": public_id
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al eliminar imagen de Cloudinary: {str(e)}"
        )


# ============================================
# CU25: Endpoints de Productos Favoritos
# ============================================

@router.get("/favoritos/", response_model=List[FavoritoResponse], name="listar_favoritos", tags=["Favoritos (CU25)"])
async def listar_favoritos(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Lista los productos favoritos del cliente autenticado con estado de disponibilidad."""
    cliente = await obtener_cliente_actual(db, current_user)
    return await catalogo_services.FavoritoService.listar(db, cliente.id, skip=skip, limit=limit)


@router.get("/favoritos/ids", response_model=FavoritoIdsResponse, name="listar_favoritos_ids", tags=["Favoritos (CU25)"])
async def listar_favoritos_ids(
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene únicamente la lista de IDs de productos marcados como favoritos (para catálogo)."""
    cliente = await obtener_cliente_actual(db, current_user)
    ids = await catalogo_services.FavoritoService.listar_ids(db, cliente.id)
    return FavoritoIdsResponse(producto_ids=ids)


@router.post("/favoritos/{producto_id}", status_code=status.HTTP_200_OK, name="agregar_favorito", tags=["Favoritos (CU25)"])
async def agregar_favorito(
    producto_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Agrega un producto a la lista de favoritos del cliente (idempotente)."""
    cliente = await obtener_cliente_actual(db, current_user)
    resultado = await catalogo_services.FavoritoService.agregar(db, cliente.id, producto_id)
    
    await BitacoraService.registrar_evento(
        db=db,
        accion="AGREGAR_FAVORITO",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Favoritos",
        detalles=f"Producto ID {producto_id} agregado a favoritos."
    )
    return resultado


@router.delete("/favoritos/{producto_id}", name="quitar_favorito", tags=["Favoritos (CU25)"])
async def quitar_favorito(
    producto_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Quita un producto de la lista de favoritos del cliente (idempotente)."""
    cliente = await obtener_cliente_actual(db, current_user)
    resultado = await catalogo_services.FavoritoService.quitar(db, cliente.id, producto_id)

    await BitacoraService.registrar_evento(
        db=db,
        accion="QUITAR_FAVORITO",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Favoritos",
        detalles=f"Producto ID {producto_id} quitado de favoritos."
    )
    return resultado


@router.post("/favoritos/{producto_id}/mover-al-carrito", name="mover_favorito_al_carrito", tags=["Favoritos (CU25)"])
async def mover_favorito_al_carrito(
    producto_id: int,
    datos: MoverFavoritoCarritoRequest,
    request: Request,
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Mueve una variante de un producto favorito directamente al carrito de compras."""
    cliente = await obtener_cliente_actual(db, current_user)
    resultado = await catalogo_services.FavoritoService.mover_al_carrito(db, cliente.id, producto_id, datos)

    await BitacoraService.registrar_evento(
        db=db,
        accion="MOVER_FAVORITO_CARRITO",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Favoritos",
        detalles=f"Variante ID {datos.variante_producto_id} del producto ID {producto_id} agregada al carrito desde favoritos."
    )
    return resultado


# ============================================
# CU26: Endpoints de Valoraciones de Producto
# ============================================

@router.get("/productos/{producto_id}/valoraciones", response_model=List[ValoracionResponse], name="listar_valoraciones_producto", tags=["Valoraciones (CU26)"])
async def listar_valoraciones_producto(
    producto_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """Lista las valoraciones públicas aprobadas de un producto."""
    return await catalogo_services.ValoracionService.listar_por_producto(db, producto_id, skip=skip, limit=limit)


@router.get("/productos/{producto_id}/puede-valorar", response_model=PuedeValorarResponse, name="verificar_puede_valorar", tags=["Valoraciones (CU26)"])
async def verificar_puede_valorar(
    producto_id: int,
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Verifica si el cliente actual ha comprado el producto y puede valorarlo (o si ya tiene valoración)."""
    cliente = await obtener_cliente_actual(db, current_user)
    return await catalogo_services.ValoracionService.puede_valorar(db, cliente.id, producto_id)


@router.get("/productos/{producto_id}/mi-valoracion", response_model=Optional[ValoracionResponse], name="obtener_mi_valoracion", tags=["Valoraciones (CU26)"])
async def obtener_mi_valoracion(
    producto_id: int,
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene la valoración propia del cliente para un producto."""
    cliente = await obtener_cliente_actual(db, current_user)
    return await catalogo_services.ValoracionService.obtener_mi_valoracion(db, cliente.id, producto_id)


@router.post("/productos/{producto_id}/valoraciones", response_model=ValoracionResponse, status_code=status.HTTP_201_CREATED, name="crear_valoracion", tags=["Valoraciones (CU26)"])
async def crear_valoracion(
    producto_id: int,
    datos: ValoracionCreate,
    request: Request,
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Registra una nueva valoración con puntuación de 1 a 5 y comentario."""
    cliente = await obtener_cliente_actual(db, current_user)
    resultado = await catalogo_services.ValoracionService.crear(db, cliente.id, producto_id, datos)

    await BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_VALORACION",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Valoraciones",
        detalles=f"Valoración creada para producto ID {producto_id} con puntuación {datos.puntuacion}★."
    )
    return resultado


@router.put("/valoraciones/{valoracion_id}", response_model=ValoracionResponse, name="editar_valoracion", tags=["Valoraciones (CU26)"])
async def editar_valoracion(
    valoracion_id: int,
    datos: ValoracionUpdate,
    request: Request,
    current_user: Usuario = Depends(require_role("Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Permite al autor editar su propia valoración existente."""
    cliente = await obtener_cliente_actual(db, current_user)
    resultado = await catalogo_services.ValoracionService.actualizar(db, valoracion_id, cliente.id, datos)

    await BitacoraService.registrar_evento(
        db=db,
        accion="EDITAR_VALORACION",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Valoraciones",
        detalles=f"Valoración ID {valoracion_id} actualizada."
    )
    return resultado


# ============================================
# Función para incluir las rutas en la app principal
# ============================================

def include_router(app):
    """Incluye las rutas en la aplicación principal."""
    app.include_router(router)


"""
Router - Gestión de Catálogo, Productos e Inventario
Contiene todos los endpoints de la App 2.
"""
from fastapi import APIRouter, Depends, Query, UploadFile, File, HTTPException, status, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List, Optional
from datetime import datetime
from decimal import Decimal

from app.database import get_db, AsyncSessionLocal
from app.security import get_current_user, require_role
from app.apps.gestion_usuarios.models import Usuario
from app.services.cloudinary_service import CloudinaryService
from app.services.websocket_manager import manager
from app.apps.gestion_catalogo import services as catalogo_services
from app.apps.gestion_catalogo.schemas import (
    # Ciudad
    CiudadCreate, CiudadUpdate, CiudadResponse,
    # Sucursal
    SucursalCreate, SucursalUpdate, SucursalResponse, SucursalConCiudadResponse,
    # Categoría
    CategoriaCreate, CategoriaUpdate, CategoriaResponse,
    # Talla y Color
    TallaCreate, TallaResponse, ColorCreate, ColorResponse,
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
)

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
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea una nueva ciudad."""
    return await catalogo_services.CiudadService.create(db, datos)


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
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza una ciudad."""
    return await catalogo_services.CiudadService.update(db, ciudad_id, datos)


@router.delete("/ciudades/{ciudad_id}", name="delete_ciudad")
async def eliminar_ciudad(
    ciudad_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina una ciudad."""
    await catalogo_services.CiudadService.delete(db, ciudad_id)
    return {"message": "Ciudad eliminada exitosamente"}


# ============================================
# Endpoints de Sucursal
# ============================================

@router.get("/sucursales/", response_model=List[SucursalConCiudadResponse], name="list_sucursales")
async def listar_sucursales(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
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
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea una nueva sucursal."""
    return await catalogo_services.SucursalService.create(db, datos)


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
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza una sucursal."""
    return await catalogo_services.SucursalService.update(db, sucursal_id, datos)


@router.delete("/sucursales/{sucursal_id}", name="delete_sucursal")
async def eliminar_sucursal(
    sucursal_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina una sucursal."""
    await catalogo_services.SucursalService.delete(db, sucursal_id)
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
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea una nueva categoría."""
    return await catalogo_services.CategoriaService.create(db, datos)


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
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza una categoría."""
    return await catalogo_services.CategoriaService.update(db, categoria_id, datos)


@router.delete("/categorias/{categoria_id}", name="delete_categoria")
async def eliminar_categoria(
    categoria_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina una categoría."""
    await catalogo_services.CategoriaService.delete(db, categoria_id)
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
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea una nueva talla."""
    return await catalogo_services.TallaService.create(db, datos)


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
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea un nuevo color."""
    return await catalogo_services.ColorService.create(db, datos)


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
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea una nueva temporada."""
    return await catalogo_services.TemporadaService.create(db, datos)


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
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza una temporada."""
    return await catalogo_services.TemporadaService.update(db, temporada_id, datos)


@router.delete("/temporadas/{temporada_id}", name="delete_temporada")
async def eliminar_temporada(
    temporada_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina una temporada."""
    await catalogo_services.TemporadaService.delete(db, temporada_id)
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
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea una nueva colección."""
    return await catalogo_services.ColeccionService.create(db, datos)


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
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza una colección."""
    return await catalogo_services.ColeccionService.update(db, coleccion_id, datos)


@router.delete("/colecciones/{coleccion_id}", name="delete_coleccion")
async def eliminar_coleccion(
    coleccion_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina una colección."""
    await catalogo_services.ColeccionService.delete(db, coleccion_id)
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
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea un nuevo proveedor."""
    return await catalogo_services.ProveedorService.create(db, datos)


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
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza un proveedor."""
    return await catalogo_services.ProveedorService.update(db, proveedor_id, datos)


@router.delete("/proveedores/{proveedor_id}", name="delete_proveedor")
async def eliminar_proveedor(
    proveedor_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina un proveedor."""
    await catalogo_services.ProveedorService.delete(db, proveedor_id)
    return {"message": "Proveedor eliminado exitosamente"}


# ============================================
# Endpoints de Producto
# ============================================

@router.get("/productos/", response_model=List[ProductoResponse], name="list_productos")
async def listar_productos(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    categoria_id: Optional[int] = None,
    estado: Optional[str] = None,
    temporada_id: Optional[int] = None,
    proveedor_id: Optional[int] = None,
    buscar: Optional[str] = None,
    current_user: Usuario = Depends(require_role("Administrador", "Encargado", "Cliente")),
    db: AsyncSession = Depends(get_db)
):
    """Lista todos los productos."""
    productos, total = await catalogo_services.ProductoService.get_all(
        db, skip, limit, categoria_id, estado, temporada_id, proveedor_id, buscar
    )
    return productos


@router.post("/productos/", response_model=ProductoResponse, name="create_producto")
async def crear_producto(
    datos: ProductoCreate,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea un nuevo producto."""
    return await catalogo_services.ProductoService.create(db, datos, datos.stock_por_sucursal)


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
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza un producto."""
    return await catalogo_services.ProductoService.update(db, producto_id, datos)


@router.delete("/productos/{producto_id}", name="delete_producto")
async def eliminar_producto(
    producto_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina un producto."""
    await catalogo_services.ProductoService.delete(db, producto_id)
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
    producto = await catalogo_services.ProductoService.get(db, producto_id)
    return producto.variantes


@router.post("/productos/{producto_id}/variantes", response_model=VarianteProductoResponse, name="create_variante")
async def crear_variante(
    producto_id: int,
    datos: AgregarVarianteRequest,
    cantidad_inicial: int = Query(0, ge=0),
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Agrega una variante a un producto."""
    return await catalogo_services.ProductoService.agregar_variante(
        db, producto_id, datos, cantidad_inicial
    )


# ============================================
# Endpoints de Inventario
# ============================================

@router.get("/inventario/", response_model=List[InventarioDetalleResponse], name="list_inventario")
async def listar_inventario(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    sucursal_id: Optional[int] = None,
    producto_id: Optional[int] = None,
    estado: Optional[str] = None,
    current_user: Usuario = Depends(require_role("Administrador", "Encargado")),
    db: AsyncSession = Depends(get_db)
):
    """Lista el inventario global."""
    inventarios, total = await catalogo_services.InventarioService.get_inventario_global(
        db, skip, limit, sucursal_id, producto_id, estado
    )
    return inventarios


@router.get("/inventario/sucursal/{sucursal_id}", response_model=List[InventarioDetalleResponse], name="inventario_sucursal")
async def inventario_por_sucursal(
    sucursal_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    current_user: Usuario = Depends(require_role("Administrador", "Encargado")),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene el inventario de una sucursal específica."""
    inventarios, total = await catalogo_services.InventarioService.get_inventario_sucursal(
        db, sucursal_id, skip, limit
    )
    return inventarios


@router.put("/inventario/{inventario_id}", response_model=InventarioResponse, name="update_inventario")
async def actualizar_inventario(
    inventario_id: int,
    datos: InventarioUpdate,
    motivo: str = Query(..., description="Motivo del ajuste"),
    current_user: Usuario = Depends(require_role("Administrador", "Encargado")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza la cantidad de inventario."""
    return await catalogo_services.InventarioService.update_cantidad(
        db, inventario_id, datos.cantidad, current_user.id, motivo
    )


# ============================================
# Endpoints de Movimiento de Inventario
# ============================================

@router.post("/inventario/movimientos", response_model=MovimientoInventarioResponse, name="create_movimiento")
async def crear_movimiento(
    datos: MovimientoInventarioCreate,
    current_user: Usuario = Depends(require_role("Administrador", "Encargado")),
    db: AsyncSession = Depends(get_db)
):
    """Registra un movimiento de inventario."""
    return await catalogo_services.InventarioService.registrar_movimiento(db, datos, current_user.id)


@router.get("/inventario/movimientos", response_model=List[MovimientoInventarioResponse], name="list_movimientos")
async def listar_movimientos(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    inventario_id: Optional[int] = None,
    tipo: Optional[str] = None,
    current_user: Usuario = Depends(require_role("Administrador", "Encargado")),
    db: AsyncSession = Depends(get_db)
):
    """Lista los movimientos de inventario."""
    from app.apps.gestion_catalogo.models import TipoMovimiento
    
    tipo_enum = TipoMovimiento[tipo] if tipo else None
    movimientos, total = await catalogo_services.InventarioService.get_movimientos(
        db, skip, limit, inventario_id, tipo_enum
    )
    return movimientos


# ============================================
# Endpoints de Catálogo y Disponibilidad (Públicos)
# ============================================

@router.get("/public/catalogo", response_model=ProductoBusquedaResponse, name="public_catalogo")
async def catalogo_publico(
    q: Optional[str] = None,
    categoria_id: Optional[int] = None,
    temporada_id: Optional[int] = None,
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
        categoria_id=categoria_id,
        temporada_id=temporada_id,
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
    categoria_id: Optional[int] = None,
    temporada_id: Optional[int] = None,
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
        categoria_id=categoria_id,
        temporada_id=temporada_id,
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
    file: UploadFile = File(...),
    folder: str = Query("fashionstore/productos"),
    current_user: Usuario = Depends(require_role("Administrador", "Encargado"))
):
    """Sube una imagen a Cloudinary y retorna sus URLs y public_id."""
    try:
        resultado = CloudinaryService.upload_image(file.file, folder=folder)
        return resultado
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al subir imagen a Cloudinary: {str(e)}"
        )


@router.delete("/upload/imagen", name="eliminar_imagen")
async def eliminar_imagen(
    public_id: str = Query(..., description="ID público de Cloudinary de la imagen"),
    current_user: Usuario = Depends(require_role("Administrador", "Encargado"))
):
    """Elimina una imagen de Cloudinary por su public_id (limpieza de imágenes huérfanas)."""
    try:
        exito = CloudinaryService.delete_image(public_id)
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
# Función para incluir las rutas en la app principal
# ============================================

def include_router(app):
    """Incluye las rutas en la aplicación principal."""
    app.include_router(router)
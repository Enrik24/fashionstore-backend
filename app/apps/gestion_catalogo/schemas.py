"""
Schemas Pydantic para validación y serialización - Gestión de Catálogo, Productos e Inventario.
"""
from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional, List, Any
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
import json


def _parse_imagenes(v: Any) -> List[str]:
    """La columna JSON a veces guarda un string serializado '["url"]'. Normalizar a lista."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
            return [s]
        except (ValueError, TypeError):
            return [s]
    return []


# ============================================
# Enums
# ============================================
class EstadoSucursalEnum(str, Enum):
    ACTIVO = "ACTIVO"
    INACTIVO = "INACTIVO"


class EstadoProductoEnum(str, Enum):
    ACTIVO = "ACTIVO"
    INACTIVO = "INACTIVO"
    AGOTADO = "AGOTADO"
    PROXIMO_INGRESO = "PROXIMO_INGRESO"


class GeneroProductoEnum(str, Enum):
    HOMBRE = "HOMBRE"
    MUJER = "MUJER"
    UNISEX = "UNISEX"


class EstadoStockEnum(str, Enum):
    DISPONIBLE = "DISPONIBLE"
    RESERVADO = "RESERVADO"
    VENDIDO = "VENDIDO"
    AGOTADO = "AGOTADO"
    PROXIMO_A_INGRESAR = "PROXIMO_A_INGRESAR"


class TipoMovimientoEnum(str, Enum):
    RECEPCION = "RECEPCION"
    VENTA = "VENTA"
    RESERVA = "RESERVA"
    DEVOLUCION = "DEVOLUCION"
    AJUSTE = "AJUSTE"
    CANCELACION_RESERVA = "CANCELACION_RESERVA"


# ============================================
# Schemas de Ciudad
# ============================================
class CiudadBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    codigo_postal: Optional[str] = Field(None, max_length=20)
    pais: Optional[str] = Field("Guatemala", max_length=100)


class CiudadCreate(CiudadBase):
    pass


class CiudadUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=1, max_length=100)
    codigo_postal: Optional[str] = Field(None, max_length=20)
    pais: Optional[str] = Field(None, max_length=100)


class CiudadResponse(CiudadBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Sucursal
# ============================================
class SucursalBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    direccion: str = Field(..., min_length=1, max_length=500)
    telefono: str = Field(..., min_length=1, max_length=20)
    horario_atencion: Optional[str] = Field(None, max_length=200)
    estado: Optional[EstadoSucursalEnum] = EstadoSucursalEnum.ACTIVO
    latitud: Optional[float] = None
    longitud: Optional[float] = None
    ciudad_id: int


class SucursalCreate(SucursalBase):
    pass


class SucursalUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=1, max_length=100)
    direccion: Optional[str] = Field(None, min_length=1, max_length=500)
    telefono: Optional[str] = Field(None, min_length=1, max_length=20)
    horario_atencion: Optional[str] = Field(None, max_length=200)
    estado: Optional[EstadoSucursalEnum] = None
    latitud: Optional[float] = None
    longitud: Optional[float] = None
    ciudad_id: Optional[int] = None


class SucursalResponse(SucursalBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)


class SucursalConCiudadResponse(SucursalResponse):
    ciudad: CiudadResponse
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Categoría
# ============================================
class CategoriaBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    descripcion: Optional[str] = None
    imagen: Optional[str] = Field(None, max_length=500)


class CategoriaCreate(CategoriaBase):
    pass


class CategoriaUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=1, max_length=100)
    descripcion: Optional[str] = None
    imagen: Optional[str] = Field(None, max_length=500)


class CategoriaResponse(CategoriaBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Talla
# ============================================
class TallaBase(BaseModel):
    valor: str = Field(..., min_length=1, max_length=10)
    tipo: Optional[str] = Field(None, max_length=50)
    descripcion: Optional[str] = Field(None, max_length=100)


class TallaCreate(TallaBase):
    pass


class TallaUpdate(BaseModel):
    valor: Optional[str] = Field(None, min_length=1, max_length=10)
    tipo: Optional[str] = Field(None, max_length=50)
    descripcion: Optional[str] = Field(None, max_length=100)


class TallaResponse(TallaBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Color
# ============================================
class ColorBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=50)
    codigo_hex: Optional[str] = Field(None, max_length=7)
    imagen_muestra: Optional[str] = Field(None, max_length=500)


class ColorCreate(ColorBase):
    pass


class ColorUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=1, max_length=50)
    codigo_hex: Optional[str] = Field(None, max_length=7)
    imagen_muestra: Optional[str] = Field(None, max_length=500)


class ColorResponse(ColorBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Temporada
# ============================================
class TemporadaBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    fecha_inicio: date
    fecha_fin: date
    descripcion: Optional[str] = None


class TemporadaCreate(TemporadaBase):
    pass


class TemporadaUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=1, max_length=100)
    fecha_inicio: Optional[date] = None
    fecha_fin: Optional[date] = None
    descripcion: Optional[str] = None


class TemporadaResponse(TemporadaBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Colección
# ============================================
class ColeccionBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    descripcion: Optional[str] = None
    temporada_id: Optional[int] = None


class ColeccionCreate(ColeccionBase):
    pass


class ColeccionUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=1, max_length=100)
    descripcion: Optional[str] = None
    temporada_id: Optional[int] = None


class ColeccionResponse(ColeccionBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Proveedor
# ============================================
class ProveedorBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=200)
    nit: str = Field(..., min_length=1, max_length=20)
    contacto: Optional[str] = Field(None, max_length=100)
    telefono: Optional[str] = Field(None, max_length=20)
    correo: Optional[str] = Field(None, max_length=255)
    direccion: Optional[str] = Field(None, max_length=500)


class ProveedorCreate(ProveedorBase):
    pass


class ProveedorUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=1, max_length=200)
    nit: Optional[str] = Field(None, min_length=1, max_length=20)
    contacto: Optional[str] = Field(None, max_length=100)
    telefono: Optional[str] = Field(None, max_length=20)
    correo: Optional[str] = Field(None, max_length=255)
    direccion: Optional[str] = Field(None, max_length=500)


class ProveedorResponse(ProveedorBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Producto
# ============================================
class ProductoBase(BaseModel):
    sku: str = Field(..., min_length=1, max_length=50)
    nombre: str = Field(..., min_length=1, max_length=200)
    descripcion: Optional[str] = None
    precio: Decimal = Field(..., ge=0)
    costo_compra: Decimal = Field(default=Decimal("0"), ge=0)
    imagenes: Optional[List[str]] = []
    estado: Optional[EstadoProductoEnum] = EstadoProductoEnum.ACTIVO
    genero: Optional[GeneroProductoEnum] = GeneroProductoEnum.UNISEX
    promedio_valoracion: Optional[Decimal] = Field(default=Decimal("0.00"))
    total_valoraciones: Optional[int] = Field(default=0)
    categoria_id: Optional[int] = None
    temporada_id: Optional[int] = None
    proveedor_id: Optional[int] = None

    @field_validator("imagenes", mode="before")
    @classmethod
    def _norm_imagenes(cls, v: Any) -> List[str]:
        return _parse_imagenes(v)


class ProductoCreate(ProductoBase):
    stock_por_sucursal: Optional[List["StockPorSucursalRequest"]] = None


class ProductoUpdate(BaseModel):
    sku: Optional[str] = Field(None, min_length=1, max_length=50)
    nombre: Optional[str] = Field(None, min_length=1, max_length=200)
    descripcion: Optional[str] = None
    precio: Optional[Decimal] = Field(None, ge=0)
    costo_compra: Optional[Decimal] = Field(None, ge=0)
    imagenes: Optional[List[str]] = None
    estado: Optional[EstadoProductoEnum] = None
    genero: Optional[GeneroProductoEnum] = None
    categoria_id: Optional[int] = None
    temporada_id: Optional[int] = None
    proveedor_id: Optional[int] = None

    @field_validator("imagenes", mode="before")
    @classmethod
    def _norm_imagenes(cls, v: Any) -> Optional[List[str]]:
        if v is None:
            return None
        return _parse_imagenes(v)


class ProductoResumenResponse(ProductoBase):
    id: int
    fecha_creacion: datetime
    
    model_config = ConfigDict(from_attributes=True)


class ProductoResponse(ProductoBase):
    id: int
    fecha_creacion: datetime
    categoria: Optional[CategoriaResponse] = None
    temporada: Optional[TemporadaResponse] = None
    proveedor: Optional[ProveedorResponse] = None
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Variante de Producto
# ============================================
class VarianteProductoBase(BaseModel):
    producto_id: int
    talla_id: Optional[int] = None
    color_id: int
    sku_variante: str = Field(..., min_length=1, max_length=50)
    precio_variante: Optional[Decimal] = Field(None, ge=0)
    costo_variante: Optional[Decimal] = Field(None, ge=0)


class VarianteProductoCreate(VarianteProductoBase):
    pass


class VarianteProductoUpdate(BaseModel):
    talla_id: Optional[int] = None
    color_id: Optional[int] = None
    sku_variante: Optional[str] = Field(None, min_length=1, max_length=50)
    precio_variante: Optional[Decimal] = Field(None, ge=0)
    costo_variante: Optional[Decimal] = Field(None, ge=0)


class VarianteProductoResponse(VarianteProductoBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)


class VarianteProductoDetalleResponse(VarianteProductoResponse):
    talla: Optional[TallaResponse] = None
    color: Optional[ColorResponse] = None
    producto: Optional[ProductoResumenResponse] = None
    
    model_config = ConfigDict(from_attributes=True)


class ProductoDetalleResponse(ProductoResponse):
    categoria: Optional[CategoriaResponse] = None
    temporada: Optional[TemporadaResponse] = None
    proveedor: Optional[ProveedorResponse] = None
    variantes: List[VarianteProductoDetalleResponse] = []
    colecciones: List[ColeccionResponse] = []
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Búsqueda y Filtros Públicos
# ============================================
class ProductoFilter(BaseModel):
    q: Optional[str] = None
    genero: Optional[GeneroProductoEnum] = None
    categoria_id: Optional[int] = None
    temporada_id: Optional[int] = None
    coleccion_id: Optional[int] = None
    talla_id: Optional[int] = None
    color_id: Optional[int] = None
    precio_min: Optional[Decimal] = None
    precio_max: Optional[Decimal] = None
    ordenar_por: Optional[str] = Field(None, description="precio_asc, precio_desc, nombre, fecha")
    pagina: int = Field(1, ge=1)
    limite: int = Field(20, ge=1, le=100)


class ProductoBusquedaResponse(BaseModel):
    items: List[ProductoDetalleResponse]
    total: int
    pagina: int
    total_paginas: int


# ============================================
# Schemas de Inventario
# ============================================
class InventarioBase(BaseModel):
    variante_producto_id: int
    sucursal_id: int
    cantidad: int = Field(0, ge=0)
    cantidad_reservada: int = Field(0, ge=0)
    cantidad_vendida: int = Field(0, ge=0)
    stock_minimo: int = Field(5, ge=0)
    estado: Optional[EstadoStockEnum] = EstadoStockEnum.DISPONIBLE


class InventarioCreate(InventarioBase):
    pass


class InventarioUpdate(BaseModel):
    cantidad: Optional[int] = Field(None, ge=0)
    cantidad_reservada: Optional[int] = Field(None, ge=0)
    cantidad_vendida: Optional[int] = Field(None, ge=0)
    stock_minimo: Optional[int] = Field(None, ge=0)
    estado: Optional[EstadoStockEnum] = None


class InventarioResponse(InventarioBase):
    id: int
    cantidad_disponible: int
    
    model_config = ConfigDict(from_attributes=True)


class InventarioDetalleResponse(InventarioResponse):
    variante_producto: VarianteProductoDetalleResponse
    sucursal: SucursalResponse
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Movimiento de Inventario
# ============================================
class MovimientoInventarioBase(BaseModel):
    inventario_id: int
    tipo: TipoMovimientoEnum
    cantidad: int = Field(..., ge=1)
    motivo: Optional[str] = Field(None, max_length=500)


class MovimientoInventarioCreate(MovimientoInventarioBase):
    pass


class MovimientoInventarioResponse(MovimientoInventarioBase):
    id: int
    fecha_hora: datetime
    usuario_id: Optional[int] = None
    
    model_config = ConfigDict(from_attributes=True)


class MovimientoInventarioDetalleResponse(MovimientoInventarioResponse):
    inventario: InventarioResponse
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Disponibilidad
# ============================================
class DisponibilidadResponse(BaseModel):
    """Respuesta de disponibilidad por sucursal."""
    sucursal_id: int
    sucursal_nombre: str
    cantidad_disponible: int
    cantidad_reservada: int
    estado: EstadoStockEnum
    latitud: Optional[float] = None
    longitud: Optional[float] = None
    direccion: Optional[str] = None
    horario_atencion: Optional[str] = None


class DisponibilidadProductoResponse(BaseModel):
    """Respuesta de disponibilidad de un producto por sucursal."""
    producto_id: int
    producto_nombre: str
    sku: str
    talla: Optional[str] = None
    color: Optional[str] = None
    disponibilidad: List[DisponibilidadResponse]
    
    model_config = ConfigDict(from_attributes=True)


class StockVarianteSucursalResponse(BaseModel):
    variante_id: int
    sku_variante: str
    sucursales: List[DisponibilidadResponse]


# ============================================
# Schemas adicionales
# ============================================
class StockPorSucursalRequest(BaseModel):
    """Request para crear stock inicial por sucursal."""
    sucursal_id: int
    cantidad: int = Field(0, ge=0)
    talla_id: Optional[int] = None
    color_id: Optional[int] = None
    costo_variante: Optional[Decimal] = Field(None, ge=0)


class AgregarVarianteRequest(BaseModel):
    """Request para agregar una variante a un producto."""
    talla_id: Optional[int] = None
    color_id: int
    sku_variante: str
    precio_variante: Optional[Decimal] = None
    costo_variante: Optional[Decimal] = Field(None, ge=0)


class AsociarColeccionRequest(BaseModel):
    """Request para asociar un producto a una colección."""
    coleccion_id: int


# ============================================
# Schemas de Favoritos (CU25)
# ============================================
class FavoritoResponse(BaseModel):
    id: int
    cliente_id: int
    producto_id: int
    producto: ProductoResumenResponse
    disponible: bool = True
    fecha_agregado: datetime

    model_config = ConfigDict(from_attributes=True)


class FavoritoIdsResponse(BaseModel):
    producto_ids: List[int]


class MoverFavoritoCarritoRequest(BaseModel):
    variante_producto_id: int
    cantidad: int = Field(1, ge=1)
    quitar_de_favoritos: bool = False


# ============================================
# Schemas de Valoraciones (CU26)
# ============================================
class EstadoValoracionEnum(str, Enum):
    PUBLICADA = "PUBLICADA"
    PENDIENTE_MODERACION = "PENDIENTE_MODERACION"
    RECHAZADA = "RECHAZADA"


class ValoracionCreate(BaseModel):
    puntuacion: int = Field(..., ge=1, le=5, description="Puntuación de 1 a 5 estrellas")
    comentario: Optional[str] = Field(None, max_length=1000, description="Opinión o reseña del cliente")


class ValoracionUpdate(BaseModel):
    puntuacion: Optional[int] = Field(None, ge=1, le=5)
    comentario: Optional[str] = Field(None, max_length=1000)


class ValoracionResponse(BaseModel):
    id: int
    producto_id: int
    cliente_id: int
    cliente_nombre: str
    puntuacion: int
    comentario: Optional[str] = None
    estado: EstadoValoracionEnum
    fecha_creacion: datetime
    fecha_actualizacion: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PuedeValorarResponse(BaseModel):
    puede_valorar: bool
    motivo: Optional[str] = None
    valoracion_existente: Optional[ValoracionResponse] = None


# ============================================
# Schemas de Recepción por Proveedor (Opción A)
# ============================================
class RecepcionItemCreate(BaseModel):
    variante_producto_id: int
    cantidad: int = Field(..., ge=1, le=10000)
    costo_unitario: Optional[Decimal] = Field(None, ge=0)


class RecepcionCreate(BaseModel):
    proveedor_id: int
    sucursal_id: int
    nro_factura: Optional[str] = Field(None, max_length=50)
    observaciones: Optional[str] = Field(None, max_length=500)
    items: List[RecepcionItemCreate] = Field(..., min_length=1)


class RecepcionDetalleItemResponse(BaseModel):
    id: int
    variante_producto_id: int
    cantidad: int
    costo_unitario: Optional[Decimal] = None
    sku_variante: Optional[str] = None
    producto_nombre: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class RecepcionResponse(BaseModel):
    id: int
    numero: str
    proveedor_id: int
    sucursal_id: int
    fecha_hora: datetime
    nro_factura: Optional[str] = None
    observaciones: Optional[str] = None
    total_unidades: int
    total_costo: Decimal
    proveedor_nombre: Optional[str] = None
    sucursal_nombre: Optional[str] = None
    detalles: List[RecepcionDetalleItemResponse] = []

    model_config = ConfigDict(from_attributes=True)
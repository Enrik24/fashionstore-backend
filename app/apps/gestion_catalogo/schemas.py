"""
Schemas Pydantic para validación y serialización - Gestión de Catálogo, Productos e Inventario.
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime, date
from decimal import Decimal
from enum import Enum


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
    imagenes: Optional[List[str]] = []
    estado: Optional[EstadoProductoEnum] = EstadoProductoEnum.ACTIVO
    categoria_id: Optional[int] = None
    temporada_id: Optional[int] = None
    proveedor_id: Optional[int] = None


class ProductoCreate(ProductoBase):
    stock_por_sucursal: Optional[List["StockPorSucursalRequest"]] = None


class ProductoUpdate(BaseModel):
    sku: Optional[str] = Field(None, min_length=1, max_length=50)
    nombre: Optional[str] = Field(None, min_length=1, max_length=200)
    descripcion: Optional[str] = None
    precio: Optional[Decimal] = Field(None, ge=0)
    imagenes: Optional[List[str]] = None
    estado: Optional[EstadoProductoEnum] = None
    categoria_id: Optional[int] = None
    temporada_id: Optional[int] = None
    proveedor_id: Optional[int] = None


class ProductoResponse(ProductoBase):
    id: int
    fecha_creacion: datetime
    
    model_config = ConfigDict(from_attributes=True)


class ProductoDetalleResponse(ProductoResponse):
    categoria: Optional[CategoriaResponse] = None
    temporada: Optional[TemporadaResponse] = None
    proveedor: Optional[ProveedorResponse] = None
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Variante de Producto
# ============================================
class VarianteProductoBase(BaseModel):
    producto_id: int
    talla_id: int
    color_id: int
    sku_variante: str = Field(..., min_length=1, max_length=50)
    precio_variante: Optional[Decimal] = Field(None, ge=0)


class VarianteProductoCreate(VarianteProductoBase):
    pass


class VarianteProductoUpdate(BaseModel):
    talla_id: Optional[int] = None
    color_id: Optional[int] = None
    sku_variante: Optional[str] = Field(None, min_length=1, max_length=50)
    precio_variante: Optional[Decimal] = Field(None, ge=0)


class VarianteProductoResponse(VarianteProductoBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)


class VarianteProductoDetalleResponse(VarianteProductoResponse):
    producto: ProductoResponse
    talla: TallaResponse
    color: ColorResponse
    
    model_config = ConfigDict(from_attributes=True)


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


class DisponibilidadProductoResponse(BaseModel):
    """Respuesta de disponibilidad de un producto por sucursal."""
    producto_id: int
    producto_nombre: str
    sku: str
    talla: str
    color: str
    disponibilidad: List[DisponibilidadResponse]
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas adicionales
# ============================================
class StockPorSucursalRequest(BaseModel):
    """Request para crear stock inicial por sucursal."""
    sucursal_id: int
    cantidad: int = Field(0, ge=0)


class AgregarVarianteRequest(BaseModel):
    """Request para agregar una variante a un producto."""
    talla_id: int
    color_id: int
    sku_variante: str
    precio_variante: Optional[Decimal] = None


class AsociarColeccionRequest(BaseModel):
    """Request para asociar un producto a una colección."""
    coleccion_id: int
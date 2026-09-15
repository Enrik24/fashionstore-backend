"""
Esquemas Pydantic para la Gestión de Ventas, Reservas y Pagos.
Contiene validaciones y serializaciones para:
- Cupones
- Carrito de compras
- Órdenes de venta digital y presencial
- Ventas presenciales
- Reservas de prendas
- Pasarelas y transacciones de pago (Stripe / PayPal)
- Comprobantes
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Any
from datetime import datetime, time, date
from decimal import Decimal
from app.apps.gestion_ventas.models import (
    TipoCupon, EstadoCupon, EstadoCarrito, TipoOrden, EstadoOrden,
    MetodoPagoPresencial, EstadoReserva, EstadoDetalleReserva,
    MetodoPagoDigital, EstadoTransaccion, TipoComprobante, EstadoPasarela
)
from app.apps.gestion_catalogo.schemas import ProductoResumenResponse, TallaResponse, ColorResponse


# ==============================================================================
# CUPONES DE DESCUENTO
# ==============================================================================

class CuponBase(BaseModel):
    codigo: str = Field(..., max_length=50, description="Código único del cupón")
    tipo: TipoCupon
    valor: Decimal = Field(..., gt=0, description="Valor del descuento (porcentaje o monto)")
    descripcion: Optional[str] = Field(None, max_length=255)
    fecha_inicio: datetime
    fecha_fin: datetime
    usos_maximos: Optional[int] = Field(None, ge=1)
    monto_minimo: Optional[Decimal] = Field(None, ge=0)
    estado: EstadoCupon = EstadoCupon.ACTIVO


class CuponCreate(CuponBase):
    pass


class CuponUpdate(BaseModel):
    codigo: Optional[str] = Field(None, max_length=50)
    tipo: Optional[TipoCupon] = None
    valor: Optional[Decimal] = Field(None, gt=0)
    descripcion: Optional[str] = None
    fecha_inicio: Optional[datetime] = None
    fecha_fin: Optional[datetime] = None
    usos_maximos: Optional[int] = Field(None, ge=1)
    monto_minimo: Optional[Decimal] = Field(None, ge=0)
    estado: Optional[EstadoCupon] = None


class CuponResponse(CuponBase):
    id: int
    usos_actuales: int
    creado_por_id: Optional[int] = None
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)


class AplicarCuponRequest(BaseModel):
    codigo: str = Field(..., description="Código del cupón a aplicar al carrito")


class CuponValidacionResponse(BaseModel):
    valido: bool
    mensaje: str
    cupon: Optional[CuponResponse] = None
    descuento_calculado: Optional[Decimal] = Decimal("0.00")


# ==============================================================================
# RESUMEN DE VARIANTE PARA ITEMS
# ==============================================================================

class VarianteResumenResponse(BaseModel):
    id: int
    sku_variante: str
    producto_id: int
    precio_variante: Optional[Decimal] = None
    talla_id: Optional[int] = None
    color_id: Optional[int] = None
    producto: Optional[ProductoResumenResponse] = None
    talla: Optional[TallaResponse] = None
    color: Optional[ColorResponse] = None

    model_config = ConfigDict(from_attributes=True)


# ==============================================================================
# CARRITO DE COMPRAS
# ==============================================================================

class ItemCarritoBase(BaseModel):
    variante_producto_id: int
    cantidad: int = Field(1, ge=1)


class ItemCarritoCreate(ItemCarritoBase):
    pass


class ItemCarritoUpdate(BaseModel):
    cantidad: int = Field(..., ge=1)


class ItemCarritoResponse(BaseModel):
    id: int
    carrito_id: int
    variante_producto_id: int
    cantidad: int
    precio_unitario: Decimal
    subtotal: Decimal
    variante_producto: Optional[VarianteResumenResponse] = None

    model_config = ConfigDict(from_attributes=True)


class CarritoResponse(BaseModel):
    id: int
    cliente_id: int
    fecha_creacion: datetime
    estado: EstadoCarrito
    cupon_id: Optional[int] = None
    descuento_aplicado: Decimal = Decimal("0.00")
    items: List[ItemCarritoResponse] = []
    subtotal: Decimal = Decimal("0.00")
    total: Decimal = Decimal("0.00")
    cupon: Optional[CuponResponse] = None

    model_config = ConfigDict(from_attributes=True)


# ==============================================================================
# ÓRDENES Y DETALLES
# ==============================================================================

class DetalleOrdenResponse(BaseModel):
    id: int
    orden_id: int
    variante_producto_id: Optional[int] = None
    cantidad: int
    precio_unitario: Decimal
    subtotal: Decimal
    variante_producto: Optional[VarianteResumenResponse] = None

    model_config = ConfigDict(from_attributes=True)


class OrdenCreateFromCarrito(BaseModel):
    sucursal_id: Optional[int] = None
    direccion_envio: Optional[str] = Field(None, max_length=500)
    tipo: TipoOrden = TipoOrden.DIGITAL


class OrdenEstadoUpdate(BaseModel):
    estado: EstadoOrden


class ComprobanteResponse(BaseModel):
    id: int
    numero: str
    tipo: TipoComprobante
    fecha_emision: datetime
    monto_total: Decimal
    archivo_pdf: Optional[str] = None
    orden_id: int

    model_config = ConfigDict(from_attributes=True)


class OrdenResponse(BaseModel):
    id: int
    numero_orden: str
    cliente_id: Optional[int] = None
    sucursal_id: Optional[int] = None
    fecha: datetime
    tipo: TipoOrden
    estado: EstadoOrden
    total: Decimal
    impuestos: Decimal
    descuentos: Decimal
    cupon_id: Optional[int] = None
    direccion_envio: Optional[str] = None
    detalles: List[DetalleOrdenResponse] = []
    comprobante: Optional[ComprobanteResponse] = None

    model_config = ConfigDict(from_attributes=True)


# ==============================================================================
# VENTAS PRESENCIALES
# ==============================================================================

class DetalleVentaPresencialCreate(BaseModel):
    variante_producto_id: int
    cantidad: int = Field(..., ge=1)
    precio_unitario: Optional[Decimal] = None


class VentaPresencialCreate(BaseModel):
    sucursal_id: int
    cliente_id: Optional[int] = None
    nit_ci_cliente: Optional[str] = Field(None, max_length=20)
    metodo_pago: MetodoPagoPresencial
    detalles: List[DetalleVentaPresencialCreate] = Field(..., min_length=1)
    cupon_codigo: Optional[str] = None


class VentaPresencialResponse(BaseModel):
    id: int
    orden_id: Optional[int] = None
    cajero_id: Optional[int] = None
    sucursal_id: int
    cliente_id: Optional[int] = None
    fecha: datetime
    metodo_pago: MetodoPagoPresencial
    orden: Optional[OrdenResponse] = None

    model_config = ConfigDict(from_attributes=True)


# ==============================================================================
# RESERVAS
# ==============================================================================

class DetalleReservaCreate(BaseModel):
    variante_producto_id: int
    cantidad: int = Field(1, ge=1)


class DetalleReservaResponse(BaseModel):
    id: int
    reserva_id: int
    variante_producto_id: int
    cantidad: int
    estado: EstadoDetalleReserva
    variante_producto: Optional[VarianteResumenResponse] = None

    model_config = ConfigDict(from_attributes=True)


class ReservaCreate(BaseModel):
    sucursal_id: int
    fecha_reserva: datetime
    horario_aproximado: Optional[time] = None
    notas: Optional[str] = None
    detalles: List[DetalleReservaCreate] = Field(..., min_length=1)


class ReservaResponse(BaseModel):
    id: int
    numero_reserva: str
    cliente_id: int
    sucursal_id: int
    fecha_creacion: datetime
    fecha_reserva: datetime
    horario_aproximado: Optional[time] = None
    estado: EstadoReserva
    notas: Optional[str] = None
    detalles: List[DetalleReservaResponse] = []

    model_config = ConfigDict(from_attributes=True)


class ItemCompletarReserva(BaseModel):
    detalle_reserva_id: int
    comprado: bool = True


class CompletarReservaRequest(BaseModel):
    items: List[ItemCompletarReserva]
    metodo_pago: MetodoPagoPresencial = MetodoPagoPresencial.EFECTIVO


# ==============================================================================
# PAGOS Y PASARELAS
# ==============================================================================

class StripeCheckoutRequest(BaseModel):
    orden_id: int
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class StripeCheckoutResponse(BaseModel):
    checkout_url: str
    session_id: str


class PayPalOrderRequest(BaseModel):
    orden_id: int
    return_url: Optional[str] = None
    cancel_url: Optional[str] = None


class PayPalOrderResponse(BaseModel):
    order_id: str
    approve_url: str
    status: str


class PayPalCaptureRequest(BaseModel):
    paypal_order_id: str
    orden_id: int


class TransaccionPagoResponse(BaseModel):
    id: int
    orden_id: int
    pasarela_pago_id: Optional[int] = None
    monto: Decimal
    moneda: str
    metodo_pago: MetodoPagoDigital
    estado: EstadoTransaccion
    referencia_externa: Optional[str] = None
    fecha: datetime
    datos_respuesta: Optional[Any] = None

    model_config = ConfigDict(from_attributes=True)


class PasarelaPagoCreate(BaseModel):
    nombre: str = Field(..., max_length=100)
    tipo: str = Field(..., max_length=50)
    configuracion: Optional[dict] = None
    estado: EstadoPasarela = EstadoPasarela.ACTIVO


class PasarelaPagoUpdate(BaseModel):
    nombre: Optional[str] = None
    configuracion: Optional[dict] = None
    estado: Optional[EstadoPasarela] = None


class PasarelaPagoResponse(BaseModel):
    id: int
    nombre: str
    tipo: str
    estado: EstadoPasarela

    model_config = ConfigDict(from_attributes=True)

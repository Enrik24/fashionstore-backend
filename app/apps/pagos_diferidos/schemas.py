"""
[EXAMEN-2] Schemas del Paquete 5 — carrito offline y pago diferido (CU15/CU16).
"""
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ============================================================
# Carrito offline (CU15)
# ============================================================

class ItemCarritoOffline(BaseModel):
    variante_producto_id: int
    cantidad: int = Field(1, ge=1)
    precio_unitario_local: Optional[Decimal] = None
    stock_local_conocido: Optional[int] = Field(None, ge=0)


class CarritoOfflineRequest(BaseModel):
    uuid_carrito: str
    cliente_id: Optional[int] = None
    sucursal_id: Optional[int] = None
    cupon_codigo: Optional[str] = None
    items: list[ItemCarritoOffline] = Field(default_factory=list)


class CarritoOfflineItemsRequest(BaseModel):
    items: list[ItemCarritoOffline] = Field(..., min_length=1)


class CarritoOfflineItemResponse(BaseModel):
    id: int
    variante_producto_id: int
    sku_variante: Optional[str] = None
    producto: Optional[str] = None
    cantidad: int
    precio_unitario: Decimal
    precio_unitario_local: Optional[Decimal] = None
    stock_local_conocido: Optional[int] = None
    stock_central: Optional[int] = None
    subtotal: Decimal


class CarritoOfflineResponse(BaseModel):
    carrito_id: int
    uuid_carrito: str
    estado: str
    cliente_id: Optional[int] = None
    sucursal_id: Optional[int] = None
    dispositivo_id: Optional[str] = None
    items: list[CarritoOfflineItemResponse] = Field(default_factory=list)
    subtotal: Decimal = Decimal("0.00")
    descuento: Decimal = Decimal("0.00")
    total: Decimal = Decimal("0.00")
    moneda: str = "BOB"
    requiere_aceptacion: bool = False


class CarritoValidacionResponse(BaseModel):
    carrito_id: int
    uuid_carrito: str
    subtotal_local: Decimal
    total_local: Decimal
    subtotal_servidor: Decimal
    total_servidor: Decimal
    diferencia: Decimal
    requiere_aceptacion: bool
    diferencias: list[dict[str, Any]] = Field(default_factory=list)
    stock_suficiente: bool
    faltantes: list[dict[str, Any]] = Field(default_factory=list)
    cupon: dict[str, Any] = Field(default_factory=dict)


class ConvertirCarritoRequest(BaseModel):
    acepta_nuevo_total: bool = False
    direccion_envio: Optional[str] = None
    cliente_id: Optional[int] = None
    sucursal_id: Optional[int] = None


class OrdenDiferidaResponse(BaseModel):
    orden_id: int
    numero_orden: str
    uuid_orden: str
    estado: str
    subtotal: Decimal
    descuento: Decimal
    total: Decimal
    moneda: str
    expira_en: Optional[datetime] = None
    fecha_limite_pago: Optional[datetime] = None
    items: int = 0
    stock_reservado: bool = False


# ============================================================
# Pago diferido (CU16)
# ============================================================

class PagoDiferidoRequest(BaseModel):
    uuid_pago: str
    uuid_orden: Optional[str] = None
    orden_id: Optional[int] = None
    uuid_carrito: Optional[str] = None
    monto: Optional[Decimal] = None
    moneda: Optional[str] = None
    acepta_nuevo_total: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class PagoDiferidoResponse(BaseModel):
    id: int
    uuid_pago: str
    orden_id: int
    numero_orden: Optional[str] = None
    monto: Decimal
    moneda: str
    estado: str
    stripe_payment_intent_id: Optional[str] = None
    client_secret: Optional[str] = None
    requiere_accion: bool = False
    idempotency_key: Optional[str] = None
    intentos: int = 0
    fecha_creacion: Optional[datetime] = None
    fecha_expiracion: Optional[datetime] = None
    fecha_ultimo_intento: Optional[datetime] = None
    ultimo_error: Optional[str] = None
    motivo_rechazo: Optional[str] = None
    reembolso_id: Optional[str] = None
    simulado: bool = False
    duplicado: bool = False


class ConfirmarPagoRequest(BaseModel):
    pass


class ReembolsoRequest(BaseModel):
    monto: Optional[Decimal] = Field(None, gt=0)
    motivo: Optional[str] = None


class EventoStripeResponse(BaseModel):
    id: int
    stripe_event_id: str
    tipo: str
    procesado: bool
    fecha_recepcion: Optional[datetime] = None
    fecha_procesamiento: Optional[datetime] = None
    pago_diferido_id: Optional[int] = None
    error_detalle: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class WebhookProcesadoResponse(BaseModel):
    recibido: bool = True
    evento: Optional[str] = None
    tipo: Optional[str] = None
    procesado: bool = False
    duplicado: bool = False
    motivo: Optional[str] = None

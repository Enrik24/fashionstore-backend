"""[EXAMEN-2] Schemas de la API comercial/logística (CU13 y CU14)."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ----------------------------------------------------------------------
# CU13 — Devoluciones y cambios
# ----------------------------------------------------------------------

class CambioItemRequest(BaseModel):
    detalle_orden_id: int
    cantidad: int = Field(1, ge=1)
    variante_cambio_id: Optional[int] = None


class CambioRequest(BaseModel):
    orden_id: Optional[int] = None
    numero_orden: Optional[str] = Field(None, max_length=50)
    numero_comprobante: Optional[str] = Field(None, max_length=50)
    uuid_venta_offline: Optional[UUID] = None
    uuid_offline: Optional[UUID] = None
    tipo: str = Field("CAMBIO", pattern="^(CAMBIO|DEVOLUCION|cambio|devolucion)$")
    motivo: str = Field(..., max_length=40)
    motivo_detalle: Optional[str] = Field(None, max_length=500)
    sucursal_id: Optional[int] = None
    metodo_pago_diferencia: str = Field("EFECTIVO", max_length=30)
    observaciones: Optional[str] = Field(None, max_length=1000)
    items: list[CambioItemRequest] = Field(..., min_length=1)


class RechazarDevolucionRequest(BaseModel):
    motivo: str = Field(..., min_length=3, max_length=500)


# ----------------------------------------------------------------------
# CU14 — Traspasos entre sucursales
# ----------------------------------------------------------------------

class TraspasoDetalleRequest(BaseModel):
    variante_producto_id: int
    cantidad: int = Field(..., gt=0)
    observaciones: Optional[str] = Field(None, max_length=500)


class TraspasoCreateRequest(BaseModel):
    sucursal_origen_id: int
    sucursal_destino_id: int
    motivo: Optional[str] = Field(None, max_length=500)
    observaciones: Optional[str] = Field(None, max_length=1000)
    detalles: list[TraspasoDetalleRequest] = Field(..., min_length=1)


class RecepcionDetalleRequest(BaseModel):
    detalle_id: int
    cantidad_recibida: int = Field(0, ge=0)
    cantidad_merma: int = Field(0, ge=0)
    observaciones: Optional[str] = Field(None, max_length=500)


class RecibirTraspasoRequest(BaseModel):
    detalles: Optional[list[RecepcionDetalleRequest]] = None
    observaciones: Optional[str] = Field(None, max_length=1000)


class RechazoDetalleRequest(BaseModel):
    detalle_id: int


class RechazarTraspasoRequest(BaseModel):
    motivo: str = Field(..., min_length=3, max_length=500)
    devolver_origen: bool = True
    detalles: Optional[list[RechazoDetalleRequest]] = None

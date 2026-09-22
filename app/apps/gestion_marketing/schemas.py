"""
Esquemas Pydantic v2 para Gestión de Marketing y Promociones (CU24) y Cupones (CU27).
"""
from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import List, Optional
from datetime import datetime
from decimal import Decimal

from app.apps.gestion_marketing.models import TipoPromocion, EstadoPromocion


# Schemas para Promociones (CU24)
class PromocionBase(BaseModel):
    nombre: str = Field(..., max_length=150, description="Nombre descriptivo de la promoción")
    descripcion: Optional[str] = Field(None, description="Descripción detallada de la oferta")
    tipo: TipoPromocion = Field(..., description="Tipo de promoción (PORCENTAJE, MONTO_FIJO, DOS_POR_UNO, ENVIO_GRATIS)")
    valor: Optional[Decimal] = Field(None, description="Valor del descuento (porcentaje o monto fijo)")
    fecha_inicio: datetime = Field(..., description="Fecha y hora de inicio de vigencia")
    fecha_fin: datetime = Field(..., description="Fecha y hora de fin de vigencia")
    condiciones: Optional[str] = Field(None, description="Términos y condiciones de la promoción")
    estado: EstadoPromocion = Field(default=EstadoPromocion.ACTIVA, description="Estado de la promoción")


class PromocionCreate(PromocionBase):
    producto_ids: List[int] = Field(default_factory=list, description="IDs de productos específicos aplicables")
    categoria_ids: List[int] = Field(default_factory=list, description="IDs de categorías aplicables")
    sucursal_ids: List[int] = Field(default_factory=list, description="IDs de sucursales aplicables (vacío = todas)")

    @model_validator(mode="after")
    def validar_fechas_y_valores(self):
        if self.fecha_fin <= self.fecha_inicio:
            raise ValueError("La fecha de fin debe ser posterior a la fecha de inicio")

        if self.tipo == TipoPromocion.PORCENTAJE:
            if self.valor is None or self.valor <= 0 or self.valor > 100:
                raise ValueError("Para descuentos en porcentaje, el valor debe ser mayor a 0 y menor o igual a 100")
        elif self.tipo == TipoPromocion.MONTO_FIJO:
            if self.valor is None or self.valor <= 0:
                raise ValueError("Para descuentos por monto fijo, el valor debe ser mayor a 0")
        elif self.tipo in (TipoPromocion.DOS_POR_UNO, TipoPromocion.ENVIO_GRATIS):
            # No requiere valor numérico obligatorio
            pass
        return self


class PromocionUpdate(BaseModel):
    nombre: Optional[str] = Field(None, max_length=150)
    descripcion: Optional[str] = None
    tipo: Optional[TipoPromocion] = None
    valor: Optional[Decimal] = None
    fecha_inicio: Optional[datetime] = None
    fecha_fin: Optional[datetime] = None
    condiciones: Optional[str] = None
    estado: Optional[EstadoPromocion] = None
    producto_ids: Optional[List[int]] = None
    categoria_ids: Optional[List[int]] = None
    sucursal_ids: Optional[List[int]] = None

    @model_validator(mode="after")
    def validar_fechas_y_valores_update(self):
        if self.fecha_inicio and self.fecha_fin and self.fecha_fin <= self.fecha_inicio:
            raise ValueError("La fecha de fin debe ser posterior a la fecha de inicio")
        if self.tipo == TipoPromocion.PORCENTAJE and self.valor is not None:
            if self.valor <= 0 or self.valor > 100:
                raise ValueError("El porcentaje debe estar entre 1 y 100")
        if self.tipo == TipoPromocion.MONTO_FIJO and self.valor is not None:
            if self.valor <= 0:
                raise ValueError("El monto fijo debe ser mayor a 0")
        return self


class PromocionEstadoUpdate(BaseModel):
    estado: EstadoPromocion = Field(..., description="Nuevo estado de la promoción")


class PromocionResponse(PromocionBase):
    id: int
    creado_por_id: Optional[int] = None
    fecha_creacion: Optional[datetime] = None
    producto_ids: List[int] = Field(default_factory=list)
    categoria_ids: List[int] = Field(default_factory=list)
    sucursal_ids: List[int] = Field(default_factory=list)
    advertencia: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class PromocionPublicaResponse(BaseModel):
    id: int
    nombre: str
    descripcion: Optional[str] = None
    tipo: TipoPromocion
    valor: Optional[Decimal] = None
    fecha_inicio: datetime
    fecha_fin: datetime
    condiciones: Optional[str] = None
    producto_ids: List[int] = Field(default_factory=list)
    categoria_ids: List[int] = Field(default_factory=list)
    sucursal_ids: List[int] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)

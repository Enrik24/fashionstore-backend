"""
[EXAMEN-2] Schemas Pydantic de la Fase 1 — Gestión SaaS (CU01-CU04).

Los esquemas de entrada **nunca** incluyen `tenant_id` cuando el tenant sale
del token; en los endpoints de plataforma sí se recibe por ruta.
"""
from datetime import date, datetime, time
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ============================================================
# Tenant
# ============================================================

class TenantCreate(BaseModel):
    """Alta completa de un tenant (CU01)."""
    razon_social: str = Field(..., min_length=1, max_length=200)
    nit: str = Field(..., min_length=1, max_length=50)
    direccion_fiscal: str = Field(..., min_length=1, max_length=500)
    correo_contacto: EmailStr
    telefono: Optional[str] = Field(None, max_length=30)
    slug: Optional[str] = Field(None, min_length=3, max_length=60, pattern=r"^[a-z0-9-]+$")
    moneda: str = Field("BOB", min_length=3, max_length=3)
    zona_horaria: str = Field("America/La_Paz", max_length=50)
    plan_id: Optional[int] = None
    admin_nombre: str = Field(..., min_length=1, max_length=100)
    admin_apellido: str = Field(..., min_length=1, max_length=100)
    admin_correo: EmailStr
    admin_contrasena: str = Field(..., min_length=8, max_length=100)


class TenantUpdate(BaseModel):
    razon_social: Optional[str] = Field(None, min_length=1, max_length=200)
    nit: Optional[str] = Field(None, min_length=1, max_length=50)
    direccion_fiscal: Optional[str] = Field(None, min_length=1, max_length=500)
    correo_contacto: Optional[EmailStr] = None
    telefono: Optional[str] = Field(None, max_length=30)
    logo_url: Optional[str] = Field(None, max_length=500)
    moneda: Optional[str] = Field(None, min_length=3, max_length=3)
    zona_horaria: Optional[str] = Field(None, max_length=50)


class TenantEstadoUpdate(BaseModel):
    estado: str = Field(..., pattern=r"^(CONFIGURACION_INICIAL|OPERATIVO|SUSPENDIDO|CANCELADO)$")
    motivo: Optional[str] = None


class TenantResponse(BaseModel):
    id: int
    razon_social: str
    nit: str
    direccion_fiscal: str
    correo_contacto: str
    telefono: Optional[str] = None
    logo_url: Optional[str] = None
    moneda: str
    zona_horaria: str
    estado: str
    slug: str
    fecha_registro: Optional[datetime] = None
    fecha_baja: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class UsoTenantResponse(BaseModel):
    tenant_id: int
    sucursales_actuales: int
    max_sucursales: int
    usuarios_actuales: int
    productos_actuales: int
    # [EXAMEN-2] Fase B: límites nuevos y consumo de respaldos.
    max_usuarios: Optional[int] = None
    max_productos: Optional[int] = None
    backups_mes_actual: int = 0
    max_backups_manuales: Optional[int] = None


class TenantDetalleResponse(TenantResponse):
    suscripcion: Optional["SuscripcionResponse"] = None
    uso: Optional[UsoTenantResponse] = None


# ============================================================
# Plan
# ============================================================

class PlanCreate(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=50)
    descripcion: Optional[str] = None
    precio_mensual: float = Field(..., ge=0)
    max_sucursales: int = Field(..., gt=0)
    permite_reportes_ia: bool = False
    permite_pago_diferido: bool = True
    estado: str = Field("ACTIVO", pattern=r"^(ACTIVO|INACTIVO)$")
    orden: int = 0
    soporte_prioritario: bool = False
    # [EXAMEN-2] Fase B (punto 6)
    max_usuarios: Optional[int] = Field(None, ge=1)
    max_productos: Optional[int] = Field(None, ge=1)
    backup_automatico: bool = False
    backup_frecuencia_horas: Optional[int] = Field(None, ge=1)
    backup_retencion_dias: int = Field(7, ge=1)
    backup_manual_mensual: Optional[int] = Field(None, ge=0)
    permite_restauracion: bool = False


class PlanUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=1, max_length=50)
    descripcion: Optional[str] = None
    precio_mensual: Optional[float] = Field(None, ge=0)
    max_sucursales: Optional[int] = Field(None, gt=0)
    permite_reportes_ia: Optional[bool] = None
    permite_pago_diferido: Optional[bool] = None
    estado: Optional[str] = Field(None, pattern=r"^(ACTIVO|INACTIVO)$")
    orden: Optional[int] = None
    soporte_prioritario: Optional[bool] = None
    max_usuarios: Optional[int] = Field(None, ge=1)
    max_productos: Optional[int] = Field(None, ge=1)
    backup_automatico: Optional[bool] = None
    backup_frecuencia_horas: Optional[int] = Field(None, ge=1)
    backup_retencion_dias: Optional[int] = Field(None, ge=1)
    backup_manual_mensual: Optional[int] = Field(None, ge=0)
    permite_restauracion: Optional[bool] = None


class PlanResponse(BaseModel):
    id: int
    nombre: str
    descripcion: Optional[str] = None
    precio_mensual: float
    max_sucursales: int
    permite_reportes_ia: bool
    permite_pago_diferido: bool
    estado: str
    orden: int
    soporte_prioritario: bool
    max_usuarios: Optional[int] = None
    max_productos: Optional[int] = None
    backup_automatico: bool = False
    backup_frecuencia_horas: Optional[int] = None
    backup_retencion_dias: int = 7
    backup_manual_mensual: Optional[int] = None
    permite_restauracion: bool = False
    model_config = ConfigDict(from_attributes=True)


class PlanConUsoResponse(PlanResponse):
    tenants_activos: int = 0
    suscripciones_activas: int = 0


# ============================================================
# [EXAMEN-2] Fase B — Respaldos y restauraciones
# ============================================================

class BackupResponse(BaseModel):
    id: int
    tenant_id: int
    tipo: str
    estado: str
    fecha_inicio: Optional[datetime] = None
    fecha_fin: Optional[datetime] = None
    tamano_bytes: Optional[int] = None
    hash_sha256: Optional[str] = None
    nombre_archivo: Optional[str] = None
    retencion_hasta: Optional[datetime] = None
    creado_por: Optional[int] = None
    error_detalle: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class RestauracionCreate(BaseModel):
    motivo: Optional[str] = Field(None, max_length=200)


class RestauracionResponse(BaseModel):
    id: int
    tenant_id: int
    backup_id: int
    solicitado_por: Optional[int] = None
    estado: str
    motivo: Optional[str] = None
    fecha_solicitud: Optional[datetime] = None
    fecha_inicio: Optional[datetime] = None
    fecha_fin: Optional[datetime] = None
    error_detalle: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class BackupUsoResponse(BaseModel):
    tenant_id: int
    backups_mes_actual: int
    max_backups_manuales: Optional[int] = None
    restantes_mes_actual: Optional[int] = None


class ImpactoPlanResponse(BaseModel):
    plan_id: int
    max_sucursales: int
    tenants_afectados: list[dict[str, Any]] = Field(default_factory=list)
    total_afectados: int = 0


# ============================================================
# Suscripción
# ============================================================

class SuscripcionCreate(BaseModel):
    tenant_id: int
    plan_id: int
    fecha_inicio: Optional[date] = None
    fecha_vencimiento: Optional[date] = None
    renovacion_automatica: bool = False


class SuscripcionUpdate(BaseModel):
    fecha_inicio: Optional[date] = None
    fecha_vencimiento: Optional[date] = None
    estado_pago: Optional[str] = None
    renovacion_automatica: Optional[bool] = None
    motivo_estado: Optional[str] = None


class CambioPlanRequest(BaseModel):
    plan_id: int


class SuscripcionResponse(BaseModel):
    id: int
    tenant_id: int
    plan_id: int
    fecha_inicio: date
    fecha_vencimiento: date
    estado: str
    estado_pago: str
    renovacion_automatica: bool
    plan: Optional[PlanResponse] = None
    model_config = ConfigDict(from_attributes=True)


class EstadoSuscripcionResponse(BaseModel):
    suscripcion_id: int
    estado: str
    motivo_estado: Optional[str] = None


# ============================================================
# Pago de suscripción
# ============================================================

class PagoSuscripcionCreate(BaseModel):
    suscripcion_id: int
    monto: float = Field(..., ge=0)
    moneda: str = Field("BOB", min_length=3, max_length=3)
    estado: str = Field("PENDIENTE", pattern=r"^(PENDIENTE|CONFIRMADO|RECHAZADO|REEMBOLSADO|EN_PROCESO|EXPIRADO|CANCELADO)$")
    stripe_payment_intent_id: Optional[str] = Field(None, max_length=120)
    referencia: Optional[str] = Field(None, max_length=120)


class PagoSuscripcionResponse(BaseModel):
    id: int
    tenant_id: int
    suscripcion_id: int
    monto: float
    moneda: str
    estado: str
    stripe_payment_intent_id: Optional[str] = None
    stripe_checkout_session_id: Optional[str] = None
    referencia: Optional[str] = None
    fecha: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class SuscripcionStripeCheckoutRequest(BaseModel):
    suscripcion_id: int
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class SuscripcionStripeCheckoutResponse(BaseModel):
    checkout_url: str
    session_id: str


class HistorialPagosResponse(BaseModel):
    total: int
    pagos: list[PagoSuscripcionResponse]


# ============================================================
# Configuración del tenant (CU04)
# ============================================================

class DatosEmpresaUpdate(BaseModel):
    razon_social: Optional[str] = Field(None, min_length=1, max_length=200)
    direccion_fiscal: Optional[str] = Field(None, min_length=1, max_length=500)
    telefono: Optional[str] = Field(None, max_length=30)
    correo_contacto: Optional[EmailStr] = None
    logo_url: Optional[str] = Field(None, max_length=500)
    moneda: Optional[str] = Field(None, min_length=3, max_length=3)
    zona_horaria: Optional[str] = Field(None, max_length=50)


class SucursalConfigCreate(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    direccion: str = Field(..., min_length=1, max_length=500)
    telefono: str = Field(..., min_length=1, max_length=20)
    horario_atencion: Optional[str] = Field(None, max_length=200)
    ciudad_id: Optional[int] = None
    latitud: Optional[float] = None
    longitud: Optional[float] = None


class UsuarioConfigCreate(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    apellido: str = Field(..., min_length=1, max_length=100)
    correo: EmailStr
    contrasena: str = Field(..., min_length=8, max_length=100)
    rol: str = Field(..., pattern=r"^(Encargado|Cajero)$")
    sucursal_id: int
    telefono: Optional[str] = Field(None, max_length=20)


class PreferenciasOfflineUpdate(BaseModel):
    descarga_automatica: Optional[bool] = None
    hora_descarga: Optional[str] = None
    incluir_clientes: Optional[bool] = None
    max_clientes: Optional[int] = Field(None, ge=0)
    habilitar_carrito_offline: Optional[bool] = None
    max_horas_sesion_offline: Optional[int] = Field(None, ge=1, le=168)
    max_dias_orden_pendiente: Optional[int] = Field(None, ge=1, le=30)


class ConfiguracionOfflineResponse(BaseModel):
    id: int
    tenant_id: int
    descarga_automatica: bool
    hora_descarga: Optional[time] = None
    incluir_clientes: bool
    max_clientes: int
    habilitar_carrito_offline: bool
    max_horas_sesion_offline: int
    max_dias_orden_pendiente: int
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class AsistenteConfiguracionResponse(BaseModel):
    tenant_id: int
    pasos_completados: list[str]
    pasos_faltantes: list[str]
    porcentaje: int
    estado_tenant: str
    puede_finalizar: bool


class ConfiguracionTenantResponse(BaseModel):
    tenant: TenantResponse
    configuracion_offline: ConfiguracionOfflineResponse
    asistente: AsistenteConfiguracionResponse


# Los schemas de dispositivos y sesión offline viven en
# `app.apps.operaciones_offline.schemas` (un `schemas.py` por paquete).

TenantDetalleResponse.model_rebuild()

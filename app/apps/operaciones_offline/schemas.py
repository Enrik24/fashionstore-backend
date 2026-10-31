"""
[EXAMEN-2] Schemas Pydantic de la Fase 1 — sesión offline y dispositivos
(CU17). La descarga y el motor de sincronización pertenecen a la Fase 2.
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.apps.gestion_saas.schemas import ConfiguracionOfflineResponse


class DispositivoRegistroRequest(BaseModel):
    id: Optional[str] = Field(None, description="UUID generado en el cliente")
    plataforma: str = Field(..., pattern=r"^(WEB|ANDROID|IOS|POS)$")
    nombre: Optional[str] = Field(None, max_length=100)


class DispositivoResponse(BaseModel):
    id: str
    tenant_id: int
    usuario_id: int
    plataforma: str
    nombre: Optional[str] = None
    activo: bool
    ultima_conexion: Optional[datetime] = None
    fecha_registro: Optional[datetime] = None


class RevocacionRequest(BaseModel):
    motivo: Optional[str] = None


class PinConfigurarRequest(BaseModel):
    contrasena_actual: str
    pin: str = Field(..., min_length=6, max_length=12, pattern=r"^\d+$")


class PinConfigurarResponse(BaseModel):
    configurado: bool
    mensaje: str


class SesionOfflineRequest(BaseModel):
    dispositivo_id: str
    pin: str = Field(..., min_length=6, max_length=12)
    tenant_id: Optional[int] = Field(None, description="Tenant guardado en el dispositivo")
    usuario_id: Optional[int] = Field(None, description="Usuario guardado en el dispositivo")
    plataforma: Optional[str] = Field(None, pattern=r"^(WEB|ANDROID|IOS|POS)$")


class SesionOfflineResponse(BaseModel):
    token_offline: str
    token_type: str = "bearer"
    expira_en: datetime
    usuario_id: int
    tenant_id: int
    dispositivo_id: str
    roles: list[str]
    permisos: list[str]
    configuracion: ConfiguracionOfflineResponse
    ultima_descarga: Optional[datetime] = None


class RenovarSesionRequest(BaseModel):
    token_offline: str


class CierreSesionOfflineResponse(BaseModel):
    cerrada: bool
    instruccion_borrado_local: dict[str, Any]


class EstadoSesionOfflineResponse(BaseModel):
    usuario_id: int
    tenant_id: int
    dispositivo_id: Optional[str] = None
    pin_configurado: bool
    dispositivo_activo: bool
    intentos_restantes: int
    max_horas_sesion_offline: int
    ultima_conexion: Optional[datetime] = None
    ultima_descarga: Optional[datetime] = None


class PreferenciasDescargaUpdate(BaseModel):
    descarga_automatica: Optional[bool] = None
    hora_descarga: Optional[str] = None
    incluir_clientes: Optional[bool] = None
    max_clientes: Optional[int] = Field(None, ge=0)
    habilitar_carrito_offline: Optional[bool] = None
    max_horas_sesion_offline: Optional[int] = Field(None, ge=1, le=168)
    max_dias_orden_pendiente: Optional[int] = Field(None, ge=1, le=30)


# ============================================================
# [EXAMEN-2] Fase 2 — descarga offline (CU05)
# ============================================================

class DescargaRegistrarRequest(BaseModel):
    dispositivo_id: str
    sucursal_id: Optional[int] = None
    version: Optional[str] = None
    hash_paquete: Optional[str] = None
    tamano_bytes: Optional[int] = Field(None, ge=0)
    completo: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class DescargaRegistrarResponse(BaseModel):
    registrada: bool
    dispositivo_id: str
    cursor_catalogo: Optional[str] = None
    ultima_sincronizacion: Optional[datetime] = None
    ultima_descarga_completa: Optional[datetime] = None


class VersionPaqueteResponse(BaseModel):
    tenant_id: int
    sucursal_id: Optional[int] = None
    version: str
    hash_paquete: str
    fecha_corte: datetime
    actualizado_en: Optional[datetime] = None


# ============================================================
# [EXAMEN-2] Fase 2 — sincronización (CU06-CU08)
# ============================================================

class OperacionLoteItem(BaseModel):
    uuid_operacion: str
    dispositivo_id: Optional[str] = None
    secuencia_local: Optional[int] = Field(None, ge=0)
    tipo_operacion: str
    sucursal_id: Optional[int] = None
    fecha_registro_local: Optional[datetime] = None
    payload: dict[str, Any] = Field(default_factory=dict)


class LoteSyncRequest(BaseModel):
    dispositivo_id: Optional[str] = None
    sucursal_id: Optional[int] = None
    operaciones: list[OperacionLoteItem] = Field(default_factory=list)


class ResultadoOperacionResponse(BaseModel):
    uuid_operacion: str
    estado: str
    motivo: Optional[str] = None
    datos_resultado: Optional[dict[str, Any]] = None
    reintentar: bool = False
    codigo_http_sugerido: int = 200


class LoteSyncResponse(BaseModel):
    lote_id: str
    tenant_id: int
    recibidas: int
    procesadas: int
    resultados: list[ResultadoOperacionResponse]
    resumen: dict[str, int]


class OperacionOfflineResponse(BaseModel):
    uuid_operacion: str
    tipo_operacion: str
    estado_sincronizacion: str
    requiere_revision: bool = False
    intentos: int = 0
    motivo_rechazo: Optional[str] = None
    error_detalle: Optional[str] = None
    resultado_json: Optional[dict[str, Any]] = None
    secuencia_local: Optional[int] = None
    sucursal_id: Optional[int] = None
    dispositivo_id: Optional[str] = None
    fecha_registro_local: Optional[datetime] = None
    fecha_recepcion_servidor: Optional[datetime] = None
    fecha_ultimo_intento: Optional[datetime] = None


class ResumenSincronizacionResponse(BaseModel):
    tenant_id: int
    desde: Optional[datetime] = None
    hasta: Optional[datetime] = None
    confirmadas: int = 0
    duplicadas: int = 0
    rechazadas: int = 0
    conflictos: int = 0
    errores_criticos: int = 0
    pendientes: int = 0
    total: int = 0


# ============================================================
# [EXAMEN-2] Fase 2 — conflictos (CU09)
# ============================================================

class ConflictoResponse(BaseModel):
    id: int
    operacion_offline_id: int
    uuid_operacion: Optional[str] = None
    tipo_conflicto: str
    resuelto: bool
    resolucion: Optional[str] = None
    estrategia_resolucion: Optional[str] = None
    observaciones: Optional[str] = None
    detalle_json: Optional[dict[str, Any]] = None
    fecha: Optional[datetime] = None
    fecha_resolucion: Optional[datetime] = None
    resuelto_por: Optional[int] = None


class ResolverConflictoRequest(BaseModel):
    decision: str = Field(..., description="CONFIRMAR, RECHAZAR o AJUSTAR")
    observacion: Optional[str] = None


class DescartarConflictoRequest(BaseModel):
    motivo: Optional[str] = None

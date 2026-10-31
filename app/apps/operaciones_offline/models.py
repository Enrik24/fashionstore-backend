"""
Modelos SQLAlchemy del Paquete 2 — Operación Offline y Sesión Offline.

Fase 1 (plan, 9.1.2): solo se crean `DispositivoSincronizacion` y
`ConfiguracionOffline`, necesarios para CU17 y CU04. Los modelos del motor
de sincronización (`OperacionOffline`, `LogConflicto`, `ClaveIdempotencia` y
`PuntoSincronizacion`) pertenecen a la Fase 2 y no se declaran aquí todavía.

También se declaran los enums compartidos que el resto de apps necesita para
extender sus modelos (`estado_sincronizacion`, `origen`, etc.).
"""
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Enum, ForeignKey, Index, Integer,
    JSON, String, Text, Time, UniqueConstraint, Uuid,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from app.database import Base


# ============================================================
# Enumeraciones compartidas del Examen 2
# ============================================================

class EstadoSincronizacion(str, enum.Enum):
    """Estado de una operación en la cola offline.

    Se agrega `NO_APLICA` como valor de apoyo para registros históricos del
    Examen 1 que no provienen de una operación offline.
    """
    PENDIENTE = "PENDIENTE"
    ENVIADA = "ENVIADA"
    CONFIRMADA = "CONFIRMADA"
    RECHAZADA = "RECHAZADA"
    CONFLICTO = "CONFLICTO"
    DUPLICADA = "DUPLICADA"
    ERROR_CRITICO = "ERROR_CRITICO"
    NO_APLICA = "NO_APLICA"


class TipoOperacionOffline(str, enum.Enum):
    """Tipos de operación admitidos por el motor de sincronización."""
    RESERVA = "RESERVA"
    VENTA_CONTADO = "VENTA_CONTADO"
    CARRITO = "CARRITO"
    ORDEN_PAGO_DIFERIDO = "ORDEN_PAGO_DIFERIDO"
    DEVOLUCION_CAMBIO = "DEVOLUCION_CAMBIO"
    PAGO_DIFERIDO = "PAGO_DIFERIDO"


class EstadoPagoDiferido(str, enum.Enum):
    """Estados de la cola de pago diferido (CU16)."""
    PENDIENTE_ENVIO = "PENDIENTE_ENVIO"
    EN_COLA = "EN_COLA"
    EN_PROCESO = "EN_PROCESO"
    REQUIERE_ACCION_CLIENTE = "REQUIERE_ACCION_CLIENTE"
    CONFIRMADO = "CONFIRMADO"
    RECHAZADO = "RECHAZADO"
    EXPIRADO = "EXPIRADO"
    CANCELADO = "CANCELADO"
    ERROR_CRITICO = "ERROR_CRITICO"


class OrigenOperacion(str, enum.Enum):
    """Origen de una operación comercial."""
    WEB = "WEB"
    MOVIL = "MOVIL"
    POS = "POS"
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"


class TipoConflicto(str, enum.Enum):
    """Clasificación de conflictos de sincronización y pagos."""
    STOCK_INSUFICIENTE = "STOCK_INSUFICIENTE"
    PRECIO_DESACTUALIZADO = "PRECIO_DESACTUALIZADO"
    PRODUCTO_INACTIVO = "PRODUCTO_INACTIVO"
    VARIANTE_INEXISTENTE = "VARIANTE_INEXISTENTE"
    VENTA_ORIGEN_INVALIDA = "VENTA_ORIGEN_INVALIDA"
    SUCURSAL_INVALIDA = "SUCURSAL_INVALIDA"
    TENANT_SUSPENDIDO = "TENANT_SUSPENDIDO"
    ORDEN_EXPIRADA = "ORDEN_EXPIRADA"
    PAGO_YA_CAPTURADO = "PAGO_YA_CAPTURADO"
    PAGO_DUPLICADO = "PAGO_DUPLICADO"


class EstrategiaResolucion(str, enum.Enum):
    """Estrategia aplicada a un conflicto."""
    AUTOMATICA = "AUTOMATICA"
    MANUAL = "MANUAL"
    RECHAZO = "RECHAZO"


# ============================================================
# 3.3.13 DispositivoSincronizacion — tabla dispositivos_sincronizacion
# ============================================================

class DispositivoSincronizacion(Base):
    """Dispositivo autorizado a operar offline para un usuario del tenant.

    Modificado en Examen 2 (SaaS multitenant): modelo nuevo. El `id` es un
    UUID generado en el cliente y la revocación obliga al borrado local
    (RN-019 / RNF-17).
    """
    __tablename__ = "dispositivos_sincronizacion"
    __table_args__ = (
        Index("ix_dispositivos_sincronizacion_tenant_usuario_activo", "tenant_id", "usuario_id", "activo"),
    )

    id = Column(Uuid(as_uuid=True), primary_key=True, nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    plataforma = Column(String(20), nullable=False)  # WEB, ANDROID, IOS, POS
    nombre = Column(String(100), nullable=True)
    ultima_conexion = Column(DateTime(timezone=True), nullable=True)
    activo = Column(Boolean, nullable=False, default=True, server_default="true")
    revocado_en = Column(DateTime(timezone=True), nullable=True)  # [EXAMEN-2] RN-019
    revocado_por_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)  # [EXAMEN-2] apoyo
    motivo_revocacion = Column(Text, nullable=True)  # [EXAMEN-2] apoyo
    fecha_registro = Column(DateTime(timezone=True), server_default=func.now())  # [EXAMEN-2] apoyo

    tenant = relationship("Tenant", back_populates="dispositivos")
    usuario = relationship("Usuario", back_populates="dispositivos", foreign_keys=[usuario_id])
    revocado_por = relationship("Usuario", foreign_keys=[revocado_por_id])

    def __repr__(self):
        return f"<DispositivoSincronizacion {self.id} tenant={self.tenant_id} activo={self.activo}>"


# ============================================================
# 3.3.14 ConfiguracionOffline — tabla configuracion_offline
# ============================================================

class ConfiguracionOffline(Base):
    """Preferencias de operación offline de un tenant (una fila por tenant).

    Modificado en Examen 2 (SaaS multitenant): modelo nuevo. CU04 configura,
    CU05 descarga y CU15/CU17 respetan los mismos límites.
    """
    __tablename__ = "configuracion_offline"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_configuracion_offline_tenant"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    descarga_automatica = Column(Boolean, nullable=False, default=True, server_default="true")
    hora_descarga = Column(Time, nullable=True)
    incluir_clientes = Column(Boolean, nullable=False, default=True, server_default="true")
    max_clientes = Column(Integer, nullable=False, default=100, server_default="100")
    habilitar_carrito_offline = Column(Boolean, nullable=False, default=True, server_default="true")  # [EXAMEN-2] CU15
    max_horas_sesion_offline = Column(Integer, nullable=False, default=12, server_default="12")  # [EXAMEN-2] CU17
    max_dias_orden_pendiente = Column(Integer, nullable=False, default=1, server_default="1")  # [EXAMEN-2] RN-016
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tenant = relationship("Tenant", back_populates="configuracion_offline")

    def __repr__(self):
        return f"<ConfiguracionOffline tenant={self.tenant_id} carrito={self.habilitar_carrito_offline}>"


# ============================================================
# 3.3.4 OperacionOffline — tabla operaciones_offline
# ============================================================

class OperacionOffline(Base):
    """Operación creada sin conexión y recibida por el motor de sincronización.

    [EXAMEN-2] Fase 2 (plan 3.3.4). El `uuid_operacion` es único **global**
    (RN-005) y la cola se ordena por `secuencia_local` (RNF-16). Los estados
    `CONFLICTO` + `requiere_revision` reemplazan al antiguo `CONFLICTO_MANUAL`.
    """
    __tablename__ = "operaciones_offline"
    __table_args__ = (
        UniqueConstraint("uuid_operacion", name="uq_operaciones_offline_uuid"),
        Index(
            "ix_operaciones_offline_tenant_estado_fecha",
            "tenant_id", "estado_sincronizacion", "fecha_registro_local",
        ),
        Index(
            "ix_operaciones_offline_tenant_dispositivo_secuencia",
            "tenant_id", "dispositivo_id", "secuencia_local",
        ),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    dispositivo_id = Column(
        Uuid(as_uuid=True),
        ForeignKey("dispositivos_sincronizacion.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="RESTRICT"), nullable=False)
    # [EXAMEN-2] RN-005: único global, no por tenant
    uuid_operacion = Column(Uuid(as_uuid=True), nullable=False, unique=True, index=True)
    secuencia_local = Column(BigInteger, nullable=False)
    tipo_operacion = Column(
        Enum(TipoOperacionOffline, name="tipo_operacion_offline"), nullable=False
    )
    payload_json = Column(JSON, nullable=False)
    estado_sincronizacion = Column(
        Enum(EstadoSincronizacion, name="estado_sincronizacion"),
        nullable=False,
        default=EstadoSincronizacion.PENDIENTE,
        server_default=EstadoSincronizacion.PENDIENTE.value,
        index=True,
    )
    intentos = Column(Integer, nullable=False, default=0, server_default="0")
    fecha_registro_local = Column(DateTime(timezone=True), nullable=False)
    fecha_recepcion_servidor = Column(DateTime(timezone=True), nullable=True)
    fecha_ultimo_intento = Column(DateTime(timezone=True), nullable=True)
    error_detalle = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # [EXAMEN-2] Apoyo del plan 3.3.4
    sucursal_id = Column(Integer, ForeignKey("sucursales.id", ondelete="SET NULL"), nullable=True)
    resultado_json = Column(JSON, nullable=True)
    motivo_rechazo = Column(Text, nullable=True)
    requiere_revision = Column(Boolean, nullable=False, default=False, server_default="false")

    # Relaciones
    dispositivo = relationship("DispositivoSincronizacion")
    conflictos = relationship(
        "LogConflicto", back_populates="operacion", cascade="all, delete-orphan"
    )
    pago_diferido = relationship(
        "PagoDiferido", back_populates="operacion_offline", uselist=False
    )

    def __repr__(self):
        return (
            f"<OperacionOffline {self.uuid_operacion} {self.tipo_operacion} "
            f"{self.estado_sincronizacion}>"
        )


# ============================================================
# 3.3.5 LogConflicto — tabla log_conflictos
# ============================================================

class LogConflicto(Base):
    """Conflicto detectado al sincronizar o conciliar un pago (plan 3.3.5)."""
    __tablename__ = "log_conflictos"
    __table_args__ = (
        Index("ix_log_conflictos_operacion_resuelto", "operacion_offline_id", "resuelto"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    operacion_offline_id = Column(
        Integer, ForeignKey("operaciones_offline.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tipo_conflicto = Column(String(50), nullable=False)
    detalle_json = Column(JSON, nullable=False)
    resuelto = Column(Boolean, nullable=False, default=False, server_default="false")
    resolucion = Column(String(50), nullable=True)
    resuelto_por = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    fecha = Column(DateTime(timezone=True), server_default=func.now())
    # [EXAMEN-2] Apoyo
    estrategia_resolucion = Column(String(50), nullable=True)
    fecha_resolucion = Column(DateTime(timezone=True), nullable=True)
    observaciones = Column(Text, nullable=True)

    operacion = relationship("OperacionOffline", back_populates="conflictos")
    usuario_resolucion = relationship("Usuario", foreign_keys=[resuelto_por])

    def __repr__(self):
        return f"<LogConflicto {self.tipo_conflicto} resuelto={self.resuelto}>"


# ============================================================
# 3.3.12 ClaveIdempotencia — tabla claves_idempotencia
# ============================================================

class ClaveIdempotencia(Base):
    """Registro de claves de idempotencia por tenant/endpoint (plan 3.3.12)."""
    __tablename__ = "claves_idempotencia"
    __table_args__ = (
        UniqueConstraint("tenant_id", "clave", "endpoint", name="uq_claves_idempotencia_tenant_clave_endpoint"),
        Index("ix_claves_idempotencia_tenant_clave", "tenant_id", "clave"),
        Index("ix_claves_idempotencia_fecha_expiracion", "fecha_expiracion"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    clave = Column(Uuid(as_uuid=True), nullable=False)
    endpoint = Column(String(120), nullable=False)
    request_hash = Column(String(64), nullable=False)
    estado = Column(String(20), nullable=False, default="EN_PROCESO", server_default="EN_PROCESO")
    respuesta_json = Column(JSON, nullable=True)
    fecha_creacion = Column(DateTime(timezone=True), server_default=func.now())
    fecha_expiracion = Column(DateTime(timezone=True), nullable=False)

    def __repr__(self):
        return f"<ClaveIdempotencia {self.clave} {self.endpoint} {self.estado}>"


# ============================================================
# 3.3.15 PuntoSincronizacion — tabla puntos_sincronizacion
# ============================================================

class PuntoSincronizacion(Base):
    """Cursor de descarga incremental por dispositivo (plan 3.3.15)."""
    __tablename__ = "puntos_sincronizacion"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dispositivo_id", name="uq_puntos_sincronizacion_tenant_dispositivo"),
        Index("ix_puntos_sincronizacion_tenant", "tenant_id"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    dispositivo_id = Column(
        Uuid(as_uuid=True),
        ForeignKey("dispositivos_sincronizacion.id", ondelete="CASCADE"),
        nullable=False,
    )
    ultima_sincronizacion = Column(DateTime(timezone=True), nullable=True)
    cursor_catalogo = Column(String(100), nullable=True)
    ultima_descarga_completa = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    dispositivo = relationship("DispositivoSincronizacion")

    def __repr__(self):
        return f"<PuntoSincronizacion tenant={self.tenant_id} dispositivo={self.dispositivo_id}>"

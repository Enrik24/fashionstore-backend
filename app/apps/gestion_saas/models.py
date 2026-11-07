"""
Modelos SQLAlchemy del Paquete 1 — Gestión SaaS y Multitenant (Examen 2).

Contiene únicamente los modelos NUEVOS del diagrama v2:
`Tenant`, `Plan`, `Suscripcion` y `PagoSuscripcion`.

Regla anti-duplicados (plan, capítulo 3): los modelos existentes del Examen 1
(`Usuario`, `Sucursal`, `Producto`, `Orden`, etc.) NO se mueven ni se recrean
aquí; se extienden en su app original con la convención `# [EXAMEN-2]`.
"""
from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, Date, DateTime, Enum, ForeignKey,
    Index, Integer, JSON, Numeric, String, Text, text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from app.database import Base
from app.apps.gestion_ventas.models import EstadoTransaccion


# ============================================================
# Enumeraciones del Paquete 1
# ============================================================

class EstadoTenant(str, enum.Enum):
    """Estados del ciclo de vida de una empresa (tenant)."""
    CONFIGURACION_INICIAL = "CONFIGURACION_INICIAL"
    OPERATIVO = "OPERATIVO"
    SUSPENDIDO = "SUSPENDIDO"
    CANCELADO = "CANCELADO"


class EstadoPlan(str, enum.Enum):
    """Estados de un plan comercial."""
    ACTIVO = "ACTIVO"
    INACTIVO = "INACTIVO"


class EstadoSuscripcion(str, enum.Enum):
    """Estados del ciclo de vida de una suscripción."""
    ACTIVA = "ACTIVA"
    SUSPENDIDA = "SUSPENDIDA"
    CANCELADA = "CANCELADA"
    VENCIDA = "VENCIDA"


# ============================================================
# 3.3.1 Tenant — tabla tenants
# ============================================================

class Tenant(Base):
    """Empresa cliente de la plataforma SaaS.

    Modificado en Examen 2 (SaaS multitenant): modelo nuevo; es la raíz del
    aislamiento lógico de todos los datos de negocio.
    """
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    razon_social = Column(String(200), nullable=False)
    nit = Column(String(50), unique=True, nullable=False, index=True)  # [EXAMEN-2] NIT único de plataforma
    direccion_fiscal = Column(String(500), nullable=False)
    correo_contacto = Column(String(255), nullable=False, index=True)  # [EXAMEN-2] contacto del tenant
    telefono = Column(String(30), nullable=True)
    logo_url = Column(String(500), nullable=True)  # [EXAMEN-2] CU04
    moneda = Column(String(3), nullable=False, default="BOB", server_default="BOB")  # [EXAMEN-2] CU04
    zona_horaria = Column(String(50), nullable=False, default="America/La_Paz", server_default="America/La_Paz")  # [EXAMEN-2] RNF-16
    estado = Column(
        Enum(EstadoTenant, name="estado_tenant"),
        nullable=False,
        default=EstadoTenant.CONFIGURACION_INICIAL,
        server_default=EstadoTenant.CONFIGURACION_INICIAL.value,
    )
    fecha_registro = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())  # [EXAMEN-2] descarga incremental
    slug = Column(String(60), unique=True, nullable=False, index=True)  # [EXAMEN-2] apoyo: identificación legible
    fecha_baja = Column(DateTime(timezone=True), nullable=True)  # [EXAMEN-2] apoyo: baja lógica

    # Relaciones
    suscripciones = relationship("Suscripcion", back_populates="tenant", cascade="all, delete-orphan")
    pagos_suscripcion = relationship("PagoSuscripcion", back_populates="tenant", cascade="all, delete-orphan")
    usuarios = relationship("Usuario", back_populates="tenant")
    sucursales = relationship("Sucursal", back_populates="tenant")
    productos = relationship("Producto", back_populates="tenant")
    dispositivos = relationship("DispositivoSincronizacion", back_populates="tenant")
    configuracion_offline = relationship(
        "ConfiguracionOffline", back_populates="tenant", uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Tenant {self.slug}: {self.razon_social} ({self.estado})>"


# ============================================================
# 3.3.2 Plan — tabla planes
# ============================================================

class Plan(Base):
    """Plan comercial contratable por un tenant.

    Modificado en Examen 2 (SaaS multitenant): modelo nuevo. Solo existen
    los planes Básico y Profesional (plan, 3.7.11).
    """
    __tablename__ = "planes"
    __table_args__ = (
        CheckConstraint("precio_mensual >= 0", name="ck_planes_precio_no_negativo"),
        CheckConstraint("max_sucursales > 0", name="ck_planes_max_sucursales_positivo"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(50), unique=True, nullable=False)
    descripcion = Column(Text, nullable=True)
    precio_mensual = Column(Numeric(10, 2), nullable=False)
    max_sucursales = Column(Integer, nullable=False)
    permite_reportes_ia = Column(Boolean, nullable=False, default=False, server_default="false")
    permite_pago_diferido = Column(Boolean, nullable=False, default=True, server_default="true")  # [EXAMEN-2] CU16 / RN-015
    estado = Column(
        Enum(EstadoPlan, name="estado_plan"),
        nullable=False,
        default=EstadoPlan.ACTIVO,
        server_default=EstadoPlan.ACTIVO.value,
    )
    orden = Column(Integer, nullable=False, default=0, server_default="0")  # [EXAMEN-2] apoyo: presentación
    soporte_prioritario = Column(Boolean, nullable=False, default=False, server_default="false")  # [EXAMEN-2] apoyo: CU02
    # [EXAMEN-2] Fase B (punto 6): límites y respaldos por plan.
    max_usuarios = Column(Integer, nullable=True)  # NULL = ilimitado
    max_productos = Column(Integer, nullable=True)  # NULL = ilimitado
    backup_automatico = Column(Boolean, nullable=False, default=False, server_default="false")
    backup_frecuencia_horas = Column(Integer, nullable=True)  # NULL = sin automático
    backup_retencion_dias = Column(Integer, nullable=False, default=7, server_default="7")
    backup_manual_mensual = Column(Integer, nullable=True)  # NULL = ilimitado, 0 = ninguno
    permite_restauracion = Column(Boolean, nullable=False, default=False, server_default="false")

    suscripciones = relationship("Suscripcion", back_populates="plan")

    def __repr__(self):
        return f"<Plan {self.nombre}: {self.precio_mensual} / {self.max_sucursales} sucursales>"


# ============================================================
# 3.3.3 Suscripcion — tabla suscripciones
# ============================================================

class Suscripcion(Base):
    """Vínculo vigente entre un tenant y un plan.

    Modificado en Examen 2 (SaaS multitenant): modelo nuevo. Una sola
    suscripción ACTIVA por tenant (índice único parcial en la migración).
    """
    __tablename__ = "suscripciones"
    __table_args__ = (
        CheckConstraint("fecha_vencimiento > fecha_inicio", name="ck_suscripciones_vigencia"),
        Index("ix_suscripciones_tenant_estado", "tenant_id", "estado"),
        # [EXAMEN-2] Una sola suscripción ACTIVA por tenant (índice único parcial,
        # creado en ex2_001 y declarado aquí para que autogenerate no lo borre).
        Index(
            "uq_suscripciones_tenant_activa",
            "tenant_id",
            unique=True,
            postgresql_where=text("estado = 'ACTIVA'"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("planes.id", ondelete="RESTRICT"), nullable=False, index=True)
    fecha_inicio = Column(Date, nullable=False)
    fecha_vencimiento = Column(Date, nullable=False)
    estado = Column(
        Enum(EstadoSuscripcion, name="estado_suscripcion"),
        nullable=False,
        default=EstadoSuscripcion.ACTIVA,
    )
    estado_pago = Column(String(20), nullable=False, default="PENDIENTE", server_default="PENDIENTE")
    stripe_customer_id = Column(String(120), nullable=True)  # [EXAMEN-2] CU03 / RN-011
    referencia_ultimo_pago = Column(String(120), nullable=True)
    renovacion_automatica = Column(Boolean, nullable=False, default=False, server_default="false")  # [EXAMEN-2] apoyo
    motivo_estado = Column(Text, nullable=True)  # [EXAMEN-2] apoyo: suspensión/cancelación

    tenant = relationship("Tenant", back_populates="suscripciones")
    plan = relationship("Plan", back_populates="suscripciones")
    pagos = relationship("PagoSuscripcion", back_populates="suscripcion", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Suscripcion tenant={self.tenant_id} plan={self.plan_id}: {self.estado}>"


# ============================================================
# 3.3.9 PagoSuscripcion — tabla pagos_suscripcion
# ============================================================

class PagoSuscripcion(Base):
    """Historial de intentos de cobro del plan contratado (RN-011).

    Modificado en Examen 2 (SaaS multitenant): modelo nuevo. Un pago
    CONFIRMADO es la única condición válida para activar o renovar.
    """
    __tablename__ = "pagos_suscripcion"
    __table_args__ = (
        CheckConstraint("monto >= 0", name="ck_pagos_suscripcion_monto_no_negativo"),
        Index("ix_pagos_suscripcion_tenant_fecha", "tenant_id", "fecha"),
        Index("ix_pagos_suscripcion_suscripcion_estado", "suscripcion_id", "estado"),
        Index("uq_pagos_suscripcion_tenant_pi", "tenant_id", "stripe_payment_intent_id", unique=True),
        Index("ix_pagos_suscripcion_stripe_session", "stripe_checkout_session_id"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    suscripcion_id = Column(Integer, ForeignKey("suscripciones.id", ondelete="CASCADE"), nullable=False, index=True)
    monto = Column(Numeric(10, 2), nullable=False)
    moneda = Column(String(3), nullable=False, default="BOB", server_default="BOB")
    estado = Column(
        Enum(EstadoTransaccion, name="estadotransaccion", create_type=False),
        nullable=False,
        default=EstadoTransaccion.PENDIENTE,
        server_default=EstadoTransaccion.PENDIENTE.value,
    )
    stripe_payment_intent_id = Column(String(120), nullable=True, index=True)
    stripe_checkout_session_id = Column(String(120), nullable=True, index=True)
    referencia = Column(String(120), nullable=True)
    fecha = Column(DateTime(timezone=True), server_default=func.now())
    idempotency_key = Column(String(255), nullable=True)  # [EXAMEN-2] apoyo: evita doble cobro
    datos_respuesta = Column(JSON, nullable=True)  # [EXAMEN-2] apoyo: respuesta de Stripe

    tenant = relationship("Tenant", back_populates="pagos_suscripcion")
    suscripcion = relationship("Suscripcion", back_populates="pagos")

    def __repr__(self):
        return f"<PagoSuscripcion tenant={self.tenant_id} monto={self.monto}: {self.estado}>"


# ============================================================
# [EXAMEN-2] Fase B (punto 6) — Respaldos y restauraciones
# ============================================================

class TipoBackup(str, enum.Enum):
    MANUAL = "MANUAL"
    AUTOMATICO = "AUTOMATICO"


class EstadoBackup(str, enum.Enum):
    EN_PROCESO = "EN_PROCESO"
    COMPLETADO = "COMPLETADO"
    FALLIDO = "FALLIDO"
    EXPIRADO = "EXPIRADO"


class EstadoRestauracion(str, enum.Enum):
    SOLICITADA = "SOLICITADA"
    EN_PROCESO = "EN_PROCESO"
    COMPLETADA = "COMPLETADA"
    FALLIDA = "FALLIDA"


class Backup(Base):
    """Respaldo por tenant (manual o automático) con retención por plan.

    [EXAMEN-2] Fase B / RF-035 / RN-023 / RNF-19: el archivo se guarda en
    `settings.BACKUP_DIR` y la fila conserva hash, tamaño y expiración.
    """
    __tablename__ = "backups"
    __table_args__ = (
        Index("ix_backups_tenant_fecha", "tenant_id", "fecha_inicio"),
        Index("ix_backups_tenant_estado", "tenant_id", "estado"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    tipo = Column(Enum(TipoBackup), nullable=False)
    estado = Column(
        Enum(EstadoBackup), nullable=False, default=EstadoBackup.EN_PROCESO,
        server_default=EstadoBackup.EN_PROCESO.value,
    )
    fecha_inicio = Column(DateTime(timezone=True), server_default=func.now())
    fecha_fin = Column(DateTime(timezone=True), nullable=True)
    tamano_bytes = Column(BigInteger, nullable=True)
    ruta = Column(String(500), nullable=True)
    hash_sha256 = Column(String(64), nullable=True)
    retencion_hasta = Column(DateTime(timezone=True), nullable=True)
    creado_por = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    error_detalle = Column(Text, nullable=True)

    tenant = relationship("Tenant")
    creador = relationship("Usuario", foreign_keys=[creado_por])
    restauraciones = relationship("Restauracion", back_populates="backup")

    def __repr__(self):
        return f"<Backup tenant={self.tenant_id} {self.tipo}: {self.estado}>"


class Restauracion(Base):
    """Solicitud de restauración de un respaldo (RF-035)."""
    __tablename__ = "restauraciones"
    __table_args__ = (
        Index("ix_restauraciones_tenant_fecha", "tenant_id", "fecha_solicitud"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    backup_id = Column(Integer, ForeignKey("backups.id", ondelete="RESTRICT"), nullable=False)
    solicitado_por = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    estado = Column(
        Enum(EstadoRestauracion), nullable=False, default=EstadoRestauracion.SOLICITADA,
        server_default=EstadoRestauracion.SOLICITADA.value,
    )
    motivo = Column(String(200), nullable=True)
    fecha_solicitud = Column(DateTime(timezone=True), server_default=func.now())
    fecha_inicio = Column(DateTime(timezone=True), nullable=True)
    fecha_fin = Column(DateTime(timezone=True), nullable=True)
    error_detalle = Column(Text, nullable=True)

    backup = relationship("Backup", back_populates="restauraciones")
    solicitante = relationship("Usuario", foreign_keys=[solicitado_por])

    def __repr__(self):
        return f"<Restauracion tenant={self.tenant_id} backup={self.backup_id}: {self.estado}>"

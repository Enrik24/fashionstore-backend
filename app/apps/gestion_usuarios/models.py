"""
Modelos SQLAlchemy para la Gestión de Usuarios y Autenticación.
Contiene: Usuario, Rol, Permiso, Cliente, Administrador, EncargadoSucursal, Cajero, Bitacora.
"""
from sqlalchemy import Column, Integer, String, DateTime, Date, ForeignKey, Enum, Text, Float, Boolean, JSON, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from typing import Optional

from app.database import Base


# Enumeraciones
class EstadoUsuario(str, enum.Enum):
    ACTIVO = "ACTIVO"
    INACTIVO = "INACTIVO"
    BLOQUEADO = "BLOQUEADO"


# Modelo: Usuario
class Usuario(Base):
    """Tabla de usuarios del sistema.

    Modificado en Examen 2 (SaaS multitenant): se agrega `tenant_id` (NULL =
    Super Administrador de plataforma), `offline_pin_hash` y
    `debe_cambiar_contrasena`.
    """
    __tablename__ = "usuarios"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(100), nullable=False)
    apellido = Column(String(100), nullable=False)
    correo = Column(String(255), unique=True, nullable=False, index=True)
    telefono = Column(String(20), nullable=True)
    fecha_nacimiento = Column(Date, nullable=True)
    contrasena_hash = Column(String(255), nullable=False)
    estado = Column(Enum(EstadoUsuario), default=EstadoUsuario.ACTIVO, nullable=False)
    fecha_registro = Column(DateTime(timezone=True), server_default=func.now())
    ultimo_acceso = Column(DateTime(timezone=True), nullable=True)
    # [EXAMEN-2] Aislamiento multitenant (nullable: Super Administrador)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    # [EXAMEN-2] CU17: PIN offline hasheado con bcrypt, nunca en claro (RNF-13)
    offline_pin_hash = Column(String(255), nullable=True)
    # [EXAMEN-2] Contraseña temporal / primer acceso
    debe_cambiar_contrasena = Column(Boolean, default=False, server_default="false", nullable=False)
    
    # Relaciones
    tenant = relationship("Tenant", back_populates="usuarios")
    roles = relationship("Rol", secondary="usuarios_roles", back_populates="usuarios")
    cliente = relationship("Cliente", back_populates="usuario", uselist=False)
    administrador = relationship("Administrador", back_populates="usuario", uselist=False)
    encargado_sucursal = relationship("EncargadoSucursal", back_populates="usuario", uselist=False)
    cajero = relationship("Cajero", back_populates="usuario", uselist=False)
    bitacoras = relationship("Bitacora", back_populates="usuario")
    dispositivos_fcm = relationship("DispositivoFCM", back_populates="usuario", cascade="all, delete-orphan")
    # [EXAMEN-2] CU17: dispositivos autorizados a operar offline
    dispositivos = relationship(
        "DispositivoSincronizacion",
        back_populates="usuario",
        foreign_keys="DispositivoSincronizacion.usuario_id",
        cascade="all, delete-orphan",
    )
    
    def __repr__(self):
        return f"<Usuario {self.correo}>"


# Modelo: Rol
class Rol(Base):
    """Tabla de roles del sistema.

    Modificado en Examen 2 (SaaS multitenant): se agrega `tenant_id`
    (NULL = rol de plataforma). El nombre sigue siendo único global para no
    romper el login del Examen 1 (plan, 3.7.1).
    """
    __tablename__ = "roles"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(50), unique=True, nullable=False)
    descripcion = Column(Text, nullable=True)
    # [EXAMEN-2] NULL = rol de plataforma (Super Administrador)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # Relaciones
    permisos = relationship("Permiso", secondary="roles_permisos", back_populates="roles")
    usuarios = relationship("Usuario", secondary="usuarios_roles", back_populates="roles")
    
    def __repr__(self):
        return f"<Rol {self.nombre}>"


# Modelo: Permiso
class Permiso(Base):
    """Tabla de permisos del sistema."""
    __tablename__ = "permisos"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(100), unique=True, nullable=False)
    descripcion = Column(Text, nullable=True)
    
    # Relaciones
    roles = relationship("Rol", secondary="roles_permisos", back_populates="permisos")
    
    def __repr__(self):
        return f"<Permiso {self.nombre}>"


# Tabla intermedia: Usuario-Rol
class UsuarioRol(Base):
    """Tabla intermedia para la relación N:M entre Usuario y Rol.

    Modificado en Examen 2 (SaaS multitenant): se agrega `tenant_id`
    NOT NULL y unicidad compuesta `(tenant_id, usuario_id, rol_id)`.
    Las escrituras deben crear `UsuarioRol` explícitamente para copiar el
    tenant del usuario (no usar `usuario.roles.append`).
    """
    __tablename__ = "usuarios_roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "usuario_id", "rol_id", name="uq_usuarios_roles_tenant_usuario_rol"),
    )
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    rol_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    # [EXAMEN-2] Se copia del usuario al asignar el rol
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    
    def __repr__(self):
        return f"<UsuarioRol usuario={self.usuario_id} rol={self.rol_id}>"


# Tabla intermedia: Rol-Permiso
class RolPermiso(Base):
    """Tabla intermedia para la relación N:M entre Rol y Permiso."""
    __tablename__ = "roles_permisos"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    rol_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    permiso_id = Column(Integer, ForeignKey("permisos.id", ondelete="CASCADE"), nullable=False)
    
    def __repr__(self):
        return f"<RolPermiso rol={self.rol_id} permiso={self.permiso_id}>"


# Modelo: Cliente
class Cliente(Base):
    """Tabla de clientes registrados en la plataforma.

    Modificado en Examen 2 (SaaS multitenant): se agrega `tenant_id`,
    contacto, fechas de sincronización y baja lógica; `usuario_id` y
    `nit_ci` pasan a nullable (cliente creado offline) con unicidad
    compuesta por tenant (plan, 3.4 y 3.6).
    """
    __tablename__ = "clientes"
    __table_args__ = (
        UniqueConstraint("usuario_id", "tenant_id", name="uq_clientes_usuario_tenant"),
        UniqueConstraint("tenant_id", "nit_ci", name="uq_clientes_tenant_nit_ci"),
    )
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=True)
    nit_ci = Column(String(20), nullable=True)
    direccion_envio = Column(String(500), nullable=True)
    preferencias = Column(JSON, nullable=True)  # JSON nativo (Postgres JSON, SQLite TEXT)
    # [EXAMEN-2] Aislamiento multitenant
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    # [EXAMEN-2] Datos de contacto para clientes creados offline
    telefono = Column(String(20), nullable=True)
    correo = Column(String(255), nullable=True)
    # [EXAMEN-2] Descarga incremental y baja lógica (RNF-14)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    # [EXAMEN-2] Fase 3: cliente generado para demostraciones o entrenamiento (RF-023)
    es_sintetico = Column(Boolean, nullable=False, default=False, server_default="false", index=True)
    
    # Relaciones
    usuario = relationship("Usuario", back_populates="cliente")
    carritos = relationship("Carrito", back_populates="cliente")
    ordenes = relationship("Orden", back_populates="cliente")
    reservas = relationship("Reserva", back_populates="cliente")
    ventas_presenciales = relationship("VentaPresencial", back_populates="cliente")
    
    def __repr__(self):
        return f"<Cliente {self.nit_ci}>"


# Modelo: Administrador
class Administrador(Base):
    """Tabla de administradores del sistema.

    Modificado en Examen 2 (SaaS multitenant): se agrega `tenant_id`
    NOT NULL. Es el mismo actor que "Administrador de Tenant" (plan, 3.7.7).
    """
    __tablename__ = "administradores"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, unique=True)
    # [EXAMEN-2] Aislamiento multitenant (se copia del usuario)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Relaciones
    usuario = relationship("Usuario", back_populates="administrador")
    reportes = relationship("Reporte", back_populates="administrador")
    
    def __repr__(self):
        return f"<Administrador {self.usuario_id}>"


# Modelo: Encargado de Sucursal
class EncargadoSucursal(Base):
    """Tabla de encargado de sucursal.

    Modificado en Examen 2 (SaaS multitenant): se agrega `tenant_id` NOT NULL.
    """
    __tablename__ = "encargados_sucursal"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=False)
    # [EXAMEN-2] Aislamiento multitenant (se copia del usuario)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Relaciones
    usuario = relationship("Usuario", back_populates="encargado_sucursal")
    sucursal = relationship("Sucursal", back_populates="encargado")
    
    def __repr__(self):
        return f"<EncargadoSucursal usuario={self.usuario_id} sucursal={self.sucursal_id}>"


# Modelo: Cajero
class Cajero(Base):
    """Tabla de cajeros.

    Modificado en Examen 2 (SaaS multitenant): se agrega `tenant_id` NOT NULL.
    """
    __tablename__ = "cajeros"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=False)
    # [EXAMEN-2] Aislamiento multitenant (se copia del usuario)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Relaciones
    usuario = relationship("Usuario", back_populates="cajero")
    sucursal = relationship("Sucursal", back_populates="cajeros")
    ventas = relationship("VentaPresencial", back_populates="cajero")
    
    def __repr__(self):
        return f"<Cajero usuario={self.usuario_id} sucursal={self.sucursal_id}>"


# Modelo: Bitácora
class Bitacora(Base):
    """Tabla de bitácora para auditoría del sistema.

    Modificado en Examen 2 (SaaS multitenant): se agrega `tenant_id`
    nullable (las acciones de plataforma quedan con NULL).
    """
    __tablename__ = "bitacora"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    fecha_hora = Column(DateTime(timezone=True), server_default=func.now())
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    # [EXAMEN-2] Aislamiento multitenant; NULL = acción de plataforma
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    ip_address = Column(String(45), nullable=True)
    accion = Column(String(255), nullable=False)
    modulo = Column(String(100), nullable=True)
    detalles = Column(Text, nullable=True)
    registro_id = Column(Integer, nullable=True)  # ID del registro afectado
    valores_anteriores = Column(JSON, nullable=True)  # Foto del antes (dict)
    valores_nuevos = Column(JSON, nullable=True)  # Foto del después (dict)
    
    # Relaciones
    usuario = relationship("Usuario", back_populates="bitacoras")
    
    @property
    def created_at(self):
        return self.fecha_hora

    @property
    def ip_origen(self):
        return self.ip_address

    @property
    def tabla_afectada(self):
        return self.modulo

    @property
    def usuario_nombre(self):
        if self.usuario:
            return f"{self.usuario.nombre} {self.usuario.apellido}".strip()
        return None

    def __repr__(self):
        return f"<Bitacora {self.fecha_hora} - {self.accion}>"


# Modelo: DispositivoFCM (Notificaciones Push)
class DispositivoFCM(Base):
    """Tabla de tokens de registro FCM por usuario y dispositivo/navegador."""
    __tablename__ = "dispositivos_fcm"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    token = Column(String(500), unique=True, nullable=False, index=True)
    tipo_dispositivo = Column(String(50), default="web", nullable=False) # web, android, ios
    activo = Column(Boolean, default=True, nullable=False)
    fecha_registro = Column(DateTime(timezone=True), server_default=func.now())
    ultimo_uso = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relaciones
    usuario = relationship("Usuario", back_populates="dispositivos_fcm")
    
    def __repr__(self):
        return f"<DispositivoFCM usuario_id={self.usuario_id} tipo={self.tipo_dispositivo}>"


# Las relaciones inversas con otras apps se configuran automáticamente
# mediante las declaraciones forward de SQLAlchemy
# No necesitamos importaciones explícitas aquí para evitar ciclos
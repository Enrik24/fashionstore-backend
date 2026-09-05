"""
Modelos SQLAlchemy para la Gestión de Usuarios y Autenticación.
Contiene: Usuario, Rol, Permiso, Cliente, Administrador, EncargadoSucursal, Cajero, Bitacora.
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum, Text, Float
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum

from app.database import Base


# Enumeraciones
class EstadoUsuario(str, enum.Enum):
    ACTIVO = "ACTIVO"
    INACTIVO = "INACTIVO"
    BLOQUEADO = "BLOQUEADO"


# Modelo: Usuario
class Usuario(Base):
    """Tabla de usuarios del sistema."""
    __tablename__ = "usuarios"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(100), nullable=False)
    apellido = Column(String(100), nullable=False)
    correo = Column(String(255), unique=True, nullable=False, index=True)
    telefono = Column(String(20), nullable=True)
    contrasena_hash = Column(String(255), nullable=False)
    estado = Column(Enum(EstadoUsuario), default=EstadoUsuario.ACTIVO, nullable=False)
    fecha_registro = Column(DateTime(timezone=True), server_default=func.now())
    ultimo_acceso = Column(DateTime(timezone=True), nullable=True)
    
    # Relaciones
    roles = relationship("Rol", secondary="usuarios_roles", back_populates="usuarios")
    cliente = relationship("Cliente", back_populates="usuario", uselist=False)
    administrador = relationship("Administrador", back_populates="usuario", uselist=False)
    encargado_sucursal = relationship("EncargadoSucursal", back_populates="usuario", uselist=False)
    cajero = relationship("Cajero", back_populates="usuario", uselist=False)
    bitacoras = relationship("Bitacora", back_populates="usuario")
    
    def __repr__(self):
        return f"<Usuario {self.correo}>"


# Modelo: Rol
class Rol(Base):
    """Tabla de roles del sistema."""
    __tablename__ = "roles"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    nombre = Column(String(50), unique=True, nullable=False)
    descripcion = Column(Text, nullable=True)
    
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
    """Tabla intermedia para la relación N:M entre Usuario y Rol."""
    __tablename__ = "usuarios_roles"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    rol_id = Column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    
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
    """Tabla de clientes registrados en la plataforma."""
    __tablename__ = "clientes"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, unique=True)
    nit_ci = Column(String(20), unique=True, nullable=False)
    direccion_envio = Column(String(500), nullable=True)
    
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
    """Tabla de administradores del sistema."""
    __tablename__ = "administradores"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, unique=True)
    
    # Relaciones
    usuario = relationship("Usuario", back_populates="administrador")
    reportes = relationship("Reporte", back_populates="administrador")
    
    def __repr__(self):
        return f"<Administrador {self.usuario_id}>"


# Modelo: Encargado de Sucursal
class EncargadoSucursal(Base):
    """Tabla de encargado de sucursal."""
    __tablename__ = "encargados_sucursal"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=False)
    
    # Relaciones
    usuario = relationship("Usuario", back_populates="encargado_sucursal")
    sucursal = relationship("Sucursal", back_populates="encargado")
    
    def __repr__(self):
        return f"<EncargadoSucursal usuario={self.usuario_id} sucursal={self.sucursal_id}>"


# Modelo: Cajero
class Cajero(Base):
    """Tabla de cajeros."""
    __tablename__ = "cajeros"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id", ondelete="CASCADE"), nullable=False)
    
    # Relaciones
    usuario = relationship("Usuario", back_populates="cajero")
    sucursal = relationship("Sucursal", back_populates="cajeros")
    ventas = relationship("VentaPresencial", back_populates="cajero")
    
    def __repr__(self):
        return f"<Cajero usuario={self.usuario_id} sucursal={self.sucursal_id}>"


# Modelo: Bitácora
class Bitacora(Base):
    """Tabla de bitácora para auditoría del sistema."""
    __tablename__ = "bitacora"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    fecha_hora = Column(DateTime(timezone=True), server_default=func.now())
    usuario_id = Column(Integer, ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True)
    ip_address = Column(String(45), nullable=True)
    accion = Column(String(255), nullable=False)
    modulo = Column(String(100), nullable=True)
    detalles = Column(Text, nullable=True)
    
    # Relaciones
    usuario = relationship("Usuario", back_populates="bitacoras")
    
    def __repr__(self):
        return f"<Bitacora {self.fecha_hora} - {self.accion}>"


# Las relaciones inversas con otras apps se configuran automáticamente
# mediante las declaraciones forward de SQLAlchemy
# No necesitamos importaciones explícitas aquí para evitar ciclos
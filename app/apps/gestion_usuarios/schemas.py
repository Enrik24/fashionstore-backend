"""
Schemas Pydantic para validación y serialización - Gestión de Usuarios y Autenticación.
"""
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional, List
from datetime import datetime
from enum import Enum


# ============================================
# Enums
# ============================================
class EstadoUsuarioEnum(str, Enum):
    ACTIVO = "ACTIVO"
    INACTIVO = "INACTIVO"
    BLOQUEADO = "BLOQUEADO"


# ============================================
# Schemas de Usuario
# ============================================
class UsuarioBase(BaseModel):
    """Schema base para usuario."""
    nombre: str = Field(..., min_length=1, max_length=100)
    apellido: str = Field(..., min_length=1, max_length=100)
    correo: EmailStr
    telefono: Optional[str] = Field(None, max_length=20)


class UsuarioCreate(UsuarioBase):
    """Schema para crear un usuario."""
    contrasena: str = Field(..., min_length=8, max_length=100)
    estado: EstadoUsuarioEnum = EstadoUsuarioEnum.ACTIVO


class UsuarioUpdate(BaseModel):
    """Schema para actualizar un usuario."""
    nombre: Optional[str] = Field(None, min_length=1, max_length=100)
    apellido: Optional[str] = Field(None, min_length=1, max_length=100)
    correo: Optional[EmailStr] = None
    telefono: Optional[str] = Field(None, max_length=20)
    estado: Optional[EstadoUsuarioEnum] = None


class UsuarioResponse(UsuarioBase):
    """Schema para respuesta de usuario."""
    id: int
    estado: EstadoUsuarioEnum
    fecha_registro: datetime
    ultimo_acceso: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)


class UsuarioLogin(BaseModel):
    """Schema para iniciar sesión."""
    correo: EmailStr
    contrasena: str


class TokenResponse(BaseModel):
    """Schema para respuesta de token."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class CambioContrasena(BaseModel):
    """Schema para cambiar contraseña."""
    contrasena_actual: str
    contrasena_nueva: str = Field(..., min_length=8, max_length=100)


# ============================================
# Schemas de Rol
# ============================================
class RolBase(BaseModel):
    """Schema base para rol."""
    nombre: str = Field(..., min_length=1, max_length=50)
    descripcion: Optional[str] = None


class RolCreate(RolBase):
    """Schema para crear un rol."""
    pass


class RolUpdate(BaseModel):
    """Schema para actualizar un rol."""
    nombre: Optional[str] = Field(None, min_length=1, max_length=50)
    descripcion: Optional[str] = None


class RolResponse(RolBase):
    """Schema para respuesta de rol."""
    id: int
    
    model_config = ConfigDict(from_attributes=True)


class PermisoResponse(BaseModel):
    """Schema para respuesta de permiso."""
    id: int
    nombre: str
    descripcion: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Cliente
# ============================================
class ClienteBase(BaseModel):
    """Schema base para cliente."""
    nit_ci: str = Field(..., min_length=1, max_length=20)
    direccion_envio: Optional[str] = Field(None, max_length=500)


class ClienteCreate(UsuarioBase):
    """Schema para crear un cliente (registro)."""
    nit_ci: str = Field(..., min_length=1, max_length=20)
    direccion_envio: Optional[str] = Field(None, max_length=500)
    contrasena: str = Field(..., min_length=8, max_length=100)


class ClienteUpdate(BaseModel):
    """Schema para actualizar un cliente."""
    nit_ci: Optional[str] = Field(None, min_length=1, max_length=20)
    direccion_envio: Optional[str] = Field(None, max_length=500)


class ClienteResponse(ClienteBase):
    """Schema para respuesta de cliente."""
    id: int
    usuario_id: int
    usuario: UsuarioResponse
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Administrador
# ============================================
class AdministradorResponse(BaseModel):
    """Schema para respuesta de administrador."""
    id: int
    usuario_id: int
    usuario: UsuarioResponse
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Encargado de Sucursal
# ============================================
class EncargadoSucursalResponse(BaseModel):
    """Schema para respuesta de encargado de sucursal."""
    id: int
    usuario_id: int
    sucursal_id: int
    usuario: UsuarioResponse
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Cajero
# ============================================
class CajeroResponse(BaseModel):
    """Schema para respuesta de cajero."""
    id: int
    usuario_id: int
    sucursal_id: int
    usuario: UsuarioResponse
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Bitácora
# ============================================
class BitacoraResponse(BaseModel):
    """Schema para respuesta de bitácora."""
    id: int
    fecha_hora: datetime
    usuario_id: Optional[int] = None
    ip_address: Optional[str] = None
    accion: str
    modulo: Optional[str] = None
    detalles: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# Schemas de Perfil de Cliente
# ============================================
class PerfilClienteResponse(BaseModel):
    """Schema para respuesta del perfil del cliente."""
    id: int
    nit_ci: str
    direccion_envio: Optional[str] = None
    nombre: str
    apellido: str
    correo: str
    telefono: Optional[str] = None
    fecha_registro: datetime
    
    model_config = ConfigDict(from_attributes=True)


class ActualizarPerfilRequest(BaseModel):
    """Schema para actualizar perfil del cliente."""
    nombre: Optional[str] = Field(None, min_length=1, max_length=100)
    apellido: Optional[str] = Field(None, min_length=1, max_length=100)
    telefono: Optional[str] = Field(None, max_length=20)
    direccion_envio: Optional[str] = Field(None, max_length=500)


# ============================================
# Schemas adicionales para listados
# ============================================
class UsuarioConRolesResponse(UsuarioResponse):
    """Schema para usuario con sus roles."""
    roles: List[RolResponse] = []
    
    model_config = ConfigDict(from_attributes=True)


class RolConPermisosResponse(RolResponse):
    """Schema para rol con sus permisos."""
    permisos: List[PermisoResponse] = []
    
    model_config = ConfigDict(from_attributes=True)


class AsignarPermisosRequest(BaseModel):
    """Schema para asignar permisos a un rol."""
    permisos_ids: List[int] = Field(default=[])


class AsignarRolRequest(BaseModel):
    """Schema para asignar un rol a un usuario."""
    rol_id: int
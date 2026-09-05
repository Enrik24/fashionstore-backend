"""
Router - Gestión de Usuarios y Autenticación
Contiene todos los endpoints de la App 1.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from typing import List, Optional
from datetime import datetime
import csv
import io

from app.database import get_db
from app.security import get_current_user, require_role
from app.apps.gestion_usuarios.models import Usuario, Cliente
from app.apps.gestion_usuarios import services as usuario_services
from app.apps.gestion_usuarios.schemas import (
    # Usuario
    UsuarioCreate, UsuarioUpdate, UsuarioResponse, UsuarioLogin, TokenResponse,
    CambioContrasena, UsuarioConRolesResponse,
    # Rol
    RolCreate, RolUpdate, RolResponse, RolConPermisosResponse, PermisoResponse,
    AsignarPermisosRequest, AsignarRolRequest,
    # Cliente
    ClienteCreate, ClienteUpdate, ClienteResponse, PerfilClienteResponse,
    ActualizarPerfilRequest,
    # Bitácora
    BitacoraResponse,
)

# Crear router
router = APIRouter(prefix="/api/v1", tags=["Gestión de Usuarios y Autenticación"])


# ============================================
# Endpoints de Autenticación
# ============================================

@router.post("/auth/login", response_model=TokenResponse, name="login")
async def login(
    datos: UsuarioLogin,
    db: AsyncSession = Depends(get_db)
):
    """
    Inicia sesión en la plataforma.
    
    Returns:
        Token de acceso y token de refresco
    """
    tokens, usuario = await usuario_services.AuthService.login(db, datos)
    return tokens


@router.post("/auth/logout", name="logout")
async def logout(
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Cierra la sesión del usuario."""
    await usuario_services.AuthService.logout(db, current_user.id)
    return {"message": "Sesión cerrada exitosamente"}


@router.post("/auth/refresh", response_model=TokenResponse, name="refresh_token")
async def refresh_token(
    datos: dict,
    db: AsyncSession = Depends(get_db)
):
    """
    Refresca el token de acceso usando el token de refresco.
    
    Body:
        - refresh_token: Token de refresco
    """
    refresh_token = datos.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Token de refresco requerido")
    
    return await usuario_services.AuthService.refresh_tokens(db, refresh_token)


@router.post("/auth/register", response_model=TokenResponse, name="register")
async def registro(
    datos: ClienteCreate,
    db: AsyncSession = Depends(get_db)
):
    """
    Registra un nuevo cliente en la plataforma.
    
    Returns:
        Token de acceso y token de refresco
    """
    cliente, tokens = await usuario_services.ClienteService.registro_cliente(db, datos)
    return tokens


@router.get("/auth/me", response_model=UsuarioConRolesResponse, name="get_current_user")
async def get_me(
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene la información del usuario actual incluyendo sus roles."""
    # Cargar los roles del usuario
    result = await db.execute(
        select(Usuario)
        .options(selectinload(Usuario.roles))
        .where(Usuario.id == current_user.id)
    )
    usuario_con_roles = result.scalar_one()
    return usuario_con_roles


@router.put("/auth/me", response_model=UsuarioResponse, name="update_profile")
async def actualizar_perfil(
    datos: UsuarioUpdate,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza el perfil del usuario actual."""
    return await usuario_services.UsuarioService.update_usuario(db, current_user.id, datos)


@router.post("/auth/change-password", name="change_password")
async def cambiar_contrasena(
    datos: CambioContrasena,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Cambia la contraseña del usuario actual."""
    await usuario_services.ClienteService.cambiar_contrasena(
        db, current_user.id, datos.contrasena_actual, datos.contrasena_nueva
    )
    return {"message": "Contraseña cambiada exitosamente"}


# ============================================
# Endpoints de Gestión de Usuarios (Admin)
# ============================================

@router.get("/users/", response_model=List[UsuarioConRolesResponse], name="list_users")
async def listar_usuarios(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    estado: Optional[str] = None,
    rol: Optional[str] = None,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Lista todos los usuarios (solo administradores)."""
    usuarios, total = await usuario_services.UsuarioService.get_usuarios(
        db, skip, limit, estado, rol
    )
    return usuarios


@router.post("/users/", response_model=UsuarioResponse, name="create_user")
async def crear_usuario(
    datos: UsuarioCreate,
    rol: str = Query("Cliente", description="Rol a asignar"),
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea un nuevo usuario (solo administradores)."""
    return await usuario_services.UsuarioService.create_usuario(db, datos, rol)


@router.get("/users/{usuario_id}", response_model=UsuarioConRolesResponse, name="get_user")
async def obtener_usuario(
    usuario_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene un usuario por su ID (solo administradores)."""
    return await usuario_services.UsuarioService.get_usuario(db, usuario_id)


@router.put("/users/{usuario_id}", response_model=UsuarioResponse, name="update_user")
async def actualizar_usuario(
    usuario_id: int,
    datos: UsuarioUpdate,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza un usuario (solo administradores)."""
    return await usuario_services.UsuarioService.update_usuario(db, usuario_id, datos)


@router.delete("/users/{usuario_id}", name="delete_user")
async def eliminar_usuario(
    usuario_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Deshabilita un usuario (solo administradores)."""
    await usuario_services.UsuarioService.delete_usuario(db, usuario_id)
    return {"message": "Usuario deshabilitado exitosamente"}


@router.post("/users/{usuario_id}/roles", response_model=UsuarioResponse, name="assign_role")
async def asignar_rol(
    usuario_id: int,
    datos: AsignarRolRequest,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Asigna un rol a un usuario (solo administradores)."""
    return await usuario_services.UsuarioService.assign_role(db, usuario_id, datos.rol_id)


@router.delete("/users/{usuario_id}/roles/{rol_id}", name="remove_role")
async def remover_rol(
    usuario_id: int,
    rol_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Remueve un rol de un usuario (solo administradores)."""
    await usuario_services.UsuarioService.remove_role(db, usuario_id, rol_id)
    return {"message": "Rol removido exitosamente"}


# ============================================
# Endpoints de Gestión de Roles
# ============================================

@router.get("/roles/", response_model=List[RolConPermisosResponse], name="list_roles")
async def listar_roles(
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Lista todos los roles (solo administradores)."""
    return await usuario_services.RolService.get_roles(db)


@router.post("/roles/", response_model=RolResponse, name="create_role")
async def crear_rol(
    datos: RolCreate,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea un nuevo rol (solo administradores)."""
    return await usuario_services.RolService.create_rol(db, datos)


@router.get("/roles/{rol_id}", response_model=RolConPermisosResponse, name="get_role")
async def obtener_rol(
    rol_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene un rol por su ID (solo administradores)."""
    return await usuario_services.RolService.get_rol(db, rol_id)


@router.put("/roles/{rol_id}", response_model=RolResponse, name="update_role")
async def actualizar_rol(
    rol_id: int,
    datos: RolUpdate,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza un rol (solo administradores)."""
    return await usuario_services.RolService.update_rol(db, rol_id, datos)


@router.delete("/roles/{rol_id}", name="delete_role")
async def eliminar_rol(
    rol_id: int,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina un rol (solo administradores)."""
    await usuario_services.RolService.delete_rol(db, rol_id)
    return {"message": "Rol eliminado exitosamente"}


@router.post("/roles/{rol_id}/permissions", response_model=RolConPermisosResponse, name="assign_permissions")
async def asignar_permisos(
    rol_id: int,
    datos: AsignarPermisosRequest,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Asigna/sincroniza permisos a un rol (solo administradores)."""
    return await usuario_services.RolService.sync_permissions(db, rol_id, datos.permisos_ids)


# ============================================
# Endpoints de Permisos
# ============================================

@router.get("/permissions/", response_model=List[PermisoResponse], name="list_permissions")
async def listar_permisos(
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Lista todos los permisos (solo administradores)."""
    return await usuario_services.PermisoService.get_permisos(db)


# ============================================
# Endpoints de Bitácora
# ============================================

@router.get("/bitacora/", response_model=List[BitacoraResponse], name="list_bitacora")
async def listar_bitacora(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    usuario_id: Optional[int] = None,
    accion: Optional[str] = None,
    modulo: Optional[str] = None,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Consulta la bitácora del sistema (solo administradores)."""
    bitacoras, total = await usuario_services.BitacoraService.get_bitacora(
        db, skip, limit, usuario_id, accion, modulo
    )
    return bitacoras


@router.get("/bitacora/export", name="export_bitacora")
async def exportar_bitacora(
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Exporta la bitácora a CSV (solo administradores)."""
    csv_content = await usuario_services.BitacoraService.exportar_bitacora_csv(db)
    
    return StreamingResponse(
        io.BytesIO(csv_content.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=bitacora.csv"}
    )


# ============================================
# Endpoints de Perfil de Cliente
# ============================================

@router.get("/cliente/perfil", response_model=PerfilClienteResponse, name="get_client_profile")
async def obtener_perfil(
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene el perfil del cliente actual."""
    cliente = await usuario_services.ClienteService.get_cliente_by_usuario(db, current_user.id)
    
    # Devolver perfil formateado
    return {
        "id": cliente.id,
        "nit_ci": cliente.nit_ci,
        "direccion_envio": cliente.direccion_envio,
        "nombre": current_user.nombre,
        "apellido": current_user.apellido,
        "correo": current_user.correo,
        "telefono": current_user.telefono,
        "fecha_registro": current_user.fecha_registro,
    }


@router.put("/cliente/perfil", response_model=PerfilClienteResponse, name="update_client_profile")
async def actualizar_perfil(
    datos: ActualizarPerfilRequest,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza el perfil del cliente actual."""
    cliente = await usuario_services.ClienteService.get_cliente_by_usuario(db, current_user.id)
    
    # Actualizar datos de usuario
    if datos.nombre:
        current_user.nombre = datos.nombre
    if datos.apellido:
        current_user.apellido = datos.apellido
    if datos.telefono:
        current_user.telefono = datos.telefono
    
    await db.commit()
    await db.refresh(current_user)
    
    # Actualizar datos de cliente
    if datos.direccion_envio:
        cliente.direccion_envio = datos.direccion_envio
        await db.commit()
        await db.refresh(cliente)
    
    return {
        "id": cliente.id,
        "nit_ci": cliente.nit_ci,
        "direccion_envio": cliente.direccion_envio,
        "nombre": current_user.nombre,
        "apellido": current_user.apellido,
        "correo": current_user.correo,
        "telefono": current_user.telefono,
        "fecha_registro": current_user.fecha_registro,
    }


@router.put("/cliente/direccion", name="update_client_address")
async def actualizar_direccion(
    direccion: str,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza la dirección de envío del cliente."""
    cliente = await usuario_services.ClienteService.get_cliente_by_usuario(db, current_user.id)
    cliente.direccion_envio = direccion
    await db.commit()
    
    return {"message": "Dirección actualizada exitosamente"}


@router.put("/cliente/preferencias", name="update_client_preferences")
async def actualizar_preferencias(
    datos: dict,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza las preferencias del cliente."""
    # TODO: Implementar preferencias de cliente
    return {"message": "Preferencias actualizadas exitosamente"}


@router.get("/cliente/historial-compras", name="get_purchase_history")
async def historial_compras(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene el historial de compras del cliente."""
    # Se implementará en la App 3
    return {"message": "Historial de compras - pendiente de implementar en App 3"}


@router.get("/cliente/historial-reservas", name="get_reservations_history")
async def historial_reservas(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene el historial de reservas del cliente."""
    # Se implementará en la App 3
    return {"message": "Historial de reservas - pendiente de implementar en App 3"}


# ============================================
# Función para incluir las rutas en la app principal
# ============================================

def include_router(app):
    """Incluye las rutas en la aplicación principal."""
    app.include_router(router)
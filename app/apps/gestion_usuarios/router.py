"""
Router - Gestión de Usuarios y Autenticación
Contiene todos los endpoints de la App 1.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from typing import List, Optional
from datetime import datetime
import csv
import io

from app.database import get_db
from app.security import get_current_user, require_role, get_client_ip
from app.apps.gestion_usuarios.models import Usuario, Cliente, EncargadoSucursal, Cajero
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
    # FCM
    RegistrarDispositivoRequest, DispositivoFCMResponse,
)
from app.services.firebase_service import FirebaseService

# Crear router
router = APIRouter(prefix="/api/v1", tags=["Gestión de Usuarios y Autenticación"])


# ============================================
# Endpoints de Autenticación
# ============================================

@router.post("/auth/login", response_model=TokenResponse, name="login")
async def login(
    datos: UsuarioLogin,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Inicia sesión en la plataforma.
    
    Returns:
        Token de acceso y token de refresco
    """
    ip_address = get_client_ip(request)
    tokens, usuario = await usuario_services.AuthService.login(db, datos, ip_address=ip_address)
    return tokens


@router.post("/auth/logout", name="logout")
async def logout(
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Cierra la sesión del usuario."""
    ip_address = get_client_ip(request)
    await usuario_services.AuthService.logout(db, current_user.id, ip_address=ip_address)
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
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Registra un nuevo cliente en la plataforma.
    
    Returns:
        Token de acceso y token de refresco
    """
    ip_address = get_client_ip(request)
    cliente, tokens = await usuario_services.ClienteService.registro_cliente(db, datos, ip_address=ip_address)
    return tokens


@router.get("/auth/me", response_model=UsuarioConRolesResponse, name="get_current_user")
async def get_me(
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene la información del usuario actual incluyendo sus roles y sucursal asignada."""
    result = await db.execute(
        select(Usuario)
        .options(
            selectinload(Usuario.roles),
            selectinload(Usuario.encargado_sucursal).selectinload(EncargadoSucursal.sucursal),
            selectinload(Usuario.cajero).selectinload(Cajero.sucursal)
        )
        .where(Usuario.id == current_user.id)
    )
    usuario_con_roles = result.scalar_one()
    
    suc_id = None
    suc_nom = None
    if usuario_con_roles.encargado_sucursal:
        suc_id = usuario_con_roles.encargado_sucursal.sucursal_id
        if usuario_con_roles.encargado_sucursal.sucursal:
            suc_nom = usuario_con_roles.encargado_sucursal.sucursal.nombre
    elif usuario_con_roles.cajero:
        suc_id = usuario_con_roles.cajero.sucursal_id
        if usuario_con_roles.cajero.sucursal:
            suc_nom = usuario_con_roles.cajero.sucursal.nombre

    resp = UsuarioConRolesResponse.model_validate(usuario_con_roles)
    resp.sucursal_id = suc_id
    resp.sucursal_nombre = suc_nom
    return resp


@router.put("/auth/me", response_model=UsuarioResponse, name="update_profile")
async def actualizar_perfil_auth(
    datos: UsuarioUpdate,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza el perfil del usuario actual."""
    antes = usuario_services.BitacoraService.foto(
        await usuario_services.UsuarioService.get_usuario(db, current_user.id)
    )
    usuario = await usuario_services.UsuarioService.update_usuario(db, current_user.id, datos)
    await usuario_services.BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_PERFIL",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Perfil",
        detalles=f"Usuario {current_user.correo} actualizó su perfil",
        registro_id=current_user.id,
        valores_anteriores=antes,
        valores_nuevos=usuario_services.BitacoraService.foto(usuario)
    )
    return usuario


@router.post("/auth/change-password", name="change_password")
async def cambiar_contrasena(
    datos: CambioContrasena,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Cambia la contraseña del usuario actual."""
    ip_address = get_client_ip(request)
    await usuario_services.ClienteService.cambiar_contrasena(
        db, current_user.id, datos.contrasena_actual, datos.contrasena_nueva, ip_address=ip_address
    )
    return {"message": "Contraseña cambiada exitosamente"}


@router.post("/cliente/change-password", name="client_change_password")
async def cambiar_contrasena_cliente(
    datos: dict,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Cambia la contraseña del cliente (endpoint alternativo que acepta formato frontend)."""
    ip_address = get_client_ip(request)
    
    # Mapear campos del frontend (password_actual/password_nuevo) a backend (contrasena_actual/contrasena_nueva)
    password_actual = datos.get('password_actual') or datos.get('contrasena_actual')
    password_nuevo = datos.get('password_nuevo') or datos.get('contrasena_nueva')
    
    if not password_actual or not password_nuevo:
        raise HTTPException(status_code=400, detail="Se requieren password_actual y password_nuevo")
    
    await usuario_services.ClienteService.cambiar_contrasena(
        db, current_user.id, password_actual, password_nuevo, ip_address=ip_address
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
    request: Request,
    rol: str = Query("Cliente", description="Rol a asignar"),
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea un nuevo usuario (solo administradores)."""
    usuario = await usuario_services.UsuarioService.create_usuario(db, datos, rol)
    await usuario_services.BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_USUARIO",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Usuarios",
        detalles=f"Usuario creado: {usuario.nombre} {usuario.apellido} ({usuario.correo}) con rol {rol}"
    )
    return usuario


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
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza un usuario (solo administradores)."""
    antes = usuario_services.BitacoraService.foto(
        await usuario_services.UsuarioService.get_usuario(db, usuario_id)
    )
    usuario = await usuario_services.UsuarioService.update_usuario(db, usuario_id, datos)
    await usuario_services.BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_USUARIO",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Usuarios",
        detalles=f"Usuario actualizado: ID {usuario.id} ({usuario.correo})",
        registro_id=usuario.id,
        valores_anteriores=antes,
        valores_nuevos=usuario_services.BitacoraService.foto(usuario)
    )
    return usuario


@router.delete("/users/{usuario_id}", name="delete_user")
async def eliminar_usuario(
    usuario_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Deshabilita un usuario (solo administradores)."""
    await usuario_services.UsuarioService.delete_usuario(db, usuario_id)
    await usuario_services.BitacoraService.registrar_evento(
        db=db,
        accion="DESHABILITAR_USUARIO",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Usuarios",
        detalles=f"Usuario deshabilitado: ID {usuario_id}"
    )
    return {"message": "Usuario deshabilitado exitosamente"}


@router.post("/users/{usuario_id}/roles", response_model=UsuarioResponse, name="assign_role")
async def asignar_rol(
    usuario_id: int,
    datos: AsignarRolRequest,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Asigna un rol a un usuario (solo administradores)."""
    roles_antes = sorted([r.nombre for r in (await usuario_services.UsuarioService.get_usuario(db, usuario_id)).roles])
    usuario = await usuario_services.UsuarioService.assign_role(db, usuario_id, datos.rol_id)
    roles_despues = sorted([r.nombre for r in (await usuario_services.UsuarioService.get_usuario(db, usuario_id)).roles])
    await usuario_services.BitacoraService.registrar_evento(
        db=db,
        accion="ASIGNAR_ROL",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Usuarios",
        detalles=f"Rol ID {datos.rol_id} asignado a usuario ID {usuario_id}",
        registro_id=usuario_id,
        valores_anteriores={"roles": roles_antes},
        valores_nuevos={"roles": roles_despues}
    )
    return usuario


@router.delete("/users/{usuario_id}/roles/{rol_id}", name="remove_role")
async def remover_rol(
    usuario_id: int,
    rol_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Remueve un rol de un usuario (solo administradores)."""
    roles_antes = sorted([r.nombre for r in (await usuario_services.UsuarioService.get_usuario(db, usuario_id)).roles])
    await usuario_services.UsuarioService.remove_role(db, usuario_id, rol_id)
    roles_despues = sorted([r.nombre for r in (await usuario_services.UsuarioService.get_usuario(db, usuario_id)).roles])
    await usuario_services.BitacoraService.registrar_evento(
        db=db,
        accion="REMOVER_ROL",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Usuarios",
        detalles=f"Rol ID {rol_id} removido de usuario ID {usuario_id}",
        registro_id=usuario_id,
        valores_anteriores={"roles": roles_antes},
        valores_nuevos={"roles": roles_despues}
    )
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
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Crea un nuevo rol (solo administradores)."""
    rol = await usuario_services.RolService.create_rol(db, datos)
    await usuario_services.BitacoraService.registrar_evento(
        db=db,
        accion="CREAR_ROL",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Roles",
        detalles=f"Rol creado: {rol.nombre} (ID: {rol.id})"
    )
    return rol


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
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza un rol (solo administradores)."""
    rol = await usuario_services.RolService.update_rol(db, rol_id, datos)
    await usuario_services.BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_ROL",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Roles",
        detalles=f"Rol actualizado: ID {rol_id} ({rol.nombre})"
    )
    return rol


@router.delete("/roles/{rol_id}", name="delete_role")
async def eliminar_rol(
    rol_id: int,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Elimina un rol (solo administradores)."""
    await usuario_services.RolService.delete_rol(db, rol_id)
    await usuario_services.BitacoraService.registrar_evento(
        db=db,
        accion="ELIMINAR_ROL",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Roles",
        detalles=f"Rol eliminado: ID {rol_id}"
    )
    return {"message": "Rol eliminado exitosamente"}


@router.post("/roles/{rol_id}/permissions", response_model=RolConPermisosResponse, name="assign_permissions")
async def asignar_permisos(
    rol_id: int,
    datos: AsignarPermisosRequest,
    request: Request,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Asigna/sincroniza permisos a un rol (solo administradores)."""
    rol = await usuario_services.RolService.sync_permissions(db, rol_id, datos.permisos_ids)
    await usuario_services.BitacoraService.registrar_evento(
        db=db,
        accion="ASIGNAR_PERMISOS_ROL",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Roles",
        detalles=f"Permisos sincronizados para rol ID {rol_id}: {datos.permisos_ids}"
    )
    return rol


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
    tabla: Optional[str] = None,
    fecha_inicio: Optional[datetime] = None,
    fecha_fin: Optional[datetime] = None,
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Consulta la bitácora del sistema (solo administradores)."""
    modulo_filtro = modulo or tabla
    bitacoras, total = await usuario_services.BitacoraService.get_bitacora(
        db, skip, limit, usuario_id, accion, modulo_filtro, fecha_inicio, fecha_fin
    )
    return bitacoras


@router.get("/bitacora/export", name="export_bitacora")
async def exportar_bitacora(
    formato: str = Query("CSV"),
    current_user: Usuario = Depends(require_role("Administrador")),
    db: AsyncSession = Depends(get_db)
):
    """Exporta la bitácora a CSV/Excel/HTML/PDF (solo administradores)."""
    from app.apps.servicios_inteligentes.export_service import responder_reporte
    bitacoras, _ = await usuario_services.BitacoraService.get_bitacora(db, limit=10000)
    datos = {"evento": [{"id": b.id, "fecha": str(getattr(b, "fecha_hora", "")),
                         "usuario": getattr(b, "usuario_id", ""), "accion": getattr(b, "accion", ""),
                         "modulo": getattr(b, "modulo", ""), "detalles": getattr(b, "detalles", "")}
                        for b in bitacoras],
             "desglose_estados": {}}
    # Reusar detalle genérico: mapear a filas de bitácora
    datos_det = {"inventario": [{"inventario_id": r["id"], "producto_nombre": r["accion"],
                                "sku": r["modulo"], "talla": "", "color": "",
                                "sucursal": str(r["usuario"]), "cantidad_disponible": 0,
                                "alerta_bajo_stock": False} for r in datos["evento"][:200]]} if datos["evento"] else {}
    f = (formato or "CSV").upper()
    if f == "CSV":
        csv_content = await usuario_services.BitacoraService.exportar_bitacora_csv(db)
        return StreamingResponse(io.BytesIO(csv_content.encode("utf-8")), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=bitacora.csv"})
    content, media, fname = responder_reporte("BITACORA", {**datos, **datos_det}, f)
    from fastapi.responses import Response as FastAPIResponse
    return FastAPIResponse(content=content, media_type=media,
                           headers={"Content-Disposition": f"attachment; filename={fname}"})


# ============================================
# Endpoints de Perfil de Cliente
# ============================================

@router.get("/cliente/perfil", response_model=PerfilClienteResponse, name="get_client_profile")
async def obtener_perfil(
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene el perfil del cliente actual."""
    import json
    
    cliente = await usuario_services.ClienteService.get_cliente_by_usuario(db, current_user.id)
    
    # Deserializar preferencias si existen
    preferencias = None
    if cliente.preferencias:
        try:
            preferencias = json.loads(cliente.preferencias) if isinstance(cliente.preferencias, str) else cliente.preferencias
        except:
            preferencias = None
    
    # Devolver perfil formateado
    return {
        "id": cliente.id,
        "nit_ci": cliente.nit_ci,
        "direccion_envio": cliente.direccion_envio,
        "nombre": current_user.nombre,
        "apellido": current_user.apellido,
        "correo": current_user.correo,
        "telefono": current_user.telefono,
        "fecha_nacimiento": current_user.fecha_nacimiento,
        "fecha_registro": current_user.fecha_registro,
        "preferencias": preferencias,
    }


@router.put("/cliente/perfil", response_model=PerfilClienteResponse, name="update_client_profile")
async def actualizar_perfil(
    datos: ActualizarPerfilRequest,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza el perfil del cliente actual."""
    import json
    from sqlalchemy import select as sa_select
    from app.apps.gestion_usuarios.models import Cliente as ClienteModel

    cliente = await usuario_services.ClienteService.get_cliente_by_usuario(db, current_user.id)
    
    # Actualizar datos de usuario (tabla usuarios)
    if datos.nombre:
        current_user.nombre = datos.nombre
    if datos.apellido:
        current_user.apellido = datos.apellido
    if datos.telefono is not None:  # Permitir vacío para borrar
        current_user.telefono = datos.telefono or None
    if datos.fecha_nacimiento is not None:
        current_user.fecha_nacimiento = datos.fecha_nacimiento
    
    # Actualizar datos de cliente (tabla clientes): nit_ci con alias ci_nit
    nuevo_nit = datos.nit_ci or datos.ci_nit
    if nuevo_nit and nuevo_nit != cliente.nit_ci:
        result = await db.execute(sa_select(ClienteModel).where(ClienteModel.nit_ci == nuevo_nit))
        existente = result.scalar_one_or_none()
        if existente and existente.id != cliente.id:
            raise HTTPException(status_code=409, detail="El NIT/CI ya está registrado")
        cliente.nit_ci = nuevo_nit

    # Actualizar dirección si viene en este mismo request
    if datos.direccion_envio is not None:  # Permitir vacío para borrar
        cliente.direccion_envio = datos.direccion_envio or None

    await db.commit()
    await db.refresh(current_user)
    await db.refresh(cliente)

    await usuario_services.BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_PERFIL_CLIENTE",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Perfil",
        detalles=f"Cliente ID {cliente.id} actualizó datos de su perfil"
    )

    preferencias = None
    if cliente.preferencias:
        try:
            preferencias = json.loads(cliente.preferencias) if isinstance(cliente.preferencias, str) else cliente.preferencias
        except:
            preferencias = None
    
    return {
        "id": cliente.id,
        "nit_ci": cliente.nit_ci,
        "direccion_envio": cliente.direccion_envio,
        "nombre": current_user.nombre,
        "apellido": current_user.apellido,
        "correo": current_user.correo,
        "telefono": current_user.telefono,
        "fecha_nacimiento": current_user.fecha_nacimiento,
        "fecha_registro": current_user.fecha_registro,
        "preferencias": preferencias,
    }


@router.put("/cliente/direccion", name="update_client_address")
async def actualizar_direccion(
    datos: dict,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza la dirección de envío del cliente."""
    cliente = await usuario_services.ClienteService.get_cliente_by_usuario(db, current_user.id)

    # Aceptar tanto el formato por partes (direccion/ciudad/referencia)
    # como el formato directo (direccion_envio) que envían otros clientes.
    direccion_base = (datos.get('direccion_envio') or datos.get('direccion') or '').strip()
    ciudad = (datos.get('ciudad') or '').strip()
    referencia = (datos.get('referencia') or '').strip()

    if datos.get('direccion_envio') and not ciudad and not referencia:
        direccion_completa = direccion_base
    else:
        direccion_completa = direccion_base
        if ciudad:
            direccion_completa += f", {ciudad}" if direccion_completa else ciudad
        if referencia:
            direccion_completa += f" (Ref: {referencia})" if direccion_completa else f"(Ref: {referencia})"
    
    cliente.direccion_envio = direccion_completa or None
    await db.commit()
    
    await usuario_services.BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_DIRECCION_CLIENTE",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Perfil",
        detalles=f"Cliente ID {cliente.id} actualizó su dirección de envío"
    )

    return {"message": "Dirección actualizada exitosamente", "direccion_envio": cliente.direccion_envio}


@router.put("/cliente/preferencias", name="update_client_preferences")
async def actualizar_preferencias(
    datos: dict,
    request: Request,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Actualiza las preferencias del cliente."""
    cliente = await usuario_services.ClienteService.get_cliente_by_usuario(db, current_user.id)

    # La columna es JSON nativa: asignar el dict directamente (no json.dumps,
    # que Postgres rechaza con DatatypeMismatchError json vs varchar).
    cliente.preferencias = datos
    await db.commit()
    await db.refresh(cliente)
    
    await usuario_services.BitacoraService.registrar_evento(
        db=db,
        accion="ACTUALIZAR_PREFERENCIAS_CLIENTE",
        usuario_id=current_user.id,
        ip_address=get_client_ip(request),
        modulo="Perfil",
        detalles=f"Cliente ID {cliente.id} actualizó sus preferencias"
    )

    return {"message": "Preferencias actualizadas exitosamente", "preferencias": datos}


@router.get("/cliente/historial-compras", name="get_purchase_history")
async def historial_compras(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene el historial de compras del cliente."""
    from app.apps.gestion_ventas.services import OrdenService
    res_c = await db.execute(select(Cliente).where(Cliente.usuario_id == current_user.id))
    cliente = res_c.scalar_one_or_none()
    if not cliente:
        raise HTTPException(status_code=404, detail="Perfil de cliente no encontrado")
    return await OrdenService.obtener_ordenes_cliente(db, cliente.id, skip=skip, limit=limit)


@router.get("/cliente/historial-reservas", name="get_reservations_history")
async def historial_reservas(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene el historial de reservas del cliente."""
    from app.apps.gestion_ventas.services import ReservaService
    res_c = await db.execute(select(Cliente).where(Cliente.usuario_id == current_user.id))
    cliente = res_c.scalar_one_or_none()
    if not cliente:
        raise HTTPException(status_code=404, detail="Perfil de cliente no encontrado")
    return await ReservaService.listar_reservas_cliente(db, cliente.id)


# ============================================
# Endpoints de Búsqueda de Clientes (POS / Ventas)
# ============================================

@router.get("/clientes/buscar", response_model=List[PerfilClienteResponse], name="search_clients")
async def buscar_clientes(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=50),
    current_user: Usuario = Depends(require_role("Administrador", "Encargado", "Cajero")),
    db: AsyncSession = Depends(get_db)
):
    """Busca clientes por NIT/CI, nombre, apellido, correo o teléfono."""
    clientes = await usuario_services.ClienteService.buscar_clientes(db, q, limit)
    return [
        {
            "id": c.id,
            "nit_ci": c.nit_ci,
            "direccion_envio": c.direccion_envio,
            "nombre": c.usuario.nombre if c.usuario else "",
            "apellido": c.usuario.apellido if c.usuario else "",
            "correo": c.usuario.correo if c.usuario else "",
            "telefono": c.usuario.telefono if c.usuario else "",
            "fecha_registro": c.usuario.fecha_registro if c.usuario else datetime.now(),
        }
        for c in clientes
    ]


@router.get("/clientes/por-nit/{nit_ci}", response_model=PerfilClienteResponse, name="get_client_by_nit")
async def obtener_cliente_por_nit(
    nit_ci: str,
    current_user: Usuario = Depends(require_role("Administrador", "Encargado", "Cajero")),
    db: AsyncSession = Depends(get_db)
):
    """Obtiene un cliente por su NIT/CI exacto."""
    c = await usuario_services.ClienteService.get_cliente_by_nit(db, nit_ci)
    if not c:
        raise HTTPException(status_code=404, detail="Cliente no encontrado con ese NIT/CI")
    return {
        "id": c.id,
        "nit_ci": c.nit_ci,
        "direccion_envio": c.direccion_envio,
        "nombre": c.usuario.nombre if c.usuario else "",
        "apellido": c.usuario.apellido if c.usuario else "",
        "correo": c.usuario.correo if c.usuario else "",
        "telefono": c.usuario.telefono if c.usuario else "",
        "fecha_registro": c.usuario.fecha_registro if c.usuario else datetime.now(),
    }


# ============================================
# Endpoints de Notificaciones Push (FCM)
# ============================================

@router.post("/notificaciones/dispositivos", response_model=DispositivoFCMResponse, name="register_fcm_device")
async def registrar_dispositivo_fcm(
    datos: RegistrarDispositivoRequest,
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Registra o actualiza el token FCM de un dispositivo para el usuario actual."""
    return await FirebaseService.registrar_dispositivo(
        db=db,
        usuario_id=current_user.id,
        token=datos.token,
        tipo_dispositivo=datos.tipo_dispositivo
    )


@router.delete("/notificaciones/dispositivos", name="unregister_fcm_device")
async def desregistrar_dispositivo_fcm(
    token: str = Query(..., min_length=10),
    current_user: Usuario = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Desactiva un token FCM (al cerrar sesión en el dispositivo)."""
    exito = await FirebaseService.eliminar_dispositivo(db=db, token=token)
    return {"ok": exito, "mensaje": "Dispositivo desregistrado correctamente" if exito else "Token no encontrado"}


# ============================================
# Función para incluir las rutas en la app principal
# ============================================

def include_router(app):
    """Incluye las rutas en la aplicación principal."""
    app.include_router(router)
"""
Servicios de negocio para Gestión de Usuarios y Autenticación.
"""
from typing import Optional, List, Tuple, Dict, Any
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, delete, func
from sqlalchemy.orm import selectinload
import secrets

from app.security import (
    get_password_hash, verify_password, 
    create_access_token, create_refresh_token, decode_token,
    construir_claims_usuario,
)
from app.config import settings
from app.apps.gestion_usuarios.models import (
    Usuario, Rol, Permiso, UsuarioRol, RolPermiso,
    Cliente, Administrador, EncargadoSucursal, Cajero, Bitacora, EstadoUsuario
)
from app.apps.gestion_usuarios.schemas import (
    UsuarioCreate, UsuarioUpdate, UsuarioLogin,
    ClienteCreate, ClienteUpdate, RolCreate, RolUpdate
)
from app.exceptions import (
    NotFoundException, ConflictException, AuthenticationException, ValidationException
)


# ============================================
# Servicios de Autenticación
# ============================================

class AuthService:
    """Servicio de autenticación."""
    
    @staticmethod
    async def login(db: AsyncSession, datos: UsuarioLogin, ip_address: Optional[str] = None) -> Tuple[dict, Usuario]:
        """
        Autentica al usuario y retorna tokens JWT.
        
        Args:
            db: Sesión de base de datos
            datos: Credenciales del usuario
            ip_address: Dirección IP del cliente
        
        Returns:
            Tupla con (tokens, usuario)
        
        Raises:
            AuthenticationException: Si las credenciales son inválidas
        """
        # Buscar usuario por correo (con roles para los claims del token)
        result = await db.execute(
            select(Usuario)
            .options(selectinload(Usuario.roles), selectinload(Usuario.tenant))
            .where(Usuario.correo == datos.correo)
        )
        usuario = result.scalar_one_or_none()
        
        # Verificar que existe y la contraseña
        if not usuario or not verify_password(datos.contrasena, usuario.contrasena_hash):
            # Registrar intento fallido en bitácora
            await AuthService._registrar_bitacora(
                db, None, "INTENTO_LOGIN_FALLIDO", 
                f"Intento de login con correo: {datos.correo}", ip_address=ip_address
            )
            raise AuthenticationException("Credenciales inválidas")
        
        # Verificar estado del usuario
        if usuario.estado == EstadoUsuario.BLOQUEADO:
            raise AuthenticationException("Usuario bloqueado. Contacte al administrador.")
        
        if usuario.estado == EstadoUsuario.INACTIVO:
            raise AuthenticationException("Usuario inactivo. Contacte al administrador.")
        
        # [EXAMEN-2] RN-004: validar tenant y suscripción (salvo Super Admin)
        tenant_info = None
        suscripcion_info = None
        if usuario.tenant_id is not None:
            from app.apps.gestion_saas.models import Suscripcion
            from app.tenant import get_suscripcion_activa

            suscripcion = await get_suscripcion_activa(db, usuario.tenant_id)
            tenant_info = {
                "id": usuario.tenant.id,
                "slug": usuario.tenant.slug,
                "razon_social": usuario.tenant.razon_social,
                "estado": usuario.tenant.estado.value if hasattr(usuario.tenant.estado, "value") else str(usuario.tenant.estado),
            } if usuario.tenant else None
            suscripcion_info = {
                "id": suscripcion.id,
                "plan_id": suscripcion.plan_id,
                "estado": suscripcion.estado.value if hasattr(suscripcion.estado, "value") else str(suscripcion.estado),
                "estado_pago": suscripcion.estado_pago,
                "fecha_vencimiento": suscripcion.fecha_vencimiento.isoformat(),
            }

        # Actualizar último acceso
        usuario.ultimo_acceso = datetime.utcnow()
        await db.commit()
        
        # Generar tokens con tenant, super admin y roles
        token_data = construir_claims_usuario(usuario)
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)
        
        # Registrar login exitoso
        await AuthService._registrar_bitacora(
            db, usuario.id, "LOGIN_EXITOSO", 
            "Usuario inició sesión", ip_address=ip_address, tenant_id=usuario.tenant_id
        )
        
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "tenant_id": usuario.tenant_id,
            "es_super_admin": token_data["es_super_admin"],
            "roles": token_data["roles"],
            "debe_cambiar_contrasena": usuario.debe_cambiar_contrasena,
            "tenant": tenant_info,
            "suscripcion": suscripcion_info,
        }, usuario
    
    @staticmethod
    async def refresh_tokens(db: AsyncSession, refresh_token: str) -> dict:
        """
        Renueva los tokens de acceso usando el token de refresco.
        
        Args:
            db: Sesión de base de datos
            refresh_token: Token de refresco
        
        Returns:
            Nuevos tokens de acceso
        """
        payload = decode_token(refresh_token)
        
        if payload.get("type") != "refresh":
            raise AuthenticationException("Token de refresco inválido")
        
        user_id_raw = payload.get("sub")
        if user_id_raw is None:
            raise AuthenticationException("Token de refresco inválido")
        
        try:
            user_id = int(user_id_raw)
        except (ValueError, TypeError):
            raise AuthenticationException("Token de refresco inválido")
        
        # Buscar usuario con roles para reconstruir los claims
        result = await db.execute(
            select(Usuario)
            .options(selectinload(Usuario.roles), selectinload(Usuario.tenant))
            .where(Usuario.id == user_id)
        )
        usuario = result.scalar_one_or_none()
        
        if not usuario or (usuario.estado != EstadoUsuario.ACTIVO and usuario.estado != "ACTIVO"):
            raise AuthenticationException("Usuario no encontrado o inactivo")

        # [EXAMEN-2] El refresco no puede cambiar de tenant (plan, 4.2)
        claim_tenant = payload.get("tenant_id")
        if claim_tenant is not None and int(claim_tenant) != usuario.tenant_id:
            raise AuthenticationException("El token pertenece a otro tenant")

        # [EXAMEN-2] RN-004: revalidar tenant y suscripción antes de emitir
        tenant_info = None
        suscripcion_info = None
        if usuario.tenant_id is not None:
            from app.tenant import get_suscripcion_activa

            if usuario.tenant is None:
                from app.apps.gestion_saas.models import Tenant

                res_tenant = await db.execute(select(Tenant).where(Tenant.id == usuario.tenant_id))
                tenant_obj = res_tenant.scalar_one_or_none()
            else:
                tenant_obj = usuario.tenant
            if tenant_obj is None or getattr(tenant_obj.estado, "value", tenant_obj.estado) in ("SUSPENDIDO", "CANCELADO"):
                raise AuthenticationException("La empresa está suspendida o cancelada")

            suscripcion = await get_suscripcion_activa(db, usuario.tenant_id)
            tenant_info = {
                "id": tenant_obj.id,
                "slug": tenant_obj.slug,
                "razon_social": tenant_obj.razon_social,
                "estado": getattr(tenant_obj.estado, "value", str(tenant_obj.estado)),
            }
            suscripcion_info = {
                "id": suscripcion.id,
                "plan_id": suscripcion.plan_id,
                "estado": getattr(suscripcion.estado, "value", str(suscripcion.estado)),
                "estado_pago": suscripcion.estado_pago,
                "fecha_vencimiento": suscripcion.fecha_vencimiento.isoformat(),
            }
        
        # Generar nuevos tokens con tenant, super admin y roles
        token_data = construir_claims_usuario(usuario)
        access_token = create_access_token(token_data)
        new_refresh_token = create_refresh_token(token_data)
        
        return {
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
            "tenant_id": usuario.tenant_id,
            "es_super_admin": token_data["es_super_admin"],
            "roles": token_data["roles"],
            "debe_cambiar_contrasena": usuario.debe_cambiar_contrasena,
            "tenant": tenant_info,
            "suscripcion": suscripcion_info,
        }
    
    @staticmethod
    async def logout(
        db: AsyncSession,
        usuario_id: int,
        ip_address: Optional[str] = None,
        tenant_id: Optional[int] = None,
    ):
        """Registra el cierre de sesión con el tenant del usuario."""
        if tenant_id is None:
            result = await db.execute(select(Usuario.tenant_id).where(Usuario.id == usuario_id))
            tenant_id = result.scalar_one_or_none()
        await AuthService._registrar_bitacora(
            db, usuario_id, "LOGOUT", 
            "Usuario cerró sesión", ip_address=ip_address, tenant_id=tenant_id
        )
    
    @staticmethod
    async def _registrar_bitacora(
        db: AsyncSession, 
        usuario_id: Optional[int], 
        accion: str, 
        detalles: str,
        modulo: str = "Autenticacion",
        ip_address: Optional[str] = None,
        tenant_id: Optional[int] = None,
    ):
        """Registra un evento en la bitácora."""
        bitacora = Bitacora(
            usuario_id=usuario_id,
            tenant_id=tenant_id,
            accion=accion,
            modulo=modulo,
            detalles=detalles,
            ip_address=ip_address
        )
        db.add(bitacora)
        await db.commit()


# ============================================
# Servicios de Gestión de Usuarios
# ============================================

class UsuarioService:
    """Servicio de gestión de usuarios."""
    
    @staticmethod
    async def create_usuario(
        db: AsyncSession,
        datos: UsuarioCreate,
        rol_nombre: str = "Cliente",
        tenant_id: Optional[int] = None,
    ) -> Usuario:
        """
        Crea un nuevo usuario en el sistema.
        
        Args:
            db: Sesión de base de datos
            datos: Datos del usuario a crear
            rol_nombre: Nombre del rol a asignar
            tenant_id: Tenant destino (por defecto, el del contexto)
        
        Returns:
            Usuario creado
        """
        from app.tenant import tenant_actual

        tenant_id = tenant_id if tenant_id is not None else tenant_actual()
        if tenant_id is None:
            raise ConflictException("No se pudo determinar el tenant del usuario")

        # [EXAMEN-2] Un tenant no puede crear Super Administradores de plataforma
        if rol_nombre == "Super Administrador":
            raise ValidationException("No se puede crear un Super Administrador desde un tenant")

        # Verificar correo duplicado (global, decisión 3.7.1)
        result = await db.execute(
            select(Usuario).where(Usuario.correo == datos.correo)
        )
        if result.scalar_one_or_none():
            raise ConflictException("El correo electrónico ya está registrado")
        
        # Crear usuario
        usuario = Usuario(
            nombre=datos.nombre,
            apellido=datos.apellido,
            correo=datos.correo,
            telefono=datos.telefono,
            contrasena_hash=get_password_hash(datos.contrasena),
            estado=datos.estado,
            tenant_id=tenant_id,
        )
        db.add(usuario)
        await db.flush()
        
        # Asignar rol del tenant
        result = await db.execute(
            select(Rol).where(Rol.nombre == rol_nombre)
        )
        rol = result.scalar_one_or_none()
        
        if rol:
            usuario_rol = UsuarioRol(
                usuario_id=usuario.id, rol_id=rol.id, tenant_id=tenant_id
            )
            db.add(usuario_rol)
            
            # Si se proporcionó sucursal_id, crear el registro de personal correspondiente
            if getattr(datos, "sucursal_id", None):
                rol_lower = rol_nombre.lower()
                if "encargado" in rol_lower:
                    encargado = EncargadoSucursal(
                        usuario_id=usuario.id,
                        sucursal_id=datos.sucursal_id,
                        tenant_id=tenant_id,
                    )
                    db.add(encargado)
                elif "cajero" in rol_lower:
                    cajero = Cajero(
                        usuario_id=usuario.id,
                        sucursal_id=datos.sucursal_id,
                        tenant_id=tenant_id,
                    )
                    db.add(cajero)
        
        await db.commit()
        await db.refresh(usuario)
        
        return usuario
    
    @staticmethod
    async def get_usuario(
        db: AsyncSession, usuario_id: int, tenant_id: Optional[int] = None
    ) -> Usuario:
        """Obtiene un usuario por su ID dentro del tenant activo."""
        from app.tenant import tenant_actual

        tenant_id = tenant_id if tenant_id is not None else tenant_actual()
        query = (
            select(Usuario)
            .options(selectinload(Usuario.roles))
            .where(Usuario.id == usuario_id)
        )
        if tenant_id is not None:
            query = query.where(Usuario.tenant_id == tenant_id)
        result = await db.execute(query)
        usuario = result.scalar_one_or_none()
        
        if not usuario:
            raise NotFoundException(f"Usuario con ID {usuario_id} no encontrado")
        
        return usuario
    
    @staticmethod
    async def get_usuarios(
        db: AsyncSession, 
        skip: int = 0, 
        limit: int = 100,
        estado: Optional[str] = None,
        rol: Optional[str] = None,
        tenant_id: Optional[int] = None,
    ) -> Tuple[List[Usuario], int]:
        """
        Lista usuarios con paginación y filtros dentro del tenant.
        
        Returns:
            Tupla con (lista de usuarios, total)
        """
        from app.tenant import tenant_actual

        tenant_id = tenant_id if tenant_id is not None else tenant_actual()
        query = select(Usuario).options(selectinload(Usuario.roles))
        
        if tenant_id is not None:
            query = query.where(Usuario.tenant_id == tenant_id)
        if estado:
            query = query.where(Usuario.estado == estado)
        
        if rol:
            query = query.join(UsuarioRol).join(Rol).where(Rol.nombre == rol)
        
        # Contar total
        count_query = select(func.count(Usuario.id))
        if tenant_id is not None:
            count_query = count_query.where(Usuario.tenant_id == tenant_id)
        if estado:
            count_query = count_query.where(Usuario.estado == estado)
        if rol:
            count_query = (
                count_query.select_from(Usuario)
                .join(UsuarioRol, UsuarioRol.usuario_id == Usuario.id)
                .join(Rol, Rol.id == UsuarioRol.rol_id)
                .where(Rol.nombre == rol)
            )
        
        total_result = await db.execute(count_query)
        total = total_result.scalar_one() or 0
        
        # Aplicar paginación
        query = query.offset(skip).limit(limit)
        result = await db.execute(query)
        
        return result.scalars().all(), total
    
    @staticmethod
    async def update_usuario(db: AsyncSession, usuario_id: int, datos: UsuarioUpdate) -> Usuario:
        """Actualiza un usuario existente."""
        usuario = await UsuarioService.get_usuario(db, usuario_id)
        
        # Verificar correo duplicado si se cambia
        if datos.correo and datos.correo != usuario.correo:
            result = await db.execute(
                select(Usuario).where(Usuario.correo == datos.correo)
            )
            if result.scalar_one_or_none():
                raise ConflictException("El correo electrónico ya está en uso")
        
        # Actualizar campos
        update_data = datos.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(usuario, field, value)
        
        await db.commit()
        await db.refresh(usuario)
        
        return usuario
    
    @staticmethod
    async def delete_usuario(db: AsyncSession, usuario_id: int):
        """Deshabilita un usuario (soft delete)."""
        usuario = await UsuarioService.get_usuario(db, usuario_id)
        
        # No permitir deshabilitar al único administrador activo
        if usuario.estado == EstadoUsuario.ACTIVO:
            result = await db.execute(
                select(Rol).where(Rol.nombre == "Administrador")
            )
            admin_rol = result.scalar_one_or_none()
            
            if admin_rol:
                # Verificar si es el único administrador
                result = await db.execute(
                    select(Usuario)
                    .join(UsuarioRol)
                    .where(
                        and_(
                            UsuarioRol.rol_id == admin_rol.id,
                            Usuario.estado == EstadoUsuario.ACTIVO
                        )
                    )
                )
                admins = result.scalars().all()
                
                if len(admins) == 1 and admins[0].id == usuario_id:
                    raise ValidationException("No se puede deshabilitar el único administrador activo")
        
        usuario.estado = EstadoUsuario.INACTIVO
        await db.commit()
    
    @staticmethod
    async def assign_role(
        db: AsyncSession, usuario_id: int, rol_id: int, tenant_id: Optional[int] = None
    ) -> Usuario:
        """Asigna un rol a un usuario dentro de su tenant."""
        from app.tenant import tenant_actual

        usuario = await UsuarioService.get_usuario(db, usuario_id)
        tenant_id = tenant_id if tenant_id is not None else (usuario.tenant_id or tenant_actual())
        if tenant_id is None:
            raise ValidationException("El usuario no tiene tenant asignado")
        
        # Verificar que el rol existe (global o del tenant)
        result = await db.execute(select(Rol).where(Rol.id == rol_id))
        rol = result.scalar_one_or_none()
        
        if not rol:
            raise NotFoundException(f"Rol con ID {rol_id} no encontrado")
        if rol.nombre == "Super Administrador":
            raise ValidationException("No se puede asignar el rol de plataforma desde un tenant")
        
        # Verificar si ya tiene el rol
        result = await db.execute(
            select(UsuarioRol).where(
                and_(
                    UsuarioRol.usuario_id == usuario_id,
                    UsuarioRol.rol_id == rol_id
                )
            )
        )
        if result.scalar_one_or_none():
            raise ConflictException("El usuario ya tiene este rol asignado")
        
        # Asignar rol copiando el tenant del usuario
        usuario_rol = UsuarioRol(
            usuario_id=usuario_id, rol_id=rol_id, tenant_id=tenant_id
        )
        db.add(usuario_rol)
        await db.commit()
        
        return usuario
    
    @staticmethod
    async def remove_role(db: AsyncSession, usuario_id: int, rol_id: int):
        """Remueve un rol de un usuario."""
        result = await db.execute(
            select(UsuarioRol).where(
                and_(
                    UsuarioRol.usuario_id == usuario_id,
                    UsuarioRol.rol_id == rol_id
                )
            )
        )
        usuario_rol = result.scalar_one_or_none()
        
        if not usuario_rol:
            raise NotFoundException("El usuario no tiene este rol asignado")
        
        await db.delete(usuario_rol)
        await db.commit()


# ============================================
# Servicios de Gestión de Roles y Permisos
# ============================================

class RolService:
    """Servicio de gestión de roles y permisos."""
    
    @staticmethod
    async def create_rol(db: AsyncSession, datos: RolCreate) -> Rol:
        """Crea un nuevo rol."""
        # Verificar nombre duplicado
        result = await db.execute(
            select(Rol).where(Rol.nombre == datos.nombre)
        )
        if result.scalar_one_or_none():
            raise ConflictException("Ya existe un rol con este nombre")
        
        rol = Rol(nombre=datos.nombre, descripcion=datos.descripcion)
        db.add(rol)
        await db.commit()
        await db.refresh(rol)
        
        return rol
    
    @staticmethod
    async def get_rol(db: AsyncSession, rol_id: int) -> Rol:
        """Obtiene un rol por su ID."""
        result = await db.execute(
            select(Rol)
            .options(selectinload(Rol.permisos))
            .where(Rol.id == rol_id)
        )
        rol = result.scalar_one_or_none()
        
        if not rol:
            raise NotFoundException(f"Rol con ID {rol_id} no encontrado")
        
        return rol
    
    @staticmethod
    async def get_roles(db: AsyncSession) -> List[Rol]:
        """Lista todos los roles con sus permisos cargados."""
        result = await db.execute(
            select(Rol).options(selectinload(Rol.permisos))
        )
        return result.scalars().all()
    
    @staticmethod
    async def update_rol(db: AsyncSession, rol_id: int, datos: RolUpdate) -> Rol:
        """Actualiza un rol."""
        rol = await RolService.get_rol(db, rol_id)
        
        update_data = datos.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(rol, field, value)
        
        await db.commit()
        await db.refresh(rol)
        
        return rol
    
    @staticmethod
    async def delete_rol(db: AsyncSession, rol_id: int):
        """Elimina un rol."""
        rol = await RolService.get_rol(db, rol_id)
        
        # Verificar que no tenga usuarios asignados
        if rol.usuarios:
            raise ConflictException("No se puede eliminar un rol que tiene usuarios asignados")
        
        await db.delete(rol)
        await db.commit()
    
    @staticmethod
    async def assign_permission(db: AsyncSession, rol_id: int, permiso_id: int) -> Rol:
        """Asigna un permiso a un rol."""
        rol = await RolService.get_rol(db, rol_id)
        
        # Verificar que el permiso existe
        result = await db.execute(select(Permiso).where(Permiso.id == permiso_id))
        permiso = result.scalar_one_or_none()
        
        if not permiso:
            raise NotFoundException(f"Permiso con ID {permiso_id} no encontrado")
        
        # Verificar si ya tiene el permiso
        if permiso in rol.permisos:
            raise ConflictException("El rol ya tiene este permiso")
        
        rol_permiso = RolPermiso(rol_id=rol_id, permiso_id=permiso_id)
        db.add(rol_permiso)
        await db.commit()
        
        return rol
    
    @staticmethod
    async def remove_permission(db: AsyncSession, rol_id: int, permiso_id: int):
        """Remueve un permiso de un rol."""
        result = await db.execute(
            select(RolPermiso).where(
                and_(
                    RolPermiso.rol_id == rol_id,
                    RolPermiso.permiso_id == permiso_id
                )
            )
        )
        rol_permiso = result.scalar_one_or_none()
        
        if not rol_permiso:
            raise NotFoundException("El rol no tiene este permiso")
        
        await db.delete(rol_permiso)
        await db.commit()

    @staticmethod
    async def sync_permissions(db: AsyncSession, rol_id: int, permisos_ids: List[int]) -> Rol:
        """Sincroniza la lista completa de permisos de un rol."""
        rol = await RolService.get_rol(db, rol_id)
        
        # Eliminar relaciones actuales
        await db.execute(delete(RolPermiso).where(RolPermiso.rol_id == rol_id))
        
        # Insertar nuevas relaciones
        for p_id in permisos_ids:
            res = await db.execute(select(Permiso).where(Permiso.id == p_id))
            if res.scalar_one_or_none():
                db.add(RolPermiso(rol_id=rol_id, permiso_id=p_id))
        
        await db.commit()
        return await RolService.get_rol(db, rol_id)


class PermisoService:
    """Servicio de gestión de permisos."""
    
    @staticmethod
    async def get_permisos(db: AsyncSession) -> List[Permiso]:
        """Lista todos los permisos."""
        result = await db.execute(select(Permiso))
        return result.scalars().all()
    
    @staticmethod
    async def create_permiso(db: AsyncSession, nombre: str, descripcion: str = None) -> Permiso:
        """Crea un nuevo permiso."""
        result = await db.execute(
            select(Permiso).where(Permiso.nombre == nombre)
        )
        if result.scalar_one_or_none():
            raise ConflictException("Ya existe un permiso con este nombre")
        
        permiso = Permiso(nombre=nombre, descripcion=descripcion)
        db.add(permiso)
        await db.commit()
        await db.refresh(permiso)
        
        return permiso


# ============================================
# Servicios de Gestión de Clientes
# ============================================

class ClienteService:
    """Servicio de gestión de clientes."""
    
    @staticmethod
    async def registro_cliente(
        db: AsyncSession, 
        datos: ClienteCreate, 
        ip_address: Optional[str] = None
    ) -> Tuple[Cliente, dict]:
        """
        Registra un nuevo cliente en la plataforma.
        
        Args:
            db: Sesión de base de datos
            datos: Datos del cliente
            ip_address: Dirección IP del cliente
        
        Returns:
            Tupla con (cliente, tokens)
        """
        # [EXAMEN-2] El registro público se crea dentro del tenant resuelto
        from app.tenant import resolver_tenant_publico, tenant_actual

        tenant_id = tenant_actual() or await resolver_tenant_publico(db)

        # Verificar correo duplicado (global)
        result = await db.execute(
            select(Usuario).where(Usuario.correo == datos.correo)
        )
        if result.scalar_one_or_none():
            raise ConflictException("El correo electrónico ya está registrado")
        
        # Verificar NIT/CI duplicado dentro del tenant
        result = await db.execute(
            select(Cliente).where(
                Cliente.nit_ci == datos.nit_ci,
                Cliente.tenant_id == tenant_id,
            )
        )
        if result.scalar_one_or_none():
            raise ConflictException("El NIT/CI ya está registrado")
        
        # Crear usuario
        usuario = Usuario(
            nombre=datos.nombre,
            apellido=datos.apellido,
            correo=datos.correo,
            telefono=datos.telefono,
            contrasena_hash=get_password_hash(datos.contrasena),
            estado=EstadoUsuario.ACTIVO,
            tenant_id=tenant_id,
        )
        db.add(usuario)
        await db.flush()
        
        # Asignar rol Cliente
        result = await db.execute(
            select(Rol).where(Rol.nombre == "Cliente")
        )
        rol_cliente = result.scalar_one_or_none()
        
        if rol_cliente:
            usuario_rol = UsuarioRol(
                usuario_id=usuario.id, rol_id=rol_cliente.id, tenant_id=tenant_id
            )
            db.add(usuario_rol)
        
        # Crear cliente
        cliente = Cliente(
            usuario_id=usuario.id,
            nit_ci=datos.nit_ci,
            direccion_envio=datos.direccion_envio,
            telefono=datos.telefono,
            correo=datos.correo,
            tenant_id=tenant_id,
        )
        db.add(cliente)
        await db.commit()
        await db.refresh(usuario)
        
        # Generar tokens con tenant y rol
        token_data = {
            "sub": str(usuario.id),
            "correo": usuario.correo,
            "tenant_id": tenant_id,
            "es_super_admin": False,
            "roles": ["Cliente"] if rol_cliente else [],
        }
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)
        
        # Registrar en bitácora
        bitacora = Bitacora(
            usuario_id=usuario.id,
            tenant_id=tenant_id,
            accion="REGISTRO_CLIENTE",
            modulo="Autenticacion",
            detalles="Nuevo cliente registrado",
            ip_address=ip_address
        )
        db.add(bitacora)
        await db.commit()
        
        return cliente, {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "tenant_id": tenant_id,
            "es_super_admin": False,
            "roles": token_data["roles"],
            "debe_cambiar_contrasena": False,
            "tenant": None,
            "suscripcion": None,
        }
    
    @staticmethod
    async def get_cliente(db: AsyncSession, cliente_id: int) -> Cliente:
        """Obtiene un cliente por su ID."""
        result = await db.execute(
            select(Cliente)
            .options(selectinload(Cliente.usuario))
            .where(Cliente.id == cliente_id)
        )
        cliente = result.scalar_one_or_none()
        
        if not cliente:
            raise NotFoundException(f"Cliente con ID {cliente_id} no encontrado")
        
        return cliente
    
    @staticmethod
    async def get_cliente_by_usuario(db: AsyncSession, usuario_id: int) -> Cliente:
        """Obtiene el cliente asociado a un usuario."""
        result = await db.execute(
            select(Cliente)
            .options(selectinload(Cliente.usuario))
            .where(Cliente.usuario_id == usuario_id)
        )
        cliente = result.scalar_one_or_none()
        
        if not cliente:
            raise NotFoundException("Cliente no encontrado para este usuario")
        
        return cliente
    
    @staticmethod
    async def update_cliente(db: AsyncSession, cliente_id: int, datos: ClienteUpdate) -> Cliente:
        """Actualiza los datos de un cliente."""
        cliente = await ClienteService.get_cliente(db, cliente_id)
        
        # Verificar NIT duplicado
        if datos.nit_ci and datos.nit_ci != cliente.nit_ci:
            result = await db.execute(
                select(Cliente).where(Cliente.nit_ci == datos.nit_ci)
            )
            if result.scalar_one_or_none():
                raise ConflictException("El NIT/CI ya está en uso")
        
        update_data = datos.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(cliente, field, value)
        
        await db.commit()
        await db.refresh(cliente)
        
        return cliente

    @staticmethod
    async def buscar_clientes(db: AsyncSession, q: str, limit: int = 10) -> List[Cliente]:
        """Busca clientes por NIT/CI, nombre, apellido, correo o teléfono."""
        q_clean = f"%{q.strip()}%"
        query = (
            select(Cliente)
            .join(Usuario, Cliente.usuario_id == Usuario.id)
            .options(selectinload(Cliente.usuario))
            .where(
                or_(
                    Cliente.nit_ci.ilike(q_clean),
                    Usuario.nombre.ilike(q_clean),
                    Usuario.apellido.ilike(q_clean),
                    Usuario.correo.ilike(q_clean),
                    Usuario.telefono.ilike(q_clean)
                )
            )
            .limit(limit)
        )
        res = await db.execute(query)
        return list(res.scalars().all())

    @staticmethod
    async def get_cliente_by_nit(db: AsyncSession, nit_ci: str) -> Optional[Cliente]:
        """Obtiene un cliente por su NIT/CI exacto."""
        query = (
            select(Cliente)
            .options(selectinload(Cliente.usuario))
            .where(Cliente.nit_ci == nit_ci.strip())
        )
        res = await db.execute(query)
        return res.scalar_one_or_none()
    
    @staticmethod
    async def cambiar_contrasena(
        db: AsyncSession, 
        usuario_id: int, 
        contrasena_actual: str, 
        contrasena_nueva: str,
        ip_address: Optional[str] = None
    ):
        """Cambia la contraseña de un usuario."""
        result = await db.execute(
            select(Usuario).where(Usuario.id == usuario_id)
        )
        usuario = result.scalar_one_or_none()
        
        if not usuario:
            raise NotFoundException("Usuario no encontrado")
        
        # Verificar contraseña actual
        if not verify_password(contrasena_actual, usuario.contrasena_hash):
            raise AuthenticationException("La contraseña actual es incorrecta")
        
        # Actualizar contraseña y limpiar la marca de cambio obligatorio
        usuario.contrasena_hash = get_password_hash(contrasena_nueva)
        usuario.debe_cambiar_contrasena = False
        await db.commit()
        
        # Registrar en bitácora
        bitacora = Bitacora(
            usuario_id=usuario_id,
            tenant_id=usuario.tenant_id,
            accion="CAMBIO_CONTRASENA",
            modulo="Perfil",
            detalles="Usuario cambió su contraseña",
            ip_address=ip_address
        )
        db.add(bitacora)
        await db.commit()


# ============================================
# Servicios de Bitácora
# ============================================

class BitacoraService:
    """Servicio de bitácora del sistema."""
    
    # Campos que jamás se registran en la bitácora (secretos).
    CAMPOS_SENSIBLES = {"contrasena_hash", "contrasena", "password", "token"}

    @staticmethod
    def foto(obj: Any, excluir: Optional[set] = None) -> Optional[dict]:
        """Foto JSON de una entidad SQLAlchemy (solo columnas, sin secretos).

        Convierte Decimal/fecha/enum a tipos serializables. Devuelve None si
        el objeto es None.
        """
        if obj is None:
            return None
        from datetime import date as _date, datetime as _dt
        from decimal import Decimal as _Dec
        from enum import Enum as _Enum
        excluidas = set(BitacoraService.CAMPOS_SENSIBLES)
        if excluir:
            excluidas |= set(excluir)
        datos: Dict[str, Any] = {}
        tabla = getattr(obj, "__table__", None)
        columnas = [c.key for c in tabla.columns] if tabla is not None else [
            k for k in vars(obj) if not k.startswith("_")
        ]
        for key in columnas:
            if key in excluidas:
                continue
            try:
                v = getattr(obj, key, None)
            except Exception:
                continue
            if isinstance(v, _Dec):
                v = float(v)
            elif isinstance(v, (_dt, _date)):
                v = v.isoformat()
            elif isinstance(v, _Enum):
                v = v.value if hasattr(v, "value") else str(v)
            elif isinstance(v, (dict, list)):
                pass
            elif v is not None and not isinstance(v, (str, int, float, bool)):
                v = str(v)
            datos[key] = v
        return datos

    @staticmethod
    async def registrar_evento(
        db: AsyncSession,
        accion: str,
        usuario_id: Optional[int] = None,
        ip_address: Optional[str] = None,
        modulo: Optional[str] = None,
        detalles: Optional[str] = None,
        registro_id: Optional[int] = None,
        valores_anteriores: Optional[dict] = None,
        valores_nuevos: Optional[dict] = None,
        tenant_id: Optional[int] = None,
    ) -> Bitacora:
        """Registra un evento en la bitácora, con diff opcional antes/después."""
        from app.tenant import tenant_actual

        # [EXAMEN-2] Auditoría por tenant (plan, 4.5)
        tenant_id = tenant_id if tenant_id is not None else tenant_actual()
        if tenant_id is None and usuario_id is not None:
            res = await db.execute(select(Usuario.tenant_id).where(Usuario.id == usuario_id))
            tenant_id = res.scalar_one_or_none()

        bitacora = Bitacora(
            usuario_id=usuario_id,
            tenant_id=tenant_id,
            accion=accion,
            modulo=modulo,
            detalles=detalles,
            ip_address=ip_address,
            registro_id=registro_id,
            valores_anteriores=valores_anteriores,
            valores_nuevos=valores_nuevos
        )
        db.add(bitacora)
        await db.commit()
        return bitacora

    @staticmethod
    async def get_bitacora(
        db: AsyncSession,
        skip: int = 0,
        limit: int = 100,
        usuario_id: Optional[int] = None,
        accion: Optional[str] = None,
        modulo: Optional[str] = None,
        fecha_inicio: Optional[datetime] = None,
        fecha_fin: Optional[datetime] = None,
        tenant_id: Optional[int] = None,
    ) -> Tuple[List[Bitacora], int]:
        """
        Consulta la bitácora con filtros dentro del tenant.
        
        Returns:
            Tupla con (registros, total)
        """
        from app.tenant import tenant_actual

        tenant_id = tenant_id if tenant_id is not None else tenant_actual()
        query = (
            select(Bitacora)
            .options(selectinload(Bitacora.usuario))
            .order_by(Bitacora.fecha_hora.desc())
        )
        count_query = select(func.count(Bitacora.id))
        if tenant_id is not None:
            query = query.where(Bitacora.tenant_id == tenant_id)
            count_query = count_query.where(Bitacora.tenant_id == tenant_id)
        
        if usuario_id:
            query = query.where(Bitacora.usuario_id == usuario_id)
            count_query = count_query.where(Bitacora.usuario_id == usuario_id)
        if accion:
            query = query.where(Bitacora.accion.ilike(f"%{accion}%"))
            count_query = count_query.where(Bitacora.accion.ilike(f"%{accion}%"))
        if modulo:
            query = query.where(Bitacora.modulo.ilike(f"%{modulo}%"))
            count_query = count_query.where(Bitacora.modulo.ilike(f"%{modulo}%"))
        if fecha_inicio:
            query = query.where(Bitacora.fecha_hora >= fecha_inicio)
            count_query = count_query.where(Bitacora.fecha_hora >= fecha_inicio)
        if fecha_fin:
            query = query.where(Bitacora.fecha_hora <= fecha_fin)
            count_query = count_query.where(Bitacora.fecha_hora <= fecha_fin)
        
        # Contar total
        count_result = await db.execute(count_query)
        total = count_result.scalar_one() or 0
        
        # Aplicar paginación
        query = query.offset(skip).limit(limit)
        result = await db.execute(query)
        
        return list(result.scalars().all()), total
    
    @staticmethod
    async def exportar_bitacora_csv(db: AsyncSession) -> str:
        """Exporta la bitácora a formato CSV."""
        bitacoras, _ = await BitacoraService.get_bitacora(db, limit=10000)
        
        csv_lines = ["ID,Fecha,Hora,Usuario_ID,IP,Accion,Modulo,Registro_ID,Detalles,Valores_Anteriores,Valores_Nuevos"]

        for b in bitacoras:
            import json as _json
            ant = _json.dumps(b.valores_anteriores, ensure_ascii=False, default=str) if b.valores_anteriores else ""
            nue = _json.dumps(b.valores_nuevos, ensure_ascii=False, default=str) if b.valores_nuevos else ""
            det = (b.detalles or "").replace('"', '""')
            linea = f'{b.id},{b.fecha_hora},{b.usuario_id or ""},{b.ip_address or ""},{b.accion},{b.modulo or ""},{b.registro_id or ""},"{det}","{ant}","{nue}"'
            csv_lines.append(linea)
        
        return "\n".join(csv_lines)


# ============================================
# Servicios de Inicialización de Datos
# ============================================

class DatosInicialesService:
    """Servicio para crear datos iniciales."""
    
    @staticmethod
    async def crear_datos_iniciales(db: AsyncSession):
        """Crea permisos, roles, planes, tenant demo y usuarios iniciales.

        Modificado en Examen 2 (SaaS multitenant): incorpora los permisos de
        plataforma, el rol y usuario Super Administrador, los planes Básico y
        Profesional, el tenant demo con suscripción activa y la configuración
        offline. Es idempotente por clave natural.
        """
        from datetime import date, timedelta

        from app.apps.gestion_saas.models import (
            EstadoPlan, EstadoSuscripcion, EstadoTenant, PagoSuscripcion,
            Plan, Suscripcion, Tenant,
        )
        from app.apps.gestion_ventas.models import EstadoTransaccion
        from app.apps.operaciones_offline.models import ConfiguracionOffline
        from app.tenant import TENANT_DEMO_SLUG

        # ------------------------------------------------------------
        # 1. Permisos (base del Examen 1 + plataforma del Examen 2)
        # ------------------------------------------------------------
        permisos_data = [
            # Gestión de usuarios
            ("gestionar_usuarios", "Permiso para gestionar usuarios"),
            ("gestionar_roles", "Permiso para gestionar roles y permisos"),
            ("ver_bitacora", "Permiso para ver la bitácora"),
            # Gestión de catálogo
            ("gestionar_sucursales", "Permiso para gestionar sucursales"),
            ("gestionar_productos", "Permiso para gestionar productos"),
            ("gestionar_inventario", "Permiso para gestionar inventario"),
            ("gestionar_categorias", "Permiso para gestionar categorías"),
            # Gestión de ventas
            ("gestionar_ventas", "Permiso para gestionar ventas"),
            ("gestionar_reservas", "Permiso para gestionar reservas"),
            ("procesar_pagos", "Permiso para procesar pagos"),
            # Reportes
            ("generar_reportes", "Permiso para generar reportes"),
            ("ver_kpis", "Permiso para ver indicadores KPIs"),
            # [EXAMEN-2] Plataforma y operación offline (plan, 9.1.9)
            ("gestionar_tenants", "Permiso para gestionar empresas tenant"),
            ("gestionar_planes", "Permiso para gestionar planes"),
            ("gestionar_suscripciones", "Permiso para gestionar suscripciones"),
            ("configurar_tenant", "Permiso para configurar el entorno del tenant"),
            ("sincronizar_offline", "Permiso para sincronizar operaciones offline"),
            ("resolver_conflictos", "Permiso para resolver conflictos de sincronización"),
            ("gestionar_traspasos", "Permiso para gestionar traspasos"),
            ("usar_reportes_ia", "Permiso para usar reportes con IA"),
            ("entrenar_modelos", "Permiso para entrenar modelos de ML"),
            ("exportar_reportes", "Permiso para exportar reportes"),
            ("operar_offline", "Permiso para operar en modo offline"),
            ("gestionar_pagos", "Permiso para gestionar pagos"),
            ("revocar_dispositivos", "Permiso para revocar dispositivos"),
        ]
        
        permisos = {}
        for nombre, desc in permisos_data:
            result = await db.execute(
                select(Permiso).where(Permiso.nombre == nombre)
            )
            perm = result.scalar_one_or_none()
            
            if not perm:
                perm = Permiso(nombre=nombre, descripcion=desc)
                db.add(perm)
                await db.flush()
            
            permisos[nombre] = perm
        
        await db.flush()

        # ------------------------------------------------------------
        # 2. Tenant demo (datos históricos del Examen 1)
        # ------------------------------------------------------------
        result = await db.execute(select(Tenant).where(Tenant.slug == TENANT_DEMO_SLUG))
        tenant = result.scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(
                razon_social="FashionStore Demo",
                nit="0000000000",
                direccion_fiscal="Av. Principal #123, La Paz",
                correo_contacto="contacto@fashionstore.com",
                telefono="00000000",
                slug=TENANT_DEMO_SLUG,
                moneda="BOB",
                zona_horaria="America/La_Paz",
                estado=EstadoTenant.OPERATIVO,
            )
            db.add(tenant)
            await db.flush()
        
        # ------------------------------------------------------------
        # 3. Roles (nombre global único; el tenant demo es el dueño)
        # ------------------------------------------------------------
        permisos_tenant = [
            p for p in permisos.keys()
            if p not in {
                "gestionar_tenants", "gestionar_planes", "gestionar_suscripciones",
                "entrenar_modelos",
            }
        ]
        roles_data = [
            ("Administrador", "Administrador del tenant con acceso completo", permisos_tenant, tenant.id),
            ("Cliente", "Cliente de la plataforma", [], tenant.id),
            ("Encargado", "Encargado de sucursal", [
                "gestionar_sucursales", "gestionar_inventario", 
                "gestionar_reservas", "gestionar_ventas", "sincronizar_offline",
                "operar_offline",
            ], tenant.id),
            ("Cajero", "Cajero de sucursal", [
                "gestionar_ventas", "procesar_pagos", "sincronizar_offline",
                "operar_offline",
            ], tenant.id),
            # [EXAMEN-2] Rol de plataforma: tenant_id = NULL
            ("Super Administrador", "Super Administrador de la plataforma SaaS", list(permisos.keys()), None),
        ]
        
        for nombre, desc, perms, rol_tenant in roles_data:
            result = await db.execute(
                select(Rol).where(Rol.nombre == nombre)
            )
            rol = result.scalar_one_or_none()
            
            if not rol:
                rol = Rol(nombre=nombre, descripcion=desc, tenant_id=rol_tenant)
                db.add(rol)
                await db.flush()
                
                # Asignar permisos
                for perm_nombre in perms:
                    if perm_nombre in permisos:
                        rol_permiso = RolPermiso(rol_id=rol.id, permiso_id=permisos[perm_nombre].id)
                        db.add(rol_permiso)
        
        await db.flush()

        # ------------------------------------------------------------
        # 4. Planes Básico y Profesional
        # ------------------------------------------------------------
        # [EXAMEN-2] Fase B (punto 6): tercer plan con límites y respaldos.
        # [EXAMEN-2] Precios unificados con PlanService.sembrar_planes: 199/399/799.
        planes_data = [
            ("Básico", "Plan inicial para una o dos sucursales", 199.00, 2, False, True, 1, False,
             10, 500, True, 168, 7, 0, False),
            ("Profesional", "Plan completo con reportes IA", 399.00, 10, True, True, 2, False,
             50, 5000, True, 24, 30, 2, False),
            ("Empresarial", "Plan ampliado con respaldos y restauración", 799.00, 50, True, True, 3, True,
             200, None, True, 6, 365, None, True),
        ]
        planes = {}
        for (
            nombre, desc, precio, max_suc, ia, diferido, orden, soporte,
            max_usr, max_prod, backup_auto, backup_horas, backup_dias,
            backup_manual, restauracion,
        ) in planes_data:
            result = await db.execute(select(Plan).where(Plan.nombre == nombre))
            plan = result.scalar_one_or_none()
            if plan is None:
                plan = Plan(
                    nombre=nombre,
                    descripcion=desc,
                    precio_mensual=precio,
                    max_sucursales=max_suc,
                    permite_reportes_ia=ia,
                    permite_pago_diferido=diferido,
                    orden=orden,
                    soporte_prioritario=soporte,
                    estado=EstadoPlan.ACTIVO,
                    max_usuarios=max_usr,
                    max_productos=max_prod,
                    backup_automatico=backup_auto,
                    backup_frecuencia_horas=backup_horas,
                    backup_retencion_dias=backup_dias,
                    backup_manual_mensual=backup_manual,
                    permite_restauracion=restauracion,
                )
                db.add(plan)
                await db.flush()
            else:
                # Completa los campos nuevos de los planes ya sembrados.
                if plan.max_usuarios is None and max_usr is not None:
                    plan.max_usuarios = max_usr
                if plan.max_productos is None and max_prod is not None:
                    plan.max_productos = max_prod
                if not plan.backup_automatico:
                    plan.backup_automatico = backup_auto
                if plan.backup_frecuencia_horas is None:
                    plan.backup_frecuencia_horas = backup_horas
                if plan.backup_retencion_dias in (None, 7) and backup_dias != 7:
                    plan.backup_retencion_dias = backup_dias
                if plan.backup_manual_mensual is None and backup_manual is not None:
                    plan.backup_manual_mensual = backup_manual
                if restauracion and not plan.permite_restauracion:
                    plan.permite_restauracion = restauracion
            planes[nombre] = plan

        # Limpieza del duplicado por encoding roto (ex2_004 "BÃ¡sico").
        # La migración original guardó el nombre con doble encoding; al
        # buscar "Básico" se creó otro plan. Se fusiona y elimina el roto.
        roto = (await db.execute(
            select(Plan).where(Plan.nombre == "BÃ¡sico")
        )).scalar_one_or_none()
        if roto is not None and "Básico" in planes and roto.id != planes["Básico"].id:
            correcto = planes["Básico"]
            suscs_rotas = (await db.execute(
                select(Suscripcion).where(Suscripcion.plan_id == roto.id)
            )).scalars().all()
            for s in suscs_rotas:
                s.plan_id = correcto.id
            await db.delete(roto)
            await db.flush()
            for n, orden_esperado in (("Básico", 1), ("Profesional", 2), ("Empresarial", 3)):
                if n in planes:
                    planes[n].orden = orden_esperado

        # ------------------------------------------------------------
        # 5. Suscripción activa + pago demo + configuración offline
        # ------------------------------------------------------------
        result = await db.execute(
            select(Suscripcion)
            .where(
                Suscripcion.tenant_id == tenant.id,
                Suscripcion.estado == EstadoSuscripcion.ACTIVA,
            )
            .order_by(Suscripcion.fecha_vencimiento.desc())
        )
        suscripcion = result.scalars().first()
        if suscripcion is None or suscripcion.fecha_vencimiento < date.today():
            suscripcion = Suscripcion(
                tenant_id=tenant.id,
                plan_id=planes["Profesional"].id,
                fecha_inicio=date.today(),
                fecha_vencimiento=date.today() + timedelta(days=365),
                estado=EstadoSuscripcion.ACTIVA,
                estado_pago="PAGADO",
                renovacion_automatica=True,
            )
            db.add(suscripcion)
            await db.flush()

        result = await db.execute(
            select(PagoSuscripcion).where(
                PagoSuscripcion.suscripcion_id == suscripcion.id,
                PagoSuscripcion.estado == EstadoTransaccion.CONFIRMADO,
            )
        )
        if result.scalars().first() is None:
            db.add(PagoSuscripcion(
                tenant_id=tenant.id,
                suscripcion_id=suscripcion.id,
                monto=planes["Profesional"].precio_mensual,
                moneda="BOB",
                estado=EstadoTransaccion.CONFIRMADO,
                referencia="DEMO-SEED",
                idempotency_key="seed-demo-profesional",
            ))

        result = await db.execute(
            select(ConfiguracionOffline).where(ConfiguracionOffline.tenant_id == tenant.id)
        )
        if result.scalar_one_or_none() is None:
            db.add(ConfiguracionOffline(
                tenant_id=tenant.id,
                descarga_automatica=True,
                incluir_clientes=True,
                max_clientes=settings.OFFLINE_MAX_CLIENTES_DEFECTO,
                habilitar_carrito_offline=True,
                max_horas_sesion_offline=settings.OFFLINE_SESION_HORAS_DEFECTO,
                max_dias_orden_pendiente=1,
            ))

        # ------------------------------------------------------------
        # 6. Super Administrador de plataforma
        # ------------------------------------------------------------
        result = await db.execute(
            select(Usuario).where(Usuario.correo == "superadmin@fashionstore.com")
        )
        superadmin = result.scalar_one_or_none()
        if superadmin is None:
            superadmin = Usuario(
                nombre="Super",
                apellido="Administrador",
                correo="superadmin@fashionstore.com",
                telefono="00000000",
                contrasena_hash=get_password_hash("SuperAdmin123!"),
                estado=EstadoUsuario.ACTIVO,
                tenant_id=None,
            )
            db.add(superadmin)
            await db.flush()

        result = await db.execute(select(Rol).where(Rol.nombre == "Super Administrador"))
        super_rol = result.scalar_one_or_none()
        if super_rol:
            result = await db.execute(
                select(UsuarioRol).where(
                    UsuarioRol.usuario_id == superadmin.id,
                    UsuarioRol.rol_id == super_rol.id,
                )
            )
            if result.scalar_one_or_none() is None:
                # El rol de plataforma no pertenece a un tenant; se usa el demo
                # para satisfacer la FK NOT NULL de la tabla puente.
                db.add(UsuarioRol(
                    usuario_id=superadmin.id,
                    rol_id=super_rol.id,
                    tenant_id=tenant.id,
                ))

        # ------------------------------------------------------------
        # 7. Administrador del tenant demo
        # ------------------------------------------------------------
        result = await db.execute(
            select(Usuario).where(Usuario.correo == "admin@fashionstore.com")
        )
        admin_user = result.scalar_one_or_none()
        if admin_user is None:
            admin_user = Usuario(
                nombre="Administrador",
                apellido="Sistema",
                correo="admin@fashionstore.com",
                telefono="00000000",
                contrasena_hash=get_password_hash("Admin123!"),
                estado=EstadoUsuario.ACTIVO,
                tenant_id=tenant.id,
            )
            db.add(admin_user)
            await db.flush()
        elif admin_user.tenant_id is None:
            admin_user.tenant_id = tenant.id

        result = await db.execute(select(Rol).where(Rol.nombre == "Administrador"))
        admin_rol = result.scalar_one_or_none()
        if admin_rol:
            result = await db.execute(
                select(UsuarioRol).where(
                    UsuarioRol.usuario_id == admin_user.id,
                    UsuarioRol.rol_id == admin_rol.id,
                )
            )
            if result.scalar_one_or_none() is None:
                db.add(UsuarioRol(
                    usuario_id=admin_user.id,
                    rol_id=admin_rol.id,
                    tenant_id=tenant.id,
                ))

        result = await db.execute(
            select(Administrador).where(Administrador.usuario_id == admin_user.id)
        )
        if result.scalar_one_or_none() is None:
            db.add(Administrador(usuario_id=admin_user.id, tenant_id=tenant.id))
        
        await db.commit()
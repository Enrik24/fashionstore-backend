"""
Servicios de negocio para Gestión de Usuarios y Autenticación.
"""
from typing import Optional, List, Tuple
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_, delete
from sqlalchemy.orm import selectinload
import secrets

from app.security import (
    get_password_hash, verify_password, 
    create_access_token, create_refresh_token, decode_token
)
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
        # Buscar usuario por correo
        result = await db.execute(
            select(Usuario).where(Usuario.correo == datos.correo)
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
        
        # Actualizar último acceso
        usuario.ultimo_acceso = datetime.utcnow()
        await db.commit()
        
        # Generar tokens
        token_data = {"sub": str(usuario.id), "correo": usuario.correo}
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)
        
        # Registrar login exitoso
        await AuthService._registrar_bitacora(
            db, usuario.id, "LOGIN_EXITOSO", 
            "Usuario inició sesión", ip_address=ip_address
        )
        
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer"
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
        
        # Buscar usuario
        result = await db.execute(select(Usuario).where(Usuario.id == user_id))
        usuario = result.scalar_one_or_none()
        
        if not usuario or (usuario.estado != EstadoUsuario.ACTIVO and usuario.estado != "ACTIVO"):
            raise AuthenticationException("Usuario no encontrado o inactivo")
        
        # Generar nuevos tokens
        token_data = {"sub": str(usuario.id), "correo": usuario.correo}
        access_token = create_access_token(token_data)
        new_refresh_token = create_refresh_token(token_data)
        
        return {
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer"
        }
    
    @staticmethod
    async def logout(db: AsyncSession, usuario_id: int, ip_address: Optional[str] = None):
        """Registra el cierre de sesión."""
        await AuthService._registrar_bitacora(
            db, usuario_id, "LOGOUT", 
            "Usuario cerró sesión", ip_address=ip_address
        )
    
    @staticmethod
    async def _registrar_bitacora(
        db: AsyncSession, 
        usuario_id: Optional[int], 
        accion: str, 
        detalles: str,
        modulo: str = "Autenticacion",
        ip_address: Optional[str] = None
    ):
        """Registra un evento en la bitácora."""
        bitacora = Bitacora(
            usuario_id=usuario_id,
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
    async def create_usuario(db: AsyncSession, datos: UsuarioCreate, rol_nombre: str = "Cliente") -> Usuario:
        """
        Crea un nuevo usuario en el sistema.
        
        Args:
            db: Sesión de base de datos
            datos: Datos del usuario a crear
            rol_nombre: Nombre del rol a asignar
        
        Returns:
            Usuario creado
        """
        # Verificar correo duplicado
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
            estado=datos.estado
        )
        db.add(usuario)
        await db.flush()
        
        # Asignar rol
        result = await db.execute(
            select(Rol).where(Rol.nombre == rol_nombre)
        )
        rol = result.scalar_one_or_none()
        
        if rol:
            usuario_rol = UsuarioRol(usuario_id=usuario.id, rol_id=rol.id)
            db.add(usuario_rol)
        
        await db.commit()
        await db.refresh(usuario)
        
        return usuario
    
    @staticmethod
    async def get_usuario(db: AsyncSession, usuario_id: int) -> Usuario:
        """Obtiene un usuario por su ID."""
        result = await db.execute(
            select(Usuario)
            .options(selectinload(Usuario.roles))
            .where(Usuario.id == usuario_id)
        )
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
        rol: Optional[str] = None
    ) -> Tuple[List[Usuario], int]:
        """
        Lista usuarios con paginación y filtros.
        
        Returns:
            Tupla con (lista de usuarios, total)
        """
        query = select(Usuario).options(selectinload(Usuario.roles))
        
        if estado:
            query = query.where(Usuario.estado == estado)
        
        if rol:
            query = query.join(UsuarioRol).join(Rol).where(Rol.nombre == rol)
        
        # Contar total
        count_query = select(Usuario)
        if estado:
            count_query = count_query.where(Usuario.estado == estado)
        
        total_result = await db.execute(count_query)
        total = len(total_result.scalars().all())
        
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
    async def assign_role(db: AsyncSession, usuario_id: int, rol_id: int) -> Usuario:
        """Asigna un rol a un usuario."""
        usuario = await UsuarioService.get_usuario(db, usuario_id)
        
        # Verificar que el rol existe
        result = await db.execute(select(Rol).where(Rol.id == rol_id))
        rol = result.scalar_one_or_none()
        
        if not rol:
            raise NotFoundException(f"Rol con ID {rol_id} no encontrado")
        
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
        
        # Asignar rol
        usuario_rol = UsuarioRol(usuario_id=usuario_id, rol_id=rol_id)
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
    async def registro_cliente(db: AsyncSession, datos: ClienteCreate) -> Tuple[Cliente, dict]:
        """
        Registra un nuevo cliente en la plataforma.
        
        Args:
            db: Sesión de base de datos
            datos: Datos del cliente
        
        Returns:
            Tupla con (cliente, tokens)
        """
        # Verificar correo duplicado
        result = await db.execute(
            select(Usuario).where(Usuario.correo == datos.correo)
        )
        if result.scalar_one_or_none():
            raise ConflictException("El correo electrónico ya está registrado")
        
        # Verificar NIT/CI duplicado
        result = await db.execute(
            select(Cliente).where(Cliente.nit_ci == datos.nit_ci)
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
            estado=EstadoUsuario.ACTIVO
        )
        db.add(usuario)
        await db.flush()
        
        # Asignar rol Cliente
        result = await db.execute(
            select(Rol).where(Rol.nombre == "Cliente")
        )
        rol_cliente = result.scalar_one_or_none()
        
        if rol_cliente:
            usuario_rol = UsuarioRol(usuario_id=usuario.id, rol_id=rol_cliente.id)
            db.add(usuario_rol)
        
        # Crear cliente
        cliente = Cliente(
            usuario_id=usuario.id,
            nit_ci=datos.nit_ci,
            direccion_envio=datos.direccion_envio
        )
        db.add(cliente)
        await db.commit()
        await db.refresh(usuario)
        
        # Generar tokens
        token_data = {"sub": str(usuario.id), "correo": usuario.correo}
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)
        
        # Registrar en bitácora
        bitacora = Bitacora(
            usuario_id=usuario.id,
            accion="REGISTRO_CLIENTE",
            modulo="Autenticacion",
            detalles="Nuevo cliente registrado"
        )
        db.add(bitacora)
        await db.commit()
        
        return cliente, {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer"
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
    async def cambiar_contrasena(
        db: AsyncSession, 
        usuario_id: int, 
        contrasena_actual: str, 
        contrasena_nueva: str
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
        
        # Actualizar contraseña
        usuario.contrasena_hash = get_password_hash(contrasena_nueva)
        await db.commit()
        
        # Registrar en bitácora
        bitacora = Bitacora(
            usuario_id=usuario_id,
            accion="CAMBIO_CONTRASENA",
            modulo="Perfil",
            detalles="Usuario cambió su contraseña"
        )
        db.add(bitacora)
        await db.commit()


# ============================================
# Servicios de Bitácora
# ============================================

class BitacoraService:
    """Servicio de bitácora del sistema."""
    
    @staticmethod
    async def get_bitacora(
        db: AsyncSession,
        skip: int = 0,
        limit: int = 100,
        usuario_id: Optional[int] = None,
        accion: Optional[str] = None,
        modulo: Optional[str] = None,
        fecha_inicio: Optional[datetime] = None,
        fecha_fin: Optional[datetime] = None
    ) -> Tuple[List[Bitacora], int]:
        """
        Consulta la bitácora con filtros.
        
        Returns:
            Tupla con (registros, total)
        """
        query = select(Bitacora).order_by(Bitacora.fecha_hora.desc())
        
        if usuario_id:
            query = query.where(Bitacora.usuario_id == usuario_id)
        if accion:
            query = query.where(Bitacora.accion.ilike(f"%{accion}%"))
        if modulo:
            query = query.where(Bitacora.modulo == modulo)
        if fecha_inicio:
            query = query.where(Bitacora.fecha_hora >= fecha_inicio)
        if fecha_fin:
            query = query.where(Bitacora.fecha_hora <= fecha_fin)
        
        # Contar total
        count_result = await db.execute(query)
        total = len(count_result.scalars().all())
        
        # Aplicar paginación
        query = query.offset(skip).limit(limit)
        result = await db.execute(query)
        
        return result.scalars().all(), total
    
    @staticmethod
    async def exportar_bitacora_csv(db: AsyncSession) -> str:
        """Exporta la bitácora a formato CSV."""
        bitacoras, _ = await BitacoraService.get_bitacora(db, limit=10000)
        
        csv_lines = ["ID,Fecha,Hora,Usuario_ID,IP,Accion,Modulo,Detalles"]
        
        for b in bitacoras:
            linea = f"{b.id},{b.fecha_hora},{b.usuario_id or ''},{b.ip_address or ''},{b.accion},{b.modulo or ''},{b.detalles or ''}"
            csv_lines.append(linea)
        
        return "\n".join(csv_lines)


# ============================================
# Servicios de Inicialización de Datos
# ============================================

class DatosInicialesService:
    """Servicio para crear datos iniciales."""
    
    @staticmethod
    async def crear_datos_iniciales(db: AsyncSession):
        """Crea los roles y permisos iniciales del sistema."""
        
        # Crear permisos
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
        
        # Crear roles
        roles_data = [
            ("Administrador", "Administrador del sistema con acceso completo", list(permisos.keys())),
            ("Cliente", "Cliente de la plataforma", []),
            ("Encargado", "Encargado de sucursal", [
                "gestionar_sucursales", "gestionar_inventario", 
                "gestionar_reservas", "gestionar_ventas"
            ]),
            ("Cajero", "Cajero de sucursal", [
                "gestionar_ventas", "procesar_pagos"
            ]),
        ]
        
        for nombre, desc, perms in roles_data:
            result = await db.execute(
                select(Rol).where(Rol.nombre == nombre)
            )
            rol = result.scalar_one_or_none()
            
            if not rol:
                rol = Rol(nombre=nombre, descripcion=desc)
                db.add(rol)
                await db.flush()
                
                # Asignar permisos
                for perm_nombre in perms:
                    if perm_nombre in permisos:
                        rol_permiso = RolPermiso(rol_id=rol.id, permiso_id=permisos[perm_nombre].id)
                        db.add(rol_permiso)
        
        # Crear administrador por defecto
        result = await db.execute(
            select(Usuario).where(Usuario.correo == "admin@fashionstore.com")
        )
        if not result.scalar_one_or_none():
            admin_user = Usuario(
                nombre="Administrador",
                apellido="Sistema",
                correo="admin@fashionstore.com",
                telefono="00000000",
                contrasena_hash=get_password_hash("Admin123!"),
                estado=EstadoUsuario.ACTIVO
            )
            db.add(admin_user)
            await db.flush()
            
            # Asignar rol Administrador
            result = await db.execute(
                select(Rol).where(Rol.nombre == "Administrador")
            )
            admin_rol = result.scalar_one_or_none()
            
            if admin_rol:
                usuario_rol = UsuarioRol(usuario_id=admin_user.id, rol_id=admin_rol.id)
                db.add(usuario_rol)
            
            # Crear registro de administrador
            admin = Administrador(usuario_id=admin_user.id)
            db.add(admin)
        
        await db.commit()
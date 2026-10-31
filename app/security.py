"""
Módulos de seguridad: JWT, hashing de contraseñas y verificación de tokens.
"""
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import uuid

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, status, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.database import get_db


def get_client_ip(request: Request) -> str:
    """
    Extrae la dirección IP del cliente, priorizando cabeceras de proxy
    como X-Forwarded-For y X-Real-IP cuando está detrás de Nginx/Render/Cloudflare.
    """
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    
    x_real_ip = request.headers.get("X-Real-IP")
    if x_real_ip:
        return x_real_ip.strip()
        
    if request.client and request.client.host:
        return request.client.host
        
    return "127.0.0.1"

# Contexto para hashing de contraseñas
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Security scheme
security = HTTPBearer()
security_optional = HTTPBearer(auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica que la contraseña plana coincida con el hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Genera el hash de una contraseña."""
    return pwd_context.hash(password)


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """
    Crea un token de acceso JWT.
    
    Args:
        data: Datos a incluir en el token
        expires_delta: Tiempo de expiración opcional
    
    Returns:
        Token JWT codificado
    """
    to_encode = data.copy()
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire, "type": "access"})
    
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def create_refresh_token(data: Dict[str, Any]) -> str:
    """
    Crea un token de refresco JWT.
    
    Args:
        data: Datos a incluir en el token
    
    Returns:
        Token de refresco JWT codificado
    """
    to_encode = data.copy()
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    expire = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def construir_claims_usuario(usuario) -> Dict[str, Any]:
    """[EXAMEN-2] Claims del token con tenant, super admin y roles."""
    roles = [getattr(r, "nombre", None) for r in (getattr(usuario, "roles", None) or [])]
    roles = [r for r in roles if r]
    return {
        "sub": str(usuario.id),
        "correo": usuario.correo,
        "tenant_id": getattr(usuario, "tenant_id", None),
        "es_super_admin": getattr(usuario, "tenant_id", None) is None and "Super Administrador" in roles,
        "roles": roles,
    }


def create_offline_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """[EXAMEN-2] CU17: JWT de propósito `offline` de vigencia corta.

    El backend no persiste tokens offline (plan, 3.7.10); la vigencia se
    controla con `ConfiguracionOffline.max_horas_sesion_offline`.
    """
    to_encode = data.copy()
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    if expires_delta is None:
        expires_delta = timedelta(hours=settings.OFFLINE_SESION_HORAS_DEFECTO)
    ahora = datetime.utcnow()
    expire = ahora + expires_delta
    to_encode.setdefault("sesion_iniciada", ahora.isoformat())
    to_encode.update({
        "jti": uuid.uuid4().hex,
        "iat": ahora,
        "exp": expire,
        "type": "offline",
        "proposito": "offline",
    })
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def verify_offline_token(token: str, db: AsyncSession) -> Dict[str, Any]:
    """[EXAMEN-2] Valida firma, propósito, usuario, tenant y dispositivo."""
    from app.apps.operaciones_offline.models import DispositivoSincronizacion
    from app.apps.gestion_usuarios.models import EstadoUsuario, Usuario
    from app.apps.gestion_saas.models import EstadoTenant, Tenant

    payload = decode_token(token)
    if payload.get("proposito") != "offline" or payload.get("type") != "offline":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El token no es de propósito offline",
        )

    try:
        usuario_id = int(payload.get("sub"))
        tenant_id = int(payload.get("tenant_id"))
        dispositivo_id = payload.get("dispositivo_id")
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token offline inválido")

    result = await db.execute(select(Usuario).where(Usuario.id == usuario_id))
    usuario = result.scalar_one_or_none()
    if usuario is None or usuario.estado != EstadoUsuario.ACTIVO:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuario inactivo")
    if usuario.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token de otro tenant")
    if not usuario.offline_pin_hash:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="PIN no configurado")

    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None or tenant.estado in (EstadoTenant.SUSPENDIDO, EstadoTenant.CANCELADO):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant suspendido")

    result = await db.execute(
        select(DispositivoSincronizacion).where(
            DispositivoSincronizacion.id == dispositivo_id,
            DispositivoSincronizacion.tenant_id == tenant_id,
            DispositivoSincronizacion.activo.is_(True),
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Dispositivo revocado")

    return payload


def decode_token(token: str) -> Dict[str, Any]:
    """
    Decodifica y verifica un token JWT.
    
    Args:
        token: Token JWT a decodificar
    
    Returns:
        Datos contenidos en el token
    
    Raises:
        HTTPException: Si el token es inválido o ha expirado
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
):
    """
    Dependencia para obtener el usuario actual desde el token JWT.
    
    Args:
        credentials: Credenciales del header Authorization
        db: Sesión de base de datos
    
    Returns:
        Datos del usuario autenticado
    
    Raises:
        HTTPException: Si el token es inválido
    """
    from app.apps.gestion_usuarios.models import Usuario, EstadoUsuario
    
    token = credentials.credentials
    payload = decode_token(token)

    # [EXAMEN-2] Un token offline no sirve para endpoints que exigen conexión
    if payload.get("proposito") == "offline" or payload.get("type") == "offline":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere un token de acceso en línea",
        )
    
    user_id_raw = payload.get("sub")
    if user_id_raw is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido: usuario no encontrado"
        )
    
    try:
        user_id = int(user_id_raw)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido: identificador no válido"
        )
    
    from sqlalchemy.orm import selectinload
    # Buscar el usuario en la base de datos con roles y cliente precargados
    result = await db.execute(
        select(Usuario)
        .options(
            selectinload(Usuario.roles),
            selectinload(Usuario.cliente)
        )
        .where(Usuario.id == user_id)
    )
    user = result.scalar_one_or_none()
    
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado"
        )
    
    # Verificar que el usuario esté activo
    if user.estado != EstadoUsuario.ACTIVO and user.estado != "ACTIVO":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuario inactivo o bloqueado"
        )

    # [EXAMEN-2] El token no puede cambiar de tenant y el tenant no puede estar cancelado
    claim_tenant = payload.get("tenant_id")
    if claim_tenant is not None and int(claim_tenant) != user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El token pertenece a otro tenant",
        )
    if user.tenant_id is not None:
        from app.apps.gestion_saas.models import EstadoTenant, Tenant

        result = await db.execute(select(Tenant).where(Tenant.id == user.tenant_id))
        tenant = result.scalar_one_or_none()
        if tenant is None or tenant.estado == EstadoTenant.CANCELADO:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant cancelado o inexistente",
            )
        # [EXAMEN-2] RN-004: un tenant suspendido no puede operar ni con un token vigente
        if tenant.estado == EstadoTenant.SUSPENDIDO:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="La empresa está suspendida",
            )

    # [EXAMEN-2] Contexto para logging/auditoría (plan, 4.5)
    from app.tenant import es_super_admin, set_tenant_context

    set_tenant_context(
        tenant_id=user.tenant_id,
        usuario_id=user.id,
        es_super_admin=es_super_admin(user),
        roles=[r.nombre for r in (user.roles or [])],
    )
    
    return user


async def get_optional_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_optional),
    db: AsyncSession = Depends(get_db)
):
    """
    Dependencia opcional para obtener el usuario actual si el token JWT está presente.
    Si no hay token o es inválido, retorna None sin lanzar excepción 401.
    """
    if not credentials or not credentials.credentials:
        return None
    try:
        from app.apps.gestion_usuarios.models import Usuario, EstadoUsuario
        from sqlalchemy.orm import selectinload

        token = credentials.credentials
        payload = decode_token(token)
        # [EXAMEN-2] Ignorar tokens offline en endpoints públicos
        if payload.get("proposito") == "offline" or payload.get("type") == "offline":
            return None
        user_id_raw = payload.get("sub")
        if user_id_raw is None:
            return None
        user_id = int(user_id_raw)
        result = await db.execute(
            select(Usuario)
            .options(
                selectinload(Usuario.roles),
                selectinload(Usuario.cliente)
            )
            .where(Usuario.id == user_id)
        )
        user = result.scalar_one_or_none()
        if user and (user.estado == EstadoUsuario.ACTIVO or user.estado == "ACTIVO"):
            return user
        return None
    except Exception:
        return None


async def get_current_active_user(current_user = Depends(get_current_user)):
    """Dependencia para verificar que el usuario esté activo."""
    return current_user


def require_role(*allowed_roles: str):
    """
    Decorador/dependencia para verificar roles.
    
    Usage:
        @app.get("/admin/...")
        async def admin_endpoint(user = Depends(require_role("Administrador"))):
            ...
    """
    async def role_checker(
        current_user = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
    ):
        from app.apps.gestion_usuarios.models import UsuarioRol, Rol
        
        # Obtener los roles del usuario
        result = await db.execute(
            select(Rol).join(UsuarioRol, Rol.id == UsuarioRol.rol_id).where(UsuarioRol.usuario_id == current_user.id)
        )
        user_roles = result.scalars().all()
        
        user_role_names = [rol.nombre for rol in user_roles]
        
        # Verificar si el usuario tiene alguno de los roles permitidos
        if not any(role in user_role_names for role in allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso denegado. Roles requeridos: {', '.join(allowed_roles)}"
            )
        
        return current_user
    
    return role_checker
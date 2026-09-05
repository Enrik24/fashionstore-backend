"""
Módulos de seguridad: JWT, hashing de contraseñas y verificación de tokens.
"""
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.database import get_db

# Contexto para hashing de contraseñas
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Security scheme
security = HTTPBearer()


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
    
    # Buscar el usuario en la base de datos
    result = await db.execute(select(Usuario).where(Usuario.id == user_id))
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
    
    return user


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
"""
Dependencias comunes para la gestión de ventas, catálogo y devoluciones.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.apps.gestion_usuarios.models import Usuario, Cliente
from app.exceptions import ForbiddenException


async def obtener_cliente_actual(db: AsyncSession, usuario: Usuario) -> Cliente:
    """Obtiene el registro de Cliente asociado al Usuario autenticado."""
    q = select(Cliente).where(Cliente.usuario_id == usuario.id)
    res = await db.execute(q)
    cliente = res.scalar_one_or_none()
    if not cliente:
        raise ForbiddenException("El usuario actual no tiene perfil de cliente registrado")
    return cliente

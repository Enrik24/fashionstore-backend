"""
[EXAMEN-2] Contexto multitenant y dependencias de aislamiento.

Implementa los componentes del capítulo 4.3 del plan:
`TenantContext` (ContextVar), resolución del tenant desde el token,
validación de estado/suscripción/plan, *scoping* de consultas y guardas de
la sesión offline (CU17).

Regla de oro: el `tenant_id` **nunca** se toma del cuerpo de la petición;
sale del JWT o, para el Super Administrador, de la cabecera `X-Tenant-ID`.
"""
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date
from logging import Filter, LogRecord
from typing import Any, Iterable, Optional

from fastapi import Depends, Header, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.exceptions import (
    CarritoNoHabilitado,
    DispositivoRevocado,
    FuncionalidadNoDisponible,
    IdempotenciaRequerida,
    NotFoundException,
    PagoDiferidoNoDisponible,
    PinNoConfigurado,
    SesionOfflineVencida,
    SuscripcionVencida,
    TenantNoEncontrado,
    TenantSuspendido,
    ValidationException,
)
from app.security import (
    decode_token,
    get_current_user,
    security_optional,
)


# ============================================================
# TenantContext — ContextVar para logging y auditoría
# ============================================================

@dataclass
class TenantContext:
    """Datos del tenant activo durante una petición."""
    tenant_id: Optional[int] = None
    usuario_id: Optional[int] = None
    es_super_admin: bool = False
    roles: list[str] = field(default_factory=list)


_tenant_context: ContextVar[Optional[TenantContext]] = ContextVar(
    "tenant_context", default=None
)


def set_tenant_context(
    tenant_id: Optional[int],
    usuario_id: Optional[int] = None,
    es_super_admin: bool = False,
    roles: Optional[Iterable[str]] = None,
) -> TenantContext:
    """Inicia/actualiza el contexto del tenant para la petición actual."""
    ctx = TenantContext(
        tenant_id=tenant_id,
        usuario_id=usuario_id,
        es_super_admin=es_super_admin,
        roles=list(roles or []),
    )
    _tenant_context.set(ctx)
    return ctx


def get_tenant_context() -> Optional[TenantContext]:
    return _tenant_context.get()


def tenant_actual() -> Optional[int]:
    """[EXAMEN-2] Tenant del contexto de la petición (o None)."""
    ctx = _tenant_context.get()
    return ctx.tenant_id if ctx else None


def tenant_actual_requerido() -> int:
    """[EXAMEN-2] Tenant obligatorio para servicios de negocio con contexto."""
    tenant_id = tenant_actual()
    if tenant_id is None:
        raise TenantNoEncontrado("No hay un tenant activo en la petición")
    return tenant_id


# Slug del tenant que representa los datos históricos del Examen 1.
TENANT_DEMO_SLUG = "fashionstore-demo"


async def get_tenant_demo(db: AsyncSession):
    """Devuelve el tenant demo usado para endpoints públicos y semillas."""
    from app.apps.gestion_saas.models import Tenant

    result = await db.execute(select(Tenant).where(Tenant.slug == TENANT_DEMO_SLUG))
    return result.scalar_one_or_none()


async def resolver_tenant_publico(db: AsyncSession) -> int:
    """[EXAMEN-2] Resuelve el tenant de un endpoint público (registro).

    El plan (4.2) exige deducir el tenant del recurso/slug y nunca del
    cuerpo. Mientras exista un único tenant demo, ese es el destino por
    defecto; si no existe, se informa un error de negocio.
    """
    tenant = await get_tenant_demo(db)
    if tenant is None:
        raise TenantNoEncontrado("No hay un tenant público configurado")
    return tenant.id


def clear_tenant_context() -> None:
    _tenant_context.set(None)


class TenantContextFilter(Filter):
    """Filtro de logging que agrega `tenant_id` y `usuario_id` (plan, 4.5)."""

    def filter(self, record: LogRecord) -> bool:
        ctx = _tenant_context.get()
        record.tenant_id = ctx.tenant_id if ctx else None
        record.usuario_id = ctx.usuario_id if ctx else None
        return True


# ============================================================
# Helpers
# ============================================================

def es_super_admin(usuario) -> bool:
    """Un Super Administrador es un usuario sin tenant con el rol de plataforma."""
    if getattr(usuario, "tenant_id", None) is not None:
        return False
    nombres = {getattr(r, "nombre", None) for r in (getattr(usuario, "roles", None) or [])}
    return "Super Administrador" in nombres


def aplicar_filtro_tenant(query, modelo, tenant_id: int):
    """Aplica `.where(modelo.tenant_id == tenant_id)` a una consulta."""
    return query.where(modelo.tenant_id == tenant_id)


def validar_pertenencia_tenant(obj, tenant_id: int, nombre: str = "Recurso"):
    """Lanza 404 (no 403) si el objeto no existe o es de otro tenant."""
    if obj is None or getattr(obj, "tenant_id", None) != tenant_id:
        raise NotFoundException(f"{nombre} no encontrado")
    return obj


async def get_tenant_por_id(db: AsyncSession, tenant_id: int):
    from app.apps.gestion_saas.models import Tenant

    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise TenantNoEncontrado()
    return tenant


# ============================================================
# Resolución del tenant
# ============================================================

async def get_current_tenant(
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Resuelve y valida el tenant de la petición (plan, 4.2)."""
    from app.apps.gestion_saas.models import EstadoTenant

    tenant_id = getattr(current_user, "tenant_id", None)

    if tenant_id is None:
        # Super Administrador: solo puede operar sobre un tenant con cabecera explícita
        if not es_super_admin(current_user):
            raise TenantNoEncontrado("El usuario no tiene tenant asignado")
        if not x_tenant_id:
            raise TenantNoEncontrado("Se requiere la cabecera X-Tenant-ID")
        try:
            tenant_id = int(x_tenant_id)
        except (TypeError, ValueError):
            raise ValidationException("X-Tenant-ID inválido")

    tenant = await get_tenant_por_id(db, tenant_id)
    if tenant.estado == EstadoTenant.CANCELADO:
        raise TenantSuspendido("El tenant está cancelado")

    set_tenant_context(
        tenant_id=tenant.id,
        usuario_id=getattr(current_user, "id", None),
        es_super_admin=es_super_admin(current_user),
        roles=[r.nombre for r in (getattr(current_user, "roles", None) or [])],
    )
    return tenant


async def get_tenant_id(tenant=Depends(get_current_tenant)) -> int:
    """Dependencia ligera que devuelve solo el identificador del tenant."""
    return tenant.id


async def get_usuario_tenant(
    current_user=Depends(get_current_user),
    tenant=Depends(get_current_tenant),
):
    """Valida que el usuario pertenezca al tenant resuelto."""
    if getattr(current_user, "tenant_id", None) not in (None, tenant.id):
        raise NotFoundException("Usuario no encontrado")
    return current_user


async def contexto_tenant_opcional(
    credentials=Depends(security_optional),
    db: AsyncSession = Depends(get_db),
):
    """[EXAMEN-2] Activa el contexto del tenant si la petición trae token.

    Los endpoints públicos del Examen 1 (por ejemplo, el catálogo de
    sucursales) siguen funcionando sin credenciales —en ese caso se comportan
    como la tienda del tenant demo—, pero cuando un usuario autenticado los
    consulta reciben únicamente los datos de su tenant.
    """
    from sqlalchemy.orm import selectinload

    from app.apps.gestion_usuarios.models import EstadoUsuario, Usuario

    if not credentials or not credentials.credentials:
        # Sin credenciales se atiende la tienda pública del tenant demo
        # (plan, 4.2: el tenant se deduce del recurso, nunca del cuerpo).
        tenant_demo = await get_tenant_demo(db)
        if tenant_demo is not None:
            set_tenant_context(tenant_id=tenant_demo.id)
        return None
    try:
        payload = decode_token(credentials.credentials)
        if payload.get("proposito") == "offline" or payload.get("type") == "offline":
            return None
        user_id = int(payload.get("sub"))
    except Exception:
        return None

    result = await db.execute(
        select(Usuario)
        .options(selectinload(Usuario.roles))
        .where(Usuario.id == user_id)
    )
    usuario = result.scalar_one_or_none()
    if usuario is None or usuario.estado != EstadoUsuario.ACTIVO:
        return None
    claim_tenant = payload.get("tenant_id")
    if claim_tenant is not None and int(claim_tenant) != usuario.tenant_id:
        return None

    set_tenant_context(
        tenant_id=usuario.tenant_id,
        usuario_id=usuario.id,
        es_super_admin=es_super_admin(usuario),
        roles=[r.nombre for r in (usuario.roles or [])],
    )
    return usuario


# ============================================================
# Guardas de plataforma, estado y plan
# ============================================================

async def require_super_admin(current_user=Depends(get_current_user)):
    """Restringe endpoints de plataforma (CU01, CU02, CU03)."""
    if not es_super_admin(current_user):
        raise FuncionalidadNoDisponible(
            "Solo el Super Administrador de la plataforma puede realizar esta operación"
        )
    return current_user


async def require_tenant_operativo(tenant=Depends(get_current_tenant)):
    """Exige `Tenant.estado = OPERATIVO` para operaciones comerciales."""
    from app.apps.gestion_saas.models import EstadoTenant

    if tenant.estado != EstadoTenant.OPERATIVO:
        raise TenantSuspendido(
            "El tenant debe completar su configuración inicial antes de operar"
        )
    return tenant


async def get_suscripcion_activa(db: AsyncSession, tenant_id: int):
    """Devuelve la suscripción ACTIVA y vigente del tenant o lanza 402."""
    from sqlalchemy.orm import selectinload

    from app.apps.gestion_saas.models import EstadoSuscripcion, Suscripcion

    result = await db.execute(
        select(Suscripcion)
        .options(selectinload(Suscripcion.plan))
        .where(
            Suscripcion.tenant_id == tenant_id,
            Suscripcion.estado == EstadoSuscripcion.ACTIVA,
        )
        .order_by(Suscripcion.fecha_vencimiento.desc())
    )
    suscripcion = result.scalars().first()
    if suscripcion is None or suscripcion.fecha_vencimiento < date.today():
        raise SuscripcionVencida()
    return suscripcion


async def require_suscripcion_activa(
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """RN-004: un tenant sin suscripción activa no puede operar."""
    return await get_suscripcion_activa(db, tenant.id)


def require_plan_feature(caracteristica: str):
    """Fábrica de dependencias: `require_plan_feature("reportes_ia")`."""

    async def checker(
        tenant=Depends(get_current_tenant),
        db: AsyncSession = Depends(get_db),
    ):
        if caracteristica == "carrito_offline":
            from app.apps.operaciones_offline.models import ConfiguracionOffline

            result = await db.execute(
                select(ConfiguracionOffline).where(ConfiguracionOffline.tenant_id == tenant.id)
            )
            config = result.scalar_one_or_none()
            if config is not None and not config.habilitar_carrito_offline:
                raise FuncionalidadNoDisponible("El carrito offline está deshabilitado")
            return True

        suscripcion = await get_suscripcion_activa(db, tenant.id)
        plan = suscripcion.plan
        atributo = {
            "reportes_ia": "permite_reportes_ia",
            "pago_diferido": "permite_pago_diferido",
        }.get(caracteristica)
        if atributo is None:
            raise FuncionalidadNoDisponible(f"Funcionalidad desconocida: {caracteristica}")
        if not getattr(plan, atributo, False):
            raise FuncionalidadNoDisponible(
                f"El plan {plan.nombre} no incluye {caracteristica}"
            )
        return True

    return checker


def require_tenant_role(*roles: str):
    """Como `require_role`, pero valida que el rol se ejerza dentro del tenant."""
    async def checker(
        current_user=Depends(get_current_user),
        tenant=Depends(get_current_tenant),
        db: AsyncSession = Depends(get_db),
    ):
        # [EXAMEN-2] El Super Administrador opera sobre un tenant concreto con
        # `X-Tenant-ID` (plan, 4.2), aunque su rol no viva en ese tenant.
        if es_super_admin(current_user):
            return current_user

        from app.apps.gestion_usuarios.models import Rol, UsuarioRol

        result = await db.execute(
            select(Rol.nombre)
            .join(UsuarioRol, Rol.id == UsuarioRol.rol_id)
            .where(
                UsuarioRol.usuario_id == current_user.id,
                UsuarioRol.tenant_id == tenant.id,
            )
        )
        nombres = set(result.scalars().all())
        if not nombres.intersection(roles):
            raise FuncionalidadNoDisponible(
                f"Acceso denegado. Roles requeridos: {', '.join(roles)}"
            )
        return current_user

    return checker


# ============================================================
# Guardas offline (CU17)
# ============================================================

async def get_dispositivo_actual(
    x_device_id: Optional[str] = Header(default=None, alias="X-Device-ID"),
    credentials=Depends(security_optional),
    db: AsyncSession = Depends(get_db),
):
    """Resuelve el dispositivo del token offline o de `X-Device-ID`."""
    from uuid import UUID

    from app.apps.operaciones_offline.models import DispositivoSincronizacion

    dispositivo_id = None
    if credentials and credentials.credentials:
        payload = decode_token(credentials.credentials)
        dispositivo_id = payload.get("dispositivo_id")
    dispositivo_id = dispositivo_id or x_device_id
    if not dispositivo_id:
        raise SesionOfflineVencida("No se identificó el dispositivo")

    try:
        device_uuid = UUID(str(dispositivo_id))
    except (TypeError, ValueError):
        raise ValidationException("dispositivo_id inválido")

    result = await db.execute(
        select(DispositivoSincronizacion).where(
            DispositivoSincronizacion.id == device_uuid
        )
    )
    return result.scalar_one_or_none()


async def require_dispositivo_activo(
    dispositivo=Depends(get_dispositivo_actual),
    current_user=Depends(get_current_user),
):
    """RN-019: un dispositivo revocado no puede operar."""
    if dispositivo is None:
        raise DispositivoRevocado("Dispositivo no registrado")
    if not dispositivo.activo or dispositivo.revocado_en is not None:
        raise DispositivoRevocado()
    if dispositivo.usuario_id != getattr(current_user, "id", None):
        raise DispositivoRevocado("El dispositivo no pertenece al usuario")
    return dispositivo


async def require_sesion_offline_valida(
    credentials=Depends(security_optional),
    db: AsyncSession = Depends(get_db),
):
    """Valida propósito, expiración, usuario, tenant y dispositivo del token offline."""
    from app.apps.operaciones_offline.models import DispositivoSincronizacion
    from app.apps.gestion_usuarios.models import EstadoUsuario, Usuario
    from app.apps.gestion_saas.models import EstadoTenant, Tenant
    from sqlalchemy.orm import selectinload

    if not credentials or not credentials.credentials:
        raise SesionOfflineVencida("Token offline ausente")

    payload = decode_token(credentials.credentials)
    if payload.get("proposito") != "offline" or payload.get("type") != "offline":
        raise SesionOfflineVencida("El token no es de propósito offline")

    usuario_id = payload.get("sub")
    dispositivo_id = payload.get("dispositivo_id")
    tenant_id = payload.get("tenant_id")
    if usuario_id is None or dispositivo_id is None or tenant_id is None:
        raise SesionOfflineVencida("Token offline incompleto")

    result = await db.execute(
        select(Usuario)
        .options(selectinload(Usuario.roles))
        .where(Usuario.id == int(usuario_id))
    )
    usuario = result.scalar_one_or_none()
    if usuario is None or usuario.estado != EstadoUsuario.ACTIVO:
        raise SesionOfflineVencida("Usuario inactivo")
    if usuario.tenant_id != int(tenant_id):
        raise SesionOfflineVencida("El token pertenece a otro tenant")
    if not usuario.offline_pin_hash:
        raise PinNoConfigurado()

    result = await db.execute(select(Tenant).where(Tenant.id == int(tenant_id)))
    tenant = result.scalar_one_or_none()
    if tenant is None or tenant.estado != EstadoTenant.OPERATIVO:
        raise TenantSuspendido()

    result = await db.execute(
        select(DispositivoSincronizacion).where(
            DispositivoSincronizacion.id == dispositivo_id,
            DispositivoSincronizacion.tenant_id == int(tenant_id),
        )
    )
    dispositivo = result.scalar_one_or_none()
    if dispositivo is None or not dispositivo.activo or dispositivo.revocado_en is not None:
        raise DispositivoRevocado()

    set_tenant_context(
        tenant_id=int(tenant_id),
        usuario_id=usuario.id,
        es_super_admin=False,
        roles=[r.nombre for r in usuario.roles],
    )
    return {"payload": payload, "usuario": usuario, "dispositivo": dispositivo, "tenant": tenant}


async def autenticacion_flexible(
    credentials=Depends(security_optional),
    x_device_id: Optional[str] = Header(default=None, alias="X-Device-ID"),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
    db: AsyncSession = Depends(get_db),
):
    """[EXAMEN-2] Acepta token online u offline en la misma ruta.

    El motor de sincronización y el carrito offline deben funcionar tanto si
    el dispositivo ya recuperó la conexión (token normal) como si sigue con su
    token de propósito `offline` (plan 9.2.5, paso 2).
    """
    from uuid import UUID as _UUID

    from sqlalchemy.orm import selectinload as _selectinload

    from app.apps.gestion_saas.models import EstadoTenant, Tenant
    from app.apps.gestion_usuarios.models import EstadoUsuario, Usuario
    from app.apps.operaciones_offline.models import DispositivoSincronizacion

    if not credentials or not credentials.credentials:
        raise SesionOfflineVencida("Token ausente")

    payload = decode_token(credentials.credentials)
    es_offline = payload.get("proposito") == "offline" or payload.get("type") == "offline"

    try:
        usuario_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise SesionOfflineVencida("Token inválido")

    result = await db.execute(
        select(Usuario)
        .options(_selectinload(Usuario.roles))
        .where(Usuario.id == usuario_id)
    )
    usuario = result.scalar_one_or_none()
    if usuario is None or usuario.estado != EstadoUsuario.ACTIVO:
        raise SesionOfflineVencida("Usuario inactivo")

    tenant_id = payload.get("tenant_id") or usuario.tenant_id
    if tenant_id is None:
        if not es_super_admin(usuario) or not x_tenant_id:
            raise TenantNoEncontrado("Se requiere la cabecera X-Tenant-ID")
        tenant_id = int(x_tenant_id)
    tenant_id = int(tenant_id)
    if usuario.tenant_id not in (None, tenant_id):
        raise SesionOfflineVencida("El token pertenece a otro tenant")

    tenant = await get_tenant_por_id(db, tenant_id)
    if tenant.estado in {EstadoTenant.SUSPENDIDO, EstadoTenant.CANCELADO}:
        raise TenantSuspendido()

    dispositivo = None
    dispositivo_id = payload.get("dispositivo_id") or x_device_id
    if dispositivo_id:
        try:
            device_uuid = _UUID(str(dispositivo_id))
        except (TypeError, ValueError):
            raise ValidationException("dispositivo_id inválido")
        result = await db.execute(
            select(DispositivoSincronizacion).where(
                DispositivoSincronizacion.id == device_uuid,
                DispositivoSincronizacion.tenant_id == tenant_id,
            )
        )
        dispositivo = result.scalar_one_or_none()
        if dispositivo is None:
            raise DispositivoRevocado("Dispositivo no registrado")
        if not dispositivo.activo or dispositivo.revocado_en is not None:
            raise DispositivoRevocado()
        if dispositivo.usuario_id != usuario.id:
            raise DispositivoRevocado("El dispositivo no pertenece al usuario")

    set_tenant_context(
        tenant_id=tenant.id,
        usuario_id=usuario.id,
        es_super_admin=es_super_admin(usuario),
        roles=[r.nombre for r in (usuario.roles or [])],
    )
    return {
        "payload": payload,
        "usuario": usuario,
        "tenant": tenant,
        "dispositivo": dispositivo,
        "token_offline": es_offline,
    }


async def require_carrito_offline(
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Verifica `ConfiguracionOffline.habilitar_carrito_offline` (CU15)."""
    from app.apps.operaciones_offline.models import ConfiguracionOffline

    result = await db.execute(
        select(ConfiguracionOffline).where(ConfiguracionOffline.tenant_id == tenant.id)
    )
    config = result.scalar_one_or_none()
    if config is not None and not config.habilitar_carrito_offline:
        raise CarritoNoHabilitado()
    return config


async def require_pago_diferido(
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Exige que el plan permita pago diferido (RN-016)."""
    suscripcion = await get_suscripcion_activa(db, tenant.id)
    if not getattr(suscripcion.plan, "permite_pago_diferido", False):
        raise PagoDiferidoNoDisponible()
    return suscripcion


async def idempotencia_obligatoria(
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> str:
    """Exige `Idempotency-Key` en los POST que crean recursos con UUID cliente."""
    if not idempotency_key or not idempotency_key.strip():
        raise IdempotenciaRequerida()
    return idempotency_key.strip()


# El scoping transversal de SQLAlchemy vive en `app/tenant_events.py` para
# romper el ciclo de importación database -> tenant -> security -> database.

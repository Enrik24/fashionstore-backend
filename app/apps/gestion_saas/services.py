"""
[EXAMEN-2] Servicios de la Fase 1 — núcleo SaaS (CU01-CU04).

Los servicios de plataforma son la única excepción al *scoping* obligatorio
(plan, 4.4.6): pueden consultar varios tenants. Los servicios de
configuración de tenant siempre reciben `tenant_id` y filtran por él.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
import re
import secrets
import unicodedata
from typing import Dict, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.exceptions import (
    ConflictException,
    LimitePlanExcedido,
    NotFoundException,
    TenantNoEncontrado,
    ValidationException,
)
from app.security import get_password_hash
from app.apps.gestion_saas.models import (
    Backup,
    EstadoBackup,
    EstadoPlan,
    EstadoSuscripcion,
    EstadoTenant,
    PagoSuscripcion,
    Plan,
    Suscripcion,
    Tenant,
    TipoBackup,
)
from app.apps.gestion_saas.schemas import (
    DatosEmpresaUpdate,
    PagoSuscripcionCreate,
    PlanCreate,
    PlanUpdate,
    PreferenciasOfflineUpdate,
    SucursalConfigCreate,
    SuscripcionCreate,
    TenantCreate,
    TenantUpdate,
    UsuarioConfigCreate,
)
from app.apps.gestion_usuarios.models import (
    Administrador,
    Bitacora,
    Cajero,
    Cliente,
    EncargadoSucursal,
    EstadoUsuario,
    Rol,
    Usuario,
    UsuarioRol,
)
from app.apps.gestion_catalogo.models import Ciudad, Producto, Sucursal
from app.apps.operaciones_offline.models import ConfiguracionOffline


# ============================================================
# Utilidades
# ============================================================

def _slugify(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalizado.lower()).strip("-")
    return (slug or "tenant")[:50]


async def _bitacora(db: AsyncSession, tenant_id: Optional[int], usuario_id: Optional[int], accion: str, detalles: str):
    db.add(Bitacora(
        tenant_id=tenant_id,
        usuario_id=usuario_id,
        accion=accion,
        modulo="saas",
        detalles=detalles,
    ))


async def _rol_por_nombre(db: AsyncSession, nombre: str) -> Rol:
    result = await db.execute(select(Rol).where(Rol.nombre == nombre))
    rol = result.scalar_one_or_none()
    if rol is None:
        rol = Rol(nombre=nombre, descripcion=f"Rol {nombre}")
        db.add(rol)
        await db.flush()
    return rol


async def _ciudad_defecto(db: AsyncSession, nombre: str = "La Paz") -> Ciudad:
    result = await db.execute(select(Ciudad).where(Ciudad.nombre == nombre))
    ciudad = result.scalars().first()
    if ciudad is None:
        ciudad = Ciudad(nombre=nombre, pais="Bolivia")
        db.add(ciudad)
        await db.flush()
    return ciudad


# ============================================================
# TenantService (CU01)
# ============================================================

class TenantService:

    @staticmethod
    async def crear_tenant(db: AsyncSession, datos: TenantCreate) -> Tenant:
        """Alta completa y transaccional: tenant, entorno, admin y suscripción."""
        result = await db.execute(select(Tenant).where(Tenant.nit == datos.nit))
        if result.scalar_one_or_none():
            raise ConflictException("El NIT ya está registrado por otro tenant")

        result = await db.execute(select(Tenant).where(Tenant.correo_contacto == datos.correo_contacto))
        if result.scalar_one_or_none():
            raise ConflictException("El correo de contacto ya está registrado")

        result = await db.execute(select(Usuario).where(Usuario.correo == datos.admin_correo))
        if result.scalar_one_or_none():
            raise ConflictException("El correo del administrador ya está registrado")

        plan = None
        if datos.plan_id is not None:
            result = await db.execute(select(Plan).where(Plan.id == datos.plan_id))
            plan = result.scalar_one_or_none()
            if plan is None or plan.estado != EstadoPlan.ACTIVO:
                raise ValidationException("El plan indicado no existe o está inactivo")
        else:
            result = await db.execute(
                select(Plan).where(Plan.estado == EstadoPlan.ACTIVO).order_by(Plan.orden.asc(), Plan.id.asc())
            )
            plan = result.scalars().first()
        if plan is None:
            raise ValidationException("No hay planes activos para asignar")

        slug_base = datos.slug or _slugify(datos.razon_social)
        slug = slug_base
        contador = 2
        while True:
            result = await db.execute(select(Tenant).where(Tenant.slug == slug))
            if result.scalar_one_or_none() is None:
                break
            slug = f"{slug_base}-{contador}"
            contador += 1

        tenant = Tenant(
            razon_social=datos.razon_social,
            nit=datos.nit,
            direccion_fiscal=datos.direccion_fiscal,
            correo_contacto=datos.correo_contacto,
            telefono=datos.telefono,
            moneda=datos.moneda,
            zona_horaria=datos.zona_horaria,
            slug=slug,
            estado=EstadoTenant.CONFIGURACION_INICIAL,
        )
        db.add(tenant)
        await db.flush()

        # Entorno mínimo: sucursal matriz y configuración offline
        ciudad = await _ciudad_defecto(db)
        db.add(Sucursal(
            tenant_id=tenant.id,
            nombre="Matriz",
            direccion=datos.direccion_fiscal,
            telefono=datos.telefono or "00000000",
            ciudad_id=ciudad.id,
        ))
        db.add(ConfiguracionOffline(
            tenant_id=tenant.id,
            max_horas_sesion_offline=settings.OFFLINE_SESION_HORAS_DEFECTO,
            max_clientes=settings.OFFLINE_MAX_CLIENTES_DEFECTO,
            max_dias_orden_pendiente=settings.OFFLINE_MAX_DIAS_PENDIENTE if settings.OFFLINE_MAX_DIAS_PENDIENTE <= 30 else 1,
        ))

        # Administrador del tenant (mismo actor que "Administrador de Tenant")
        admin_user = Usuario(
            nombre=datos.admin_nombre,
            apellido=datos.admin_apellido,
            correo=datos.admin_correo,
            telefono=datos.telefono,
            contrasena_hash=get_password_hash(datos.admin_contrasena),
            estado=EstadoUsuario.ACTIVO,
            tenant_id=tenant.id,
            debe_cambiar_contrasena=True,
        )
        db.add(admin_user)
        await db.flush()

        rol_admin = await _rol_por_nombre(db, "Administrador")
        db.add(UsuarioRol(usuario_id=admin_user.id, rol_id=rol_admin.id, tenant_id=tenant.id))
        db.add(Administrador(usuario_id=admin_user.id, tenant_id=tenant.id))

        hoy = date.today()
        suscripcion = Suscripcion(
            tenant_id=tenant.id,
            plan_id=plan.id,
            fecha_inicio=hoy,
            fecha_vencimiento=hoy + timedelta(days=30),
            estado=EstadoSuscripcion.ACTIVA,
            estado_pago="PENDIENTE",
        )
        db.add(suscripcion)

        await _bitacora(
            db, tenant.id, admin_user.id, "TENANT_CREADO",
            f"Alta del tenant {tenant.slug} con plan {plan.nombre}",
        )

        await db.commit()
        await db.refresh(tenant)
        return tenant

    @staticmethod
    async def listar_tenants(
        db: AsyncSession,
        skip: int = 0,
        limit: int = 50,
        estado: Optional[str] = None,
        q: Optional[str] = None,
    ) -> tuple[list[Tenant], int]:
        query = select(Tenant)
        count_query = select(func.count(Tenant.id))
        if estado:
            query = query.where(Tenant.estado == estado)
            count_query = count_query.where(Tenant.estado == estado)
        if q:
            patron = f"%{q.strip()}%"
            filtro = or_(
                Tenant.razon_social.ilike(patron),
                Tenant.nit.ilike(patron),
                Tenant.correo_contacto.ilike(patron),
                Tenant.slug.ilike(patron),
            )
            query = query.where(filtro)
            count_query = count_query.where(filtro)
        total = (await db.execute(count_query)).scalar_one() or 0
        result = await db.execute(query.order_by(Tenant.id.desc()).offset(skip).limit(limit))
        return list(result.scalars().all()), total

    @staticmethod
    async def obtener_tenant(db: AsyncSession, tenant_id: int) -> Tenant:
        result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = result.scalar_one_or_none()
        if tenant is None:
            raise TenantNoEncontrado()
        return tenant

    @staticmethod
    async def actualizar_tenant(db: AsyncSession, tenant_id: int, datos: TenantUpdate) -> Tenant:
        tenant = await TenantService.obtener_tenant(db, tenant_id)
        if datos.nit and datos.nit != tenant.nit:
            result = await db.execute(select(Tenant).where(Tenant.nit == datos.nit, Tenant.id != tenant_id))
            if result.scalar_one_or_none():
                raise ConflictException("El NIT ya está registrado por otro tenant")
        if datos.correo_contacto and datos.correo_contacto != tenant.correo_contacto:
            result = await db.execute(
                select(Tenant).where(Tenant.correo_contacto == datos.correo_contacto, Tenant.id != tenant_id)
            )
            if result.scalar_one_or_none():
                raise ConflictException("El correo de contacto ya está registrado")
        for campo, valor in datos.model_dump(exclude_unset=True).items():
            setattr(tenant, campo, valor)
        await db.commit()
        await db.refresh(tenant)
        return tenant

    @staticmethod
    async def cambiar_estado(db: AsyncSession, tenant_id: int, estado: str, motivo: Optional[str] = None) -> Tenant:
        tenant = await TenantService.obtener_tenant(db, tenant_id)
        try:
            nuevo = EstadoTenant(estado)
        except ValueError:
            raise ValidationException("Estado de tenant inválido")
        tenant.estado = nuevo
        tenant.fecha_baja = datetime.utcnow() if nuevo == EstadoTenant.CANCELADO else None
        if nuevo == EstadoTenant.SUSPENDIDO:
            result = await db.execute(
                select(Suscripcion).where(
                    Suscripcion.tenant_id == tenant_id,
                    Suscripcion.estado == EstadoSuscripcion.ACTIVA,
                )
            )
            for suscripcion in result.scalars().all():
                suscripcion.estado = EstadoSuscripcion.SUSPENDIDA
                suscripcion.motivo_estado = motivo or "Tenant suspendido"
        await _bitacora(db, tenant_id, None, "TENANT_ESTADO", f"Nuevo estado: {nuevo.value}. {motivo or ''}")
        await db.commit()
        await db.refresh(tenant)
        return tenant

    @staticmethod
    async def uso_tenant(db: AsyncSession, tenant_id: int) -> dict:
        await TenantService.obtener_tenant(db, tenant_id)
        sucursales = (await db.execute(
            select(func.count(Sucursal.id)).where(Sucursal.tenant_id == tenant_id)
        )).scalar_one() or 0
        usuarios = (await db.execute(
            select(func.count(Usuario.id)).where(Usuario.tenant_id == tenant_id)
        )).scalar_one() or 0
        productos = (await db.execute(
            select(func.count(Producto.id)).where(Producto.tenant_id == tenant_id)
        )).scalar_one() or 0
        max_sucursales = 0
        result = await db.execute(
            select(Suscripcion)
            .options(selectinload(Suscripcion.plan))
            .where(Suscripcion.tenant_id == tenant_id)
            .order_by(Suscripcion.fecha_vencimiento.desc())
        )
        suscripcion = result.scalars().first()
        if suscripcion and suscripcion.plan:
            max_sucursales = suscripcion.plan.max_sucursales
        # [EXAMEN-2] Fase B (punto 6): límites nuevos y respaldos del mes.
        max_usuarios = suscripcion.plan.max_usuarios if suscripcion and suscripcion.plan else None
        max_productos = suscripcion.plan.max_productos if suscripcion and suscripcion.plan else None
        max_backups_manuales = (
            suscripcion.plan.backup_manual_mensual if suscripcion and suscripcion.plan else None
        )
        inicio_mes = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        backups_mes = (await db.execute(
            select(func.count(Backup.id)).where(
                Backup.tenant_id == tenant_id,
                Backup.tipo == TipoBackup.MANUAL,
                Backup.fecha_inicio >= inicio_mes,
            )
        )).scalar_one() or 0
        return {
            "tenant_id": tenant_id,
            "sucursales_actuales": sucursales,
            "max_sucursales": max_sucursales,
            "usuarios_actuales": usuarios,
            "productos_actuales": productos,
            "max_usuarios": max_usuarios,
            "max_productos": max_productos,
            "backups_mes_actual": int(backups_mes),
            "max_backups_manuales": max_backups_manuales,
        }

    @staticmethod
    async def obtener_detalle(db: AsyncSession, tenant_id: int) -> dict:
        tenant = await TenantService.obtener_tenant(db, tenant_id)
        result = await db.execute(
            select(Suscripcion)
            .options(selectinload(Suscripcion.plan))
            .where(Suscripcion.tenant_id == tenant_id)
            .order_by(Suscripcion.fecha_vencimiento.desc())
        )
        suscripcion = result.scalars().first()
        return {
            "tenant": tenant,
            "suscripcion": suscripcion,
            "uso": await TenantService.uso_tenant(db, tenant_id),
        }

    @staticmethod
    async def reenviar_credenciales(db: AsyncSession, tenant_id: int) -> dict:
        tenant = await TenantService.obtener_tenant(db, tenant_id)
        result = await db.execute(
            select(Usuario)
            .join(UsuarioRol, UsuarioRol.usuario_id == Usuario.id)
            .join(Rol, Rol.id == UsuarioRol.rol_id)
            .where(Usuario.tenant_id == tenant_id, Rol.nombre == "Administrador")
        )
        admin = result.scalars().first()
        if admin is None:
            raise NotFoundException("El tenant no tiene administrador")
        temporal = f"Tmp{secrets.token_urlsafe(8)}!"
        admin.contrasena_hash = get_password_hash(temporal)
        admin.debe_cambiar_contrasena = True
        await _bitacora(db, tenant_id, admin.id, "CREDENCIALES_REENVIADAS", f"Correo: {admin.correo}")
        await db.commit()
        return {"correo": admin.correo, "contrasena_temporal": temporal, "tenant": tenant.slug}


# ============================================================
# PlanService (CU02)
# ============================================================

class PlanService:

    @staticmethod
    async def crear_plan(db: AsyncSession, datos: PlanCreate) -> Plan:
        result = await db.execute(select(Plan).where(Plan.nombre == datos.nombre))
        if result.scalar_one_or_none():
            raise ConflictException("Ya existe un plan con ese nombre")
        plan = Plan(**datos.model_dump())
        db.add(plan)
        await db.commit()
        await db.refresh(plan)
        return plan

    @staticmethod
    async def listar_planes(db: AsyncSession) -> list[Plan]:
        result = await db.execute(select(Plan).order_by(Plan.orden.asc(), Plan.id.asc()))
        return list(result.scalars().all())

    @staticmethod
    async def obtener_plan(db: AsyncSession, plan_id: int) -> Plan:
        result = await db.execute(select(Plan).where(Plan.id == plan_id))
        plan = result.scalar_one_or_none()
        if plan is None:
            raise NotFoundException("Plan no encontrado")
        return plan

    @staticmethod
    async def actualizar_plan(db: AsyncSession, plan_id: int, datos: PlanUpdate) -> Plan:
        plan = await PlanService.obtener_plan(db, plan_id)
        if datos.nombre and datos.nombre != plan.nombre:
            result = await db.execute(select(Plan).where(Plan.nombre == datos.nombre, Plan.id != plan_id))
            if result.scalar_one_or_none():
                raise ConflictException("Ya existe un plan con ese nombre")
        if datos.estado == EstadoPlan.INACTIVO.value:
            result = await db.execute(
                select(func.count(Suscripcion.id)).where(
                    Suscripcion.plan_id == plan_id,
                    Suscripcion.estado == EstadoSuscripcion.ACTIVA,
                )
            )
            if (result.scalar_one() or 0) > 0:
                raise ConflictException("No se puede desactivar un plan con suscripciones activas")
        for campo, valor in datos.model_dump(exclude_unset=True).items():
            setattr(plan, campo, valor)
        await db.commit()
        await db.refresh(plan)
        return plan

    @staticmethod
    async def eliminar_plan(db: AsyncSession, plan_id: int) -> None:
        plan = await PlanService.obtener_plan(db, plan_id)
        result = await db.execute(
            select(func.count(Suscripcion.id)).where(Suscripcion.plan_id == plan_id)
        )
        if (result.scalar_one() or 0) > 0:
            raise ConflictException("No se puede eliminar un plan con suscripciones")
        await db.delete(plan)
        await db.commit()

    @staticmethod
    async def impacto_plan(db: AsyncSession, plan_id: int) -> dict:
        plan = await PlanService.obtener_plan(db, plan_id)
        result = await db.execute(
            select(Suscripcion)
            .options(selectinload(Suscripcion.tenant))
            .where(Suscripcion.estado == EstadoSuscripcion.ACTIVA)
        )
        afectados = []
        for suscripcion in result.scalars().all():
            uso = await TenantService.uso_tenant(db, suscripcion.tenant_id)
            if uso["sucursales_actuales"] > plan.max_sucursales:
                afectados.append({
                    "tenant_id": suscripcion.tenant_id,
                    "razon_social": suscripcion.tenant.razon_social if suscripcion.tenant else None,
                    "sucursales_actuales": uso["sucursales_actuales"],
                })
        return {
            "plan_id": plan.id,
            "max_sucursales": plan.max_sucursales,
            "tenants_afectados": afectados,
            "total_afectados": len(afectados),
        }

    @staticmethod
    async def con_uso(db: AsyncSession, plan: Plan) -> dict:
        activas = (await db.execute(
            select(func.count(Suscripcion.id)).where(
                Suscripcion.plan_id == plan.id,
                Suscripcion.estado == EstadoSuscripcion.ACTIVA,
            )
        )).scalar_one() or 0
        data = {c.name: getattr(plan, c.name) for c in plan.__table__.columns}
        data["tenants_activos"] = activas
        data["suscripciones_activas"] = activas
        return data

    @staticmethod
    async def sembrar_planes(db: AsyncSession):
        """Semillas idempotentes de los tres planes (plan, 3.7.11 y punto 6)."""
        planes = [
            dict(nombre="Básico", descripcion="Hasta 2 sucursales, sin reportes IA",
                 precio_mensual=Decimal("199.00"), max_sucursales=2, max_usuarios=10,
                 max_productos=500, permite_reportes_ia=False, permite_pago_diferido=True,
                 orden=1, soporte_prioritario=False, backup_automatico=True,
                 backup_frecuencia_horas=168, backup_retencion_dias=7,
                 backup_manual_mensual=0, permite_restauracion=False),
            dict(nombre="Profesional", descripcion="Hasta 10 sucursales, reportes IA y pago diferido",
                 precio_mensual=Decimal("399.00"), max_sucursales=10, max_usuarios=50,
                 max_productos=5000, permite_reportes_ia=True, permite_pago_diferido=True,
                 orden=2, soporte_prioritario=False, backup_automatico=True,
                 backup_frecuencia_horas=24, backup_retencion_dias=30,
                 backup_manual_mensual=2, permite_restauracion=False),
            dict(nombre="Empresarial", descripcion="Límites ampliados, respaldos cada 6 h y restauración",
                 precio_mensual=Decimal("799.00"), max_sucursales=50, max_usuarios=200,
                 max_productos=None, permite_reportes_ia=True, permite_pago_diferido=True,
                 orden=3, soporte_prioritario=True, backup_automatico=True,
                 backup_frecuencia_horas=6, backup_retencion_dias=365,
                 backup_manual_mensual=None, permite_restauracion=True),
        ]
        for datos in planes:
            result = await db.execute(select(Plan).where(Plan.nombre == datos["nombre"]))
            plan = result.scalar_one_or_none()
            if plan is None:
                db.add(Plan(**datos))
                continue
            # Completa los campos de la Fase B sin tocar precios ya definidos.
            if plan.max_usuarios is None and datos["max_usuarios"] is not None:
                plan.max_usuarios = datos["max_usuarios"]
            if plan.max_productos is None and datos["max_productos"] is not None:
                plan.max_productos = datos["max_productos"]
            if not plan.backup_automatico:
                plan.backup_automatico = datos["backup_automatico"]
            if plan.backup_frecuencia_horas is None:
                plan.backup_frecuencia_horas = datos["backup_frecuencia_horas"]
            if plan.backup_manual_mensual is None and datos["backup_manual_mensual"] is not None:
                plan.backup_manual_mensual = datos["backup_manual_mensual"]
            if datos["permite_restauracion"]:
                plan.permite_restauracion = True
        await db.flush()


# ============================================================
# SuscripcionService (CU03)
# ============================================================

class SuscripcionService:

    @staticmethod
    async def _validar_limite_sucursales(db: AsyncSession, tenant_id: int, plan: Plan):
        actuales = (await db.execute(
            select(func.count(Sucursal.id)).where(Sucursal.tenant_id == tenant_id)
        )).scalar_one() or 0
        if actuales > plan.max_sucursales:
            raise LimitePlanExcedido(
                f"El tenant tiene {actuales} sucursales y el plan permite {plan.max_sucursales}"
            )

    @staticmethod
    async def _plan_activo(db: AsyncSession, tenant_id: int) -> Optional[Plan]:
        result = await db.execute(
            select(Suscripcion)
            .options(selectinload(Suscripcion.plan))
            .where(
                Suscripcion.tenant_id == tenant_id,
                Suscripcion.estado == EstadoSuscripcion.ACTIVA,
            )
        )
        suscripcion = result.scalars().first()
        if suscripcion is None:
            raise ValidationException("El tenant no tiene una suscripción activa")
        return suscripcion.plan

    @staticmethod
    async def verificar_limite_usuarios(db: AsyncSession, tenant_id: int):
        """[EXAMEN-2] Fase B: bloquea el alta de usuarios por encima del plan."""
        plan = await SuscripcionService._plan_activo(db, tenant_id)
        if plan is None or plan.max_usuarios is None:
            return plan
        actuales = (await db.execute(
            select(func.count(Usuario.id)).where(
                Usuario.tenant_id == tenant_id,
            )
        )).scalar_one() or 0
        if actuales >= plan.max_usuarios:
            raise LimitePlanExcedido(
                f"El plan {plan.nombre} permite {plan.max_usuarios} usuarios"
            )
        return plan

    @staticmethod
    async def verificar_limite_productos(db: AsyncSession, tenant_id: int):
        """[EXAMEN-2] Fase B: bloquea el alta de productos por encima del plan."""
        plan = await SuscripcionService._plan_activo(db, tenant_id)
        if plan is None or plan.max_productos is None:
            return plan
        actuales = (await db.execute(
            select(func.count(Producto.id)).where(
                Producto.tenant_id == tenant_id,
                Producto.deleted_at.is_(None),
            )
        )).scalar_one() or 0
        if actuales >= plan.max_productos:
            raise LimitePlanExcedido(
                f"El plan {plan.nombre} permite {plan.max_productos} productos"
            )
        return plan

    @staticmethod
    async def _validar_limites_plan(db: AsyncSession, tenant_id: int, plan: Plan) -> None:
        """Valida sucursales, usuarios y productos antes de asignar un plan."""
        await SuscripcionService._validar_limite_sucursales(db, tenant_id, plan)
        if plan.max_usuarios is not None:
            usuarios = (await db.execute(
                select(func.count(Usuario.id)).where(
                Usuario.tenant_id == tenant_id,
                )
            )).scalar_one() or 0
            if usuarios > plan.max_usuarios:
                raise LimitePlanExcedido(
                    f"El tenant tiene {usuarios} usuarios y el plan permite {plan.max_usuarios}"
                )
        if plan.max_productos is not None:
            productos = (await db.execute(
                select(func.count(Producto.id)).where(
                    Producto.tenant_id == tenant_id,
                    Producto.deleted_at.is_(None),
                )
            )).scalar_one() or 0
            if productos > plan.max_productos:
                raise LimitePlanExcedido(
                    f"El tenant tiene {productos} productos y el plan permite {plan.max_productos}"
                )

    @staticmethod
    async def verificar_limite_sucursales(db: AsyncSession, tenant_id: int):
        result = await db.execute(
            select(Suscripcion)
            .options(selectinload(Suscripcion.plan))
            .where(Suscripcion.tenant_id == tenant_id, Suscripcion.estado == EstadoSuscripcion.ACTIVA)
        )
        suscripcion = result.scalars().first()
        if suscripcion is None:
            raise ValidationException("El tenant no tiene una suscripción activa")
        actuales = (await db.execute(
            select(func.count(Sucursal.id)).where(
                Sucursal.tenant_id == tenant_id,
                Sucursal.deleted_at.is_(None),
            )
        )).scalar_one() or 0
        if actuales >= suscripcion.plan.max_sucursales:
            raise LimitePlanExcedido(
                f"El plan {suscripcion.plan.nombre} permite {suscripcion.plan.max_sucursales} sucursales"
            )
        return suscripcion

    @staticmethod
    async def asignar(db: AsyncSession, datos: SuscripcionCreate) -> Suscripcion:
        result = await db.execute(select(Tenant).where(Tenant.id == datos.tenant_id))
        if result.scalar_one_or_none() is None:
            raise TenantNoEncontrado()
        plan = await PlanService.obtener_plan(db, datos.plan_id)
        if plan.estado != EstadoPlan.ACTIVO:
            raise ValidationException("El plan está inactivo")

        result = await db.execute(
            select(Suscripcion).where(
                Suscripcion.tenant_id == datos.tenant_id,
                Suscripcion.estado == EstadoSuscripcion.ACTIVA,
            )
        )
        if result.scalars().first():
            raise ConflictException("El tenant ya tiene una suscripción activa")

        await SuscripcionService._validar_limites_plan(db, datos.tenant_id, plan)
        hoy = date.today()
        suscripcion = Suscripcion(
            tenant_id=datos.tenant_id,
            plan_id=datos.plan_id,
            fecha_inicio=datos.fecha_inicio or hoy,
            fecha_vencimiento=datos.fecha_vencimiento or (hoy + timedelta(days=30)),
            estado=EstadoSuscripcion.ACTIVA,
            renovacion_automatica=datos.renovacion_automatica,
        )
        db.add(suscripcion)
        await db.commit()
        # [EXAMEN-2] Se recarga con `plan` para que la respuesta no dispare
        # una carga perezosa fuera del contexto async (MissingGreenlet).
        return await SuscripcionService.obtener(db, suscripcion.id)

    @staticmethod
    async def obtener(db: AsyncSession, suscripcion_id: int) -> Suscripcion:
        result = await db.execute(
            select(Suscripcion)
            .options(selectinload(Suscripcion.plan))
            .where(Suscripcion.id == suscripcion_id)
        )
        suscripcion = result.scalar_one_or_none()
        if suscripcion is None:
            raise NotFoundException("Suscripción no encontrada")
        return suscripcion

    @staticmethod
    async def cambiar_plan(db: AsyncSession, suscripcion_id: int, plan_id: int) -> Suscripcion:
        suscripcion = await SuscripcionService.obtener(db, suscripcion_id)
        plan = await PlanService.obtener_plan(db, plan_id)
        if plan.estado != EstadoPlan.ACTIVO:
            raise ValidationException("El plan está inactivo")
        await SuscripcionService._validar_limites_plan(db, suscripcion.tenant_id, plan)
        suscripcion.plan_id = plan.id
        await db.commit()
        await db.refresh(suscripcion)
        return suscripcion

    @staticmethod
    async def renovar(db: AsyncSession, suscripcion_id: int, dias: int = 30) -> Suscripcion:
        suscripcion = await SuscripcionService.obtener(db, suscripcion_id)
        base = max(date.today(), suscripcion.fecha_vencimiento)
        suscripcion.fecha_vencimiento = base + timedelta(days=dias)
        suscripcion.estado = EstadoSuscripcion.ACTIVA
        await db.commit()
        await db.refresh(suscripcion)
        return suscripcion

    @staticmethod
    async def _cambiar_estado(db: AsyncSession, suscripcion_id: int, estado: EstadoSuscripcion, motivo: str) -> Suscripcion:
        suscripcion = await SuscripcionService.obtener(db, suscripcion_id)
        suscripcion.estado = estado
        suscripcion.motivo_estado = motivo
        tenant = await TenantService.obtener_tenant(db, suscripcion.tenant_id)
        if estado == EstadoSuscripcion.SUSPENDIDA:
            tenant.estado = EstadoTenant.SUSPENDIDO
        elif estado == EstadoSuscripcion.ACTIVA:
            tenant.estado = EstadoTenant.OPERATIVO
            tenant.fecha_baja = None
        elif estado == EstadoSuscripcion.CANCELADA:
            tenant.estado = EstadoTenant.CANCELADO
            tenant.fecha_baja = datetime.utcnow()
        await _bitacora(db, suscripcion.tenant_id, None, "SUSCRIPCION_ESTADO", f"{estado.value}: {motivo}")
        await db.commit()
        await db.refresh(suscripcion)
        return suscripcion

    @staticmethod
    async def suspender(db: AsyncSession, suscripcion_id: int, motivo: str = "Suspensión manual"):
        return await SuscripcionService._cambiar_estado(db, suscripcion_id, EstadoSuscripcion.SUSPENDIDA, motivo)

    @staticmethod
    async def reactivar(db: AsyncSession, suscripcion_id: int, motivo: str = "Reactivación manual"):
        return await SuscripcionService._cambiar_estado(db, suscripcion_id, EstadoSuscripcion.ACTIVA, motivo)

    @staticmethod
    async def cancelar(db: AsyncSession, suscripcion_id: int, motivo: str = "Cancelación manual"):
        return await SuscripcionService._cambiar_estado(db, suscripcion_id, EstadoSuscripcion.CANCELADA, motivo)

    @staticmethod
    async def mi_suscripcion(db: AsyncSession, tenant_id: int) -> Optional[Suscripcion]:
        result = await db.execute(
            select(Suscripcion)
            .options(selectinload(Suscripcion.plan))
            .where(Suscripcion.tenant_id == tenant_id)
            .order_by(Suscripcion.fecha_vencimiento.desc())
        )
        return result.scalars().first()


# ============================================================
# PagoSuscripcionService
# ============================================================

class PagoSuscripcionService:

    @staticmethod
    async def registrar(db: AsyncSession, datos: PagoSuscripcionCreate) -> PagoSuscripcion:
        suscripcion = await SuscripcionService.obtener(db, datos.suscripcion_id)
        if datos.stripe_payment_intent_id:
            result = await db.execute(
                select(PagoSuscripcion).where(
                    PagoSuscripcion.tenant_id == suscripcion.tenant_id,
                    PagoSuscripcion.stripe_payment_intent_id == datos.stripe_payment_intent_id,
                )
            )
            if result.scalar_one_or_none():
                raise ConflictException("El pago ya está registrado")
        from app.apps.gestion_ventas.models import EstadoTransaccion

        datos_pago = datos.model_dump(exclude={"suscripcion_id"})
        datos_pago["estado"] = EstadoTransaccion(datos_pago["estado"])
        pago = PagoSuscripcion(
            tenant_id=suscripcion.tenant_id,
            suscripcion_id=suscripcion.id,
            **datos_pago,
        )
        db.add(pago)
        await db.commit()
        await db.refresh(pago)
        return pago

    @staticmethod
    async def crear_sesion_stripe(
        db: AsyncSession, suscripcion_id: int,
        email_usuario: Optional[str] = None,
        success_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
        tenant_id: Optional[int] = None,
    ) -> Dict[str, str]:
        from app.apps.gestion_ventas.models import EstadoTransaccion
        from app.services.stripe_service import StripeService

        suscripcion = await SuscripcionService.obtener(db, suscripcion_id)
        if tenant_id is not None and suscripcion.tenant_id != tenant_id:
            raise NotFoundException("Suscripción no encontrada")
        plan = suscripcion.plan
        if plan is None:
            plan = await PlanService.obtener_plan(db, suscripcion.plan_id)

        if not email_usuario:
            tenant = await TenantService.obtener_tenant(db, suscripcion.tenant_id)
            email_usuario = tenant.correo_contacto

        session_data = StripeService.create_checkout_session_suscripcion(
            suscripcion_id=suscripcion.id,
            tenant_id=suscripcion.tenant_id,
            plan_id=plan.id,
            plan_nombre=plan.nombre,
            precio_mensual=plan.precio_mensual,
            email_usuario=email_usuario,
            success_url=success_url,
            cancel_url=cancel_url,
        )

        pago = PagoSuscripcion(
            tenant_id=suscripcion.tenant_id,
            suscripcion_id=suscripcion.id,
            monto=plan.precio_mensual,
            moneda="USD",
            estado=EstadoTransaccion.PENDIENTE,
            stripe_checkout_session_id=session_data["session_id"],
            referencia=session_data["session_id"],
            datos_respuesta={"session_id": session_data["session_id"], "plan_id": plan.id},
        )
        db.add(pago)
        await db.commit()
        return session_data

    @staticmethod
    async def procesar_pago_stripe_completado(
        db: AsyncSession, session_id: str, suscripcion_id: Optional[int] = None
    ) -> str:
        from app.apps.gestion_ventas.models import EstadoTransaccion
        from app.services.stripe_service import StripeService

        session_stripe = StripeService.retrieve_session(session_id)
        status = getattr(session_stripe, "payment_status", None) or "unknown"
        if status != "paid":
            return status

        result = await db.execute(
            select(PagoSuscripcion).where(
                PagoSuscripcion.stripe_checkout_session_id == session_id
            )
        )
        pago = result.scalar_one_or_none()
        if pago is None and suscripcion_id is not None:
            result = await db.execute(
                select(PagoSuscripcion)
                .where(
                    PagoSuscripcion.suscripcion_id == suscripcion_id,
                    PagoSuscripcion.estado == EstadoTransaccion.PENDIENTE,
                )
                .order_by(PagoSuscripcion.fecha.desc())
                .limit(1)
            )
            pago = result.scalar_one_or_none()

        if pago is None:
            return status

        payment_intent = getattr(session_stripe, "payment_intent", None)
        if payment_intent:
            pago.stripe_payment_intent_id = payment_intent
        respuesta = dict(pago.datos_respuesta or {})
        respuesta.update({
            "session_id": session_id,
            "payment_intent": payment_intent,
            "payment_status": status,
        })
        pago.datos_respuesta = respuesta
        await db.flush()

        if pago.estado != EstadoTransaccion.CONFIRMADO:
            await PagoSuscripcionService.confirmar(db, pago.id)
        return status

    @staticmethod
    async def historial(db: AsyncSession, tenant_id: int, skip: int = 0, limit: int = 50) -> tuple[list[PagoSuscripcion], int]:
        query = select(PagoSuscripcion).where(PagoSuscripcion.tenant_id == tenant_id)
        total = (await db.execute(
            select(func.count(PagoSuscripcion.id)).where(PagoSuscripcion.tenant_id == tenant_id)
        )).scalar_one() or 0
        result = await db.execute(
            query.order_by(PagoSuscripcion.fecha.desc()).offset(skip).limit(limit)
        )
        return list(result.scalars().all()), total

    @staticmethod
    async def confirmar(db: AsyncSession, pago_id: int) -> PagoSuscripcion:
        result = await db.execute(select(PagoSuscripcion).where(PagoSuscripcion.id == pago_id))
        pago = result.scalar_one_or_none()
        if pago is None:
            raise NotFoundException("Pago no encontrado")
        from app.apps.gestion_ventas.models import EstadoTransaccion

        pago.estado = EstadoTransaccion.CONFIRMADO
        suscripcion = await SuscripcionService.obtener(db, pago.suscripcion_id)
        suscripcion.estado = EstadoSuscripcion.ACTIVA
        suscripcion.estado_pago = "CONFIRMADO"
        tenant = await TenantService.obtener_tenant(db, pago.tenant_id)
        if tenant.estado != EstadoTenant.CANCELADO:
            tenant.estado = EstadoTenant.OPERATIVO
        await db.commit()
        await db.refresh(pago)
        return pago


# ============================================================
# ConfiguracionTenantService (CU04)
# ============================================================

class ConfiguracionTenantService:

    @staticmethod
    async def _config_offline(db: AsyncSession, tenant_id: int) -> ConfiguracionOffline:
        result = await db.execute(
            select(ConfiguracionOffline).where(ConfiguracionOffline.tenant_id == tenant_id)
        )
        config = result.scalar_one_or_none()
        if config is None:
            config = ConfiguracionOffline(
                tenant_id=tenant_id,
                max_horas_sesion_offline=settings.OFFLINE_SESION_HORAS_DEFECTO,
                max_clientes=settings.OFFLINE_MAX_CLIENTES_DEFECTO,
            )
            db.add(config)
            await db.flush()
        return config

    @staticmethod
    async def asistente(db: AsyncSession, tenant_id: int) -> dict:
        tenant = await TenantService.obtener_tenant(db, tenant_id)
        pasos = []
        faltantes = []

        if tenant.direccion_fiscal and tenant.moneda:
            pasos.append("empresa")
        else:
            faltantes.append("empresa")

        sucursales = (await db.execute(
            select(func.count(Sucursal.id)).where(Sucursal.tenant_id == tenant_id)
        )).scalar_one() or 0
        (pasos if sucursales > 0 else faltantes).append("sucursales")

        admins = (await db.execute(
            select(func.count(Administrador.id)).where(Administrador.tenant_id == tenant_id)
        )).scalar_one() or 0
        (pasos if admins > 0 else faltantes).append("usuarios")

        config = await ConfiguracionTenantService._config_offline(db, tenant_id)
        (pasos if config is not None else faltantes).append("offline")

        total = len(pasos) + len(faltantes)
        porcentaje = int(round(len(pasos) * 100 / total)) if total else 0
        return {
            "tenant_id": tenant_id,
            "pasos_completados": pasos,
            "pasos_faltantes": faltantes,
            "porcentaje": porcentaje,
            "estado_tenant": tenant.estado.value if hasattr(tenant.estado, "value") else str(tenant.estado),
            "puede_finalizar": not faltantes,
        }

    @staticmethod
    async def actualizar_empresa(db: AsyncSession, tenant_id: int, datos: DatosEmpresaUpdate) -> Tenant:
        return await TenantService.actualizar_tenant(db, tenant_id, TenantUpdate(**datos.model_dump(exclude_unset=True)))

    @staticmethod
    async def crear_sucursal(db: AsyncSession, tenant_id: int, datos: SucursalConfigCreate) -> Sucursal:
        """[EXAMEN-2] Fase 2.5 (C3): alias delgado de `SucursalService.create`.

        Conserva la URL, el schema y el 201 del asistente CU04, pero delega en
        el dueño único del CRUD para no divergir en límite de plan, deduplicado,
        ciudad por defecto ni baja lógica.
        """
        from app.apps.gestion_catalogo.schemas import SucursalCreate
        from app.apps.gestion_catalogo.services import SucursalService

        datos_adaptados = SucursalCreate(
            nombre=datos.nombre,
            direccion=datos.direccion,
            telefono=datos.telefono,
            horario_atencion=datos.horario_atencion,
            latitud=datos.latitud,
            longitud=datos.longitud,
            ciudad_id=datos.ciudad_id,
        )
        sucursal = await SucursalService.create(
            db, datos_adaptados, tenant_id=tenant_id
        )
        await _bitacora(db, tenant_id, None, "SUCURSAL_CREADA", f"Sucursal {sucursal.nombre}")
        await db.commit()
        return sucursal

    @staticmethod
    async def crear_usuario(db: AsyncSession, tenant_id: int, datos: UsuarioConfigCreate) -> Usuario:
        # [EXAMEN-2] Fase B: límite de usuarios del plan.
        await SuscripcionService.verificar_limite_usuarios(db, tenant_id)
        result = await db.execute(select(Sucursal).where(
            Sucursal.id == datos.sucursal_id, Sucursal.tenant_id == tenant_id
        ))
        if result.scalar_one_or_none() is None:
            raise NotFoundException("Sucursal no encontrada en el tenant")

        result = await db.execute(select(Usuario).where(Usuario.correo == datos.correo))
        if result.scalar_one_or_none():
            raise ConflictException("El correo ya está registrado")

        usuario = Usuario(
            nombre=datos.nombre,
            apellido=datos.apellido,
            correo=datos.correo,
            telefono=datos.telefono,
            contrasena_hash=get_password_hash(datos.contrasena),
            estado=EstadoUsuario.ACTIVO,
            tenant_id=tenant_id,
            debe_cambiar_contrasena=True,
        )
        db.add(usuario)
        await db.flush()

        rol = await _rol_por_nombre(db, datos.rol)
        db.add(UsuarioRol(usuario_id=usuario.id, rol_id=rol.id, tenant_id=tenant_id))
        if datos.rol == "Encargado":
            db.add(EncargadoSucursal(
                usuario_id=usuario.id, sucursal_id=datos.sucursal_id, tenant_id=tenant_id
            ))
        else:
            db.add(Cajero(
                usuario_id=usuario.id, sucursal_id=datos.sucursal_id, tenant_id=tenant_id
            ))
        await _bitacora(db, tenant_id, usuario.id, "USUARIO_TENANT_CREADO", f"{datos.rol}: {datos.correo}")
        await db.commit()
        await db.refresh(usuario)
        return usuario

    @staticmethod
    async def actualizar_offline(db: AsyncSession, tenant_id: int, datos: PreferenciasOfflineUpdate) -> ConfiguracionOffline:
        config = await ConfiguracionTenantService._config_offline(db, tenant_id)
        cambios = datos.model_dump(exclude_unset=True)
        if cambios.get("hora_descarga"):
            from datetime import time as _time

            try:
                hora, minuto = cambios["hora_descarga"].split(":")[:2]
                cambios["hora_descarga"] = _time(int(hora), int(minuto))
            except (ValueError, AttributeError):
                raise ValidationException("hora_descarga debe tener formato HH:MM")
        for campo, valor in cambios.items():
            setattr(config, campo, valor)
        await db.commit()
        await db.refresh(config)
        return config

    @staticmethod
    async def finalizar(db: AsyncSession, tenant_id: int) -> Tenant:
        tenant = await TenantService.obtener_tenant(db, tenant_id)
        if tenant.estado == EstadoTenant.CANCELADO:
            raise ValidationException("Un tenant cancelado no puede activarse")
        estado_asistente = await ConfiguracionTenantService.asistente(db, tenant_id)
        if not estado_asistente["puede_finalizar"]:
            raise ValidationException(
                "Faltan pasos de configuración: " + ", ".join(estado_asistente["pasos_faltantes"])
            )
        tenant.estado = EstadoTenant.OPERATIVO
        await _bitacora(db, tenant_id, None, "TENANT_OPERATIVO", "Configuración inicial finalizada")
        await db.commit()
        await db.refresh(tenant)
        return tenant

    @staticmethod
    async def obtener_configuracion(db: AsyncSession, tenant_id: int) -> dict:
        tenant = await TenantService.obtener_tenant(db, tenant_id)
        config = await ConfiguracionTenantService._config_offline(db, tenant_id)
        return {
            "tenant": tenant,
            "configuracion_offline": config,
            "asistente": await ConfiguracionTenantService.asistente(db, tenant_id),
        }

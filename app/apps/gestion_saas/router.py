"""
[EXAMEN-2] Router de la Fase 1 — Gestión SaaS (CU01-CU04).

- Endpoints `/api/v1/saas/tenants`, `/planes`, `/suscripciones` y webhooks:
  solo Super Administrador de plataforma.
- Endpoints `/api/v1/saas/configuracion`, `/mi-suscripcion` y `/mi-plan`:
  Administrador del tenant resuelto por el token.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import NotFoundException
from app.tenant import (
    require_super_admin,
    require_tenant_role,
    get_current_tenant,
)
from app.apps.gestion_saas.schemas import (
    AsistenteConfiguracionResponse,
    CambioPlanRequest,
    ConfiguracionOfflineResponse,
    ConfiguracionTenantResponse,
    DatosEmpresaUpdate,
    EstadoSuscripcionResponse,
    HistorialPagosResponse,
    ImpactoPlanResponse,
    PagoSuscripcionCreate,
    PagoSuscripcionResponse,
    PlanConUsoResponse,
    PlanCreate,
    PlanResponse,
    PlanUpdate,
    PreferenciasOfflineUpdate,
    SuscripcionCreate,
    SuscripcionResponse,
    SuscripcionUpdate,
    SucursalConfigCreate,
    TenantCreate,
    TenantDetalleResponse,
    TenantEstadoUpdate,
    TenantResponse,
    TenantUpdate,
    UsoTenantResponse,
    UsuarioConfigCreate,
)
from app.apps.gestion_saas.services import (
    ConfiguracionTenantService,
    PagoSuscripcionService,
    PlanService,
    SuscripcionService,
    TenantService,
)
from app.apps.operaciones_offline.services import ConfiguracionOfflineService

router = APIRouter(prefix="/api/v1/saas", tags=["SaaS Multitenant"])


# ============================================================
# Plataforma — Super Administrador
# ============================================================

@router.post("/tenants", response_model=TenantResponse, status_code=201)
async def registrar_tenant(
    datos: TenantCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    """CU01: alta completa (tenant + entorno + administrador + suscripción)."""
    tenant = await TenantService.crear_tenant(db, datos)
    return tenant


@router.get("/tenants")
async def listar_tenants(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    estado: str | None = None,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    tenants, total = await TenantService.listar_tenants(db, skip, limit, estado, q)
    return {
        "total": total,
        "items": [TenantResponse.model_validate(t) for t in tenants],
    }


@router.get("/tenants/{tenant_id}", response_model=TenantDetalleResponse)
async def obtener_tenant(
    tenant_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    detalle = await TenantService.obtener_detalle(db, tenant_id)
    data = TenantResponse.model_validate(detalle["tenant"]).model_dump()
    data["suscripcion"] = (
        SuscripcionResponse.model_validate(detalle["suscripcion"])
        if detalle["suscripcion"] else None
    )
    data["uso"] = UsoTenantResponse(**detalle["uso"])
    return data


@router.put("/tenants/{tenant_id}", response_model=TenantResponse)
async def actualizar_tenant(
    tenant_id: int,
    datos: TenantUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    return await TenantService.actualizar_tenant(db, tenant_id, datos)


@router.patch("/tenants/{tenant_id}/estado", response_model=TenantResponse)
async def cambiar_estado_tenant(
    tenant_id: int,
    datos: TenantEstadoUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    return await TenantService.cambiar_estado(db, tenant_id, datos.estado, datos.motivo)


@router.get("/tenants/{tenant_id}/uso", response_model=UsoTenantResponse)
async def uso_tenant(
    tenant_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    return await TenantService.uso_tenant(db, tenant_id)


@router.post("/tenants/{tenant_id}/reenviar-credenciales")
async def reenviar_credenciales(
    tenant_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    return await TenantService.reenviar_credenciales(db, tenant_id)


# ---------------------------- Planes ----------------------------

@router.post("/planes", response_model=PlanResponse, status_code=201)
async def crear_plan(
    datos: PlanCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    return await PlanService.crear_plan(db, datos)


@router.get("/planes")
async def listar_planes(
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    planes = await PlanService.listar_planes(db)
    return [PlanConUsoResponse(**await PlanService.con_uso(db, p)) for p in planes]


@router.get("/planes/{plan_id}", response_model=PlanConUsoResponse)
async def obtener_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    plan = await PlanService.obtener_plan(db, plan_id)
    return await PlanService.con_uso(db, plan)


@router.put("/planes/{plan_id}", response_model=PlanResponse)
async def actualizar_plan(
    plan_id: int,
    datos: PlanUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    return await PlanService.actualizar_plan(db, plan_id, datos)


@router.delete("/planes/{plan_id}")
async def eliminar_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    await PlanService.eliminar_plan(db, plan_id)
    return {"eliminado": True, "plan_id": plan_id}


@router.get("/planes/{plan_id}/impacto", response_model=ImpactoPlanResponse)
async def impacto_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    return await PlanService.impacto_plan(db, plan_id)


# ------------------------- Suscripciones -------------------------

@router.post("/suscripciones", response_model=SuscripcionResponse, status_code=201)
async def asignar_suscripcion(
    datos: SuscripcionCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    return await SuscripcionService.asignar(db, datos)


@router.put("/suscripciones/{suscripcion_id}/plan", response_model=SuscripcionResponse)
async def cambiar_plan(
    suscripcion_id: int,
    datos: CambioPlanRequest,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    return await SuscripcionService.cambiar_plan(db, suscripcion_id, datos.plan_id)


@router.post("/suscripciones/{suscripcion_id}/renovar", response_model=SuscripcionResponse)
async def renovar_suscripcion(
    suscripcion_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    return await SuscripcionService.renovar(db, suscripcion_id)


@router.post("/suscripciones/{suscripcion_id}/suspender", response_model=EstadoSuscripcionResponse)
async def suspender_suscripcion(
    suscripcion_id: int,
    motivo: str = Query("Suspensión manual"),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    suscripcion = await SuscripcionService.suspender(db, suscripcion_id, motivo)
    return {
        "suscripcion_id": suscripcion.id,
        "estado": suscripcion.estado,
        "motivo_estado": suscripcion.motivo_estado,
    }


@router.post("/suscripciones/{suscripcion_id}/reactivar", response_model=EstadoSuscripcionResponse)
async def reactivar_suscripcion(
    suscripcion_id: int,
    motivo: str = Query("Reactivación manual"),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    suscripcion = await SuscripcionService.reactivar(db, suscripcion_id, motivo)
    return {
        "suscripcion_id": suscripcion.id,
        "estado": suscripcion.estado,
        "motivo_estado": suscripcion.motivo_estado,
    }


@router.post("/suscripciones/{suscripcion_id}/cancelar", response_model=EstadoSuscripcionResponse)
async def cancelar_suscripcion(
    suscripcion_id: int,
    motivo: str = Query("Cancelación manual"),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    suscripcion = await SuscripcionService.cancelar(db, suscripcion_id, motivo)
    return {
        "suscripcion_id": suscripcion.id,
        "estado": suscripcion.estado,
        "motivo_estado": suscripcion.motivo_estado,
    }


@router.post("/suscripciones/checkout")
async def checkout_suscripcion(
    datos: PagoSuscripcionCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_super_admin),
):
    """RN-011: crea el intento de cobro. Sin clave Stripe opera en modo simulado."""
    pago = await PagoSuscripcionService.registrar(db, datos)
    return {
        "modo": "simulado",
        "pago_id": pago.id,
        "estado": pago.estado,
        "mensaje": "Configure STRIPE_API_KEY para cobrar con Stripe",
    }


@router.post("/webhooks/stripe")
async def webhook_stripe(payload: dict, db: AsyncSession = Depends(get_db)):
    """Confirmación de pago de suscripción (idempotente por payment_intent)."""
    from sqlalchemy import select

    from app.apps.gestion_saas.models import PagoSuscripcion
    from app.apps.gestion_ventas.models import EstadoTransaccion

    payment_intent = payload.get("data", {}).get("object", {}).get("id") or payload.get("payment_intent_id")
    if not payment_intent:
        return {"recibido": True, "procesado": False, "motivo": "Sin payment_intent_id"}
    result = await db.execute(
        select(PagoSuscripcion).where(PagoSuscripcion.stripe_payment_intent_id == payment_intent)
    )
    pago = result.scalar_one_or_none()
    if pago is None:
        return {"recibido": True, "procesado": False, "motivo": "Pago no registrado"}
    if pago.estado == EstadoTransaccion.CONFIRMADO:
        return {"recibido": True, "procesado": False, "motivo": "Pago ya confirmado"}
    await PagoSuscripcionService.confirmar(db, pago.id)
    return {"recibido": True, "procesado": True, "pago_id": pago.id}


# ============================================================
# Tenant — Administrador del tenant
# ============================================================

@router.get("/configuracion/asistente", response_model=AsistenteConfiguracionResponse)
async def asistente_configuracion(
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_tenant_role("Administrador")),
):
    return await ConfiguracionTenantService.asistente(db, tenant.id)


@router.get("/configuracion", response_model=ConfiguracionTenantResponse)
async def obtener_configuracion(
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_tenant_role("Administrador")),
):
    return await ConfiguracionTenantService.obtener_configuracion(db, tenant.id)


@router.put("/configuracion/empresa", response_model=TenantResponse)
async def actualizar_empresa(
    datos: DatosEmpresaUpdate,
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_tenant_role("Administrador")),
):
    return await ConfiguracionTenantService.actualizar_empresa(db, tenant.id, datos)


@router.post("/configuracion/sucursales", status_code=201)
async def crear_sucursal_config(
    datos: SucursalConfigCreate,
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_tenant_role("Administrador")),
):
    sucursal = await ConfiguracionTenantService.crear_sucursal(db, tenant.id, datos)
    return {
        "id": sucursal.id,
        "tenant_id": sucursal.tenant_id,
        "nombre": sucursal.nombre,
        "direccion": sucursal.direccion,
        "telefono": sucursal.telefono,
    }


@router.post("/configuracion/usuarios", status_code=201)
async def crear_usuario_config(
    datos: UsuarioConfigCreate,
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_tenant_role("Administrador")),
):
    usuario = await ConfiguracionTenantService.crear_usuario(db, tenant.id, datos)
    return {
        "id": usuario.id,
        "tenant_id": usuario.tenant_id,
        "correo": usuario.correo,
        "rol": datos.rol,
        "debe_cambiar_contrasena": usuario.debe_cambiar_contrasena,
    }


@router.put("/configuracion/offline", response_model=ConfiguracionOfflineResponse)
async def actualizar_offline(
    datos: PreferenciasOfflineUpdate,
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_tenant_role("Administrador")),
):
    return await ConfiguracionOfflineService.actualizar_configuracion(
        db, tenant.id, datos.model_dump(exclude_unset=True)
    )


@router.post("/configuracion/finalizar", response_model=TenantResponse)
async def finalizar_configuracion(
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_tenant_role("Administrador")),
):
    return await ConfiguracionTenantService.finalizar(db, tenant.id)


@router.get("/mi-suscripcion", response_model=SuscripcionResponse)
async def mi_suscripcion(
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    suscripcion = await SuscripcionService.mi_suscripcion(db, tenant.id)
    if suscripcion is None:
        raise NotFoundException("El tenant no tiene suscripción")
    return suscripcion


@router.get("/mi-plan/funcionalidades")
async def funcionalidades_plan(
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    suscripcion = await SuscripcionService.mi_suscripcion(db, tenant.id)
    config = await ConfiguracionOfflineService.obtener_configuracion(db, tenant.id)
    return {
        "plan": suscripcion.plan.nombre if suscripcion and suscripcion.plan else None,
        "reportes_ia": bool(suscripcion and suscripcion.plan and suscripcion.plan.permite_reportes_ia),
        "pago_diferido": bool(suscripcion and suscripcion.plan and suscripcion.plan.permite_pago_diferido),
        "carrito_offline": config.habilitar_carrito_offline,
        "max_sucursales": suscripcion.plan.max_sucursales if suscripcion and suscripcion.plan else 0,
    }


@router.get("/mi-suscripcion/pagos", response_model=HistorialPagosResponse)
async def historial_pagos(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    tenant=Depends(get_current_tenant),
    db: AsyncSession = Depends(get_db),
):
    pagos, total = await PagoSuscripcionService.historial(db, tenant.id, skip, limit)
    return {"total": total, "pagos": pagos}

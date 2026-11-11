"""
Punto de entrada principal de la aplicación FastAPI.
FashionStore - Plataforma de Comercio Electrónico
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import logging

from app.config import settings
from app.database import engine, Base
import app.apps.gestion_usuarios.models
import app.apps.gestion_catalogo.models
import app.apps.gestion_ventas.models
import app.apps.servicios_inteligentes.models
import app.apps.gestion_marketing.models
# [EXAMEN-2] Fase 1: núcleo SaaS y operación offline
import app.apps.gestion_saas.models
import app.apps.operaciones_offline.models
# [EXAMEN-2] Fase 2: pagos diferidos
import app.apps.pagos_diferidos.models
# [EXAMEN-2] Fase 3: analítica avanzada y gestión comercial/logística
import app.apps.analitica_avanzada.models
import app.apps.gestion_comercial_logistica.models
from app.apps.gestion_usuarios.router import router as router_usuarios
from app.apps.gestion_catalogo.router import router as router_catalogo
from app.apps.gestion_ventas.router import router as router_ventas
from app.apps.servicios_inteligentes.router import router as router_inteligencia
from app.apps.gestion_marketing.router import router as router_marketing
# [EXAMEN-2] Routers de la Fase 1
from app.apps.gestion_saas.router import router as router_saas
from app.apps.operaciones_offline.router import (
    router as router_offline,
    ws_router as ws_router_offline,
)
# [EXAMEN-2] Fase 2: carrito, pagos y WebSocket
from app.apps.pagos_diferidos.router import (
    router as router_pagos,
    ws_router as ws_router_pagos,
)
# [EXAMEN-2] Fase 3: analítica y devoluciones/traspasos
from app.apps.analitica_avanzada.router import router as router_analitica
from app.apps.gestion_comercial_logistica.router import router as router_comercial
from app.apps.gestion_usuarios.services import DatosInicialesService
from app.apps.gestion_catalogo.services import DatosInicialesCatalogoService
from app.apps.operaciones_offline.handlers import (
    registrar_handlers as registrar_handlers_offline,
)
from app.apps.pagos_diferidos.handlers import (
    registrar_handlers as registrar_handlers_pagos,
)
from app.apps.gestion_comercial_logistica.handlers import (
    registrar_handlers as registrar_handlers_comercial,
)
from app.exceptions import BusinessException
from app.tenant import TenantContextFilter, clear_tenant_context, get_tenant_context

# Configuración de logging
logging.basicConfig(level=logging.INFO)
# [EXAMEN-2] tenant_id y usuario_id en cada línea de log (plan, 4.5)
_tenant_filter = TenantContextFilter()
for _handler in logging.getLogger().handlers:
    _handler.addFilter(_tenant_filter)
logger = logging.getLogger(__name__)

# [EXAMEN-2] Fase 2 (plan 4.7): el Paquete 2 no importa a los Paquetes 4/5, por
# eso cada paquete registra aquí sus manejadores. Se hace también en el
# `lifespan` para tolerar reinicios del proceso.
registrar_handlers_offline()
registrar_handlers_pagos()
registrar_handlers_comercial()


async def _job_expiracion_pagos_offline() -> None:
    """Job periódico de expiración de pagos, órdenes y operaciones colgadas."""
    from app.apps.operaciones_offline.sincronizacion_service import (
        SincronizacionService,
    )
    from app.apps.pagos_diferidos.pago_service import PagoDiferidoService
    from app.database import AsyncSessionLocal

    while True:
        await asyncio.sleep(max(30, settings.PAGO_DIFERIDO_JOB_INTERVALO_SEG))
        try:
            async with AsyncSessionLocal() as session:
                pagos = await PagoDiferidoService.expirar_pagos(session)
                operaciones = await SincronizacionService.expirar_operaciones_pendientes(
                    session
                )
                if pagos.get("pagos_expirados") or pagos.get("ordenes_vencidas") or operaciones:
                    logger.info(
                        "Job de expiración: pagos=%s ordenes=%s operaciones=%s",
                        pagos.get("pagos_expirados"),
                        pagos.get("ordenes_vencidas"),
                        operaciones,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error en el job de expiración de pagos/offline")


async def _job_respaldos() -> None:
    """[EXAMEN-2] Fase B: retención y respaldos automáticos por plan (RF-035)."""
    from app.apps.gestion_saas.backup_service import BackupService
    from app.database import AsyncSessionLocal

    while True:
        await asyncio.sleep(max(60, settings.BACKUP_JOB_INTERVALO_SEG))
        try:
            async with AsyncSessionLocal() as session:
                expirados = await BackupService.aplicar_retencion(session)
                pendientes = await BackupService.respaldos_pendientes(session)
                for tenant_id in pendientes:
                    await BackupService.crear_automatico(session, tenant_id)
                if expirados or pendientes:
                    logger.info(
                        "Job de respaldos: expirados=%s pendientes=%s",
                        expirados,
                        pendientes,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error en el job de respaldos")


async def _job_revocacion_dispositivos() -> None:
    """Revoca a diario los dispositivos inactivos (CU17, RN-019)."""
    from app.apps.operaciones_offline.services import DispositivoService
    from app.database import AsyncSessionLocal

    while True:
        await asyncio.sleep(24 * 60 * 60)
        try:
            async with AsyncSessionLocal() as session:
                revocados = await DispositivoService.revocar_por_inactividad(session)
                if revocados:
                    logger.info(
                        "Dispositivos revocados por inactividad: %s", revocados
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error en el job de revocación de dispositivos")


async def _job_keepalive() -> None:
    """[EXAMEN-2] Anti-sleep del free tier de Render (temporal, quitar tras la
    defensa del examen): cada KEEPALIVE_INTERVALO_SEG golpea la URL pública de
    la propia API (/health) para que Render no suspenda la instancia por
    inactividad. Render cuenta tráfico que entra por el proxy público, por eso
    el ping va a RENDER_PUBLIC_URL y no a localhost (eso no lo despertaría).
    Solo activa si RENDER_PUBLIC_URL está definida: en local y en tests no corre.
    """
    import httpx

    if not settings.RENDER_PUBLIC_URL:
        logger.info("Keep-alive de Render desactivado (RENDER_PUBLIC_URL vacía)")
        return

    url = f"{settings.RENDER_PUBLIC_URL.rstrip('/')}/health"
    logger.info("Keep-alive de Render activo: self-ping a %s cada %s s", url, settings.KEEPALIVE_INTERVALO_SEG)
    while True:
        await asyncio.sleep(max(60, settings.KEEPALIVE_INTERVALO_SEG))
        try:
            async with httpx.AsyncClient(timeout=15) as cliente:
                r = await cliente.get(url)
            logger.info("Keep-alive ping %s: %s", url, r.status_code)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Keep-alive ping falló (reintenta en el próximo ciclo)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manejo del ciclo de vida de la aplicación.
    Se ejecuta al iniciar y al cerrar.
    """
    # Inicio de la aplicación
    logger.info("Iniciando FashionStore API...")

    registrar_handlers_offline()
    registrar_handlers_pagos()
    registrar_handlers_comercial()
    job_expiracion = asyncio.create_task(_job_expiracion_pagos_offline())
    app.state.job_expiracion = job_expiracion
    job_revocacion = asyncio.create_task(_job_revocacion_dispositivos())
    app.state.job_revocacion = job_revocacion
    job_respaldos = asyncio.create_task(_job_respaldos())
    app.state.job_respaldos = job_respaldos
    # [EXAMEN-2] Keep-alive del free tier de Render (no haga nada si la var no existe)
    job_keepalive = asyncio.create_task(_job_keepalive())
    app.state.job_keepalive = job_keepalive
    
    # Crear tablas en la base de datos
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Tablas de base de datos creadas")
    
    # Crear datos iniciales
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.database import AsyncSessionLocal
    
    async with AsyncSessionLocal() as session:
        try:
            # Crear roles, permisos y usuario administrador
            await DatosInicialesService.crear_datos_iniciales(session)
            logger.info("Datos iniciales de usuarios creados")
            
            # Crear categorías, tallas, colores iniciales del tenant demo
            from app.tenant import get_tenant_demo

            tenant_demo = await get_tenant_demo(session)
            await DatosInicialesCatalogoService.crear_datos_iniciales(
                session, tenant_id=tenant_demo.id if tenant_demo else None
            )
            logger.info("Datos iniciales del catálogo creados")
        except Exception as e:
            logger.warning(f"Error al crear datos iniciales (puede que ya existan): {e}")
    
    yield
    
    # Cierre de la aplicación
    logger.info("Cerrando FashionStore API...")
    job_expiracion.cancel()
    job_revocacion.cancel()
    job_respaldos.cancel()
    try:
        await job_expiracion
    except asyncio.CancelledError:
        pass
    try:
        await job_revocacion
    except asyncio.CancelledError:
        pass
    try:
        await job_respaldos
    except asyncio.CancelledError:
        pass
    job_keepalive.cancel()
    try:
        await job_keepalive
    except asyncio.CancelledError:
        pass
    await engine.dispose()


# Crear la aplicación FastAPI
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Backend API para FashionStore - Plataforma de Comercio Electrónico",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    # Permite los dominios variables (URLs de preview de Vercel). Es None si
    # no se configuró CORS_ORIGINS_REGEX.
    allow_origin_regex=settings.cors_origins_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# [EXAMEN-2] Errores de negocio con el contrato uniforme (plan, capítulo 14)
@app.exception_handler(BusinessException)
async def business_exception_handler(request, exc: BusinessException):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


# [EXAMEN-2] Contexto y logging por tenant (plan, 4.5)
@app.middleware("http")
async def tenant_context_middleware(request, call_next):
    import time

    clear_tenant_context()
    inicio = time.perf_counter()
    estado = "error"
    try:
        response = await call_next(request)
        estado = response.status_code
        return response
    finally:
        ctx = get_tenant_context()
        duracion_ms = round((time.perf_counter() - inicio) * 1000, 2)
        logger.info(
            "request method=%s path=%s tenant_id=%s usuario_id=%s estado=%s duracion_ms=%s",
            request.method,
            request.url.path,
            getattr(ctx, "tenant_id", None),
            getattr(ctx, "usuario_id", None),
            estado,
            duracion_ms,
        )
        clear_tenant_context()


# Incluir routers
app.include_router(router_usuarios)
app.include_router(router_catalogo)
# [EXAMEN-2] Fase 3: se registra antes que `gestion_ventas` para que
# `/devoluciones/historial` y `/devoluciones/pendientes-offline` no caigan en
# la ruta genérica `/devoluciones/{solicitud_id}` del módulo de ventas.
app.include_router(router_comercial)
app.include_router(router_ventas)
app.include_router(router_inteligencia)
app.include_router(router_marketing)
# [EXAMEN-2] Fase 1: SaaS y sesión offline
app.include_router(router_saas)
app.include_router(router_offline)
# [EXAMEN-2] Fase 2: carrito, pago diferido y WebSockets
app.include_router(router_pagos)
app.include_router(ws_router_offline)
app.include_router(ws_router_pagos)
# [EXAMEN-2] Fase 3: reportes IA, dashboard/ML, devoluciones y traspasos
app.include_router(router_analitica)


@app.get("/", tags=["Root"])
async def root():
    """Endpoint raíz de la API."""
    return {
        "message": "Bienvenido a FashionStore API",
        "version": settings.APP_VERSION,
        "docs": "/docs"
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Endpoint de verificación de estado."""
    return {"status": "healthy", "service": settings.APP_NAME}


if __name__ == "__main__":
    import uvicorn
    import os
    
    # Usar puerto de variable de entorno (Render) o 8000 por defecto
    port = int(os.environ.get("PORT", 8000))
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=settings.DEBUG
    )
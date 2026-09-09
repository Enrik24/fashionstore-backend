"""
Punto de entrada principal de la aplicación FastAPI.
FashionStore - Plataforma de Comercio Electrónico
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

from app.config import settings
from app.database import engine, Base
import app.apps.gestion_ventas.models
import app.apps.servicios_intelientes.models
from app.apps.gestion_usuarios.router import router as router_usuarios
from app.apps.gestion_catalogo.router import router as router_catalogo
from app.apps.gestion_usuarios.services import DatosInicialesService
from app.apps.gestion_catalogo.services import DatosInicialesCatalogoService

# Configuración de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manejo del ciclo de vida de la aplicación.
    Se ejecuta al iniciar y al cerrar.
    """
    # Inicio de la aplicación
    logger.info("Iniciando FashionStore API...")
    
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
            
            # Crear categorías, tallas, colores iniciales
            await DatosInicialesCatalogoService.crear_datos_iniciales(session)
            logger.info("Datos iniciales del catálogo creados")
        except Exception as e:
            logger.warning(f"Error al crear datos iniciales (puede que ya existan): {e}")
    
    yield
    
    # Cierre de la aplicación
    logger.info("Cerrando FashionStore API...")
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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Incluir routers
app.include_router(router_usuarios)
app.include_router(router_catalogo)


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
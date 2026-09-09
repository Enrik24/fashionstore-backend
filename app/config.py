"""
Configuración global de la aplicación FastAPI.
Carga las variables de entorno y proporciona configuración centralizada.
"""
from pydantic_settings import BaseSettings
from typing import List
import os
from functools import lru_cache


class Settings(BaseSettings):
    """Configuración de la aplicación cargada desde variables de entorno."""
    
    # Base de datos
    DB_NAME: str = "tiendaRopa"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "12345"
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DATABASE_URL: str = ""
    
    # SSL para proveedores cloud como Supabase (True por defecto para producción)
    DB_SSL: bool = True
    
    # JWT
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # Groq API
    GROQ_API_KEY: str = ""
    
    # Cloudinary
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""
    
    # Stripe
    STRIPE_API_KEY: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_SUCCESS_URL: str = "http://localhost:4200/pago-exitoso"
    STRIPE_CANCEL_URL: str = "http://localhost:4200/pago-cancelado"
    
    # PayPal
    PAYPAL_CLIENT_ID: str = ""
    PAYPAL_CLIENT_SECRET: str = ""
    PAYPAL_MODE: str = "sandbox"
    
    # App
    APP_NAME: str = "FashionStore API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    CORS_ORIGINS: str = "http://localhost:4200,http://localhost:3000"
    
    class Config:
        env_file = ".env"
        case_sensitive = True
    
    def __init__(self, **kwargs):
        super().__init__(kwargs)
        
        # Render proporciona DATABASE_URL con formato postgres://
        # Necesitamos convertirlo a postgresql+asyncpg:// para SQLAlchemy async
        database_url = os.environ.get("DATABASE_URL", "")
        if database_url.startswith("postgres://"):
            # Convertir postgres:// a postgresql+asyncpg://
            self.DATABASE_URL = database_url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif database_url.startswith("postgresql://"):
            # Convertir postgresql:// a postgresql+asyncpg://
            self.DATABASE_URL = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif not self.DATABASE_URL:
            # Construir DATABASE_URL desde variables individuales (desarrollo local)
            self.DATABASE_URL = (
                f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
                f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            )
        
        # Activar SSL automáticamente si el host es de Supabase u otros proveedores cloud
        if self.DATABASE_URL and ("supabase" in self.DATABASE_URL or "rds.amazonaws" in self.DATABASE_URL or "azure" in self.DATABASE_URL):
            self.DB_SSL = True
    
    @property
    def cors_origins_list(self) -> List[str]:
        """Retorna lista de orígenes CORS desde la cadena de configuración."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]


@lru_cache()
def get_settings() -> Settings:
    """Retorna la configuración cacheada."""
    return Settings()


settings = get_settings()
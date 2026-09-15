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
    
    # SSL para proveedores cloud como Supabase (False por defecto, se activa automáticamente para cloud)
    DB_SSL: bool = False
    
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
        
        # Obtener DATABASE_URL de variable de entorno directamente
        # (pydantic-settings ya la carga, pero la verificamos explícitamente)
        env_database_url = os.environ.get("DATABASE_URL", "")
        
        # Obtener variables individuales de entorno
        env_db_host = os.environ.get("DB_HOST", "")
        env_db_port = os.environ.get("DB_PORT", "")
        env_db_name = os.environ.get("DB_NAME", "")
        env_db_user = os.environ.get("DB_USER", "")
        env_db_password = os.environ.get("DB_PASSWORD", "")
        
        # Estrategia de construcción de DATABASE_URL:
        # 1. Si hay variables individuales (Render las inyecta), usarlas
        # 2. Si hay DATABASE_URL en env, convertirla al formato correcto
        # 3. Usar el valor por defecto del campo
        
        if env_db_host and env_db_port and env_db_name and env_db_user:
            # Render inyecta variables individuales - construir URL desde ellas
            self.DATABASE_URL = (
                f"postgresql+asyncpg://{env_db_user}:{env_db_password}"
                f"@{env_db_host}:{env_db_port}/{env_db_name}"
            )
        elif env_database_url:
            # Convertir formato de Render (postgres:// o postgresql://) a asyncpg
            if env_database_url.startswith("postgres://"):
                self.DATABASE_URL = env_database_url.replace("postgres://", "postgresql+asyncpg://", 1)
            elif env_database_url.startswith("postgresql://"):
                self.DATABASE_URL = env_database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif env_database_url.startswith("postgresql+asyncpg://"):
                self.DATABASE_URL = env_database_url
            else:
                # Si ya tiene el formato correcto o es otro formato, usar tal cual
                self.DATABASE_URL = env_database_url
        elif not self.DATABASE_URL:
            # Desarrollo local - construir desde variables de la clase
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
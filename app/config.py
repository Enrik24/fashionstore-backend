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
    DEBUG: bool = True
    CORS_ORIGINS: str = "http://localhost:4200,http://localhost:3000"
    
    class Config:
        env_file = ".env"
        case_sensitive = True
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Construir DATABASE_URL si no está definida
        if not self.DATABASE_URL:
            self.DATABASE_URL = (
                f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
                f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            )
    
    @property
    def cors_origins_list(self) -> List[str]:
        """Retorna lista de orígenes CORS desde la cadena de configuración."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]


@lru_cache()
def get_settings() -> Settings:
    """Retorna la configuración cacheada."""
    return Settings()


settings = get_settings()
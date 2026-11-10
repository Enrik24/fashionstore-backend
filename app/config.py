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
    STRIPE_SUSCRIPCION_SUCCESS_URL: str = "http://localhost:4200/suscripcion-exitosa"
    STRIPE_SUSCRIPCION_CANCEL_URL: str = "http://localhost:4200/suscripcion-cancelada"
    
    # PayPal
    PAYPAL_CLIENT_ID: str = ""
    PAYPAL_CLIENT_SECRET: str = ""
    PAYPAL_MODE: str = "sandbox"
    
    # Firebase Cloud Messaging
    FIREBASE_CREDENTIALS_PATH: str = "firebase-credentials.json"
    FIREBASE_CREDENTIALS_JSON: str = ""
    FIREBASE_PROJECT_ID: str = ""
    
    # App
    APP_NAME: str = "FashionStore API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    CORS_ORIGINS: str = "http://localhost:4200,http://localhost:3000"
    
    # CU26 y CU28 Config
    # [EXAMEN-2] RN-012: el plazo de devolución/cambio es de 7 días calendario.
    PLAZO_DEVOLUCION_DIAS: int = 7
    PLAZO_CAMBIO_DIAS: int = 7
    PLAZO_CAMBIO_PROMOCION_HORAS: int = 48
    PALABRAS_PROHIBIDAS: str = "estafa,fraude,falso,pesimo,basura,mierda,idiota,ladron,robado,horrible"

    # ============================================================
    # [EXAMEN-2] Variables del capítulo 6 del plan (Fase 1)
    # ============================================================
    # SaaS multitenant
    SAAS_MODO_ESTRICTO: bool = True
    TENANT_HEADER_HABILITADO: bool = True

    # Operación offline
    OFFLINE_LOTE_MAX_OPERACIONES: int = 50
    OFFLINE_MAX_DIAS_PENDIENTE: int = 30
    OFFLINE_BACKOFF_BASE_SEG: int = 5
    SYNC_CONFLICTO_UMBRAL_PRECIO: float = 0.05
    OFFLINE_PIN_MAX_INTENTOS: int = 5
    OFFLINE_PIN_LONGITUD_MIN: int = 6
    OFFLINE_SESION_HORAS_DEFECTO: int = 12
    OFFLINE_MAX_CLIENTES_DEFECTO: int = 100
    OFFLINE_RETENCION_DIAS: int = 90
    DISPOSITIVO_REVOCACION_AUTOMATICA_DIAS: int = 90

    # Idempotencia
    IDEMPOTENCIA_TTL_HORAS: int = 48

    # Analítica / IA / ML (se usan en la Fase 3, se declaran para no romper el contrato)
    DASHBOARD_CACHE_TTL_SEG: int = 300
    IA_MAX_FILAS: int = 5000
    IA_TIMEOUT_SEG: int = 20
    IA_PLANTILLAS_HABILITADAS: str = ""
    ML_RUTA_MODELOS: str = "./modelos_ml"
    ML_MIN_MESES_HISTORIAL: int = 3
    ML_MAX_FILAS_ENTRENAMIENTO: int = 200000
    EXPORT_MAX_MB: int = 50

    # Pagos diferidos y suscripciones (Stripe)
    STRIPE_WEBHOOK_SECRET_SUSCRIPCIONES: str = ""
    STRIPE_WEBHOOK_SECRET_PAGOS: str = ""
    STRIPE_PRICE_BASICO: str = ""
    STRIPE_PRICE_PROFESIONAL: str = ""
    STRIPE_MONEDA: str = "bob"
    STRIPE_API_VERSION: str = ""
    PAGO_DIFERIDO_VIGENCIA_HORAS: int = 24
    PAGO_DIFERIDO_MAX_REINTENTOS: int = 5
    PAGO_DIFERIDO_BACKOFF_BASE_SEG: int = 10
    PAGO_DIFERIDO_JOB_INTERVALO_SEG: int = 300

    # [EXAMEN-2] Fase B (punto 6): respaldos de base de datos por tenant.
    BACKUP_MODO: str = "logico"  # "logico" (JSON.GZ) | "sql" (INSERTs por tenant) | "pg_dump" (producción)
    BACKUP_DIR: str = "./backups"
    BACKUP_PERMITE_RESTAURACION: bool = False
    BACKUP_JOB_INTERVALO_SEG: int = 3600
    BACKUP_TIMEOUT_SEG: int = 300

    # [EXAMEN-2] Keep-alive del free tier de Render (temporal, solo para la
    # defensa del examen): self-ping HTTPS a la URL pública para que el
    #servicio free no se duerma tras ~15 min sin tráfico. Vacío = OFF, por
    # lo que en desarrollo local y en la suite de tests no corre nada extra.
    RENDER_PUBLIC_URL: str = ""
    KEEPALIVE_INTERVALO_SEG: int = 600

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
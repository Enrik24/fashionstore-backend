"""
Configuración global de la aplicación FastAPI.
Carga las variables de entorno y proporciona configuración centralizada.
"""
from typing import List
from pathlib import Path
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Ruta absoluta al .env del backend: así la configuración no depende del
# directorio desde el que se arranque (uvicorn, gunicorn, alembic, pytest).
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"

class Settings(BaseSettings):
    """Configuración de la aplicación cargada desde variables de entorno."""
    
    # Base de datos
    DB_NAME: str = "tiendaRopa"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "12345"
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DATABASE_URL: str = ""
    
    # SSL para proveedores cloud como Supabase/Render (False por defecto, se
    # activa automáticamente para hosts cloud). Vacío en el .env equivale a False.
    DB_SSL: bool = False

    @field_validator("DB_SSL", mode="before")
    @classmethod
    def _parse_db_ssl(cls, value):
        """Acepta vacío/None como False en lugar de fallar al validar el bool."""
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return False
        return value
    
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

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )
    
    @model_validator(mode="after")
    def _resolver_database_url(self):
        
        # ------------------------------------------------------------------
        # Selección de la base de datos: una sola fuente, sin mezclar.
        #   1. Si DATABASE_URL tiene valor (dashboard de Render o .env), esa
        #      manda y las variables DB_HOST/DB_PORT/DB_NAME/DB_USER/
        #      DB_PASSWORD se ignoran por completo.
        #   2. Si DATABASE_URL está vacía o no existe, se arma la URL con las
        #      variables DB_* del .env local.
        # ------------------------------------------------------------------
        database_url = (self.DATABASE_URL or "").strip()
        
        if database_url:
            # Render entrega "postgres://" o "postgresql://"; asyncpg necesita
            # el driver explícito. No se alteran usuario, contraseña ni params.
            if database_url.startswith("postgres://"):
                database_url = "postgresql+asyncpg://" + database_url[len("postgres://"):]
            elif database_url.startswith("postgresql://"):
                database_url = "postgresql+asyncpg://" + database_url[len("postgresql://"):]
        else:
            # Desarrollo local - construir desde las variables del .env
            database_url = (
                f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
                f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            )
        
        self.DATABASE_URL = database_url
        
        # Activar SSL automáticamente si el host es de Supabase u otros proveedores cloud.
        # En Render: el host externo (dpg-xxxx-a.<region>-postgres.render.com) exige
        # SSL; el host interno (dpg-xxxx-a) no lo necesita, así que este check no aplica.
        if self.DATABASE_URL and (
            "supabase" in self.DATABASE_URL
            or "rds.amazonaws" in self.DATABASE_URL
            or "azure" in self.DATABASE_URL
            or ".render.com" in self.DATABASE_URL
        ):
            self.DB_SSL = True

        return self
    
    @property
    def cors_origins_list(self) -> List[str]:
        """Retorna lista de orígenes CORS desde la cadena de configuración."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache()
def get_settings() -> Settings:
    """Retorna la configuración cacheada."""
    return Settings()


settings = get_settings()
from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    PROJECT_NAME: str = "D' OLGA SUPERAPP"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: str = "development"

    # PostgreSQL
    DATABASE_URL: str = "postgresql+asyncpg://dolga:dolga@localhost:5432/dolga"

    # JWT
    SECRET_KEY: str = "change-me-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 8
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14
    ALGORITHM: str = "HS256"

    # Clave Fernet usada para cifrar las credenciales de los equipos (MikroTik, UISP, OLT).
    # Se genera con:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    DEVICE_SECRET_KEY: str = ""

    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Valores por defecto de los conectores
    MIKROTIK_TIMEOUT: float = 8.0
    MIKROTIK_VERIFY_TLS: bool = False

    # Monitoreo: RouterOS no guarda histórico, así que lo muestreamos nosotros.
    MONITOR_ENABLED: bool = True
    MONITOR_INTERVAL_SECONDS: int = 30
    MONITOR_RETENTION_HOURS: int = 72

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value):
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

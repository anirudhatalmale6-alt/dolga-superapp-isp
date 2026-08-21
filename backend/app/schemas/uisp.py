from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.services.uisp.client import normalize_base_url


class ControllerBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(
        min_length=1,
        max_length=255,
        description="Dirección de UISP. Acepta 'uisp.midominio.net' o la URL completa.",
    )
    verify_tls: bool = False
    is_active: bool = True
    notes: Optional[str] = None

    @field_validator("base_url")
    @classmethod
    def _normalize(cls, value: str) -> str:
        # Se normaliza al entrar para que quede una sola forma en la base de
        # datos, sin importar cómo la haya escrito el operador.
        try:
            return normalize_base_url(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class ControllerCreate(ControllerBase):
    token: str = Field(
        min_length=1,
        max_length=512,
        description="App Key de UISP (Configuración -> Usuarios -> App keys).",
    )


class ControllerUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    verify_tls: Optional[bool] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None
    # Solo se escribe si viene; nunca se devuelve.
    token: Optional[str] = None

    @field_validator("base_url")
    @classmethod
    def _normalize(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        try:
            return normalize_base_url(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class ControllerOut(ControllerBase):
    """Nunca incluye el token: el frontend solo conoce el id del controlador."""

    id: int
    last_seen_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ControllerTestResult(BaseModel):
    ok: bool
    base_url: Optional[str] = None
    sites: Optional[int] = None
    devices: Optional[int] = None
    error: Optional[str] = None

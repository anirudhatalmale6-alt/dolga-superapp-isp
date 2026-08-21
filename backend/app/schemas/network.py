from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.models.network import DeviceVendor, MikrotikApiMode


class DeviceBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    host: str = Field(min_length=1, max_length=120)
    api_mode: MikrotikApiMode = MikrotikApiMode.AUTO
    rest_port: int = Field(default=443, ge=1, le=65535)
    rest_use_tls: bool = True
    api_port: int = Field(default=8728, ge=1, le=65535)
    api_use_tls: bool = False
    verify_tls: bool = False
    site: Optional[str] = None
    notes: Optional[str] = None
    is_active: bool = True


class DeviceCreate(DeviceBase):
    vendor: DeviceVendor = DeviceVendor.MIKROTIK
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=255)


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    api_mode: Optional[MikrotikApiMode] = None
    rest_port: Optional[int] = Field(default=None, ge=1, le=65535)
    rest_use_tls: Optional[bool] = None
    api_port: Optional[int] = Field(default=None, ge=1, le=65535)
    api_use_tls: Optional[bool] = None
    verify_tls: Optional[bool] = None
    site: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None
    username: Optional[str] = None
    # Solo se escribe si viene; nunca se devuelve.
    password: Optional[str] = None


class DeviceOut(DeviceBase):
    """Nunca incluye la contraseña: el frontend jamás recibe credenciales."""

    id: int
    vendor: DeviceVendor
    username: str
    last_seen_at: Optional[datetime] = None
    last_error: Optional[str] = None
    routeros_version: Optional[str] = None
    board_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConnectionTestResult(BaseModel):
    ok: bool
    transport: Optional[str] = None
    identity: Optional[str] = None
    version: Optional[str] = None
    board_name: Optional[str] = None
    error: Optional[str] = None


class SuspendRequest(BaseModel):
    username: str = Field(min_length=1, description="Usuario PPPoE del cliente")
    method: Literal["disable_secret", "change_profile", "address_list"] = "disable_secret"
    suspended_profile: str = "CORTADO"
    address_list: str = "morosos"
    comment: Optional[str] = None


class RestoreRequest(BaseModel):
    username: str = Field(min_length=1)
    method: Literal["disable_secret", "change_profile", "address_list"] = "disable_secret"
    active_profile: Optional[str] = None
    address_list: str = "morosos"
    comment: Optional[str] = None


class ActionResult(BaseModel):
    username: str
    method: str
    suspended: bool
    session_closed: bool
    actions: List[str]
    previous_state: Optional[Dict[str, Any]] = None

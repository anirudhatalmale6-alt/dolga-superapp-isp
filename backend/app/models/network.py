from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class DeviceVendor(str, enum.Enum):
    MIKROTIK = "mikrotik"
    UBIQUITI = "ubiquiti"
    OLT = "olt"


class MikrotikApiMode(str, enum.Enum):
    """Cómo hablamos con el equipo.

    REST  -> RouterOS v7 (http/https, /rest/...)
    API   -> API binaria de RouterOS (puerto 8728 plano / 8729 TLS), v6 y v7
    AUTO  -> intenta REST y cae a la API binaria si no está disponible
    """

    REST = "rest"
    API = "api"
    AUTO = "auto"


class NetworkDevice(Base, TimestampMixin):
    """Equipo de red administrado (router MikroTik, por ahora).

    Las credenciales se guardan cifradas en `password_encrypted` y nunca se
    serializan hacia el frontend.
    """

    __tablename__ = "network_devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    vendor: Mapped[DeviceVendor] = mapped_column(
        Enum(DeviceVendor, name="device_vendor", values_callable=lambda e: [i.value for i in e]),
        default=DeviceVendor.MIKROTIK,
        nullable=False,
    )
    host: Mapped[str] = mapped_column(String(120), nullable=False)
    api_mode: Mapped[MikrotikApiMode] = mapped_column(
        Enum(MikrotikApiMode, name="mikrotik_api_mode", values_callable=lambda e: [i.value for i in e]),
        default=MikrotikApiMode.AUTO,
        nullable=False,
    )
    rest_port: Mapped[int] = mapped_column(Integer, default=443, nullable=False)
    rest_use_tls: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    api_port: Mapped[int] = mapped_column(Integer, default=8728, nullable=False)
    api_use_tls: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    username: Mapped[str] = mapped_column(String(120), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    site: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Interfaz que se grafica como "tráfico hacia la calle". Si se deja vacía se
    # deduce sola: la que lleva la ruta por defecto activa de menor distancia.
    wan_interface: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)

    # Último estado conocido (lo refresca el monitoreo)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    routeros_version: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    board_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class UispController(Base, TimestampMixin):
    """Una instalación de UISP (propia o la nube de Ubiquiti).

    No es un equipo: es el controlador que ya tiene adoptadas las antenas. Por
    eso vive en su propia tabla y no en `network_devices`.

    El token se guarda cifrado con la misma clave que las credenciales de
    MikroTik y nunca se serializa hacia el frontend.
    """

    __tablename__ = "uisp_controllers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Se guarda ya normalizada, con /nms/api/v2.1 al final.
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # Las instalaciones propias suelen usar certificado autofirmado.
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

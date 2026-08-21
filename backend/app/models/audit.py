from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OperationLog(Base):
    """Bitácora de todo lo que la plataforma le pidió a un equipo.

    Cumple dos funciones distintas y las dos importan:

    1. Auditoría de operación. En un ISP hay que poder responder "¿quién
       suspendió a este cliente y a qué hora?" sin depender de la memoria de
       nadie.
    2. Prueba de que el dato es real. Cada fila guarda las llamadas que
       efectivamente salieron hacia el equipo (`upstream`): la URL exacta de
       UISP o la sentencia exacta de RouterOS, con su código de respuesta y su
       demora. Un dato inventado no deja esa huella.

    No se guarda ningún secreto: ni la App Key, ni la clave del router, ni el
    token de sesión. Solo la URL o el comando, sin cabeceras.
    """

    __tablename__ = "operation_logs"
    __table_args__ = (
        Index("ix_operation_logs_occurred", "occurred_at"),
        Index("ix_operation_logs_target", "target_kind", "target_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # El usuario se copia por valor: si mañana se da de baja, la bitácora tiene
    # que seguir diciendo quién ejecutó la acción.
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    user_email: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    user_role: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    source_ip: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)

    # "mikrotik" | "uisp" | "monitoreo"
    target_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    target_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    target_name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)

    # Nombre legible de la operación: "Reiniciar equipo Ubiquiti".
    operation: Mapped[str] = mapped_column(String(120), nullable=False)
    endpoint: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Las acciones cambian algo en el equipo; las lecturas no. Se separan
    # porque se conservan distinto: una consulta no vale la pena a los tres
    # meses, una suspensión sí.
    is_action: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # JSON con la lista de llamadas reales al equipo. Texto y no JSONB para que
    # la misma tabla sirva en PostgreSQL y en SQLite (pruebas y demo).
    upstream: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    upstream_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

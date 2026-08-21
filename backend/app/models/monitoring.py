from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DeviceSample(Base):
    """Una muestra periódica del estado de un equipo.

    RouterOS entrega el valor de *ahora*, no el histórico. Las curvas de tráfico
    y las minigráficas de CPU salen de esta tabla, que llena el servicio de
    monitoreo consultando cada equipo cada pocos segundos.
    """

    __tablename__ = "device_samples"
    __table_args__ = (
        # El acceso siempre es "las últimas N muestras de un equipo".
        Index("ix_device_samples_device_taken", "device_id", "taken_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("network_devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Si el equipo no respondió guardamos igual la muestra: un hueco en la
    # gráfica es información, y así el histórico distingue "sin datos" de "cero".
    reachable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    cpu_load_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    memory_used_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    disk_used_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # None cuando la placa no trae sensor de temperatura.
    temperature_c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    clients_online: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    uptime_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    wan_interface: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    wan_rx_mbps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    wan_tx_mbps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

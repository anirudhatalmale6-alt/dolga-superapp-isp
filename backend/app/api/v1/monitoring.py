"""Vista de flota e histórico.

Estos endpoints NO golpean los routers: leen lo que el servicio de monitoreo ya
guardó. Así la pantalla principal abre igual de rápido con 5 equipos que con 50,
y un router caído no deja la página colgada esperando un timeout.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_device
from app.db.session import get_session
from app.models.network import NetworkDevice
from app.models.user import User
from app.services import monitoring

router = APIRouter(prefix="/monitoring", tags=["monitoreo"])


@router.get("/fleet", summary="Totales de la red y estado de todos los equipos")
async def fleet(
    session: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)
) -> Dict[str, Any]:
    return await monitoring.fleet_summary(session)


@router.get(
    "/devices/{device_id}/history",
    summary="Serie de tiempo de un equipo (tráfico, CPU, memoria, clientes)",
)
async def device_history(
    minutes: int = Query(default=60, ge=1, le=4320),
    device: NetworkDevice = Depends(get_device),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> Dict[str, Any]:
    samples = await monitoring.history(session, device.id, minutes=minutes)
    points: List[Dict[str, Any]] = [
        {
            "t": sample.taken_at.isoformat(),
            "reachable": sample.reachable,
            "cpu": sample.cpu_load_percent,
            "memory": sample.memory_used_percent,
            "temperature": sample.temperature_c,
            "clients": sample.clients_online,
            "rx_mbps": sample.wan_rx_mbps,
            "tx_mbps": sample.wan_tx_mbps,
        }
        for sample in samples
    ]
    return {
        "device_id": device.id,
        "minutes": minutes,
        "interval_seconds": monitoring.settings.MONITOR_INTERVAL_SECONDS,
        "points": points,
    }

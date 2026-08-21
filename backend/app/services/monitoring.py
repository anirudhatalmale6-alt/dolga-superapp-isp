"""Servicio de monitoreo: muestrea todos los equipos y guarda el histórico.

RouterOS entrega el valor instantáneo, nunca la serie de tiempo. Para poder
dibujar el tráfico de la última hora o la minigráfica de CPU de cada equipo hay
que ir preguntando y acumulando. Eso hace este módulo:

* `poll_once`  -> consulta todos los equipos activos en paralelo y guarda una
                  muestra por cada uno (incluida la de los que no respondieron).
* `monitor_loop` -> lo repite cada `MONITOR_INTERVAL_SECONDS` y poda lo viejo.
* `fleet_summary` -> arma los totales y la tabla de equipos con la última
                  muestra de cada uno, sin volver a golpear los routers.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.crypto import DecryptionError, decrypt_secret
from app.models.monitoring import DeviceSample
from app.models.network import MikrotikApiMode, NetworkDevice
from app.services.mikrotik.client import MikrotikClient
from app.services.mikrotik.exceptions import MikrotikError
from app.services.mikrotik.service import MikrotikService

logger = logging.getLogger(__name__)


def client_for(device: NetworkDevice) -> MikrotikClient:
    mode = device.api_mode
    return MikrotikClient(
        host=device.host,
        username=device.username,
        password=decrypt_secret(device.password_encrypted),
        mode=mode.value if isinstance(mode, MikrotikApiMode) else str(mode),
        rest_port=device.rest_port,
        rest_use_tls=device.rest_use_tls,
        api_port=device.api_port,
        api_use_tls=device.api_use_tls,
        verify_tls=device.verify_tls,
        timeout=settings.MIKROTIK_TIMEOUT,
    )


async def sample_device(device: NetworkDevice) -> tuple[DeviceSample, Optional[Dict[str, Any]]]:
    """Consulta un equipo y devuelve (muestra, ficha), responda o no.

    Un equipo caído produce una muestra con `reachable=False`: el hueco en la
    gráfica y el motivo del fallo son parte del histórico que queremos.

    La "ficha" (modelo, versión) no cambia entre muestras, así que no se guarda
    en cada fila: se refresca sobre el equipo.
    """
    try:
        client = client_for(device)
    except DecryptionError as exc:
        return DeviceSample(device_id=device.id, reachable=False, error=str(exc)), None

    try:
        snapshot = await MikrotikService(client).snapshot(device.wan_interface)
    except MikrotikError as exc:
        return DeviceSample(device_id=device.id, reachable=False, error=str(exc)), None
    except Exception as exc:  # pragma: no cover - defensivo: el loop no debe morir
        logger.exception("Fallo inesperado muestreando %s", device.name)
        return DeviceSample(device_id=device.id, reachable=False, error=repr(exc)), None
    finally:
        await client.close()

    sample = DeviceSample(
        device_id=device.id,
        reachable=True,
        cpu_load_percent=snapshot["cpu_load_percent"],
        memory_used_percent=snapshot["memory_used_percent"],
        disk_used_percent=snapshot["disk_used_percent"],
        temperature_c=snapshot["temperature_c"],
        clients_online=snapshot["clients_online"],
        uptime_seconds=snapshot["uptime_seconds"],
        wan_interface=snapshot["wan_interface"],
        wan_rx_mbps=snapshot["wan_rx_mbps"],
        wan_tx_mbps=snapshot["wan_tx_mbps"],
    )
    meta = {
        "board_name": snapshot["model"],
        "routeros_version": snapshot["version"],
    }
    return sample, meta


async def poll_once(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Muestrea todos los equipos activos. Devuelve cuántas muestras guardó."""
    async with session_factory() as session:
        result = await session.execute(
            select(NetworkDevice).where(NetworkDevice.is_active.is_(True))
        )
        devices: Sequence[NetworkDevice] = result.scalars().all()

    if not devices:
        return 0

    # Un equipo lento no debe retrasar al resto: todos en paralelo.
    results = await asyncio.gather(*(sample_device(device) for device in devices))

    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        for device, (sample, meta) in zip(devices, results):
            session.add(sample)
            stored = await session.get(NetworkDevice, device.id)
            if stored is None:
                continue
            if sample.reachable:
                stored.last_seen_at = now
                stored.last_error = None
                if meta:
                    stored.board_name = meta["board_name"] or stored.board_name
                    stored.routeros_version = meta["routeros_version"] or stored.routeros_version
            else:
                stored.last_error = sample.error
        await session.commit()

    return len(results)


async def prune_old_samples(session_factory: async_sessionmaker[AsyncSession]) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.MONITOR_RETENTION_HOURS)
    async with session_factory() as session:
        result = await session.execute(delete(DeviceSample).where(DeviceSample.taken_at < cutoff))
        await session.commit()
        return result.rowcount or 0


async def monitor_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Bucle de fondo. Se cancela solo al apagar la aplicación."""
    interval = max(5, settings.MONITOR_INTERVAL_SECONDS)
    logger.info("Monitoreo activo: muestreo cada %ss", interval)
    ticks = 0
    while True:
        try:
            count = await poll_once(session_factory)
            ticks += 1
            # La poda es barata pero no hace falta en cada vuelta.
            if ticks % 60 == 0:
                removed = await prune_old_samples(session_factory)
                if removed:
                    logger.info("Monitoreo: %s muestras antiguas eliminadas", removed)
            logger.debug("Monitoreo: %s equipos muestreados", count)
        except asyncio.CancelledError:
            logger.info("Monitoreo detenido")
            raise
        except Exception:  # pragma: no cover - el bucle nunca debe morir
            logger.exception("Fallo en el ciclo de monitoreo; se reintenta")
        await asyncio.sleep(interval)


# ----------------------------------------------------------------- consultas


async def latest_samples(session: AsyncSession, device_ids: Sequence[int]) -> Dict[int, DeviceSample]:
    """Última muestra de cada equipo, sin volver a consultar los routers."""
    latest: Dict[int, DeviceSample] = {}
    for device_id in device_ids:
        result = await session.execute(
            select(DeviceSample)
            .where(DeviceSample.device_id == device_id)
            .order_by(DeviceSample.taken_at.desc())
            .limit(1)
        )
        sample = result.scalar_one_or_none()
        if sample is not None:
            latest[device_id] = sample
    return latest


async def history(
    session: AsyncSession, device_id: int, minutes: int = 60, limit: int = 720
) -> List[DeviceSample]:
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    result = await session.execute(
        select(DeviceSample)
        .where(DeviceSample.device_id == device_id, DeviceSample.taken_at >= since)
        .order_by(DeviceSample.taken_at.asc())
        .limit(limit)
    )
    return list(result.scalars().all())


def _is_online(sample: Optional[DeviceSample], stale_after_seconds: int) -> bool:
    if sample is None or not sample.reachable:
        return False
    taken = sample.taken_at
    if taken.tzinfo is None:
        taken = taken.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - taken).total_seconds() <= stale_after_seconds


async def fleet_summary(session: AsyncSession) -> Dict[str, Any]:
    """Totales de la flota y la tabla de equipos, desde el histórico guardado."""
    result = await session.execute(select(NetworkDevice).order_by(NetworkDevice.name))
    devices = list(result.scalars().all())
    samples = await latest_samples(session, [d.id for d in devices])

    # Una muestra vieja no prueba que el equipo siga arriba: si el monitoreo se
    # detuvo, preferimos decir "sin datos" antes que pintar todo en línea.
    stale_after = max(60, settings.MONITOR_INTERVAL_SECONDS * 3)

    rows: List[Dict[str, Any]] = []
    for device in devices:
        sample = samples.get(device.id)
        online = _is_online(sample, stale_after)
        rows.append(
            {
                "id": device.id,
                "name": device.name,
                "host": device.host,
                "site": device.site,
                "location": device.location,
                "model": device.board_name,
                "routeros_version": device.routeros_version,
                "is_active": device.is_active,
                "online": online,
                "has_data": sample is not None,
                "last_seen_at": device.last_seen_at,
                "last_error": device.last_error if not online else None,
                "clients_online": sample.clients_online if online else 0,
                "cpu_load_percent": sample.cpu_load_percent if online else None,
                "memory_used_percent": sample.memory_used_percent if online else None,
                "disk_used_percent": sample.disk_used_percent if online else None,
                "temperature_c": sample.temperature_c if online else None,
                "uptime_seconds": sample.uptime_seconds if online else None,
                "wan_interface": sample.wan_interface if online else None,
                "wan_rx_mbps": sample.wan_rx_mbps if online else None,
                "wan_tx_mbps": sample.wan_tx_mbps if online else None,
            }
        )

    online_rows = [r for r in rows if r["online"]]
    return {
        "devices_total": len(rows),
        "devices_online": len(online_rows),
        "devices_offline": len(rows) - len(online_rows),
        # Suma de sesiones PPPoE activas en toda la red.
        "clients_total": sum(r["clients_online"] or 0 for r in online_rows),
        "wan_rx_mbps": round(sum(r["wan_rx_mbps"] or 0 for r in online_rows), 2),
        "wan_tx_mbps": round(sum(r["wan_tx_mbps"] or 0 for r in online_rows), 2),
        "monitor_interval_seconds": settings.MONITOR_INTERVAL_SECONDS,
        "devices": rows,
    }

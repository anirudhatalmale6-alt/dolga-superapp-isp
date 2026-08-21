"""Pruebas del servicio de monitoreo.

Lo que importa verificar aquí:
  * que se guarda una muestra por equipo, incluida la de los que NO respondieron,
  * que un equipo caído no impide muestrear a los demás,
  * que el resumen de flota no declara "en línea" con datos viejos,
  * que la poda respeta la ventana de retención.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.crypto import encrypt_secret
from app.db.base import Base
from app.models.monitoring import DeviceSample
from app.models.network import MikrotikApiMode, NetworkDevice
from app.services import monitoring
from tests.fake_routeros import FakeRouterOSBinaryServer, FakeRouterOSState

USER = "api-dolga"
PASSWORD = "S3cret!"


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    state = FakeRouterOSState(USER, PASSWORD)
    server = await FakeRouterOSBinaryServer(state).start()

    async with Session() as session:
        session.add_all(
            [
                NetworkDevice(
                    name="Cerro La Torre",
                    host="127.0.0.1",
                    api_mode=MikrotikApiMode.API,
                    api_port=server.port,
                    username=USER,
                    password_encrypted=encrypt_secret(PASSWORD),
                    site="Torre Principal",
                ),
                NetworkDevice(
                    name="Los Limones",
                    host="127.0.0.1",
                    api_mode=MikrotikApiMode.API,
                    api_port=9,  # puerto cerrado: equipo caído
                    username=USER,
                    password_encrypted=encrypt_secret(PASSWORD),
                    site="Los Limones",
                ),
            ]
        )
        await session.commit()

    yield Session, state, server

    await server.stop()
    await engine.dispose()


@pytest.mark.asyncio
async def test_poll_stores_a_sample_per_device_including_the_dead_one(env):
    Session, _, _ = env
    stored = await monitoring.poll_once(Session)
    assert stored == 2

    async with Session() as session:
        rows = (await session.execute(select(DeviceSample))).scalars().all()

    assert len(rows) == 2
    ok = next(r for r in rows if r.reachable)
    dead = next(r for r in rows if not r.reachable)

    assert ok.cpu_load_percent == 17
    assert ok.clients_online == 2
    assert ok.temperature_c == 59.0
    assert ok.wan_interface == "ether1-wan"
    assert ok.wan_rx_mbps == 38.4

    # Del equipo caído queda constancia y el motivo, no un hueco silencioso.
    assert dead.cpu_load_percent is None
    assert "conectar" in (dead.error or "")


@pytest.mark.asyncio
async def test_poll_updates_device_last_seen_and_error(env):
    Session, _, _ = env
    await monitoring.poll_once(Session)

    async with Session() as session:
        devices = (await session.execute(select(NetworkDevice).order_by(NetworkDevice.name))).scalars().all()

    cerro = next(d for d in devices if d.name == "Cerro La Torre")
    limones = next(d for d in devices if d.name == "Los Limones")
    assert cerro.last_seen_at is not None
    assert cerro.last_error is None
    assert limones.last_error is not None


@pytest.mark.asyncio
async def test_poll_records_model_and_version_on_the_device(env):
    """La ficha del equipo se refresca sola: no hay que 'probar conexión' a mano."""
    Session, _, _ = env
    await monitoring.poll_once(Session)

    async with Session() as session:
        cerro = (
            await session.execute(select(NetworkDevice).where(NetworkDevice.name == "Cerro La Torre"))
        ).scalar_one()

    assert cerro.board_name == "RB5009UG+S+"
    assert cerro.routeros_version.startswith("7.14.3")


@pytest.mark.asyncio
async def test_fleet_summary_totals(env):
    Session, _, _ = env
    await monitoring.poll_once(Session)

    async with Session() as session:
        summary = await monitoring.fleet_summary(session)

    assert summary["devices_total"] == 2
    assert summary["devices_online"] == 1
    assert summary["devices_offline"] == 1
    # Solo cuentan los clientes de los equipos que sí respondieron.
    assert summary["clients_total"] == 2
    assert summary["wan_rx_mbps"] == 38.4

    by_name = {d["name"]: d for d in summary["devices"]}
    assert by_name["Cerro La Torre"]["online"] is True
    assert by_name["Cerro La Torre"]["temperature_c"] == 59.0
    assert by_name["Los Limones"]["online"] is False
    assert by_name["Los Limones"]["clients_online"] == 0
    assert by_name["Los Limones"]["last_error"]


@pytest.mark.asyncio
async def test_fleet_summary_without_any_sample_is_not_online(env):
    """Sin monitoreo corriendo no hay que pintar la flota en verde."""
    Session, _, _ = env
    async with Session() as session:
        summary = await monitoring.fleet_summary(session)

    assert summary["devices_total"] == 2
    assert summary["devices_online"] == 0
    assert all(d["has_data"] is False for d in summary["devices"])


@pytest.mark.asyncio
async def test_stale_sample_does_not_count_as_online(env):
    """Una muestra vieja no prueba que el equipo siga arriba."""
    Session, _, _ = env
    await monitoring.poll_once(Session)

    stale = datetime.now(timezone.utc) - timedelta(
        seconds=settings.MONITOR_INTERVAL_SECONDS * 10 + 600
    )
    async with Session() as session:
        rows = (await session.execute(select(DeviceSample))).scalars().all()
        for row in rows:
            row.taken_at = stale
        await session.commit()
        summary = await monitoring.fleet_summary(session)

    assert summary["devices_online"] == 0
    assert summary["clients_total"] == 0


@pytest.mark.asyncio
async def test_history_returns_samples_in_order(env):
    Session, _, _ = env
    await monitoring.poll_once(Session)
    await monitoring.poll_once(Session)

    async with Session() as session:
        device_id = (await session.execute(select(NetworkDevice.id).order_by(NetworkDevice.name))).scalars().first()
        points = await monitoring.history(session, device_id, minutes=60)

    assert len(points) == 2
    assert points[0].taken_at <= points[1].taken_at


@pytest.mark.asyncio
async def test_prune_removes_only_samples_past_the_retention_window(env):
    Session, _, _ = env
    await monitoring.poll_once(Session)

    old = datetime.now(timezone.utc) - timedelta(hours=settings.MONITOR_RETENTION_HOURS + 5)
    async with Session() as session:
        rows = (await session.execute(select(DeviceSample))).scalars().all()
        rows[0].taken_at = old  # solo una queda fuera de la ventana
        await session.commit()

    removed = await monitoring.prune_old_samples(Session)
    assert removed == 1

    async with Session() as session:
        remaining = (await session.execute(select(func.count()).select_from(DeviceSample))).scalar_one()
    assert remaining == 1


@pytest.mark.asyncio
async def test_poll_uses_the_configured_wan_interface(env):
    """Si el operador fija la WAN a mano, se respeta en vez de deducirla."""
    Session, _, _ = env
    async with Session() as session:
        device = (await session.execute(select(NetworkDevice).order_by(NetworkDevice.name))).scalars().first()
        device.wan_interface = "ether2-lan"
        await session.commit()

    await monitoring.poll_once(Session)

    async with Session() as session:
        sample = (
            await session.execute(
                select(DeviceSample).where(DeviceSample.reachable.is_(True))
            )
        ).scalars().first()

    assert sample.wan_interface == "ether2-lan"


@pytest.mark.asyncio
async def test_inactive_devices_are_not_polled(env):
    Session, _, _ = env
    async with Session() as session:
        device = (
            await session.execute(select(NetworkDevice).where(NetworkDevice.name == "Los Limones"))
        ).scalar_one()
        device.is_active = False
        await session.commit()

    stored = await monitoring.poll_once(Session)
    assert stored == 1

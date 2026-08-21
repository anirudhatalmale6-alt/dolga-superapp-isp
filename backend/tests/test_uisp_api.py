"""Pruebas de la API HTTP de Ubiquiti/UISP: React -> FastAPI -> UISP.

Además del camino feliz se verifica que la App Key nunca sale hacia el
frontend ni se guarda en claro, y que solo el administrador puede registrar
o borrar controladores.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.uisp import UispController  # noqa: F401
from app.models.user import User, UserRole
from tests.fake_uisp import FakeUispServer, FakeUispState

TOKEN = "app-key-de-prueba"


@pytest_asyncio.fixture
async def ctx():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session

    async with Session() as session:
        session.add_all(
            [
                User(
                    email="admin@dolga.net",
                    full_name="Administrador",
                    hashed_password=hash_password("Admin12345"),
                    role=UserRole.ADMIN,
                ),
                User(
                    email="tecnico@dolga.net",
                    full_name="Tecnico Campo",
                    hashed_password=hash_password("Tecnico12345"),
                    role=UserRole.TECNICO,
                ),
            ]
        )
        await session.commit()

    state = FakeUispState(TOKEN)
    server = FakeUispServer(state).start()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, state, server, Session

    app.dependency_overrides.clear()
    server.stop()
    await engine.dispose()


async def _login(client: AsyncClient, email: str, password: str) -> dict:
    response = await client.post(
        "/api/v1/auth/login-json", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _register(client: AsyncClient, headers: dict, base_url: str) -> int:
    response = await client.post(
        "/api/v1/uisp/controllers",
        headers=headers,
        json={"name": "UISP Principal", "base_url": base_url, "token": TOKEN, "verify_tls": False},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.asyncio
async def test_requires_authentication(ctx):
    client, _, _, _ = ctx
    assert (await client.get("/api/v1/uisp/controllers")).status_code == 401
    assert (await client.get("/api/v1/uisp/1/overview")).status_code == 401


@pytest.mark.asyncio
async def test_base_url_is_normalized_on_save(ctx):
    """El operador escribe el host pelado y se guarda la URL completa."""
    client, _, server, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")

    host = server.base_url.replace("http://", "")
    response = await client.post(
        "/api/v1/uisp/controllers",
        headers=headers,
        json={"name": "UISP", "base_url": f"http://{host}/", "token": TOKEN},
    )
    assert response.status_code == 201, response.text
    assert response.json()["base_url"] == f"http://{host}/nms/api/v2.1"


@pytest.mark.asyncio
async def test_token_never_returned_and_stored_encrypted(ctx):
    client, _, server, Session = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    controller_id = await _register(client, headers, server.base_url)

    listing = await client.get("/api/v1/uisp/controllers", headers=headers)
    assert TOKEN not in listing.text
    assert "token" not in listing.json()[0]

    async with Session() as session:
        stored = (
            await session.execute(text("SELECT token_encrypted FROM uisp_controllers"))
        ).scalar_one()
    assert TOKEN not in stored
    assert stored.startswith("gAAAAA")  # token Fernet

    # Y tampoco aparece al consultar el detalle o la topología.
    topology = await client.get(f"/api/v1/uisp/{controller_id}/topology", headers=headers)
    assert TOKEN not in topology.text


@pytest.mark.asyncio
async def test_test_connection_ok_and_failure(ctx):
    client, _, server, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    controller_id = await _register(client, headers, server.base_url)

    ok = await client.post(f"/api/v1/uisp/controllers/{controller_id}/test-connection", headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.json()["ok"] is True
    assert ok.json()["devices"] == 10

    detail = (await client.get("/api/v1/uisp/controllers", headers=headers)).json()[0]
    assert detail["last_seen_at"] is not None

    # Apuntamos a un puerto cerrado: falla con mensaje, no con una excepción.
    await client.patch(
        f"/api/v1/uisp/controllers/{controller_id}",
        headers=headers,
        json={"base_url": "http://127.0.0.1:9"},
    )
    bad = await client.post(f"/api/v1/uisp/controllers/{controller_id}/test-connection", headers=headers)
    assert bad.status_code == 200
    assert bad.json()["ok"] is False
    assert "conectar" in bad.json()["error"]


@pytest.mark.asyncio
async def test_overview_and_topology_endpoints(ctx):
    client, _, server, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    controller_id = await _register(client, headers, server.base_url)
    base = f"/api/v1/uisp/{controller_id}"

    overview = (await client.get(f"{base}/overview", headers=headers)).json()
    assert overview["sectors_total"] == 3
    assert overview["stations_online"] == 4
    assert overview["weak_signal_count"] == 1

    topology = (await client.get(f"{base}/topology", headers=headers)).json()
    assert len(topology["sectors"]) == 3

    stations = (await client.get(f"{base}/sectors/ap-1/stations", headers=headers)).json()
    assert stations[0]["signal_dbm"] == -84

    sites = (await client.get(f"{base}/sites", headers=headers)).json()
    assert len(sites) == 2


@pytest.mark.asyncio
async def test_devices_role_filter(ctx):
    client, _, server, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    controller_id = await _register(client, headers, server.base_url)

    aps = (
        await client.get(f"/api/v1/uisp/{controller_id}/devices?role=ap", headers=headers)
    ).json()
    assert len(aps) == 3
    assert all(d["role"] == "ap" for d in aps)


@pytest.mark.asyncio
async def test_device_detail_and_statistics(ctx):
    client, _, server, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    controller_id = await _register(client, headers, server.base_url)
    base = f"/api/v1/uisp/{controller_id}"

    device = (await client.get(f"{base}/devices/st-11", headers=headers)).json()
    assert device["signal_quality"] == "excelente"

    stats = (await client.get(f"{base}/devices/ap-1/statistics?interval=hour", headers=headers)).json()
    assert stats["available"] is True
    assert len(stats["series"]["cpu"]) == 3


@pytest.mark.asyncio
async def test_unreachable_uisp_returns_504(ctx):
    client, _, server, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    controller_id = await _register(client, headers, server.base_url)
    await client.patch(
        f"/api/v1/uisp/controllers/{controller_id}",
        headers=headers,
        json={"base_url": "http://127.0.0.1:9"},
    )
    response = await client.get(f"/api/v1/uisp/{controller_id}/overview", headers=headers)
    assert response.status_code == 504


@pytest.mark.asyncio
async def test_bad_token_returns_502(ctx):
    client, _, server, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    controller_id = await _register(client, headers, server.base_url)
    await client.patch(
        f"/api/v1/uisp/controllers/{controller_id}", headers=headers, json={"token": "equivocado"}
    )
    response = await client.get(f"/api/v1/uisp/{controller_id}/overview", headers=headers)
    assert response.status_code == 502
    assert "App Key" in response.json()["detail"]


@pytest.mark.asyncio
async def test_technician_can_read_but_not_manage_controllers(ctx):
    client, _, server, _ = ctx
    admin = await _login(client, "admin@dolga.net", "Admin12345")
    controller_id = await _register(client, admin, server.base_url)
    tech = await _login(client, "tecnico@dolga.net", "Tecnico12345")

    assert (await client.get(f"/api/v1/uisp/{controller_id}/overview", headers=tech)).status_code == 200
    assert (
        await client.post(
            "/api/v1/uisp/controllers",
            headers=tech,
            json={"name": "X", "base_url": "uisp.x.net", "token": "t"},
        )
    ).status_code == 403
    assert (
        await client.delete(f"/api/v1/uisp/controllers/{controller_id}", headers=tech)
    ).status_code == 403


@pytest.mark.asyncio
async def test_invalid_base_url_is_rejected(ctx):
    client, _, _, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    response = await client.post(
        "/api/v1/uisp/controllers", headers=headers, json={"name": "X", "base_url": "   ", "token": "t"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_device_actions_and_role_limits(ctx):
    client, state, server, _ = ctx
    admin = await _login(client, "admin@dolga.net", "Admin12345")
    controller_id = await _register(client, admin, server.base_url)
    base = f"/api/v1/uisp/{controller_id}/devices"
    tech = await _login(client, "tecnico@dolga.net", "Tecnico12345")

    # Localizar no interrumpe el servicio: lo puede hacer un técnico.
    locate = await client.post(f"{base}/ap-1/locate", headers=tech)
    assert locate.status_code == 200, locate.text
    assert ("ap-1", "locate") in state.actions

    # Reiniciar corta el enlace: el técnico no puede.
    assert (await client.post(f"{base}/ap-1/reboot", headers=tech)).status_code == 403
    # Actualizar firmware, solo el administrador.
    assert (await client.post(f"{base}/ap-1/upgrade", headers=tech)).status_code == 403
    assert [a for a in state.actions if a[1] in ("reboot", "upgrade")] == []

    reboot = await client.post(f"{base}/ap-1/reboot", headers=admin)
    assert reboot.status_code == 200
    assert ("ap-1", "reboot") in state.actions


@pytest.mark.asyncio
async def test_action_missing_in_this_uisp_version_returns_422(ctx):
    client, state, server, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    controller_id = await _register(client, headers, server.base_url)
    state.supported_actions.discard("upgrade")

    response = await client.post(
        f"/api/v1/uisp/{controller_id}/devices/ap-1/upgrade", headers=headers
    )
    assert response.status_code == 422
    assert "no ofrece la acción" in response.json()["detail"]


@pytest.mark.asyncio
async def test_controller_not_found(ctx):
    client, _, _, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    assert (await client.get("/api/v1/uisp/999/overview", headers=headers)).status_code == 404

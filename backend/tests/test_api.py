"""Pruebas de la API HTTP completas: React -> FastAPI -> RouterOS.

El router de este test es el RouterOS simulado (API binaria, socket real). La
base de datos es SQLite en memoria para no depender de un PostgreSQL en el
entorno de pruebas; el esquema es el mismo que se crea en PostgreSQL.

Lo que se verifica aquí, además del camino feliz:
  * el frontend nunca recibe la clave del equipo,
  * la clave se guarda cifrada en la tabla, no en claro,
  * los roles limitan quién puede suspender y quién puede editar equipos.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.network import NetworkDevice  # noqa: F401
from app.models.user import User, UserRole
from tests.fake_routeros import FakeRouterOSBinaryServer, FakeRouterOSState

ROUTER_USER = "api-dolga"
ROUTER_PASSWORD = "S3cret!"


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

    state = FakeRouterOSState(ROUTER_USER, ROUTER_PASSWORD)
    server = await FakeRouterOSBinaryServer(state).start()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, state, server, Session

    app.dependency_overrides.clear()
    await server.stop()
    await engine.dispose()


async def _login(client: AsyncClient, email: str, password: str) -> dict:
    response = await client.post(
        "/api/v1/auth/login-json", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _register_router(client: AsyncClient, headers: dict, port: int) -> int:
    response = await client.post(
        "/api/v1/devices",
        headers=headers,
        json={
            "name": "Borde principal",
            "host": "127.0.0.1",
            "api_mode": "api",
            "api_port": port,
            "api_use_tls": False,
            "username": ROUTER_USER,
            "password": ROUTER_PASSWORD,
            "site": "Estación Centro",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.asyncio
async def test_health():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_endpoints_require_authentication(ctx):
    client, _, _, _ = ctx
    assert (await client.get("/api/v1/devices")).status_code == 401
    assert (await client.get("/api/v1/mikrotik/1/resource")).status_code == 401


@pytest.mark.asyncio
async def test_login_with_wrong_password(ctx):
    client, _, _, _ = ctx
    response = await client.post(
        "/api/v1/auth/login-json", json={"email": "admin@dolga.net", "password": "incorrecta"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_device_password_is_never_returned_and_stored_encrypted(ctx):
    client, _, server, Session = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    device_id = await _register_router(client, headers, server.port)

    listing = await client.get("/api/v1/devices", headers=headers)
    assert listing.status_code == 200
    body = listing.text
    assert ROUTER_PASSWORD not in body
    assert "password" not in listing.json()[0]

    detail = await client.get(f"/api/v1/devices/{device_id}", headers=headers)
    assert ROUTER_PASSWORD not in detail.text

    # Y en la base de datos tampoco está en claro.
    async with Session() as session:
        stored = (await session.execute(text("SELECT password_encrypted FROM network_devices"))).scalar_one()
    assert ROUTER_PASSWORD not in stored
    assert stored.startswith("gAAAAA")  # token Fernet


@pytest.mark.asyncio
async def test_test_connection_updates_device_status(ctx):
    client, _, server, Session = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    device_id = await _register_router(client, headers, server.port)

    response = await client.post(f"/api/v1/devices/{device_id}/test-connection", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    assert data["identity"] == "DOLGA-BORDE-01"
    assert data["transport"] == "api"

    detail = (await client.get(f"/api/v1/devices/{device_id}", headers=headers)).json()
    assert detail["routeros_version"].startswith("7.14.3")
    assert detail["board_name"] == "RB5009UG+S+"
    assert detail["last_seen_at"] is not None


@pytest.mark.asyncio
async def test_test_connection_reports_failure_without_crashing(ctx):
    client, _, server, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    device_id = await _register_router(client, headers, server.port)

    # Apuntamos el equipo a un puerto cerrado.
    await client.patch(f"/api/v1/devices/{device_id}", headers=headers, json={"api_port": 9})
    response = await client.post(f"/api/v1/devices/{device_id}/test-connection", headers=headers)
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "conectar" in response.json()["error"]


@pytest.mark.asyncio
async def test_mikrotik_read_endpoints(ctx):
    client, _, server, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    device_id = await _register_router(client, headers, server.port)
    base = f"/api/v1/mikrotik/{device_id}"

    resource = (await client.get(f"{base}/resource", headers=headers)).json()
    assert resource["cpu_load_percent"] == 17
    assert resource["memory_used_percent"] == 62.5

    interfaces = (await client.get(f"{base}/interfaces", headers=headers)).json()
    assert len(interfaces) == 3

    traffic = (await client.get(f"{base}/interfaces/ether1-wan/traffic", headers=headers)).json()
    assert traffic["rx_mbps"] == 38.4

    overview = (await client.get(f"{base}/pppoe/overview", headers=headers)).json()
    assert overview["online"] == 2
    assert overview["suspended"] == 1

    dashboard = (await client.get(f"{base}/dashboard", headers=headers)).json()
    assert dashboard["interfaces"]["running"] == 2


@pytest.mark.asyncio
async def test_pppoe_overview_search_filter(ctx):
    client, _, server, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    device_id = await _register_router(client, headers, server.port)

    response = await client.get(
        f"/api/v1/mikrotik/{device_id}/pppoe/overview",
        headers=headers,
        params={"search": "maria"},
    )
    clients = response.json()["clients"]
    assert [c["username"] for c in clients] == ["cliente001"]


@pytest.mark.asyncio
async def test_suspend_and_restore_through_api(ctx):
    client, state, server, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    device_id = await _register_router(client, headers, server.port)
    base = f"/api/v1/mikrotik/{device_id}"

    response = await client.post(
        f"{base}/pppoe/suspend",
        headers=headers,
        json={"username": "cliente001", "method": "disable_secret", "comment": "Factura 001 vencida"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["session_closed"] is True

    secret = next(s for s in state.tables["ppp/secret"] if s["name"] == "cliente001")
    assert secret["disabled"] == "true"
    assert secret["comment"] == "Factura 001 vencida"

    response = await client.post(
        f"{base}/pppoe/restore",
        headers=headers,
        json={"username": "cliente001", "method": "disable_secret", "comment": "Pago recibido"},
    )
    assert response.status_code == 200
    secret = next(s for s in state.tables["ppp/secret"] if s["name"] == "cliente001")
    assert secret["disabled"] == "false"


@pytest.mark.asyncio
async def test_suspend_unknown_user_returns_422(ctx):
    client, _, server, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    device_id = await _register_router(client, headers, server.port)

    response = await client.post(
        f"/api/v1/mikrotik/{device_id}/pppoe/suspend",
        headers=headers,
        json={"username": "fantasma"},
    )
    assert response.status_code == 422
    assert "fantasma" in response.json()["detail"]


@pytest.mark.asyncio
async def test_unreachable_router_returns_504(ctx):
    client, _, server, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    device_id = await _register_router(client, headers, server.port)
    await client.patch(f"/api/v1/devices/{device_id}", headers=headers, json={"api_port": 9})

    response = await client.get(f"/api/v1/mikrotik/{device_id}/resource", headers=headers)
    assert response.status_code == 504


@pytest.mark.asyncio
async def test_technician_cannot_suspend_or_manage_devices(ctx):
    client, _, server, _ = ctx
    admin = await _login(client, "admin@dolga.net", "Admin12345")
    device_id = await _register_router(client, admin, server.port)

    tech = await _login(client, "tecnico@dolga.net", "Tecnico12345")

    # Puede leer el estado de la red...
    assert (await client.get(f"/api/v1/mikrotik/{device_id}/resource", headers=tech)).status_code == 200
    # ...pero no cortar el servicio de un cliente.
    suspend = await client.post(
        f"/api/v1/mikrotik/{device_id}/pppoe/suspend", headers=tech, json={"username": "cliente001"}
    )
    assert suspend.status_code == 403
    # ...ni registrar o borrar equipos.
    assert (await client.delete(f"/api/v1/devices/{device_id}", headers=tech)).status_code == 403
    assert (
        await client.post(
            "/api/v1/auth/users",
            headers=tech,
            json={"email": "x@y.z", "full_name": "X", "password": "12345678"},
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_device_not_found(ctx):
    client, _, _, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    assert (await client.get("/api/v1/mikrotik/999/resource", headers=headers)).status_code == 404


# ------------------------------------------------- salud, WAN y mantenimiento


@pytest.mark.asyncio
async def test_health_and_wan_endpoints(ctx):
    client, _, server, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    device_id = await _register_router(client, headers, server.port)
    base = f"/api/v1/mikrotik/{device_id}"

    health = (await client.get(f"{base}/health", headers=headers)).json()
    assert health["available"] is True
    assert health["temperature_c"] == 59.0

    wan = (await client.get(f"{base}/wan", headers=headers)).json()
    assert wan[0]["interface"] == "ether1-wan"
    assert wan[0]["gateway"] == "10.10.0.254"

    snapshot = (await client.get(f"{base}/snapshot", headers=headers)).json()
    assert snapshot["clients_online"] == 2


@pytest.mark.asyncio
async def test_updates_and_reboot(ctx):
    client, state, server, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    device_id = await _register_router(client, headers, server.port)
    base = f"/api/v1/mikrotik/{device_id}"

    updates = (await client.get(f"{base}/updates", headers=headers)).json()
    assert updates["update_available"] is True

    response = await client.post(f"{base}/reboot", headers=headers)
    assert response.status_code == 200
    assert state.rebooted == 1


@pytest.mark.asyncio
async def test_shutdown_requires_the_exact_device_name(ctx):
    """La confirmación se valida en el servidor: sin el nombre no se apaga nada."""
    client, state, server, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    device_id = await _register_router(client, headers, server.port)
    base = f"/api/v1/mikrotik/{device_id}"

    wrong = await client.post(f"{base}/shutdown", headers=headers, json={"confirm_name": "cualquier cosa"})
    assert wrong.status_code == 400
    assert state.shutdown_count == 0

    right = await client.post(f"{base}/shutdown", headers=headers, json={"confirm_name": "Borde principal"})
    assert right.status_code == 200, right.text
    assert state.shutdown_count == 1
    assert "no volverá solo" in right.json()["message"].lower()


@pytest.mark.asyncio
async def test_only_admin_can_shutdown(ctx):
    client, state, server, _ = ctx
    admin = await _login(client, "admin@dolga.net", "Admin12345")
    device_id = await _register_router(client, admin, server.port)
    tech = await _login(client, "tecnico@dolga.net", "Tecnico12345")

    response = await client.post(
        f"/api/v1/mikrotik/{device_id}/shutdown", headers=tech, json={"confirm_name": "Borde principal"}
    )
    assert response.status_code == 403
    assert state.shutdown_count == 0


# ------------------------------------------------------------------ monitoreo


@pytest.mark.asyncio
async def test_fleet_endpoint(ctx):
    client, _, server, Session = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    await _register_router(client, headers, server.port)

    # Sin muestras todavía: nada debe declararse en línea.
    empty = (await client.get("/api/v1/monitoring/fleet", headers=headers)).json()
    assert empty["devices_total"] == 1
    assert empty["devices_online"] == 0

    from app.services import monitoring

    await monitoring.poll_once(Session)

    fleet = (await client.get("/api/v1/monitoring/fleet", headers=headers)).json()
    assert fleet["devices_online"] == 1
    assert fleet["clients_total"] == 2
    assert fleet["devices"][0]["cpu_load_percent"] == 17


@pytest.mark.asyncio
async def test_history_endpoint(ctx):
    client, _, server, Session = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    device_id = await _register_router(client, headers, server.port)

    from app.services import monitoring

    await monitoring.poll_once(Session)
    await monitoring.poll_once(Session)

    history = (
        await client.get(f"/api/v1/monitoring/devices/{device_id}/history?minutes=30", headers=headers)
    ).json()
    assert len(history["points"]) == 2
    assert history["points"][0]["rx_mbps"] == 38.4
    assert history["points"][0]["reachable"] is True


@pytest.mark.asyncio
async def test_monitoring_requires_authentication(ctx):
    client, _, _, _ = ctx
    assert (await client.get("/api/v1/monitoring/fleet")).status_code == 401

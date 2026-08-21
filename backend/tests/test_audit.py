"""Pruebas de la bitácora de operaciones.

Lo que se verifica aquí no es sólo que se escriba una fila. Es que la fila
sirva para lo que se hizo: distinguir un dato que salió del equipo de uno
inventado. Por eso las pruebas miran el contenido de `upstream` y comprueban
que ahí está la URL o la sentencia real, con el puerto del servidor simulado
que atendió la llamada.

También se comprueba lo contrario: que ni la App Key ni la clave del router
terminen escritas en la base de datos.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.audit import OperationLog
from app.models.network import NetworkDevice  # noqa: F401
from app.models.uisp import UispController  # noqa: F401
from app.models.user import User, UserRole
from app.services.audit import purge_old_logs
from tests.fake_routeros import FakeRouterOSBinaryServer, FakeRouterOSState
from tests.fake_uisp import FakeUispServer, FakeUispState

ROUTER_USER = "api-dolga"
ROUTER_PASSWORD = "S3cret!"
APP_KEY = "app-key-que-no-debe-aparecer"


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

    uisp_state = FakeUispState(APP_KEY)
    uisp_server = FakeUispServer(uisp_state).start()
    router_state = FakeRouterOSState(ROUTER_USER, ROUTER_PASSWORD)
    router_server = await FakeRouterOSBinaryServer(router_state).start()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, uisp_server, router_server, Session

    app.dependency_overrides.clear()
    uisp_server.stop()
    await router_server.stop()
    await engine.dispose()


async def _login(client: AsyncClient, email: str, password: str) -> dict:
    response = await client.post(
        "/api/v1/auth/login-json", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _register_uisp(client: AsyncClient, headers: dict, base_url: str) -> int:
    response = await client.post(
        "/api/v1/uisp/controllers",
        headers=headers,
        json={"name": "UISP Torre", "base_url": base_url, "token": APP_KEY, "verify_tls": False},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


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
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _logs(Session) -> list[OperationLog]:
    async with Session() as session:
        result = await session.execute(select(OperationLog).order_by(OperationLog.id))
        return list(result.scalars().all())


# ------------------------------------------------------------------- Ubiquiti


@pytest.mark.asyncio
async def test_uisp_read_records_the_real_url_it_called(ctx):
    """La bitácora tiene que guardar la URL que salió, no un texto genérico."""
    client, uisp_server, _, Session = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    controller_id = await _register_uisp(client, headers, uisp_server.base_url)

    assert (
        await client.get(f"/api/v1/uisp/{controller_id}/topology", headers=headers)
    ).status_code == 200

    filas = await _logs(Session)
    assert len(filas) == 1
    fila = filas[0]
    assert fila.operation == "Consultar sectores y clientes"
    assert fila.target_name == "UISP Torre"
    assert fila.user_email == "admin@dolga.net"
    assert fila.ok is True
    assert fila.is_action is False

    llamadas = json.loads(fila.upstream)
    assert fila.upstream_calls == len(llamadas) >= 1
    # El puerto del servidor simulado prueba que la llamada fue de verdad: si
    # el dato estuviera inventado, no habría a dónde apuntar.
    assert all(uisp_server.base_url in c["request"] for c in llamadas)
    assert all(c["http_status"] == 200 for c in llamadas)
    assert any(c["request"].endswith("/devices") for c in llamadas)


@pytest.mark.asyncio
async def test_action_is_marked_as_action_with_its_author(ctx):
    client, uisp_server, _, Session = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    controller_id = await _register_uisp(client, headers, uisp_server.base_url)

    response = await client.post(
        f"/api/v1/uisp/{controller_id}/devices/ap-1/reboot", headers=headers
    )
    assert response.status_code == 200, response.text

    fila = (await _logs(Session))[-1]
    assert fila.is_action is True
    assert fila.operation == "Reiniciar equipo Ubiquiti"
    assert fila.user_role == "admin"
    llamadas = json.loads(fila.upstream)
    assert any("POST" in c["request"] and "reboot" in c["request"] for c in llamadas)


@pytest.mark.asyncio
async def test_failed_call_is_recorded_with_its_error(ctx):
    """Un intento fallido es justo lo que después hay que poder mirar."""
    client, uisp_server, _, Session = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    controller_id = await _register_uisp(client, headers, uisp_server.base_url)
    # Puerto cerrado: no hay UISP del otro lado.
    await client.patch(
        f"/api/v1/uisp/controllers/{controller_id}",
        headers=headers,
        json={"base_url": "http://127.0.0.1:9"},
    )

    assert (
        await client.get(f"/api/v1/uisp/{controller_id}/overview", headers=headers)
    ).status_code == 504

    fila = (await _logs(Session))[-1]
    assert fila.ok is False
    assert fila.detail
    llamadas = json.loads(fila.upstream)
    assert llamadas and llamadas[0]["ok"] is False
    assert "conectar" in llamadas[0]["error"]


@pytest.mark.asyncio
async def test_app_key_never_reaches_the_log(ctx):
    client, uisp_server, _, Session = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    controller_id = await _register_uisp(client, headers, uisp_server.base_url)

    await client.get(f"/api/v1/uisp/{controller_id}/overview", headers=headers)
    await client.post(f"/api/v1/uisp/{controller_id}/devices/ap-1/locate", headers=headers)

    filas = await _logs(Session)
    assert filas
    volcado = json.dumps(
        [
            {"detail": f.detail, "upstream": f.upstream, "endpoint": f.endpoint}
            for f in filas
        ]
    )
    assert APP_KEY not in volcado
    assert "x-auth-token" not in volcado.lower()


# ------------------------------------------------------------------- MikroTik


@pytest.mark.asyncio
async def test_mikrotik_suspension_records_the_routeros_sentences(ctx):
    client, _, router_server, Session = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    device_id = await _register_router(client, headers, router_server.port)

    response = await client.post(
        f"/api/v1/mikrotik/{device_id}/pppoe/suspend",
        headers=headers,
        json={"username": "cliente001", "method": "disable_secret"},
    )
    assert response.status_code == 200, response.text

    fila = (await _logs(Session))[-1]
    assert fila.operation == "Suspender cliente"
    assert fila.is_action is True
    assert fila.target_name == "Borde principal"

    llamadas = json.loads(fila.upstream)
    assert all(c["target"] == "routeros-api" for c in llamadas)
    sentencias = " | ".join(c["request"] for c in llamadas)
    # La suspensión son varias sentencias: buscar el secreto, deshabilitarlo y
    # tumbar la sesión activa. Las tres tienen que quedar registradas.
    assert "/ppp/secret/print" in sentencias
    assert "/ppp/secret/set" in sentencias
    assert str(router_server.port) in sentencias


@pytest.mark.asyncio
async def test_router_password_never_reaches_the_log(ctx):
    client, _, router_server, Session = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    device_id = await _register_router(client, headers, router_server.port)

    await client.get(f"/api/v1/mikrotik/{device_id}/resource", headers=headers)

    filas = await _logs(Session)
    assert filas
    assert ROUTER_PASSWORD not in json.dumps([f.upstream for f in filas])


# -------------------------------------------------------------------- lectura


@pytest.mark.asyncio
async def test_operations_endpoint_lists_and_filters(ctx):
    client, uisp_server, _, _ = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    controller_id = await _register_uisp(client, headers, uisp_server.base_url)

    await client.get(f"/api/v1/uisp/{controller_id}/overview", headers=headers)
    await client.post(f"/api/v1/uisp/{controller_id}/devices/ap-1/locate", headers=headers)

    todas = (await client.get("/api/v1/audit/operations", headers=headers)).json()
    assert len(todas) == 2
    # Más reciente primero.
    assert todas[0]["operation"] == "Localizar equipo (parpadeo de LED)"
    assert todas[0]["upstream"][0]["request"].startswith("POST ")

    acciones = (
        await client.get("/api/v1/audit/operations?only_actions=true", headers=headers)
    ).json()
    assert [f["operation"] for f in acciones] == ["Localizar equipo (parpadeo de LED)"]

    resumen = (await client.get("/api/v1/audit/summary", headers=headers)).json()
    assert resumen["operations"] == 2
    assert resumen["actions"] == 1
    assert resumen["failed"] == 0
    assert resumen["upstream_calls"] >= 2


@pytest.mark.asyncio
async def test_technician_cannot_read_the_audit_log(ctx):
    client, _, _, _ = ctx
    tech = await _login(client, "tecnico@dolga.net", "Tecnico12345")
    assert (await client.get("/api/v1/audit/operations", headers=tech)).status_code == 403
    assert (await client.get("/api/v1/audit/summary", headers=tech)).status_code == 403
    assert (await client.get("/api/v1/audit/operations")).status_code == 401


@pytest.mark.asyncio
async def test_listing_saved_controllers_is_not_an_operation(ctx):
    """Listar lo que hay guardado no toca la red: no ensucia la bitácora."""
    client, uisp_server, _, Session = ctx
    headers = await _login(client, "admin@dolga.net", "Admin12345")
    await _register_uisp(client, headers, uisp_server.base_url)

    await client.get("/api/v1/uisp/controllers", headers=headers)
    await client.get("/api/v1/devices", headers=headers)

    assert await _logs(Session) == []


# ------------------------------------------------------------------- retención


@pytest.mark.asyncio
async def test_purge_keeps_actions_longer_than_reads(ctx):
    _, _, _, Session = ctx
    hace_treinta = datetime.now(timezone.utc) - timedelta(days=30)

    async with Session() as session:
        session.add_all(
            [
                OperationLog(
                    occurred_at=hace_treinta,
                    target_kind="uisp",
                    operation="Consulta vieja",
                    is_action=False,
                ),
                OperationLog(
                    occurred_at=hace_treinta,
                    target_kind="uisp",
                    operation="Reinicio viejo",
                    is_action=True,
                ),
                OperationLog(
                    occurred_at=datetime.now(timezone.utc),
                    target_kind="uisp",
                    operation="Consulta de hoy",
                    is_action=False,
                ),
            ]
        )
        await session.commit()

        eliminadas = await purge_old_logs(
            session, read_retention_days=14, action_retention_days=365
        )

    assert eliminadas == 1
    quedan = {fila.operation for fila in await _logs(Session)}
    assert quedan == {"Reinicio viejo", "Consulta de hoy"}

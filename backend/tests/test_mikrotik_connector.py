"""Pruebas del conector MikroTik contra un RouterOS simulado.

Cada prueba corre dos veces: una contra la API binaria (socket real, protocolo
de RouterOS 6/7) y otra contra REST v7 (uvicorn real con Basic Auth). Así
verificamos que la capa de negocio devuelve exactamente lo mismo sin importar
por dónde entró la consulta.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.services.mikrotik.client import API, REST, MikrotikClient
from app.services.mikrotik.exceptions import (
    MikrotikAuthError,
    MikrotikCommandError,
    MikrotikConnectionError,
)
from app.services.mikrotik.service import MikrotikService, human_bytes, parse_uptime
from tests.fake_routeros import (
    FakeRouterOSBinaryServer,
    FakeRouterOSRestServer,
    FakeRouterOSState,
)

USER = "api-dolga"
PASSWORD = "S3cret!"


@pytest_asyncio.fixture(params=[API, REST], ids=["api-binaria", "rest-v7"])
async def wired(request):
    """Devuelve (servicio, estado) con el transporte que toque."""
    state = FakeRouterOSState(USER, PASSWORD)
    transport = request.param

    if transport == API:
        server = await FakeRouterOSBinaryServer(state).start()
        client = MikrotikClient(
            "127.0.0.1", USER, PASSWORD, mode=API, api_port=server.port, timeout=5.0
        )
        try:
            yield MikrotikService(client), state
        finally:
            await client.close()
            await server.stop()
    else:
        server = FakeRouterOSRestServer(state).start()
        client = MikrotikClient(
            "127.0.0.1",
            USER,
            PASSWORD,
            mode=REST,
            rest_port=server.port,
            rest_use_tls=False,
            timeout=5.0,
        )
        try:
            yield MikrotikService(client), state
        finally:
            await client.close()
            server.stop()


# ------------------------------------------------------------------ helpers puros


def test_parse_uptime_formats():
    assert parse_uptime("1w2d03:04:05") == 604800 + 2 * 86400 + 3 * 3600 + 4 * 60 + 5
    assert parse_uptime("4h20m11s") == 4 * 3600 + 20 * 60 + 11
    assert parse_uptime("00:00:45") == 45
    assert parse_uptime(None) is None
    assert parse_uptime("no-es-uptime") is None


def test_human_bytes():
    assert human_bytes(512) == "512 B"
    assert human_bytes(1024) == "1.0 KiB"
    assert human_bytes(1073741824) == "1.0 GiB"
    assert human_bytes(None) is None


# ------------------------------------------------------------------ sistema


@pytest.mark.asyncio
async def test_system_resource(wired):
    svc, _ = wired
    resource = await svc.system_resource()

    assert resource["identity"] == "DOLGA-BORDE-01"
    assert resource["board_name"] == "RB5009UG+S+"
    assert resource["version"].startswith("7.14.3")
    assert resource["cpu_load_percent"] == 17
    assert resource["cpu_count"] == 4
    assert resource["memory_total_bytes"] == 1073741824
    assert resource["memory_used_bytes"] == 1073741824 - 402653184
    assert resource["memory_used_percent"] == 62.5
    assert resource["memory_total_human"] == "1.0 GiB"
    assert resource["uptime_seconds"] == 788645
    assert resource["disk_used_percent"] == 37.5


@pytest.mark.asyncio
async def test_routerboard(wired):
    svc, _ = wired
    board = await svc.routerboard()
    assert board["serial_number"] == "HEX1234ABCD"
    assert board["current_firmware"] == "7.14.3"


# ------------------------------------------------------------------ interfaces


@pytest.mark.asyncio
async def test_interfaces(wired):
    svc, _ = wired
    interfaces = await svc.interfaces()

    assert [i["name"] for i in interfaces] == ["ether1-wan", "ether2-lan", "sfp-sfpplus1"]
    wan = interfaces[0]
    assert wan["running"] is True
    assert wan["disabled"] is False
    assert wan["rx_bytes"] == 982374982374
    assert wan["rx_human"] == "914.9 GiB"
    assert wan["link_downs"] == 2
    assert wan["comment"] == "Enlace principal"

    sfp = interfaces[2]
    assert sfp["running"] is False
    assert sfp["disabled"] is True


@pytest.mark.asyncio
async def test_interface_traffic(wired):
    svc, _ = wired
    traffic = await svc.interface_traffic("ether1-wan")
    assert traffic["interface"] == "ether1-wan"
    assert traffic["rx_bits_per_second"] == 38400000
    assert traffic["rx_mbps"] == 38.4
    assert traffic["tx_mbps"] == 12.8


@pytest.mark.asyncio
async def test_interface_traffic_unknown_interface(wired):
    svc, _ = wired
    with pytest.raises(MikrotikCommandError):
        await svc.interface_traffic("no-existe")


@pytest.mark.asyncio
async def test_ip_addresses(wired):
    svc, _ = wired
    addresses = await svc.ip_addresses()
    assert {a["address"] for a in addresses} == {"192.168.88.1/24", "10.10.0.1/24"}


# ------------------------------------------------------------------ PPPoE


@pytest.mark.asyncio
async def test_pppoe_active_and_secrets(wired):
    svc, _ = wired
    active = await svc.pppoe_active()
    assert [s["username"] for s in active] == ["cliente001", "cliente002"]
    assert active[0]["address"] == "10.20.0.11"
    assert active[0]["uptime_seconds"] == 4 * 3600 + 20 * 60 + 11
    assert active[1]["uptime_seconds"] == 2 * 86400 + 3600 + 15 * 60 + 3

    secrets = await svc.pppoe_secrets()
    assert len(secrets) == 3
    assert secrets[2]["disabled"] is True


@pytest.mark.asyncio
async def test_pppoe_overview_cross_references_sessions(wired):
    svc, _ = wired
    overview = await svc.pppoe_overview()

    assert overview["total_secrets"] == 3
    assert overview["online"] == 2
    assert overview["offline"] == 0
    assert overview["suspended"] == 1

    by_user = {c["username"]: c for c in overview["clients"]}
    assert by_user["cliente001"]["status"] == "online"
    assert by_user["cliente001"]["session"]["caller_id"] == "48:A9:8A:AA:BB:01"
    assert by_user["cliente003"]["status"] == "suspendido"
    assert by_user["cliente003"]["session"] is None


@pytest.mark.asyncio
async def test_pppoe_overview_includes_radius_only_sessions(wired):
    svc, state = wired
    state.tables["ppp/active"].append(
        {
            ".id": "*22",
            "name": "radius-user",
            "service": "pppoe",
            "address": "10.30.0.9",
            "uptime": "10m",
            "caller-id": "AA:BB:CC:DD:EE:FF",
        }
    )
    overview = await svc.pppoe_overview()
    by_user = {c["username"]: c for c in overview["clients"]}
    assert by_user["radius-user"]["online"] is True
    assert by_user["radius-user"]["id"] is None
    assert overview["online"] == 3


@pytest.mark.asyncio
async def test_dashboard_summary(wired):
    svc, _ = wired
    data = await svc.dashboard()
    assert data["pppoe"] == {"total_secrets": 3, "online": 2, "offline": 0, "suspended": 1}
    assert data["interfaces"] == {"total": 3, "running": 2, "down": 0, "disabled": 1}
    assert data["resource"]["cpu_load_percent"] == 17


# --------------------------------------------------------- suspensión y reactivación


@pytest.mark.asyncio
async def test_suspend_by_disabling_secret_closes_session(wired):
    svc, state = wired
    result = await svc.suspend_customer("cliente001", method="disable_secret")

    assert result["suspended"] is True
    assert result["session_closed"] is True
    secret = next(s for s in state.tables["ppp/secret"] if s["name"] == "cliente001")
    assert secret["disabled"] == "true"
    assert all(s["name"] != "cliente001" for s in state.tables["ppp/active"])

    overview = await svc.pppoe_overview()
    by_user = {c["username"]: c for c in overview["clients"]}
    assert by_user["cliente001"]["status"] == "suspendido"


@pytest.mark.asyncio
async def test_restore_after_suspension(wired):
    svc, state = wired
    await svc.suspend_customer("cliente001", method="disable_secret")
    result = await svc.restore_customer("cliente001", method="disable_secret")

    assert result["suspended"] is False
    secret = next(s for s in state.tables["ppp/secret"] if s["name"] == "cliente001")
    assert secret["disabled"] == "false"


@pytest.mark.asyncio
async def test_suspend_by_changing_profile(wired):
    svc, state = wired
    await svc.suspend_customer("cliente002", method="change_profile", suspended_profile="CORTADO")
    secret = next(s for s in state.tables["ppp/secret"] if s["name"] == "cliente002")
    assert secret["profile"] == "CORTADO"
    assert secret["disabled"] == "false"  # sigue pudiendo autenticar, pero al portal de corte

    await svc.restore_customer("cliente002", method="change_profile", active_profile="PLAN-10M")
    secret = next(s for s in state.tables["ppp/secret"] if s["name"] == "cliente002")
    assert secret["profile"] == "PLAN-10M"


@pytest.mark.asyncio
async def test_restore_by_profile_requires_target_profile(wired):
    svc, _ = wired
    with pytest.raises(ValueError):
        await svc.restore_customer("cliente002", method="change_profile")


@pytest.mark.asyncio
async def test_suspend_by_address_list_uses_active_ip(wired):
    svc, state = wired
    result = await svc.suspend_customer(
        "cliente001", method="address_list", address_list="morosos"
    )

    assert "10.20.0.11" in " ".join(result["actions"])
    entries = state.tables["ip/firewall/address-list"]
    assert len(entries) == 1
    assert entries[0]["list"] == "morosos"
    assert entries[0]["address"] == "10.20.0.11"

    await svc.restore_customer("cliente001", method="address_list", address_list="morosos")
    assert state.tables["ip/firewall/address-list"] == []


@pytest.mark.asyncio
async def test_suspend_unknown_customer_raises(wired):
    svc, _ = wired
    with pytest.raises(MikrotikCommandError) as exc:
        await svc.suspend_customer("no-existe")
    assert "no-existe" in str(exc.value)


@pytest.mark.asyncio
async def test_kick_offline_customer_returns_false(wired):
    svc, _ = wired
    assert await svc.kick_session("cliente003") is False


@pytest.mark.asyncio
async def test_unknown_suspend_method(wired):
    svc, _ = wired
    with pytest.raises(ValueError):
        await svc.suspend_customer("cliente001", method="apagar-el-router")


# ------------------------------------------------------------------ errores


@pytest.mark.asyncio
async def test_bad_credentials_binary():
    state = FakeRouterOSState(USER, PASSWORD)
    server = await FakeRouterOSBinaryServer(state).start()
    client = MikrotikClient("127.0.0.1", USER, "clave-mala", mode=API, api_port=server.port, timeout=5.0)
    try:
        with pytest.raises(MikrotikAuthError):
            await client.print("system/resource")
    finally:
        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_bad_credentials_rest():
    state = FakeRouterOSState(USER, PASSWORD)
    server = FakeRouterOSRestServer(state).start()
    client = MikrotikClient(
        "127.0.0.1", USER, "clave-mala", mode=REST, rest_port=server.port, rest_use_tls=False, timeout=5.0
    )
    try:
        with pytest.raises(MikrotikAuthError):
            await client.print("system/resource")
    finally:
        await client.close()
        server.stop()


@pytest.mark.asyncio
async def test_legacy_md5_login_routeros_6():
    """RouterOS < 6.43 exige el reto MD5; el cliente debe resolverlo solo."""
    state = FakeRouterOSState(USER, PASSWORD, legacy_login=True)
    server = await FakeRouterOSBinaryServer(state).start()
    client = MikrotikClient("127.0.0.1", USER, PASSWORD, mode=API, api_port=server.port, timeout=5.0)
    try:
        rows = await client.print("system/identity")
        assert rows[0]["name"] == "DOLGA-BORDE-01"
    finally:
        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_legacy_login_with_wrong_password_fails():
    state = FakeRouterOSState(USER, PASSWORD, legacy_login=True)
    server = await FakeRouterOSBinaryServer(state).start()
    client = MikrotikClient("127.0.0.1", USER, "otra", mode=API, api_port=server.port, timeout=5.0)
    try:
        with pytest.raises(MikrotikAuthError):
            await client.print("system/identity")
    finally:
        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_unreachable_host_raises_connection_error():
    # Puerto cerrado en loopback: falla rápido y con un mensaje entendible.
    client = MikrotikClient("127.0.0.1", USER, PASSWORD, mode=API, api_port=9, timeout=2.0)
    try:
        with pytest.raises(MikrotikConnectionError):
            await client.print("system/resource")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_auto_mode_falls_back_to_binary_api():
    """Modo AUTO: si el REST no responde, entra por la API binaria."""
    state = FakeRouterOSState(USER, PASSWORD)
    server = await FakeRouterOSBinaryServer(state).start()
    client = MikrotikClient(
        "127.0.0.1",
        USER,
        PASSWORD,
        mode="auto",
        rest_port=9,  # cerrado a propósito
        rest_use_tls=False,
        api_port=server.port,
        timeout=2.0,
    )
    try:
        info = await client.ping_check()
        assert info["identity"] == "DOLGA-BORDE-01"
        assert client.transport == API
    finally:
        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_auto_mode_prefers_rest_when_available():
    state = FakeRouterOSState(USER, PASSWORD)
    rest = FakeRouterOSRestServer(state).start()
    client = MikrotikClient(
        "127.0.0.1",
        USER,
        PASSWORD,
        mode="auto",
        rest_port=rest.port,
        rest_use_tls=False,
        api_port=9,
        timeout=2.0,
    )
    try:
        info = await client.ping_check()
        assert client.transport == REST
        assert info["version"].startswith("7.14.3")
    finally:
        await client.close()
        rest.stop()

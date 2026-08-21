"""Pruebas del conector Ubiquiti/UISP contra un UISP simulado (HTTP real).

Lo que interesa verificar:
  * que la URL se normaliza sin importar cómo la escriba el operador,
  * que un equipo que no reporta un dato queda en None y no en cero,
  * que el conteo de clientes por sector sale de las estaciones asociadas,
  * que un token inválido se distingue de un servidor caído.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.services.uisp.client import UispClient, normalize_base_url
from app.services.uisp.exceptions import UispAuthError, UispConnectionError
from app.services.uisp.service import UispService, signal_quality
from tests.fake_uisp import FakeUispServer, FakeUispState

TOKEN = "app-key-de-prueba"


@pytest_asyncio.fixture
async def uisp():
    state = FakeUispState(TOKEN)
    server = FakeUispServer(state).start()
    client = UispClient(server.base_url, TOKEN, verify_tls=False, timeout=5.0)
    try:
        yield UispService(client), state, server
    finally:
        await client.close()
        server.stop()


# ---------------------------------------------------------------- normalización


@pytest.mark.parametrize(
    "entrada",
    [
        "uisp.midominio.net",
        "https://uisp.midominio.net",
        "https://uisp.midominio.net/",
        "https://uisp.midominio.net/nms/api/v2.1",
        "https://uisp.midominio.net/nms/api/v2.1/",
        "  https://uisp.midominio.net  ",
    ],
)
def test_normalize_base_url(entrada):
    assert normalize_base_url(entrada) == "https://uisp.midominio.net/nms/api/v2.1"


def test_normalize_base_url_keeps_port_and_scheme():
    assert normalize_base_url("http://192.168.1.50:9081") == "http://192.168.1.50:9081/nms/api/v2.1"


def test_normalize_base_url_rejects_empty():
    with pytest.raises(ValueError):
        normalize_base_url("   ")


def test_signal_quality_thresholds():
    assert signal_quality(-45) == "excelente"
    assert signal_quality(-60) == "excelente"
    assert signal_quality(-61) == "buena"
    assert signal_quality(-70) == "buena"
    assert signal_quality(-75) == "regular"
    assert signal_quality(-81) == "mala"
    assert signal_quality(None) is None


# --------------------------------------------------------------------- lectura


@pytest.mark.asyncio
async def test_sites(uisp):
    svc, _, _ = uisp
    sites = await svc.sites()
    assert [s["name"] for s in sites] == ["Torre Principal", "Sitio Sabana"]
    assert sites[0]["latitude"] == 18.9712
    assert sites[0]["address"] == "Cerro La Torre"


@pytest.mark.asyncio
async def test_devices_are_normalized(uisp):
    svc, _, _ = uisp
    devices = {d["id"]: d for d in await svc.devices()}

    ap = devices["ap-1"]
    assert ap["name"] == "Sector Norte 1"
    assert ap["role"] == "ap"
    assert ap["model"] == "LiteAP AC 120"
    assert ap["online"] is True
    assert ap["site_name"] == "Torre Principal"
    assert ap["frequency_mhz"] == 5745
    assert ap["downlink_capacity_mbps"] == 130.0
    assert ap["ssid"] == "DOLGA-SECTOR-NORTE-1"

    station = devices["st-11"]
    assert station["role"] == "station"
    assert station["signal_dbm"] == -58
    assert station["signal_quality"] == "excelente"
    assert station["ap_name"] == "Sector Norte 1"
    assert station["distance_m"] == 2400


@pytest.mark.asyncio
async def test_device_without_radio_fields_reports_none_not_zero(uisp):
    """Un switch no tiene señal ni frecuencia: eso es None, no 0."""
    svc, _, _ = uisp
    switch = next(d for d in await svc.devices() if d["id"] == "sw-1")

    assert switch["role"] == "switch"
    assert switch["signal_dbm"] is None
    assert switch["signal_quality"] is None
    assert switch["frequency_mhz"] is None
    assert switch["ssid"] is None
    # Lo que sí reporta, se lee bien.
    assert switch["cpu_percent"] == 9
    assert switch["online"] is True


@pytest.mark.asyncio
async def test_offline_device_has_no_invented_metrics(uisp):
    svc, _, _ = uisp
    ap = next(d for d in await svc.devices() if d["id"] == "ap-3")
    assert ap["online"] is False
    assert ap["status"] == "disconnected"
    assert ap["cpu_percent"] is None
    assert ap["uptime_seconds"] is None


@pytest.mark.asyncio
async def test_topology_counts_clients_from_associated_stations(uisp):
    svc, _, _ = uisp
    topology = await svc.topology()
    by_id = {s["id"]: s for s in topology["sectors"]}

    norte = by_id["ap-1"]
    assert norte["clients_total"] == 3
    assert norte["clients_online"] == 3
    assert norte["worst_signal_dbm"] == -84
    assert norte["average_signal_dbm"] == -69.7

    sur = by_id["ap-2"]
    assert sur["clients_total"] == 2
    assert sur["clients_online"] == 1  # una estación desconectada
    assert sur["clients_offline"] == 1

    sabana = by_id["ap-3"]
    assert sabana["online"] is False
    assert sabana["clients_total"] == 0

    # Una estación sin apDevice no se pierde: queda listada aparte.
    assert [s["id"] for s in topology["stations_without_sector"]] == ["st-31"]
    assert [d["id"] for d in topology["other_devices"]] == ["sw-1"]


@pytest.mark.asyncio
async def test_stations_of_sorts_worst_signal_first(uisp):
    """La lista de un sector es la lista de a quién hay que ir a revisar."""
    svc, _, _ = uisp
    stations = await svc.stations_of("ap-1")
    assert [s["id"] for s in stations] == ["st-13", "st-12", "st-11"]
    assert stations[0]["signal_dbm"] == -84
    assert stations[0]["signal_quality"] == "mala"


@pytest.mark.asyncio
async def test_overview(uisp):
    svc, _, _ = uisp
    overview = await svc.overview()

    assert overview["sites_total"] == 2
    assert overview["sectors_total"] == 3
    assert overview["sectors_online"] == 2
    assert overview["sectors_offline"] == 1
    assert overview["stations_total"] == 6
    assert overview["stations_online"] == 4
    assert overview["stations_offline"] == 2
    assert overview["other_devices_total"] == 1
    # Solo un cliente por debajo de -80 dBm.
    assert overview["weak_signal_count"] == 1
    assert overview["weak_signal_stations"][0]["id"] == "st-13"


@pytest.mark.asyncio
async def test_interfaces(uisp):
    svc, _, _ = uisp
    interfaces = await svc.interfaces("ap-1")
    assert [i["name"] for i in interfaces] == ["eth0", "ath0"]
    assert interfaces[0]["speed_mbps"] == 1000
    assert interfaces[0]["rx_mbps"] == 48.0
    assert interfaces[1]["speed_mbps"] is None  # el wifi no reporta velocidad de enlace


@pytest.mark.asyncio
async def test_statistics(uisp):
    svc, _, _ = uisp
    stats = await svc.statistics("ap-1", interval="hour")
    assert stats["available"] is True
    assert len(stats["series"]["cpu"]) == 3
    assert stats["series"]["signal"][0]["v"] == -59
    assert stats["series"]["receive_rate"][1]["v"] == 48000000


@pytest.mark.asyncio
async def test_statistics_unsupported_is_reported_not_crashed(uisp):
    """Si esta versión de UISP no da estadísticas, se dice; no se rompe la vista."""
    svc, state, _ = uisp
    state.statistics_supported = False
    stats = await svc.statistics("ap-1")
    assert stats["available"] is False
    assert stats["series"] == {}


@pytest.mark.asyncio
async def test_collections_wrapped_in_items_are_supported():
    """Algunas versiones devuelven {"items": [...]} en vez de una lista."""
    state = FakeUispState(TOKEN, wrap_collections=True)
    server = FakeUispServer(state).start()
    client = UispClient(server.base_url, TOKEN, timeout=5.0)
    try:
        devices = await UispService(client).devices()
        assert len(devices) == len(state.devices)
    finally:
        await client.close()
        server.stop()


@pytest.mark.asyncio
async def test_ping_check(uisp):
    svc, _, server = uisp
    info = await svc.ping_check()
    assert info["ok"] is True
    assert info["sites"] == 2
    assert info["devices"] == 10
    assert info["base_url"].endswith("/nms/api/v2.1")


# ---------------------------------------------------------------------- errores


@pytest.mark.asyncio
async def test_invalid_token_raises_auth_error():
    state = FakeUispState(TOKEN)
    server = FakeUispServer(state).start()
    client = UispClient(server.base_url, "token-equivocado", timeout=5.0)
    try:
        with pytest.raises(UispAuthError):
            await UispService(client).devices()
    finally:
        await client.close()
        server.stop()


@pytest.mark.asyncio
async def test_unreachable_server_raises_connection_error():
    """Servidor caído: mensaje distinto al de token inválido, a propósito."""
    client = UispClient("http://127.0.0.1:9", TOKEN, timeout=2.0)
    try:
        with pytest.raises(UispConnectionError):
            await UispService(client).devices()
    finally:
        await client.close()

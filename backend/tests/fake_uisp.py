"""Un UISP de mentira para probar el conector sin tocar una instalación real.

Levanta un uvicorn que responde `/nms/api/v2.1/...` con autenticación por
cabecera `x-auth-token`, igual que UISP. Las respuestas imitan la forma real:
`identification` / `overview` / `attributes` anidados, campos ausentes en los
equipos que no los reportan, y estaciones enlazadas a su sector por `apDevice`.
"""

from __future__ import annotations

import copy
import socket
import threading
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

SITES: List[Dict[str, Any]] = [
    {
        "identification": {"id": "site-1", "name": "Torre Principal", "type": "site", "status": "active"},
        "description": {
            "address": "Cerro La Torre",
            "location": {"latitude": 18.9712, "longitude": -69.0553},
        },
    },
    {
        "identification": {"id": "site-2", "name": "Sitio Sabana", "type": "site", "status": "active"},
        "description": {"address": "Sabana del Mar", "location": {"latitude": 19.0611, "longitude": -69.3906}},
    },
]


def _ap(device_id: str, name: str, site_id: str, site_name: str, online: bool = True, **over) -> Dict[str, Any]:
    overview = {
        "status": "active" if online else "disconnected",
        "cpu": 21,
        "ram": 48,
        "uptime": 1209600,
        "frequency": 5745,
        "channelWidth": 40,
        "wirelessMode": "ap-ptmp",
        "transmitPower": 22,
        "downlinkCapacity": 130000000,
        "uplinkCapacity": 65000000,
        "lastSeen": "2026-08-21T17:30:00.000Z",
    }
    overview.update(over)
    if not online:
        # Un equipo caído no reporta métricas; UISP las omite.
        for key in ("cpu", "ram", "uptime", "downlinkCapacity", "uplinkCapacity"):
            overview.pop(key, None)
    return {
        "identification": {
            "id": device_id,
            "name": name,
            "displayName": name,
            "mac": f"04:18:D6:{device_id[-2:]}:AA:01",
            "model": "LAP-120",
            "modelName": "LiteAP AC 120",
            "type": "airMax",
            "role": "ap",
            "status": overview["status"],
            "firmwareVersion": "8.7.11",
            "site": {"id": site_id, "name": site_name, "type": "site", "status": "active"},
        },
        "overview": overview,
        "ipAddress": f"10.50.0.{device_id[-1]}/24",
        "attributes": {"ssid": f"DOLGA-{name.upper().replace(' ', '-')}"},
    }


def _station(
    device_id: str,
    name: str,
    ap_id: Optional[str],
    ap_name: Optional[str],
    site_id: str,
    site_name: str,
    signal: Optional[int],
    online: bool = True,
) -> Dict[str, Any]:
    overview: Dict[str, Any] = {
        "status": "active" if online else "disconnected",
        "lastSeen": "2026-08-21T17:29:00.000Z",
    }
    if online:
        overview.update(
            {
                "cpu": 17,
                "ram": 39,
                "uptime": 86400,
                "downlinkCapacity": 90000000,
                "uplinkCapacity": 30000000,
                "distance": 2400,
                "frequency": 5745,
                "transmitPower": 18,
            }
        )
        if signal is not None:
            overview["signal"] = signal
            overview["remoteSignal"] = signal - 2
    return {
        "identification": {
            "id": device_id,
            "name": name,
            "displayName": name,
            "mac": f"04:18:D6:{device_id[-2:]}:BB:02",
            "model": "LBE-5AC-Gen2",
            "modelName": "LiteBeam AC Gen2",
            "type": "airMax",
            "role": "station",
            "status": overview["status"],
            "firmwareVersion": "8.7.11",
            "site": {"id": site_id, "name": site_name, "type": "site", "status": "active"},
        },
        "overview": overview,
        "ipAddress": f"10.60.0.{device_id[-2:]}/24",
        "attributes": ({"apDevice": {"id": ap_id, "name": ap_name}} if ap_id else {}),
    }


DEVICES: List[Dict[str, Any]] = [
    _ap("ap-1", "Sector Norte 1", "site-1", "Torre Principal"),
    _ap("ap-2", "Sector Sur 2", "site-1", "Torre Principal"),
    _ap("ap-3", "Sector Sabana 1", "site-2", "Sitio Sabana", online=False),
    _station("st-11", "Cliente Maria Perez", "ap-1", "Sector Norte 1", "site-1", "Torre Principal", -58),
    _station("st-12", "Cliente Jose Ramirez", "ap-1", "Sector Norte 1", "site-1", "Torre Principal", -67),
    _station("st-13", "Cliente Ana Diaz", "ap-1", "Sector Norte 1", "site-1", "Torre Principal", -84),
    _station("st-21", "Cliente Luis Mena", "ap-2", "Sector Sur 2", "site-1", "Torre Principal", -72),
    _station(
        "st-22", "Cliente Rosa Nunez", "ap-2", "Sector Sur 2", "site-1", "Torre Principal", None, online=False
    ),
    _station("st-31", "Cliente Sin Sector", None, None, "site-2", "Sitio Sabana", None, online=False),
    # Un equipo que no es antena: no reporta señal ni frecuencia.
    {
        "identification": {
            "id": "sw-1",
            "name": "Switch Torre",
            "displayName": "Switch Torre",
            "mac": "04:18:D6:99:CC:03",
            "model": "ES-8-150W",
            "modelName": "EdgeSwitch 8 150W",
            "type": "edgeSwitch",
            "role": "switch",
            "status": "active",
            "firmwareVersion": "1.9.3",
            "site": {"id": "site-1", "name": "Torre Principal", "type": "site", "status": "active"},
        },
        "overview": {"status": "active", "cpu": 9, "ram": 30, "uptime": 5184000},
        "ipAddress": "10.50.0.20/24",
    },
]

INTERFACES: List[Dict[str, Any]] = [
    {
        "identification": {"name": "eth0", "displayName": "eth0", "type": "eth", "mac": "04:18:D6:11:AA:01"},
        "status": {"enabled": True, "plugged": True, "speed": "1000Mbps"},
        "statistics": {"rxrate": 48000000, "txrate": 19000000, "rxbytes": 91827364, "txbytes": 41827364, "errors": 0, "dropped": 2},
    },
    {
        "identification": {"name": "ath0", "displayName": "ath0", "type": "wifi", "mac": "04:18:D6:11:AA:02"},
        "status": {"enabled": True, "plugged": True, "speed": None},
        "statistics": {"rxrate": 44000000, "txrate": 17000000, "rxbytes": 81827364, "txbytes": 31827364, "errors": 1, "dropped": 0},
    },
]

STATISTICS = {
    "cpu": [{"x": 1755795000000, "y": 19}, {"x": 1755795300000, "y": 23}, {"x": 1755795600000, "y": 21}],
    "ram": [{"x": 1755795000000, "y": 47}, {"x": 1755795300000, "y": 48}, {"x": 1755795600000, "y": 48}],
    "signal": [{"x": 1755795000000, "y": -59}, {"x": 1755795300000, "y": -58}, {"x": 1755795600000, "y": -60}],
    "receiveRate": [{"x": 1755795000000, "y": 41000000}, {"x": 1755795300000, "y": 48000000}],
    "transmitRate": [{"x": 1755795000000, "y": 16000000}, {"x": 1755795300000, "y": 19000000}],
}


class FakeUispState:
    def __init__(self, token: str = "app-key-de-prueba", wrap_collections: bool = False):
        self.token = token
        # Algunas versiones envuelven la colección en {"items": [...]}; el
        # cliente tiene que soportar las dos formas.
        self.wrap_collections = wrap_collections
        self.sites = copy.deepcopy(SITES)
        self.devices = copy.deepcopy(DEVICES)
        self.interfaces = copy.deepcopy(INTERFACES)
        self.statistics = copy.deepcopy(STATISTICS)
        self.calls: List[str] = []
        self.statistics_supported = True


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def build_uisp_app(state: FakeUispState) -> FastAPI:
    app = FastAPI()
    prefix = "/nms/api/v2.1"

    def _auth_ok(request: Request) -> bool:
        return request.headers.get("x-auth-token") == state.token

    def _collection(rows: List[Dict[str, Any]]):
        return JSONResponse({"items": rows} if state.wrap_collections else rows)

    @app.middleware("http")
    async def check_token(request: Request, call_next):
        state.calls.append(request.url.path)
        if not _auth_ok(request):
            return JSONResponse({"message": "Invalid token"}, status_code=401)
        return await call_next(request)

    @app.get(f"{prefix}/sites")
    async def sites():
        return _collection(state.sites)

    @app.get(f"{prefix}/devices")
    async def devices():
        return _collection(state.devices)

    @app.get(f"{prefix}/devices/{{device_id}}")
    async def device(device_id: str):
        for row in state.devices:
            if (row.get("identification") or {}).get("id") == device_id:
                return JSONResponse(row)
        return JSONResponse({"message": "Device not found"}, status_code=404)

    @app.get(f"{prefix}/devices/{{device_id}}/interfaces")
    async def interfaces(device_id: str):
        return _collection(state.interfaces)

    @app.get(f"{prefix}/devices/{{device_id}}/statistics")
    async def statistics(device_id: str, interval: str = "hour"):
        if not state.statistics_supported:
            return JSONResponse({"message": "Not found"}, status_code=404)
        return JSONResponse(state.statistics)

    return app


class FakeUispServer:
    def __init__(self, state: FakeUispState, host: str = "127.0.0.1"):
        import uvicorn

        self.state = state
        self.host = host
        self.port = _free_port()
        config = uvicorn.Config(
            build_uisp_app(state), host=host, port=self.port, log_level="warning", lifespan="off"
        )
        self._server = uvicorn.Server(config)
        self._thread: Optional[threading.Thread] = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> "FakeUispServer":
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        waited = 0.0
        while not self._server.started and waited < 10.0:
            threading.Event().wait(0.05)
            waited += 0.05
        if not self._server.started:
            raise RuntimeError("El UISP de prueba no arrancó a tiempo")
        return self

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

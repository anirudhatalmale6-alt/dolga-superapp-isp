"""Un RouterOS de mentira para probar el conector sin tocar un equipo real.

Implementa el mismo estado detrás de los dos transportes:

* `FakeRouterOSBinaryServer` habla el protocolo binario del puerto 8728
  (palabras con longitud codificada, login moderno y legacy, `!re`/`!done`/`!trap`).
* `FakeRouterOSRestServer` levanta un uvicorn real que responde `/rest/...`
  con autenticación básica, igual que RouterOS v7.

Así las pruebas ejercitan sockets y HTTP de verdad, no mocks.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import socket
import threading
from typing import Any, Dict, List, Optional

# Importados a nivel de módulo: con `from __future__ import annotations` FastAPI
# resuelve las anotaciones contra los globals del módulo, así que `Request` no
# puede ser un nombre local dentro de build_rest_app().
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

DEFAULT_STATE: Dict[str, Any] = {
    "system/resource": [
        {
            ".id": "*0",
            "uptime": "1w2d03:04:05",
            "version": "7.14.3 (stable)",
            "build-time": "Feb/20/2024 14:00:00",
            "factory-software": "6.48.6",
            "free-memory": "402653184",
            "total-memory": "1073741824",
            "cpu": "ARM64",
            "cpu-count": "4",
            "cpu-frequency": "1400",
            "cpu-load": "17",
            "free-hdd-space": "83886080",
            "total-hdd-space": "134217728",
            "architecture-name": "arm64",
            "board-name": "RB5009UG+S+",
            "platform": "MikroTik",
        }
    ],
    "system/identity": [{".id": "*0", "name": "DOLGA-BORDE-01"}],
    "system/routerboard": [
        {
            ".id": "*0",
            "model": "RB5009UG+S+",
            "serial-number": "HEX1234ABCD",
            "firmware-type": "rb5009",
            "current-firmware": "7.14.3",
            "upgrade-firmware": "7.14.3",
        }
    ],
    "interface": [
        {
            ".id": "*1",
            "name": "ether1-wan",
            "type": "ether",
            "mtu": "1500",
            "actual-mtu": "1500",
            "mac-address": "48:A9:8A:11:22:33",
            "running": "true",
            "disabled": "false",
            "rx-byte": "982374982374",
            "tx-byte": "129387129387",
            "rx-packet": "812734981",
            "tx-packet": "612734981",
            "link-downs": "2",
            "comment": "Enlace principal",
        },
        {
            ".id": "*2",
            "name": "ether2-lan",
            "type": "ether",
            "mtu": "1500",
            "mac-address": "48:A9:8A:11:22:34",
            "running": "true",
            "disabled": "false",
            "rx-byte": "12938712",
            "tx-byte": "98237498",
            "rx-packet": "128371",
            "tx-packet": "918273",
            "link-downs": "0",
        },
        {
            ".id": "*3",
            "name": "sfp-sfpplus1",
            "type": "ether",
            "mtu": "1500",
            "mac-address": "48:A9:8A:11:22:35",
            "running": "false",
            "disabled": "true",
            "rx-byte": "0",
            "tx-byte": "0",
            "rx-packet": "0",
            "tx-packet": "0",
            "link-downs": "0",
        },
    ],
    "ip/address": [
        {".id": "*1", "address": "192.168.88.1/24", "network": "192.168.88.0", "interface": "ether2-lan", "disabled": "false"},
        {".id": "*2", "address": "10.10.0.1/24", "network": "10.10.0.0", "interface": "ether1-wan", "disabled": "false"},
    ],
    "ip/route": [
        {
            ".id": "*0",
            "dst-address": "0.0.0.0/0",
            "gateway": "10.10.0.254",
            "immediate-gw": "10.10.0.254%ether1-wan",
            "distance": "1",
            "active": "true",
            "dynamic": "false",
        },
        {
            ".id": "*1",
            "dst-address": "192.168.88.0/24",
            "gateway": "ether2-lan",
            "distance": "0",
            "active": "true",
            "dynamic": "true",
        },
    ],
    # RouterOS 7 devuelve una fila por lectura. Las placas sin sensor devuelven
    # una lista vacía, que es el caso que hay que manejar sin inventar números.
    "system/health": [
        {".id": "*0", "name": "temperature", "value": "59", "type": "C"},
        {".id": "*1", "name": "voltage", "value": "24.1", "type": "V"},
    ],
    "system/package/update": [
        {
            ".id": "*0",
            "channel": "stable",
            "installed-version": "7.14.3",
            "latest-version": "7.15.2",
            "status": "New version is available",
        }
    ],
    "ppp/profile": [
        {".id": "*0", "name": "default", "local-address": "10.20.0.1", "remote-address": "pool-clientes"},
        {".id": "*1", "name": "PLAN-10M", "local-address": "10.20.0.1", "remote-address": "pool-clientes", "rate-limit": "10M/10M"},
        {".id": "*2", "name": "PLAN-30M", "local-address": "10.20.0.1", "remote-address": "pool-clientes", "rate-limit": "30M/30M"},
        {".id": "*3", "name": "CORTADO", "local-address": "10.20.0.1", "remote-address": "pool-cortados", "rate-limit": "128k/128k", "address-list": "morosos"},
    ],
    "ppp/secret": [
        {".id": "*10", "name": "cliente001", "service": "pppoe", "profile": "PLAN-30M", "remote-address": "10.20.0.11", "disabled": "false", "comment": "Maria Perez"},
        {".id": "*11", "name": "cliente002", "service": "pppoe", "profile": "PLAN-10M", "remote-address": "10.20.0.12", "disabled": "false", "comment": "Jose Ramirez"},
        {".id": "*12", "name": "cliente003", "service": "pppoe", "profile": "PLAN-10M", "remote-address": "10.20.0.13", "disabled": "true", "comment": "Suspendido por falta de pago"},
    ],
    "ppp/active": [
        {".id": "*20", "name": "cliente001", "service": "pppoe", "caller-id": "48:A9:8A:AA:BB:01", "address": "10.20.0.11", "uptime": "4h20m11s", "encoding": "", "session-id": "0x81000001"},
        {".id": "*21", "name": "cliente002", "service": "pppoe", "caller-id": "48:A9:8A:AA:BB:02", "address": "10.20.0.12", "uptime": "2d01:15:03", "encoding": "", "session-id": "0x81000002"},
    ],
    "ip/firewall/address-list": [],
}


class FakeRouterOSState:
    """Estado compartido por los dos transportes."""

    def __init__(
        self,
        username: str = "api-dolga",
        password: str = "S3cret!",
        legacy_login: bool = False,
        has_health_sensor: bool = True,
        vary: bool = False,
        seed: int = 0,
    ):
        self.username = username
        self.password = password
        self.legacy_login = legacy_login
        # `vary` hace que CPU, temperatura y tráfico se muevan entre lecturas,
        # para que el modo demostración dibuje curvas y no líneas planas. Es
        # determinista a propósito (nada de aleatorio) para no romper pruebas.
        self.vary = vary
        self.seed = seed
        self._tick = 0
        self.tables: Dict[str, List[Dict[str, str]]] = copy.deepcopy(DEFAULT_STATE)
        if not has_health_sensor:
            # Un RB750Gr3 o un RB2011 no traen sensor: el menú existe pero
            # responde vacío.
            self.tables["system/health"] = []
        self._next_id = 1000
        self.calls: List[str] = []
        self.rebooted = 0
        self.shutdown_count = 0

    # ------------------------------------------------------------- utilidades

    def _new_id(self) -> str:
        self._next_id += 1
        return f"*{self._next_id}"

    def table(self, path: str) -> List[Dict[str, str]]:
        path = path.strip("/")
        if path not in self.tables:
            raise KeyError(f"no such command prefix ({path})")
        return self.tables[path]

    # ------------------------------------------------------------ operaciones

    def _wobble(self, base: float, amplitude: float, phase: int = 0) -> float:
        """Oscilación determinista alrededor de `base`, sin usar aleatoriedad."""
        import math

        angle = (self._tick + self.seed * 7 + phase) / 6.0
        return base + amplitude * math.sin(angle) + (amplitude / 3.0) * math.sin(angle * 2.7)

    def do_print(
        self, path: str, query: Optional[Dict[str, str]] = None, proplist: Optional[List[str]] = None
    ) -> List[Dict[str, str]]:
        self.calls.append(f"print {path} {query or {}}")
        if self.vary and path.strip("/") == "system/resource":
            self._tick += 1
            row = self.tables["system/resource"][0]
            row["cpu-load"] = str(max(2, min(96, int(self._wobble(32, 14)))))
            free = int(self._wobble(0.52, 0.06) * 1073741824)
            row["free-memory"] = str(max(52428800, free))
            if self.tables.get("system/health"):
                for entry in self.tables["system/health"]:
                    if entry.get("name") == "temperature":
                        entry["value"] = str(max(30, min(78, int(self._wobble(56, 6, phase=3)))))
        rows = self.table(path)
        result = []
        for row in rows:
            if query and any(row.get(key) != value for key, value in query.items() if key != ".proplist"):
                continue
            if proplist:
                keep = set(proplist) | {".id"}
                result.append({k: v for k, v in row.items() if k in keep})
            else:
                result.append(dict(row))
        return result

    def do_set(self, path: str, item_id: str, attrs: Dict[str, str]) -> None:
        self.calls.append(f"set {path} {item_id} {attrs}")
        for row in self.table(path):
            if row.get(".id") == item_id:
                row.update({k: v for k, v in attrs.items() if k != ".id"})
                return
        raise KeyError("no such item")

    def do_add(self, path: str, attrs: Dict[str, str]) -> str:
        self.calls.append(f"add {path} {attrs}")
        new_id = self._new_id()
        row = {".id": new_id, **{k: v for k, v in attrs.items() if k != ".id"}}
        self.table(path).append(row)
        return new_id

    def do_remove(self, path: str, item_id: str) -> None:
        self.calls.append(f"remove {path} {item_id}")
        rows = self.table(path)
        for index, row in enumerate(rows):
            if row.get(".id") == item_id:
                rows.pop(index)
                return
        raise KeyError("no such item")

    def do_command(self, path: str, command: str, attrs: Dict[str, str]) -> List[Dict[str, str]]:
        self.calls.append(f"cmd {path}/{command} {attrs}")
        if path.strip("/") == "interface" and command == "monitor-traffic":
            name = attrs.get("interface", "")
            if not any(row.get("name") == name for row in self.tables["interface"]):
                raise KeyError(f"input does not match any value of interface")
            rx_bps, tx_bps = 38400000, 12800000
            if self.vary:
                rx_bps = int(self._wobble(78_000_000, 22_000_000, phase=1))
                tx_bps = int(self._wobble(34_000_000, 11_000_000, phase=5))
            return [
                {
                    "name": name,
                    "rx-packets-per-second": str(max(0, rx_bps // 12000)),
                    "rx-bits-per-second": str(max(0, rx_bps)),
                    "tx-packets-per-second": str(max(0, tx_bps // 12000)),
                    "tx-bits-per-second": str(max(0, tx_bps)),
                }
            ]
        if path.strip("/") == "system" and command == "reboot":
            self.rebooted += 1
            return []
        if path.strip("/") == "system" and command == "shutdown":
            self.shutdown_count += 1
            return []
        if path.strip("/") == "system/package/update" and command == "check-for-updates":
            return []
        if command == "print":
            return self.do_print(path, {k: v for k, v in attrs.items() if not k.startswith(".")})
        raise KeyError(f"no such command ({command})")


# ---------------------------------------------------------------- API binaria


def _encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    if length < 0x4000:
        return (length | 0x8000).to_bytes(2, "big")
    if length < 0x200000:
        return (length | 0xC00000).to_bytes(3, "big")
    if length < 0x10000000:
        return (length | 0xE0000000).to_bytes(4, "big")
    return b"\xf0" + length.to_bytes(4, "big")


def _encode_word(word: str) -> bytes:
    payload = word.encode()
    return _encode_length(len(payload)) + payload


class FakeRouterOSBinaryServer:
    def __init__(self, state: FakeRouterOSState, host: str = "127.0.0.1"):
        self.state = state
        self.host = host
        self.port: int = 0
        self._server: Optional[asyncio.AbstractServer] = None
        self._challenge = b"\x11\x22\x33\x44\x55\x66\x77\x88\x99\xaa\xbb\xcc\xdd\xee\xff\x00"

    async def start(self) -> "FakeRouterOSBinaryServer":
        self._server = await asyncio.start_server(self._handle, self.host, 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # --------------------------------------------------------------- protocolo

    @staticmethod
    async def _read_length(reader: asyncio.StreamReader) -> int:
        first = (await reader.readexactly(1))[0]
        if first < 0x80:
            return first
        if first < 0xC0:
            return ((first << 8) + (await reader.readexactly(1))[0]) & ~0x8000
        if first < 0xE0:
            return ((first << 16) + int.from_bytes(await reader.readexactly(2), "big")) & ~0xC00000
        if first < 0xF0:
            return ((first << 24) + int.from_bytes(await reader.readexactly(3), "big")) & ~0xE0000000
        return int.from_bytes(await reader.readexactly(4), "big")

    async def _read_sentence(self, reader: asyncio.StreamReader) -> List[str]:
        words: List[str] = []
        while True:
            length = await self._read_length(reader)
            if length == 0:
                return words
            words.append((await reader.readexactly(length)).decode())

    @staticmethod
    def _write(writer: asyncio.StreamWriter, words: List[str]) -> None:
        writer.write(b"".join(_encode_word(word) for word in words) + b"\x00")

    def _done(self, writer, attrs: Optional[Dict[str, str]] = None) -> None:
        words = ["!done"] + [f"={k}={v}" for k, v in (attrs or {}).items()]
        self._write(writer, words)

    def _rows(self, writer, rows: List[Dict[str, str]]) -> None:
        for row in rows:
            self._write(writer, ["!re"] + [f"={k}={v}" for k, v in row.items()])
        self._done(writer)

    def _trap(self, writer, message: str) -> None:
        self._write(writer, ["!trap", f"=message={message}"])
        self._done(writer)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        authenticated = False
        try:
            while True:
                try:
                    words = await self._read_sentence(reader)
                except (asyncio.IncompleteReadError, ConnectionResetError):
                    return
                if not words:
                    continue
                command = words[0]
                attrs: Dict[str, str] = {}
                queries: Dict[str, str] = {}
                for word in words[1:]:
                    if word.startswith("="):
                        key, _, value = word[1:].partition("=")
                        attrs[key] = value
                    elif word.startswith("?"):
                        key, _, value = word[1:].partition("=")
                        queries[key] = value

                if command == "/login":
                    authenticated = self._handle_login(writer, attrs)
                    await writer.drain()
                    continue

                if not authenticated:
                    self._trap(writer, "not logged in")
                    await writer.drain()
                    continue

                self._dispatch(writer, command, attrs, queries)
                await writer.drain()
        finally:
            writer.close()

    def _handle_login(self, writer, attrs: Dict[str, str]) -> bool:
        if self.state.legacy_login:
            if "response" in attrs:
                expected = "00" + hashlib.md5(
                    b"\x00" + self.state.password.encode() + self._challenge
                ).hexdigest()
                if attrs.get("name") == self.state.username and attrs["response"] == expected:
                    self._done(writer)
                    return True
                self._trap(writer, "cannot log in")
                return False
            # Primer paso: entregamos el reto.
            self._done(writer, {"ret": self._challenge.hex()})
            return False

        if attrs.get("name") == self.state.username and attrs.get("password") == self.state.password:
            self._done(writer)
            return True
        self._trap(writer, "invalid user name or password (6)")
        return False

    def _dispatch(self, writer, command: str, attrs: Dict[str, str], queries: Dict[str, str]) -> None:
        parts = command.strip("/").split("/")
        verb = parts[-1]
        path = "/".join(parts[:-1])
        proplist = attrs.pop(".proplist", None)
        try:
            if verb == "print":
                rows = self.state.do_print(
                    path, queries or None, proplist.split(",") if proplist else None
                )
                self._rows(writer, rows)
            elif verb == "set":
                self.state.do_set(path, attrs.pop(".id", ""), attrs)
                self._done(writer)
            elif verb == "add":
                new_id = self.state.do_add(path, attrs)
                self._done(writer, {"ret": new_id})
            elif verb == "remove":
                self.state.do_remove(path, attrs.get(".id", ""))
                self._done(writer)
            else:
                rows = self.state.do_command(path, verb, attrs)
                self._rows(writer, rows)
        except KeyError as exc:
            self._trap(writer, str(exc).strip("'"))


# ----------------------------------------------------------------- REST v7


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def build_rest_app(state: FakeRouterOSState):
    """App ASGI que imita `/rest/...` de RouterOS v7."""
    app = FastAPI()

    def _auth_ok(request: Request) -> bool:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("basic "):
            return False
        try:
            decoded = base64.b64decode(header.split(" ", 1)[1]).decode()
        except Exception:
            return False
        user, _, password = decoded.partition(":")
        return user == state.username and password == state.password

    @app.api_route("/rest/{full_path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
    async def rest(full_path: str, request: Request) -> Response:
        if not _auth_ok(request):
            return JSONResponse(
                {"error": 401, "message": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="RouterOS"'},
            )

        query = {k: v for k, v in request.query_params.items()}
        proplist = query.pop(".proplist", None)
        body: Dict[str, Any] = {}
        if request.method in ("POST", "PATCH", "PUT"):
            raw = await request.body()
            if raw:
                try:
                    body = await request.json()
                except Exception:
                    body = {}

        segments = full_path.strip("/").split("/")
        try:
            if request.method == "GET":
                rows = state.do_print(
                    "/".join(segments), query or None, proplist.split(",") if proplist else None
                )
                return JSONResponse(rows)

            if request.method == "PATCH":
                # /rest/ppp/secret/*10
                item_id = segments[-1]
                state.do_set("/".join(segments[:-1]), item_id, {k: str(v) for k, v in body.items()})
                return JSONResponse({".id": item_id, **body})

            if request.method == "PUT":
                new_id = state.do_add("/".join(segments), {k: str(v) for k, v in body.items()})
                return JSONResponse({".id": new_id, **body})

            if request.method == "DELETE":
                item_id = segments[-1]
                state.do_remove("/".join(segments[:-1]), item_id)
                return Response(status_code=204)

            # POST -> comando
            rows = state.do_command(
                "/".join(segments[:-1]), segments[-1], {k: str(v) for k, v in body.items()}
            )
            return JSONResponse(rows)
        except KeyError as exc:
            return JSONResponse({"error": 400, "message": str(exc).strip("'")}, status_code=400)

    return app


class FakeRouterOSRestServer:
    """Levanta un uvicorn real en un hilo aparte."""

    def __init__(self, state: FakeRouterOSState, host: str = "127.0.0.1"):
        import uvicorn

        self.state = state
        self.host = host
        self.port = _free_port()
        config = uvicorn.Config(
            build_rest_app(state), host=host, port=self.port, log_level="warning", lifespan="off"
        )
        self._server = uvicorn.Server(config)
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "FakeRouterOSRestServer":
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = 10.0
        step = 0.05
        waited = 0.0
        while not self._server.started and waited < deadline:
            threading.Event().wait(step)
            waited += step
        if not self._server.started:
            raise RuntimeError("El RouterOS REST de prueba no arrancó a tiempo")
        return self

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

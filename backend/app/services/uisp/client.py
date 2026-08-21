"""Cliente de la API de UISP (antes UNMS).

UISP expone `https://<host>/nms/api/v2.1/...` y se autentica con una App Key
que se genera en Configuración -> Usuarios -> App keys, enviada en la cabecera
`x-auth-token`. También acepta usuario y clave por `/user/login`, pero la App
Key es mejor: se puede revocar sola, sin tocar la cuenta del operador.

Igual que con MikroTik, este cliente vive solo en el servidor. El token se
guarda cifrado en PostgreSQL y el navegador nunca lo ve.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from app.services.audit import Exchange

from .exceptions import UispAuthError, UispConnectionError, UispRequestError


def normalize_base_url(raw: str) -> str:
    """Acepta lo que el operador tenga a mano y lo deja en la forma correcta.

    Todas estas entradas terminan en `https://uisp.midominio.net/nms/api/v2.1`:

        uisp.midominio.net
        https://uisp.midominio.net
        https://uisp.midominio.net/
        https://uisp.midominio.net/nms/api/v2.1
        https://uisp.midominio.net/nms/api/v2.1/
    """
    value = (raw or "").strip().rstrip("/")
    if not value:
        raise ValueError("La dirección de UISP no puede estar vacía")
    if "://" not in value:
        value = f"https://{value}"

    parsed = urlparse(value)
    if not parsed.netloc:
        raise ValueError(f"Dirección de UISP inválida: {raw!r}")

    path = parsed.path.rstrip("/")
    marker = "/nms/api/"
    if marker in path:
        # Ya trae la ruta de la API (con la versión que sea): la respetamos.
        path = path[: path.index(marker)] + path[path.index(marker) :]
    else:
        path = f"{path}/nms/api/v2.1"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


class UispClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        verify_tls: bool = False,
        timeout: float = 15.0,
        on_exchange: Optional[Callable[[Exchange], None]] = None,
    ) -> None:
        # Muchas instalaciones de UISP usan certificado autofirmado; por eso
        # verify_tls es opcional y por defecto va apagado.
        self.base_url = normalize_base_url(base_url)
        self.token = token
        self.verify_tls = verify_tls
        self.timeout = timeout
        # Callback de bitácora: anota cada llamada real hacia UISP. Va aquí, en
        # el transporte, y no en la capa de arriba, para que lo que quede
        # registrado sea la URL que de verdad salió del servidor.
        self.on_exchange = on_exchange
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"x-auth-token": self.token, "Accept": "application/json"},
                verify=self.verify_tls,
                timeout=self.timeout,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "UispClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    def _note(self, exchange: Exchange) -> None:
        """Anota el intercambio si hay bitácora. Nunca interrumpe la llamada."""
        if self.on_exchange is None:
            return
        try:
            self.on_exchange(exchange)
        except Exception:  # pragma: no cover - defensivo
            pass

    async def _send(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        writing: bool,
    ) -> httpx.Response:
        """Único punto por donde sale una petición hacia UISP.

        Está centralizado a propósito: así la bitácora ve absolutamente todas
        las llamadas, incluidas las que fallan, sin depender de que cada método
        se acuerde de registrarse.
        """
        started = time.perf_counter()

        def elapsed() -> int:
            return int((time.perf_counter() - started) * 1000)

        def anotar(*, ok: bool, http_status=None, rows=None, error=None) -> None:
            self._note(
                Exchange(
                    target="uisp",
                    request=f"{method.upper()} {self.base_url}{url}",
                    ok=ok,
                    duration_ms=elapsed(),
                    http_status=http_status,
                    rows=rows,
                    error=error,
                )
            )

        try:
            response = await self._get_client().request(
                method, url, params=params, json=json_body
            )
        except httpx.ConnectError as exc:
            anotar(ok=False, error=f"no se pudo conectar: {exc}")
            raise UispConnectionError(
                f"No se pudo conectar a UISP en {self.base_url}: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            verbo = f"ejecutando {method}" if writing else "consultando"
            anotar(ok=False, error=f"timeout tras {self.timeout} s")
            raise UispConnectionError(f"Timeout {verbo} {self.base_url}{url}") from exc
        except httpx.HTTPError as exc:  # pragma: no cover - red
            anotar(ok=False, error=str(exc))
            raise UispConnectionError(f"Error HTTP hacia {self.base_url}{url}: {exc}") from exc

        if response.status_code in (401, 403):
            anotar(ok=False, http_status=response.status_code, error="App Key rechazada")
            if writing:
                raise UispAuthError(
                    f"UISP rechazó la acción (HTTP {response.status_code}). La App Key "
                    "necesita permiso de escritura para ejecutar acciones sobre equipos."
                )
            raise UispAuthError(
                "UISP rechazó el token (HTTP "
                f"{response.status_code}). Verifica que la App Key siga activa y "
                "tenga permiso de lectura."
            )
        if response.status_code == 404:
            anotar(ok=False, http_status=404, error="ruta no encontrada en este UISP")
            if writing:
                raise UispRequestError(
                    f"Esta versión de UISP no ofrece la acción {url}.", path=url, status_code=404
                )
            raise UispRequestError(
                f"UISP no conoce la ruta {url}. Puede que esta versión de UISP no "
                "ofrezca ese recurso.",
                path=url,
                status_code=404,
            )
        if response.status_code >= 400:
            detail = ""
            try:
                body = response.json()
                if isinstance(body, dict):
                    detail = body.get("message") or body.get("error") or ""
            except ValueError:
                detail = response.text[:300]
            anotar(ok=False, http_status=response.status_code, error=detail or None)
            raise UispRequestError(
                detail or f"HTTP {response.status_code} en {url}",
                path=url,
                status_code=response.status_code,
            )

        anotar(ok=True, http_status=response.status_code, rows=_count(response))
        return response

    async def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = path if path.startswith("/") else f"/{path}"
        response = await self._send("GET", url, params=params, writing=False)
        if not response.content:
            return []
        try:
            return response.json()
        except ValueError as exc:
            raise UispRequestError(
                f"UISP devolvió una respuesta que no es JSON en {url}", path=url
            ) from exc

    async def request(
        self, method: str, path: str, json_body: Optional[Dict[str, Any]] = None
    ) -> Any:
        """POST / PUT / PATCH / DELETE contra UISP.

        Las acciones de escritura varían bastante entre versiones de UISP. Si
        una instalación no ofrece un endpoint, UISP responde 404 y aquí sale un
        `UispRequestError` con ese mensaje: la plataforma dice "esta versión de
        UISP no ofrece esa acción" en vez de fingir que se ejecutó.
        """
        url = path if path.startswith("/") else f"/{path}"
        response = await self._send(method, url, json_body=json_body, writing=True)
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {}

    async def post(self, path: str, json_body: Optional[Dict[str, Any]] = None) -> Any:
        return await self.request("POST", path, json_body)

    async def get_list(self, path: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Como `get`, pero garantiza una lista de diccionarios.

        UISP a veces envuelve la colección en `{"items": [...]}` según versión.
        """
        data = await self.get(path, params)
        if isinstance(data, dict):
            for key in ("items", "data", "results"):
                if isinstance(data.get(key), list):
                    return [row for row in data[key] if isinstance(row, dict)]
            return [data]
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        return []


def _count(response: httpx.Response) -> Optional[int]:
    """Cuántos elementos trajo la respuesta, para dejarlo en la bitácora.

    Es una cifra informativa: si el cuerpo no es una colección se anota None en
    vez de inventar un 1, que se leería como "vino un equipo".
    """
    if not response.content:
        return 0
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, list):
        return len(body)
    if isinstance(body, dict):
        for key in ("items", "data", "results"):
            if isinstance(body.get(key), list):
                return len(body[key])
    return None

"""Cliente de la API de UISP (antes UNMS).

UISP expone `https://<host>/nms/api/v2.1/...` y se autentica con una App Key
que se genera en Configuración -> Usuarios -> App keys, enviada en la cabecera
`x-auth-token`. También acepta usuario y clave por `/user/login`, pero la App
Key es mejor: se puede revocar sola, sin tocar la cuenta del operador.

Igual que con MikroTik, este cliente vive solo en el servidor. El token se
guarda cifrado en PostgreSQL y el navegador nunca lo ve.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

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
    ) -> None:
        # Muchas instalaciones de UISP usan certificado autofirmado; por eso
        # verify_tls es opcional y por defecto va apagado.
        self.base_url = normalize_base_url(base_url)
        self.token = token
        self.verify_tls = verify_tls
        self.timeout = timeout
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

    async def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = path if path.startswith("/") else f"/{path}"
        try:
            response = await self._get_client().get(url, params=params)
        except httpx.ConnectError as exc:
            raise UispConnectionError(
                f"No se pudo conectar a UISP en {self.base_url}: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise UispConnectionError(f"Timeout consultando {self.base_url}{url}") from exc
        except httpx.HTTPError as exc:  # pragma: no cover - red
            raise UispConnectionError(f"Error HTTP hacia {self.base_url}{url}: {exc}") from exc

        if response.status_code in (401, 403):
            raise UispAuthError(
                "UISP rechazó el token (HTTP "
                f"{response.status_code}). Verifica que la App Key siga activa y "
                "tenga permiso de lectura."
            )
        if response.status_code == 404:
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
            raise UispRequestError(
                detail or f"HTTP {response.status_code} en {url}",
                path=url,
                status_code=response.status_code,
            )

        if not response.content:
            return []
        try:
            return response.json()
        except ValueError as exc:
            raise UispRequestError(
                f"UISP devolvió una respuesta que no es JSON en {url}", path=url
            ) from exc

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

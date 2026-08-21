"""Cliente REST de RouterOS v7 (`/rest/...`).

RouterOS 7 expone toda la consola por HTTP con autenticación básica. Este
cliente traduce las mismas operaciones que la API binaria para que la capa
superior no tenga que saber cuál transporte está en uso.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from .exceptions import (
    MikrotikAuthError,
    MikrotikCommandError,
    MikrotikConnectionError,
)


class RouterOSRestClient:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 443,
        use_tls: bool = True,
        verify_tls: bool = False,
        timeout: float = 8.0,
    ) -> None:
        scheme = "https" if use_tls else "http"
        self.base_url = f"{scheme}://{host}:{port}/rest"
        self.username = username
        self.password = password
        self.verify_tls = verify_tls
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                auth=(self.username, self.password),
                verify=self.verify_tls,
                timeout=self.timeout,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "RouterOSRestClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    # ------------------------------------------------------------------ interno

    @staticmethod
    def _normalize(payload: Any) -> List[Dict[str, str]]:
        """RouterOS devuelve dict para recursos únicos y lista para colecciones."""
        if payload is None or payload == "":
            return []
        if isinstance(payload, dict):
            return [payload]
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        return []

    async def _request(self, method: str, path: str, json_body: Optional[dict] = None) -> List[Dict[str, str]]:
        client = self._get_client()
        url = path if path.startswith("/") else "/" + path
        try:
            response = await client.request(method, url, json=json_body)
        except httpx.ConnectError as exc:
            raise MikrotikConnectionError(f"No se pudo conectar a {self.base_url}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise MikrotikConnectionError(f"Timeout consultando {self.base_url}{url}") from exc
        except httpx.HTTPError as exc:  # pragma: no cover - red
            raise MikrotikConnectionError(f"Error HTTP hacia {self.base_url}{url}: {exc}") from exc

        if response.status_code in (401, 403):
            raise MikrotikAuthError(
                f"RouterOS rechazó las credenciales del usuario '{self.username}' "
                f"(HTTP {response.status_code}). Verifica que el usuario tenga el permiso 'rest-api'."
            )
        if response.status_code >= 400:
            detail = ""
            try:
                body = response.json()
                if isinstance(body, dict):
                    detail = body.get("message") or body.get("detail") or ""
            except Exception:
                detail = response.text[:300]
            raise MikrotikCommandError(
                detail or f"HTTP {response.status_code} en {url}", command=url
            )

        if not response.content:
            return []
        try:
            return self._normalize(response.json())
        except ValueError:
            return []

    # ------------------------------------------------------------- API pública

    async def get(self, path: str, query: Optional[Dict[str, str]] = None) -> List[Dict[str, str]]:
        """GET /rest/<path>. `query` aplica filtros del lado del router."""
        url = path
        if query:
            url += "?" + "&".join(f"{k}={v}" for k, v in query.items())
        return await self._request("GET", url)

    async def post(self, path: str, body: Optional[dict] = None) -> List[Dict[str, str]]:
        """POST /rest/<path> — ejecuta comandos (print, monitor-traffic, remove...)."""
        return await self._request("POST", path, json_body=body or {})

    async def patch(self, path: str, body: dict) -> List[Dict[str, str]]:
        """PATCH /rest/<path>/<id> — actualiza una entrada existente."""
        return await self._request("PATCH", path, json_body=body)

    async def put(self, path: str, body: dict) -> List[Dict[str, str]]:
        """PUT /rest/<path> — crea una entrada."""
        return await self._request("PUT", path, json_body=body)

    async def delete(self, path: str) -> List[Dict[str, str]]:
        return await self._request("DELETE", path)

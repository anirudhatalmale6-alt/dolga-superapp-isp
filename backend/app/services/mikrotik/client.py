"""Capa unificada sobre los dos transportes de RouterOS.

Arriba de esta clase nadie sabe si el equipo respondió por REST (v7) o por la
API binaria (v6/v7): ambos devuelven listas de diccionarios con las mismas
claves que muestra la consola (`name`, `rx-byte`, `.id`, ...).

En modo AUTO se intenta REST primero y, si el servicio no está disponible, se
cae a la API binaria y se recuerda la elección para las siguientes llamadas.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .binary_api import RouterOSBinaryClient
from .exceptions import MikrotikAuthError, MikrotikConnectionError, MikrotikError
from .rest import RouterOSRestClient

logger = logging.getLogger(__name__)

REST = "rest"
API = "api"
AUTO = "auto"


def _menu_to_path(menu: str) -> str:
    """`ppp/secret` o `/ppp/secret` -> `ppp/secret`."""
    return menu.strip("/")


class MikrotikClient:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        mode: str = AUTO,
        rest_port: int = 443,
        rest_use_tls: bool = True,
        api_port: int = 8728,
        api_use_tls: bool = False,
        verify_tls: bool = False,
        timeout: float = 8.0,
    ) -> None:
        self.host = host
        self.mode = mode
        self._active_transport: Optional[str] = None if mode == AUTO else mode
        self._rest = RouterOSRestClient(
            host, username, password, rest_port, rest_use_tls, verify_tls, timeout
        )
        self._api = RouterOSBinaryClient(
            host, username, password, api_port, api_use_tls, verify_tls, timeout
        )

    @property
    def transport(self) -> Optional[str]:
        """Transporte efectivamente usado en la última llamada."""
        return self._active_transport

    async def close(self) -> None:
        await self._rest.close()
        await self._api.close()

    async def __aenter__(self) -> "MikrotikClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    # ------------------------------------------------------------ despacho

    async def _dispatch(self, rest_call, api_call):
        """Ejecuta la variante correcta según el modo configurado."""
        if self._active_transport == REST:
            return await rest_call()
        if self._active_transport == API:
            return await api_call()

        # AUTO: probamos REST y recordamos el resultado.
        try:
            result = await rest_call()
            self._active_transport = REST
            return result
        except MikrotikAuthError:
            # Credenciales malas: la API binaria fallaría igual, no insistimos.
            raise
        except (MikrotikConnectionError, MikrotikError) as exc:
            logger.info(
                "REST no disponible en %s (%s); reintentando por API binaria", self.host, exc
            )
            result = await api_call()
            self._active_transport = API
            return result

    # ------------------------------------------------------- operaciones CRUD

    async def print(
        self,
        menu: str,
        query: Optional[Dict[str, Any]] = None,
        proplist: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        path = _menu_to_path(menu)

        async def rest_call():
            params = {k: _as_ros(v) for k, v in (query or {}).items()}
            if proplist:
                params[".proplist"] = ",".join(proplist)
            return await self._rest.get(path, params or None)

        async def api_call():
            queries = [f"?{k}={_as_ros(v)}" for k, v in (query or {}).items()]
            return await self._api.command(f"/{path}/print", queries=queries, proplist=proplist)

        return await self._dispatch(rest_call, api_call)

    async def get_by_id(self, menu: str, item_id: str) -> Optional[Dict[str, str]]:
        rows = await self.print(menu, query={".id": item_id})
        return rows[0] if rows else None

    async def set(self, menu: str, item_id: str, attrs: Dict[str, Any]) -> List[Dict[str, str]]:
        path = _menu_to_path(menu)
        body = {k: _as_ros(v) for k, v in attrs.items()}

        async def rest_call():
            return await self._rest.patch(f"{path}/{item_id}", body)

        async def api_call():
            return await self._api.command(f"/{path}/set", params={".id": item_id, **body})

        return await self._dispatch(rest_call, api_call)

    async def add(self, menu: str, attrs: Dict[str, Any]) -> List[Dict[str, str]]:
        path = _menu_to_path(menu)
        body = {k: _as_ros(v) for k, v in attrs.items()}

        async def rest_call():
            return await self._rest.put(path, body)

        async def api_call():
            return await self._api.command(f"/{path}/add", params=body)

        return await self._dispatch(rest_call, api_call)

    async def remove(self, menu: str, item_id: str) -> List[Dict[str, str]]:
        path = _menu_to_path(menu)

        async def rest_call():
            return await self._rest.delete(f"{path}/{item_id}")

        async def api_call():
            return await self._api.command(f"/{path}/remove", params={".id": item_id})

        return await self._dispatch(rest_call, api_call)

    async def run(
        self, menu: str, command: str, attrs: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, str]]:
        """Comandos que no son CRUD: `monitor-traffic`, `torch`, `reboot`, ..."""
        path = _menu_to_path(menu)
        body = {k: _as_ros(v) for k, v in (attrs or {}).items()}

        async def rest_call():
            return await self._rest.post(f"{path}/{command}", body)

        async def api_call():
            return await self._api.command(f"/{path}/{command}", params=body)

        return await self._dispatch(rest_call, api_call)

    async def ping_check(self) -> Dict[str, Any]:
        """Prueba de conectividad usada por 'Probar conexión' y por el monitoreo."""
        rows = await self.print("system/resource")
        identity = await self.print("system/identity")
        data = rows[0] if rows else {}
        return {
            "ok": True,
            "transport": self._active_transport,
            "identity": (identity[0].get("name") if identity else None),
            "version": data.get("version"),
            "board_name": data.get("board-name"),
        }


def _as_ros(value: Any) -> str:
    """Convierte tipos de Python al formato que espera RouterOS."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)

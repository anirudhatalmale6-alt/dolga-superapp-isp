"""Operaciones de negocio sobre un router MikroTik.

Traduce la salida cruda de RouterOS a estructuras estables y tipadas para el
frontend, y expone las acciones que pide el proyecto: CPU, memoria, interfaces,
tráfico, clientes PPPoE, estado de conexiones, suspensión y reactivación.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .client import MikrotikClient
from .exceptions import MikrotikCommandError

_UPTIME_RE = re.compile(
    r"(?:(?P<w>\d+)w)?(?:(?P<d>\d+)d)?(?:(?P<h>\d+)h)?"
    r"(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s)?$"
)
_CLOCK_RE = re.compile(r"^(?:(?P<w>\d+)w)?(?:(?P<d>\d+)d)?(?P<h>\d+):(?P<m>\d+):(?P<s>\d+)$")


def parse_uptime(value: Optional[str]) -> Optional[int]:
    """`1w2d03:04:05` o `4h20m11s` -> segundos."""
    if not value:
        return None
    value = value.strip()
    match = _CLOCK_RE.match(value) or _UPTIME_RE.match(value)
    if not match:
        return None
    parts = {k: int(v) for k, v in match.groupdict(default="0").items()}
    if not any(parts.values()) and value not in ("0s", "00:00:00"):
        return None
    return parts["w"] * 604800 + parts["d"] * 86400 + parts["h"] * 3600 + parts["m"] * 60 + parts["s"]


def to_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def to_bool(value: Any) -> bool:
    return str(value).strip().lower() in ("true", "yes", "1")


def human_bytes(num: Optional[int]) -> Optional[str]:
    if num is None:
        return None
    size = float(num)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} EiB"


class MikrotikService:
    def __init__(self, client: MikrotikClient) -> None:
        self.client = client

    # --------------------------------------------------------------- sistema

    async def system_resource(self) -> Dict[str, Any]:
        rows = await self.client.print("system/resource")
        identity_rows = await self.client.print("system/identity")
        data = rows[0] if rows else {}

        total_mem = to_int(data.get("total-memory"))
        free_mem = to_int(data.get("free-memory"))
        used_mem = (total_mem - free_mem) if (total_mem is not None and free_mem is not None) else None

        total_hdd = to_int(data.get("total-hdd-space"))
        free_hdd = to_int(data.get("free-hdd-space"))

        return {
            "identity": identity_rows[0].get("name") if identity_rows else None,
            "board_name": data.get("board-name"),
            "version": data.get("version"),
            "architecture": data.get("architecture-name"),
            "platform": data.get("platform"),
            "cpu": data.get("cpu"),
            "cpu_count": to_int(data.get("cpu-count")),
            "cpu_frequency_mhz": to_int(data.get("cpu-frequency")),
            "cpu_load_percent": to_int(data.get("cpu-load"), 0),
            "memory_total_bytes": total_mem,
            "memory_free_bytes": free_mem,
            "memory_used_bytes": used_mem,
            "memory_used_percent": round(used_mem / total_mem * 100, 1)
            if used_mem is not None and total_mem
            else None,
            "memory_total_human": human_bytes(total_mem),
            "memory_used_human": human_bytes(used_mem),
            "disk_total_bytes": total_hdd,
            "disk_free_bytes": free_hdd,
            "disk_used_percent": round((total_hdd - free_hdd) / total_hdd * 100, 1)
            if total_hdd and free_hdd is not None
            else None,
            "uptime": data.get("uptime"),
            "uptime_seconds": parse_uptime(data.get("uptime")),
            "transport": self.client.transport,
        }

    async def routerboard(self) -> Dict[str, Any]:
        try:
            rows = await self.client.print("system/routerboard")
        except MikrotikCommandError:
            return {}
        data = rows[0] if rows else {}
        return {
            "model": data.get("model"),
            "serial_number": data.get("serial-number"),
            "firmware_type": data.get("firmware-type"),
            "current_firmware": data.get("current-firmware"),
            "upgrade_firmware": data.get("upgrade-firmware"),
        }

    # ------------------------------------------------------------ interfaces

    async def interfaces(self) -> List[Dict[str, Any]]:
        rows = await self.client.print("interface")
        result = []
        for row in rows:
            rx = to_int(row.get("rx-byte"))
            tx = to_int(row.get("tx-byte"))
            result.append(
                {
                    "id": row.get(".id"),
                    "name": row.get("name"),
                    "type": row.get("type"),
                    "comment": row.get("comment"),
                    "mac_address": row.get("mac-address"),
                    "mtu": to_int(row.get("mtu")) or to_int(row.get("actual-mtu")),
                    "running": to_bool(row.get("running")),
                    "disabled": to_bool(row.get("disabled")),
                    "rx_bytes": rx,
                    "tx_bytes": tx,
                    "rx_human": human_bytes(rx),
                    "tx_human": human_bytes(tx),
                    "rx_packets": to_int(row.get("rx-packet")),
                    "tx_packets": to_int(row.get("tx-packet")),
                    "link_downs": to_int(row.get("link-downs")),
                    "last_link_up_time": row.get("last-link-up-time"),
                }
            )
        return result

    async def interface_traffic(self, interface: str) -> Dict[str, Any]:
        """Tasa instantánea de una interfaz (`/interface/monitor-traffic once`)."""
        rows = await self.client.run(
            "interface", "monitor-traffic", {"interface": interface, "once": True}
        )
        data = rows[0] if rows else {}
        rx_bps = to_int(data.get("rx-bits-per-second"), 0) or 0
        tx_bps = to_int(data.get("tx-bits-per-second"), 0) or 0
        return {
            "interface": data.get("name") or interface,
            "rx_bits_per_second": rx_bps,
            "tx_bits_per_second": tx_bps,
            "rx_mbps": round(rx_bps / 1_000_000, 3),
            "tx_mbps": round(tx_bps / 1_000_000, 3),
            "rx_packets_per_second": to_int(data.get("rx-packets-per-second"), 0),
            "tx_packets_per_second": to_int(data.get("tx-packets-per-second"), 0),
        }

    async def ip_addresses(self) -> List[Dict[str, Any]]:
        rows = await self.client.print("ip/address")
        return [
            {
                "id": row.get(".id"),
                "address": row.get("address"),
                "network": row.get("network"),
                "interface": row.get("interface"),
                "disabled": to_bool(row.get("disabled")),
                "comment": row.get("comment"),
            }
            for row in rows
        ]

    # ----------------------------------------------------------------- PPPoE

    async def pppoe_active(self) -> List[Dict[str, Any]]:
        rows = await self.client.print("ppp/active")
        return [
            {
                "id": row.get(".id"),
                "username": row.get("name"),
                "service": row.get("service"),
                "address": row.get("address"),
                "caller_id": row.get("caller-id"),
                "uptime": row.get("uptime"),
                "uptime_seconds": parse_uptime(row.get("uptime")),
                "encoding": row.get("encoding"),
                "session_id": row.get("session-id"),
                "limit_bytes_in": to_int(row.get("limit-bytes-in")),
                "limit_bytes_out": to_int(row.get("limit-bytes-out")),
                "comment": row.get("comment"),
            }
            for row in rows
        ]

    async def pppoe_secrets(self) -> List[Dict[str, Any]]:
        rows = await self.client.print("ppp/secret")
        return [
            {
                "id": row.get(".id"),
                "username": row.get("name"),
                "service": row.get("service"),
                "profile": row.get("profile"),
                "remote_address": row.get("remote-address"),
                "local_address": row.get("local-address"),
                "disabled": to_bool(row.get("disabled")),
                "comment": row.get("comment"),
                "last_logged_out": row.get("last-logged-out"),
            }
            for row in rows
        ]

    async def ppp_profiles(self) -> List[Dict[str, Any]]:
        rows = await self.client.print("ppp/profile")
        return [
            {
                "id": row.get(".id"),
                "name": row.get("name"),
                "local_address": row.get("local-address"),
                "remote_address": row.get("remote-address"),
                "rate_limit": row.get("rate-limit"),
                "address_list": row.get("address-list"),
            }
            for row in rows
        ]

    async def pppoe_overview(self) -> Dict[str, Any]:
        """Secrets + sesiones activas cruzados: quién está online y quién no."""
        secrets = await self.pppoe_secrets()
        active = await self.pppoe_active()
        by_user = {session["username"]: session for session in active if session.get("username")}

        clients = []
        for secret in secrets:
            session = by_user.get(secret["username"])
            clients.append(
                {
                    **secret,
                    "online": session is not None,
                    "session": session,
                    "status": "suspendido"
                    if secret["disabled"]
                    else ("online" if session else "offline"),
                }
            )

        # Sesiones activas sin secret local (autenticación por RADIUS, por ejemplo)
        known = {secret["username"] for secret in secrets}
        for username, session in by_user.items():
            if username not in known:
                clients.append(
                    {
                        "id": None,
                        "username": username,
                        "service": session.get("service"),
                        "profile": None,
                        "remote_address": session.get("address"),
                        "local_address": None,
                        "disabled": False,
                        "comment": None,
                        "last_logged_out": None,
                        "online": True,
                        "session": session,
                        "status": "online",
                    }
                )

        return {
            "total_secrets": len(secrets),
            "online": sum(1 for c in clients if c["online"]),
            "offline": sum(1 for c in clients if not c["online"] and not c["disabled"]),
            "suspended": sum(1 for c in clients if c["disabled"]),
            "clients": sorted(clients, key=lambda c: (not c["online"], c["username"] or "")),
        }

    # ---------------------------------------------------- suspensión / corte

    async def _find_secret(self, username: str) -> Dict[str, str]:
        rows = await self.client.print("ppp/secret", query={"name": username})
        # RouterOS 6 ignora algunos filtros por REST; filtramos también aquí.
        match = next((row for row in rows if row.get("name") == username), None)
        if match is None:
            raise MikrotikCommandError(
                f"No existe un secret PPPoE llamado '{username}' en este router",
                command="ppp/secret",
            )
        return match

    async def kick_session(self, username: str) -> bool:
        """Cierra la sesión PPPoE activa para que el cambio aplique al instante."""
        rows = await self.client.print("ppp/active", query={"name": username})
        session = next((row for row in rows if row.get("name") == username), None)
        if not session or not session.get(".id"):
            return False
        await self.client.remove("ppp/active", session[".id"])
        return True

    async def suspend_customer(
        self,
        username: str,
        method: str = "disable_secret",
        suspended_profile: str = "CORTADO",
        address_list: str = "morosos",
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Suspende un cliente PPPoE.

        method:
          disable_secret  -> deshabilita el secret (no puede volver a autenticar)
          change_profile  -> mueve el secret a un perfil de corte (portal de aviso)
          address_list    -> agrega su IP a una address-list de morosos
        """
        secret = await self._find_secret(username)
        secret_id = secret[".id"]
        previous: Dict[str, Any] = {"profile": secret.get("profile"), "disabled": to_bool(secret.get("disabled"))}
        actions: List[str] = []

        if method == "disable_secret":
            await self.client.set("ppp/secret", secret_id, {"disabled": True})
            actions.append("secret deshabilitado")
        elif method == "change_profile":
            await self.client.set("ppp/secret", secret_id, {"profile": suspended_profile})
            actions.append(f"perfil cambiado a '{suspended_profile}'")
        elif method == "address_list":
            rows = await self.client.print("ppp/active", query={"name": username})
            session = next((row for row in rows if row.get("name") == username), None)
            ip = (session or {}).get("address") or secret.get("remote-address")
            if not ip:
                raise MikrotikCommandError(
                    f"No se pudo determinar la IP de '{username}' para agregarla a la address-list",
                    command="ip/firewall/address-list",
                )
            await self.client.add(
                "ip/firewall/address-list",
                {"list": address_list, "address": ip, "comment": comment or f"Suspendido: {username}"},
            )
            previous["address"] = ip
            actions.append(f"IP {ip} agregada a la lista '{address_list}'")
        else:
            raise ValueError(f"Método de suspensión desconocido: {method}")

        if comment is not None and method != "address_list":
            await self.client.set("ppp/secret", secret_id, {"comment": comment})

        kicked = await self.kick_session(username)
        if kicked:
            actions.append("sesión activa cerrada")

        return {
            "username": username,
            "method": method,
            "suspended": True,
            "session_closed": kicked,
            "previous_state": previous,
            "actions": actions,
        }

    async def restore_customer(
        self,
        username: str,
        method: str = "disable_secret",
        active_profile: Optional[str] = None,
        address_list: str = "morosos",
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reactiva un cliente suspendido y cierra la sesión de corte si la hay."""
        secret = await self._find_secret(username)
        secret_id = secret[".id"]
        actions: List[str] = []

        if method == "disable_secret":
            await self.client.set("ppp/secret", secret_id, {"disabled": False})
            actions.append("secret habilitado")
        elif method == "change_profile":
            if not active_profile:
                raise ValueError("Para reactivar por perfil hay que indicar el perfil activo del plan")
            await self.client.set("ppp/secret", secret_id, {"profile": active_profile})
            actions.append(f"perfil restaurado a '{active_profile}'")
        elif method == "address_list":
            rows = await self.client.print("ip/firewall/address-list", query={"list": address_list})
            session_rows = await self.client.print("ppp/active", query={"name": username})
            session = next((row for row in session_rows if row.get("name") == username), None)
            candidates = {
                (session or {}).get("address"),
                secret.get("remote-address"),
            }
            removed = 0
            for row in rows:
                if row.get("list") != address_list:
                    continue
                if row.get("address") in candidates or username in (row.get("comment") or ""):
                    await self.client.remove("ip/firewall/address-list", row[".id"])
                    removed += 1
            actions.append(f"{removed} entrada(s) eliminadas de '{address_list}'")
        else:
            raise ValueError(f"Método de reactivación desconocido: {method}")

        if comment is not None:
            await self.client.set("ppp/secret", secret_id, {"comment": comment})

        kicked = await self.kick_session(username)
        if kicked:
            actions.append("sesión de corte cerrada para forzar reconexión")

        return {
            "username": username,
            "method": method,
            "suspended": False,
            "session_closed": kicked,
            "actions": actions,
        }

    # ------------------------------------------------------------- resumen

    async def dashboard(self) -> Dict[str, Any]:
        resource = await self.system_resource()
        pppoe = await self.pppoe_overview()
        ifaces = await self.interfaces()
        return {
            "resource": resource,
            "pppoe": {
                "total_secrets": pppoe["total_secrets"],
                "online": pppoe["online"],
                "offline": pppoe["offline"],
                "suspended": pppoe["suspended"],
            },
            "interfaces": {
                "total": len(ifaces),
                "running": sum(1 for i in ifaces if i["running"]),
                "down": sum(1 for i in ifaces if not i["running"] and not i["disabled"]),
                "disabled": sum(1 for i in ifaces if i["disabled"]),
            },
        }

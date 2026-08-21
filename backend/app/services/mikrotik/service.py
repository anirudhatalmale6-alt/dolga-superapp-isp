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

    async def health(self) -> Dict[str, Any]:
        """Temperatura y voltaje de `/system/health`.

        Ojo: solo las placas con sensor entregan estos valores. Un RB4011 sí
        reporta temperatura; un RB750Gr3 o un RB2011 no tienen sensor y el menú
        devuelve vacío. Por eso `available` puede ser False y eso NO es un
        error: es un dato que ese hardware no puede dar. Nunca inventamos un
        número en ese caso.
        """
        try:
            rows = await self.client.print("system/health")
        except MikrotikCommandError:
            # RouterOS 6 en placas sin sensores ni siquiera expone el menú.
            return {"available": False, "temperature_c": None, "voltage_v": None}

        values: Dict[str, str] = {}
        for row in rows:
            if "name" in row and "value" in row:
                # RouterOS 7: una fila por lectura -> {"name": "temperature", "value": "59"}
                values[row["name"]] = row["value"]
            else:
                # RouterOS 6: una sola fila con todas las claves.
                values.update({k: v for k, v in row.items() if not k.startswith(".")})

        def _num(*keys: str) -> Optional[float]:
            for key in keys:
                if key in values:
                    try:
                        return float(str(values[key]).strip())
                    except ValueError:
                        continue
            return None

        temperature = _num("temperature", "board-temperature1", "board-temperature")
        cpu_temperature = _num("cpu-temperature")
        # Si la placa solo reporta la del CPU, esa es la que mostramos.
        if temperature is None:
            temperature = cpu_temperature

        return {
            "available": temperature is not None,
            "temperature_c": temperature,
            "cpu_temperature_c": cpu_temperature,
            "voltage_v": _num("voltage"),
            "raw": values or None,
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

    async def routes(self) -> List[Dict[str, Any]]:
        rows = await self.client.print("ip/route")
        return [
            {
                "id": row.get(".id"),
                "dst_address": row.get("dst-address"),
                "gateway": row.get("gateway"),
                "immediate_gw": row.get("immediate-gw"),
                "distance": to_int(row.get("distance")),
                "active": to_bool(row.get("active")),
                "dynamic": to_bool(row.get("dynamic")),
                "comment": row.get("comment"),
            }
            for row in rows
        ]

    @staticmethod
    def _interface_of_route(route: Dict[str, Any], addresses: List[Dict[str, Any]]) -> Optional[str]:
        """Deduce por qué interfaz sale una ruta.

        RouterOS 7 trae `immediate-gw` con la forma `192.168.1.254%ether1`.
        En RouterOS 6 hay que cruzar el gateway contra las redes de /ip/address.
        """
        immediate = route.get("immediate_gw") or ""
        if "%" in immediate:
            return immediate.split("%", 1)[1]

        gateway = (route.get("gateway") or "").split(",")[0].strip()
        if not gateway:
            return None
        # El gateway puede ser directamente el nombre de una interfaz (PPPoE cliente).
        for address in addresses:
            if address["interface"] == gateway:
                return gateway
        # Si no, buscamos la interfaz cuya red contiene al gateway.
        for address in addresses:
            network = (address.get("address") or "").split("/")[0]
            if not network:
                continue
            prefix = ".".join(network.split(".")[:3])
            if prefix and gateway.startswith(prefix + "."):
                return address["interface"]
        return None

    async def wan_interfaces(self, measure_traffic: bool = True) -> List[Dict[str, Any]]:
        """Interfaces que llevan tráfico a la calle, con su IP, gateway y velocidad.

        Se consideran WAN las interfaces por donde sale una ruta por defecto
        (0.0.0.0/0). Se listan también las inactivas —un respaldo LTE caído, por
        ejemplo— porque justamente ese estado es lo que hay que ver.
        """
        addresses = await self.ip_addresses()
        routes = await self.routes()
        interfaces = {i["name"]: i for i in await self.interfaces()}

        defaults = [r for r in routes if (r.get("dst_address") or "").startswith("0.0.0.0/0")]
        by_address = {a["interface"]: a for a in addresses if not a["disabled"]}

        result: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for route in sorted(defaults, key=lambda r: (r.get("distance") or 0)):
            name = self._interface_of_route(route, addresses)
            if not name or name in seen:
                continue
            seen.add(name)
            iface = interfaces.get(name, {})
            address = by_address.get(name, {})
            entry = {
                "interface": name,
                "comment": iface.get("comment"),
                "running": iface.get("running", False),
                "disabled": iface.get("disabled", False),
                "connected": bool(iface.get("running")) and route.get("active", False),
                "address": address.get("address"),
                "gateway": (route.get("gateway") or "").split(",")[0].strip() or None,
                "distance": route.get("distance"),
                "rx_mbps": None,
                "tx_mbps": None,
            }
            if measure_traffic and entry["running"]:
                try:
                    traffic = await self.interface_traffic(name)
                    entry["rx_mbps"] = traffic["rx_mbps"]
                    entry["tx_mbps"] = traffic["tx_mbps"]
                except MikrotikCommandError:
                    pass
            else:
                entry["rx_mbps"] = 0.0
                entry["tx_mbps"] = 0.0
            result.append(entry)
        return result

    async def primary_wan(self) -> Optional[str]:
        """Nombre de la interfaz WAN principal (la ruta por defecto de menor distancia)."""
        wans = await self.wan_interfaces(measure_traffic=False)
        for wan in wans:
            if wan["running"]:
                return wan["interface"]
        return wans[0]["interface"] if wans else None

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

    # ------------------------------------------------------- mantenimiento

    async def reboot(self) -> Dict[str, Any]:
        """Reinicia el equipo. Vuelve solo en 1-2 minutos."""
        await self.client.run("system", "reboot")
        return {"action": "reboot", "ok": True}

    async def shutdown(self) -> Dict[str, Any]:
        """Apaga el equipo.

        A diferencia de reiniciar, el equipo NO vuelve solo: hay que ir al sitio
        a darle corriente. Por eso la API lo expone detrás del rol admin y de una
        confirmación explícita.
        """
        await self.client.run("system", "shutdown")
        return {"action": "shutdown", "ok": True}

    async def check_updates(self) -> Dict[str, Any]:
        """Consulta si hay una versión nueva de RouterOS. No instala nada."""
        try:
            await self.client.run("system/package/update", "check-for-updates")
            rows = await self.client.print("system/package/update")
        except MikrotikCommandError as exc:
            return {"available": False, "error": str(exc)}
        data = rows[0] if rows else {}
        installed = data.get("installed-version")
        latest = data.get("latest-version")
        return {
            "channel": data.get("channel"),
            "installed_version": installed,
            "latest_version": latest,
            "update_available": bool(latest and installed and latest != installed),
            "status": data.get("status"),
        }

    # ------------------------------------------------------------- resumen

    async def snapshot(self, wan_interface: Optional[str] = None) -> Dict[str, Any]:
        """Una lectura compacta del equipo, pensada para la tabla de la flota y
        para el servicio de monitoreo que guarda el histórico.

        Hace el mínimo de consultas posible porque se ejecuta contra todos los
        equipos cada pocos segundos.
        """
        resource = await self.system_resource()
        health = await self.health()
        board = await self.routerboard()
        active = await self.pppoe_active()

        wan = wan_interface
        if not wan:
            wan = await self.primary_wan()

        rx_mbps = tx_mbps = None
        if wan:
            try:
                traffic = await self.interface_traffic(wan)
                rx_mbps = traffic["rx_mbps"]
                tx_mbps = traffic["tx_mbps"]
            except MikrotikCommandError:
                pass

        return {
            "identity": resource["identity"],
            "model": board.get("model") or resource.get("board_name"),
            "serial_number": board.get("serial_number"),
            "version": resource["version"],
            "uptime": resource["uptime"],
            "uptime_seconds": resource["uptime_seconds"],
            "cpu_load_percent": resource["cpu_load_percent"],
            "memory_used_percent": resource["memory_used_percent"],
            "memory_used_human": resource["memory_used_human"],
            "memory_total_human": resource["memory_total_human"],
            "disk_used_percent": resource["disk_used_percent"],
            # None cuando la placa no tiene sensor: el frontend muestra "n/d".
            "temperature_c": health["temperature_c"],
            "temperature_available": health["available"],
            "clients_online": len(active),
            "wan_interface": wan,
            "wan_rx_mbps": rx_mbps,
            "wan_tx_mbps": tx_mbps,
            "transport": self.client.transport,
        }

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

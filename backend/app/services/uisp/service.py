"""Traduce lo que devuelve UISP a estructuras estables para el frontend.

UISP cambia de forma entre versiones y no todos los equipos traen los mismos
campos: una antena airMAX reporta señal y frecuencia, un switch no. Por eso
aquí nunca se asume que un campo existe. Lo que el equipo no reporta queda en
None y la interfaz muestra "n/d", igual que hicimos con la temperatura de los
MikroTik sin sensor.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .client import UispClient
from .exceptions import UispRequestError

# Roles de UISP que nos interesan.
ROLE_AP = "ap"
ROLE_STATION = "station"

# Umbrales de señal para airMAX en 5 GHz. Son una regla práctica de operación,
# no un valor que reporte el equipo: el dBm crudo se muestra siempre al lado
# para que el técnico juzgue por su cuenta.
SIGNAL_THRESHOLDS = (
    (-60, "excelente"),
    (-70, "buena"),
    (-80, "regular"),
)


def signal_quality(dbm: Optional[float]) -> Optional[str]:
    if dbm is None:
        return None
    for limit, label in SIGNAL_THRESHOLDS:
        if dbm >= limit:
            return label
    return "mala"


def to_float(value: Any) -> Optional[float]:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> Optional[int]:
    number = to_float(value)
    return int(number) if number is not None else None


def bps_to_mbps(value: Any) -> Optional[float]:
    number = to_float(value)
    return round(number / 1_000_000, 2) if number is not None else None


class UispService:
    def __init__(self, client: UispClient) -> None:
        self.client = client

    # ------------------------------------------------------------- normalizado

    @staticmethod
    def _device(raw: Dict[str, Any]) -> Dict[str, Any]:
        ident = raw.get("identification") or {}
        overview = raw.get("overview") or {}
        attributes = raw.get("attributes") or {}
        site = ident.get("site") or {}
        ap = attributes.get("apDevice") or {}

        signal = to_float(overview.get("signal"))
        status = (overview.get("status") or ident.get("status") or "").lower()

        return {
            "id": ident.get("id"),
            "name": ident.get("displayName") or ident.get("name"),
            "mac": ident.get("mac"),
            "model": ident.get("modelName") or ident.get("model"),
            "type": ident.get("type"),
            "role": (ident.get("role") or "").lower() or None,
            "firmware": ident.get("firmwareVersion"),
            "status": status or None,
            "online": status == "active",
            "site_id": site.get("id"),
            "site_name": site.get("name"),
            "ip_address": raw.get("ipAddress"),
            "cpu_percent": to_float(overview.get("cpu")),
            "ram_percent": to_float(overview.get("ram")),
            "uptime_seconds": to_int(overview.get("uptime")),
            "last_seen": overview.get("lastSeen"),
            # Radio. Un switch o un router no traen nada de esto y queda en None.
            "signal_dbm": signal,
            "signal_quality": signal_quality(signal),
            "remote_signal_dbm": to_float(overview.get("remoteSignal")),
            "frequency_mhz": to_int(overview.get("frequency")),
            "channel_width_mhz": to_int(overview.get("channelWidth")),
            "wireless_mode": overview.get("wirelessMode"),
            "transmit_power_dbm": to_float(overview.get("transmitPower")),
            "distance_m": to_int(overview.get("distance")),
            "ssid": attributes.get("ssid"),
            "downlink_capacity_mbps": bps_to_mbps(overview.get("downlinkCapacity")),
            "uplink_capacity_mbps": bps_to_mbps(overview.get("uplinkCapacity")),
            "downlink_utilization": to_float(overview.get("downlinkUtilization")),
            "uplink_utilization": to_float(overview.get("uplinkUtilization")),
            "stations_count": to_int(overview.get("stationsCount")),
            # Para una estación, a qué sector está asociada.
            "ap_id": ap.get("id"),
            "ap_name": ap.get("name"),
        }

    @staticmethod
    def _site(raw: Dict[str, Any]) -> Dict[str, Any]:
        ident = raw.get("identification") or {}
        description = raw.get("description") or {}
        location = description.get("location") or {}
        return {
            "id": ident.get("id"),
            "name": ident.get("name"),
            "type": ident.get("type"),
            "status": ident.get("status"),
            "address": description.get("address"),
            "latitude": to_float(location.get("latitude")),
            "longitude": to_float(location.get("longitude")),
        }

    # ---------------------------------------------------------------- consultas

    async def sites(self) -> List[Dict[str, Any]]:
        rows = await self.client.get_list("/sites")
        return [self._site(row) for row in rows]

    async def devices(self) -> List[Dict[str, Any]]:
        rows = await self.client.get_list("/devices")
        return [self._device(row) for row in rows]

    async def device(self, device_id: str) -> Dict[str, Any]:
        raw = await self.client.get(f"/devices/{device_id}")
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        return self._device(raw or {})

    async def interfaces(self, device_id: str) -> List[Dict[str, Any]]:
        try:
            rows = await self.client.get_list(f"/devices/{device_id}/interfaces")
        except UispRequestError:
            # No todos los tipos de equipo exponen interfaces en UISP.
            return []
        result = []
        for row in rows:
            ident = row.get("identification") or {}
            status = row.get("status") or {}
            statistics = row.get("statistics") or {}
            result.append(
                {
                    "name": ident.get("displayName") or ident.get("name"),
                    "type": ident.get("type"),
                    "mac": ident.get("mac"),
                    "enabled": status.get("enabled"),
                    "plugged": status.get("plugged"),
                    "speed_mbps": to_int((status.get("speed") or "").replace("Mbps", "").strip())
                    if isinstance(status.get("speed"), str)
                    else to_int(status.get("speed")),
                    "rx_mbps": bps_to_mbps(statistics.get("rxrate")),
                    "tx_mbps": bps_to_mbps(statistics.get("txrate")),
                    "rx_bytes": to_int(statistics.get("rxbytes")),
                    "tx_bytes": to_int(statistics.get("txbytes")),
                    "errors": to_int(statistics.get("errors")),
                    "dropped": to_int(statistics.get("dropped")),
                }
            )
        return result

    async def statistics(self, device_id: str, interval: str = "hour") -> Dict[str, Any]:
        """Serie de tiempo que UISP ya guarda por su cuenta.

        A diferencia de MikroTik, aquí no hace falta que nosotros muestreemos:
        UISP lleva su propio histórico y nos lo entrega ya calculado.
        """
        try:
            raw = await self.client.get(
                f"/devices/{device_id}/statistics", params={"interval": interval}
            )
        except UispRequestError as exc:
            return {"available": False, "reason": str(exc), "series": {}}

        if not isinstance(raw, dict):
            return {"available": False, "reason": "respuesta inesperada", "series": {}}

        def _series(key: str) -> List[Dict[str, Any]]:
            points = raw.get(key)
            if not isinstance(points, list):
                return []
            cleaned = []
            for point in points:
                if not isinstance(point, dict):
                    continue
                cleaned.append({"t": point.get("x"), "v": to_float(point.get("y"))})
            return cleaned

        return {
            "available": True,
            "interval": interval,
            "series": {
                "cpu": _series("cpu"),
                "ram": _series("ram"),
                "signal": _series("signal"),
                "ping": _series("ping"),
                "receive_rate": _series("receiveRate"),
                "transmit_rate": _series("transmitRate"),
            },
        }

    # ------------------------------------------------------------------ vistas

    async def topology(self) -> Dict[str, Any]:
        """Sectores con sus estaciones colgando, que es como se opera un WISP.

        El conteo de clientes por sector se calcula cruzando las estaciones
        contra su `apDevice`, no se toma del campo `stationsCount` del equipo:
        ese campo no siempre viene, y cuando viene puede estar rezagado.
        """
        devices = await self.devices()
        sites = {site["id"]: site for site in await self.sites()}

        sectors = [d for d in devices if d["role"] == ROLE_AP]
        stations = [d for d in devices if d["role"] == ROLE_STATION]
        others = [d for d in devices if d["role"] not in (ROLE_AP, ROLE_STATION)]

        by_ap: Dict[str, List[Dict[str, Any]]] = {}
        huerfanas: List[Dict[str, Any]] = []
        for station in stations:
            if station["ap_id"]:
                by_ap.setdefault(station["ap_id"], []).append(station)
            else:
                # Una estación sin sector asociado casi siempre está desconectada.
                huerfanas.append(station)

        sector_rows = []
        for sector in sectors:
            asociadas = by_ap.get(sector["id"], [])
            online = [s for s in asociadas if s["online"]]
            señales = [s["signal_dbm"] for s in online if s["signal_dbm"] is not None]
            sector_rows.append(
                {
                    **sector,
                    "site": sites.get(sector["site_id"]),
                    "clients_total": len(asociadas),
                    "clients_online": len(online),
                    "clients_offline": len(asociadas) - len(online),
                    "worst_signal_dbm": min(señales) if señales else None,
                    "average_signal_dbm": round(sum(señales) / len(señales), 1) if señales else None,
                }
            )

        sector_rows.sort(key=lambda s: (not s["online"], -(s["clients_online"] or 0)))

        return {
            "sectors": sector_rows,
            "stations": stations,
            "stations_without_sector": huerfanas,
            "other_devices": others,
            "sites": list(sites.values()),
        }

    async def stations_of(self, ap_id: str) -> List[Dict[str, Any]]:
        stations = [d for d in await self.devices() if d["role"] == ROLE_STATION]
        asociadas = [s for s in stations if s["ap_id"] == ap_id]
        # Peor señal primero: es la lista de a quién hay que ir a revisar.
        asociadas.sort(key=lambda s: (s["signal_dbm"] is None, s["signal_dbm"] or 0))
        return asociadas

    async def overview(self) -> Dict[str, Any]:
        data = await self.topology()
        sectors = data["sectors"]
        stations = data["stations"]
        online_stations = [s for s in stations if s["online"]]
        señales = [s["signal_dbm"] for s in online_stations if s["signal_dbm"] is not None]

        # Clientes con señal pobre: la lista corta de a quién visitar.
        débiles = [s for s in online_stations if s["signal_dbm"] is not None and s["signal_dbm"] < -80]

        return {
            "sites_total": len(data["sites"]),
            "sectors_total": len(sectors),
            "sectors_online": sum(1 for s in sectors if s["online"]),
            "sectors_offline": sum(1 for s in sectors if not s["online"]),
            "stations_total": len(stations),
            "stations_online": len(online_stations),
            "stations_offline": len(stations) - len(online_stations),
            "other_devices_total": len(data["other_devices"]),
            "average_signal_dbm": round(sum(señales) / len(señales), 1) if señales else None,
            "weak_signal_count": len(débiles),
            "weak_signal_stations": sorted(débiles, key=lambda s: s["signal_dbm"])[:20],
        }

    # ------------------------------------------------------------- acciones

    # Lo que la API de UISP permite hacer sobre un equipo. La configuración de
    # radio (frecuencia, potencia, ancho de canal, SSID) NO está aquí: eso vive
    # en airOS, dentro de la antena, y UISP no lo expone por API. Si el cliente
    # lo necesita hay que hablar con airOS directamente, y es otro trabajo.
    ACTIONS = {
        "reboot": ("POST", "/devices/{id}/system/reboot", "Reinicio enviado al equipo."),
        "locate": (
            "POST",
            "/devices/{id}/locate",
            "El equipo empezará a parpadear sus LED para poder ubicarlo en la torre.",
        ),
        "upgrade": (
            "POST",
            "/devices/{id}/system/upgrade",
            "Actualización de firmware enviada. El equipo se reinicia al terminar.",
        ),
    }

    async def action(self, device_id: str, action: str) -> Dict[str, Any]:
        if action not in self.ACTIONS:
            raise ValueError(f"Acción desconocida para un equipo Ubiquiti: {action}")
        method, template, message = self.ACTIONS[action]
        await self.client.request(method, template.format(id=device_id))
        return {"device_id": device_id, "action": action, "ok": True, "message": message}

    async def reboot(self, device_id: str) -> Dict[str, Any]:
        return await self.action(device_id, "reboot")

    async def locate(self, device_id: str) -> Dict[str, Any]:
        """Hace parpadear los LED del equipo para encontrarlo físicamente."""
        return await self.action(device_id, "locate")

    async def upgrade(self, device_id: str) -> Dict[str, Any]:
        return await self.action(device_id, "upgrade")

    async def ping_check(self) -> Dict[str, Any]:
        """Prueba de conexión: confirma que la URL y el token sirven."""
        sites = await self.client.get_list("/sites")
        devices = await self.client.get_list("/devices")
        return {
            "ok": True,
            "sites": len(sites),
            "devices": len(devices),
            "base_url": self.client.base_url,
        }

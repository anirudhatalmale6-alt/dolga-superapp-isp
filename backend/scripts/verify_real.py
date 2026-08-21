"""Prueba contra equipos reales, para comparar contra UISP o Winbox.

Este script no levanta la plataforma: usa los mismos conectores que usa el
backend y muestra, uno al lado del otro:

    * la llamada exacta que salió hacia el equipo (URL o sentencia, código,
      demora, cuántas filas volvieron),
    * el dato crudo tal como lo entregó el equipo,
    * lo que la plataforma calcula a partir de ese dato.

La idea es sencilla: se corre esto y se compara con lo que muestra UISP o
Winbox en la misma pantalla. Si coincide, el dato es real.

Se puede correr desde la propia red del ISP; no hace falta darle acceso a
nadie. Nada sale de esta máquina: no envía ninguna información a ningún lado,
y las credenciales solo viven en las variables de entorno de la corrida.

    UISP:
        export UISP_URL=uisp.midominio.net
        export UISP_KEY=<App Key de solo lectura>
        python -m scripts.verify_real uisp

    MikroTik:
        export MT_HOST=192.168.88.1
        export MT_USER=consulta
        export MT_PASS=<clave>
        python -m scripts.verify_real mikrotik

Con `--json salida.json` guarda todo en un archivo (las credenciales no se
incluyen) por si conviene revisarlo después.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.audit import Exchange  # noqa: E402
from app.services.mikrotik.client import MikrotikClient  # noqa: E402
from app.services.mikrotik.service import MikrotikService  # noqa: E402
from app.services.uisp.client import UispClient  # noqa: E402
from app.services.uisp.service import UispService  # noqa: E402

LLAMADAS: List[Exchange] = []


def anotar(exchange: Exchange) -> None:
    LLAMADAS.append(exchange)
    print(f"   {exchange.as_line()}")


def titulo(texto: str) -> None:
    print(f"\n{texto}\n{'-' * len(texto)}")


def falta(*nombres: str) -> None:
    print("Faltan variables de entorno: " + ", ".join(nombres))
    raise SystemExit(2)


# ----------------------------------------------------------------- Ubiquiti


async def probar_uisp() -> Dict[str, Any]:
    url = os.environ.get("UISP_URL")
    key = os.environ.get("UISP_KEY")
    if not url or not key:
        falta("UISP_URL", "UISP_KEY")

    verify = os.environ.get("UISP_VERIFY_TLS", "0") == "1"
    client = UispClient(base_url=url, token=key, verify_tls=verify, timeout=30.0, on_exchange=anotar)
    svc = UispService(client)

    try:
        titulo("1. Conexión")
        print(f"   Servidor: {client.base_url}")
        ping = await svc.ping_check()
        print(f"   Respuesta: {ping}")

        titulo("2. Equipos que UISP tiene adoptados")
        equipos = await svc.devices()
        por_rol: Dict[str, int] = {}
        for equipo in equipos:
            por_rol[equipo["role"]] = por_rol.get(equipo["role"], 0) + 1
        print(f"   Total: {len(equipos)}")
        for rol, cantidad in sorted(por_rol.items()):
            print(f"     {rol}: {cantidad}")

        titulo("3. Resumen (compáralo con la pantalla principal de UISP)")
        resumen = await svc.overview()
        for clave, valor in resumen.items():
            # Algunas claves traen la lista completa de equipos flojos; en la
            # pantalla sobra, alcanza con cuántos son.
            if isinstance(valor, list):
                print(f"   {clave}: {len(valor)} equipo(s)")
            else:
                print(f"   {clave}: {valor}")

        titulo("4. Sectores y clientes por sector")
        topologia = await svc.topology()
        for sector in topologia["sectors"]:
            estado = "EN LÍNEA" if sector["online"] else "CAÍDO"
            print(
                f"   {sector['name']:<28} {estado:<9} "
                f"clientes {sector['clients_online']}/{sector['clients_total']}  "
                f"señal media {sector['average_signal_dbm']}  peor {sector['worst_signal_dbm']}"
            )
        huerfanas = topologia.get("stations_without_sector") or []
        if huerfanas:
            print(f"   ({len(huerfanas)} estación(es) sin sector asociado en UISP)")

        titulo("5. Clientes del primer sector, de peor a mejor señal")
        if topologia["sectors"]:
            primero = topologia["sectors"][0]
            estaciones = await svc.stations_of(primero["id"])
            print(f"   Sector: {primero['name']}")
            for estacion in estaciones[:15]:
                print(
                    f"     {estacion['name']:<30} {str(estacion['signal_dbm']):>6} dBm  "
                    f"{estacion['signal_quality'] or 'n/d':<10} "
                    f"{'en línea' if estacion['online'] else 'desconectado'}"
                )
            if len(estaciones) > 15:
                print(f"     … y {len(estaciones) - 15} más")
        else:
            print("   UISP no reporta ningún equipo con rol de sector.")

        return {
            "tipo": "uisp",
            "servidor": client.base_url,
            "resumen": resumen,
            "sectores": topologia["sectors"],
            "equipos_por_rol": por_rol,
        }
    finally:
        await client.close()


# ----------------------------------------------------------------- MikroTik


async def probar_mikrotik() -> Dict[str, Any]:
    host = os.environ.get("MT_HOST")
    usuario = os.environ.get("MT_USER")
    clave = os.environ.get("MT_PASS")
    if not host or not usuario or clave is None:
        falta("MT_HOST", "MT_USER", "MT_PASS")

    client = MikrotikClient(
        host=host,
        username=usuario,
        password=clave,
        mode=os.environ.get("MT_MODE", "auto"),
        api_port=int(os.environ.get("MT_API_PORT", "8728")),
        rest_port=int(os.environ.get("MT_REST_PORT", "443")),
        timeout=15.0,
        on_exchange=anotar,
    )
    svc = MikrotikService(client)

    try:
        titulo("1. Conexión")
        ping = await client.ping_check()
        print(f"   Transporte usado: {client.transport}")
        print(f"   Identidad: {ping['identity']}  RouterOS {ping['version']}  ({ping['board_name']})")

        titulo("2. Recursos (compáralo con System > Resources en Winbox)")
        recurso = await svc.system_resource()
        print(f"   CPU: {recurso['cpu_load_percent']}%  ({recurso['cpu_count']} núcleo(s))")
        print(
            f"   Memoria: {recurso['memory_used_human']} de {recurso['memory_total_human']} "
            f"({recurso['memory_used_percent']}%)"
        )
        print(f"   Uptime: {recurso['uptime']}")
        salud = await svc.health()
        if salud.get("available"):
            print(f"   Temperatura: {salud.get('temperature_c')} °C")
        else:
            # No todas las placas traen sensor. Se dice, no se inventa un número.
            print("   Temperatura: n/d (esta placa no tiene sensor)")

        titulo("3. Interfaces (compáralo con Interfaces en Winbox)")
        interfaces = await svc.interfaces()
        for iface in interfaces[:12]:
            print(
                f"   {iface['name']:<20} {iface['type']:<12} "
                f"{'activa' if iface.get('running') else 'inactiva'}"
            )
        if len(interfaces) > 12:
            print(f"   … y {len(interfaces) - 12} más")

        titulo("4. Clientes PPPoE (compáralo con PPP > Active Connections)")
        overview = await svc.pppoe_overview()
        print(f"   Secretos configurados: {overview['total_secrets']}")
        print(f"   Sesiones activas:      {overview['online']}")
        print(f"   Desconectados:         {overview['offline']}")
        print(f"   Suspendidos:           {overview['suspended']}")
        for cliente in overview["clients"][:10]:
            sesion = cliente.get("session") or {}
            print(
                f"     {cliente['username']:<24} {cliente['status']:<14} "
                f"{sesion.get('address') or '—'}"
            )

        return {
            "tipo": "mikrotik",
            "host": host,
            "transporte": client.transport,
            "identidad": ping,
            "recursos": {
                "cpu": recurso["cpu_load_percent"],
                "memoria_usada_pct": recurso["memory_used_percent"],
                "uptime": recurso["uptime"],
            },
            "pppoe": {
                "secretos": overview["total_secrets"],
                "online": overview["online"],
                "offline": overview["offline"],
                "suspendidos": overview["suspended"],
            },
            "interfaces": len(interfaces),
        }
    finally:
        await client.close()


# ---------------------------------------------------------------------- main


async def main() -> int:
    parser = argparse.ArgumentParser(description="Prueba los conectores contra equipos reales")
    parser.add_argument("objetivo", choices=["uisp", "mikrotik"])
    parser.add_argument("--json", dest="salida", help="Guardar el resultado en un archivo")
    args = parser.parse_args()

    print(f"Prueba real contra {args.objetivo.upper()}")
    print("Cada línea sangrada es una llamada que salió de esta máquina hacia el equipo.\n")

    try:
        resultado = await (probar_uisp() if args.objetivo == "uisp" else probar_mikrotik())
    except Exception as exc:
        titulo("FALLÓ")
        print(f"   {type(exc).__name__}: {exc}")
        print("\n   Las llamadas que se alcanzaron a hacer quedaron listadas arriba.")
        return 1

    titulo("Llamadas realizadas")
    print(f"   {len(LLAMADAS)} llamada(s) al equipo")
    total_ms = sum(llamada.duration_ms for llamada in LLAMADAS)
    print(f"   Tiempo total en el equipo: {total_ms} ms")

    if args.salida:
        with open(args.salida, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "resultado": resultado,
                    "llamadas": [llamada.as_line() for llamada in LLAMADAS],
                },
                fh,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        print(f"\nGuardado en {args.salida} (sin credenciales).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

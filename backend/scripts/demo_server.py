"""Levanta la SuperApp en modo demostración, sin PostgreSQL ni router real.

Arranca:
  * varios RouterOS simulados (mismo protocolo que un MikroTik de verdad),
  * la API de FastAPI sobre SQLite en memoria,
  * el servicio de monitoreo, que va llenando el histórico de las gráficas,
  * usuarios de prueba y los equipos ya registrados.

    python -m scripts.demo_server            # http://127.0.0.1:8000

Sirve para ver la plataforma funcionando antes de tener acceso a la red del ISP.
Al conectar los equipos reales solo cambia el registro en /devices.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Debe fijarse antes de importar la configuración: evita que el arranque intente
# crear las tablas contra el PostgreSQL de desarrollo, y acelera el muestreo
# para que las curvas se dibujen enseguida.
os.environ["ENVIRONMENT"] = "demo"
os.environ.setdefault("MONITOR_INTERVAL_SECONDS", "5")

import uvicorn
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.crypto import encrypt_secret
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.network import MikrotikApiMode, NetworkDevice
from app.models.user import User, UserRole
from tests.fake_routeros import FakeRouterOSBinaryServer, FakeRouterOSState

DEMO_ADMIN = ("admin@dolga.net", "Admin12345")
DEMO_COBRANZA = ("cobranza@dolga.net", "Cobranza12345")
ROUTER_USER = "api-dolga"
ROUTER_PASSWORD = "S3cret!"

# (nombre, sitio, ubicación, modelo, tiene sensor de temperatura, clientes PPPoE)
FLOTA = [
    ("Cerro La Torre", "Torre Principal", "Cerro La Torre", "RB4011iGS+RM", True, 150),
    ("Sabana del Mar", "Sitio Sabana", "Sabana del Mar", "RB750Gr3", False, 85),
    ("Miches Centro", "Miches Centro", "Calle Duarte 12", "RB760iGS", False, 120),
    ("El Cedro", "El Cedro", "Loma El Cedro", "RB2011UiAS", False, 95),
]


def _poblar_clientes(state: FakeRouterOSState, cantidad: int, prefijo: str) -> None:
    """Genera secrets y sesiones PPPoE para que los conteos se vean realistas."""
    state.tables["ppp/secret"] = []
    state.tables["ppp/active"] = []
    for index in range(cantidad):
        usuario = f"{prefijo}{index + 1:04d}"
        suspendido = index % 17 == 0  # algunos morosos
        desconectado = index % 11 == 0 and not suspendido
        state.tables["ppp/secret"].append(
            {
                ".id": f"*{2000 + index}",
                "name": usuario,
                "service": "pppoe",
                "profile": "PLAN-30M" if index % 3 == 0 else "PLAN-10M",
                "remote-address": f"10.20.{index // 250}.{index % 250 + 2}",
                "disabled": "true" if suspendido else "false",
                "comment": "Suspendido por falta de pago" if suspendido else f"Cliente {index + 1}",
            }
        )
        if not suspendido and not desconectado:
            state.tables["ppp/active"].append(
                {
                    ".id": f"*{6000 + index}",
                    "name": usuario,
                    "service": "pppoe",
                    "caller-id": f"48:A9:8A:{index // 256:02X}:{index % 256:02X}:0{index % 9}",
                    "address": f"10.20.{index // 250}.{index % 250 + 2}",
                    "uptime": f"{index % 20}d{index % 24:02d}:{index % 60:02d}:11",
                    "session-id": f"0x8100{index:04x}",
                }
            )


async def bootstrap() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    devices = []
    for indice, (nombre, sitio, ubicacion, modelo, sensor, clientes) in enumerate(FLOTA):
        state = FakeRouterOSState(
            ROUTER_USER, ROUTER_PASSWORD, has_health_sensor=sensor, vary=True, seed=indice
        )
        state.tables["system/identity"][0]["name"] = nombre.upper().replace(" ", "-")
        state.tables["system/resource"][0]["board-name"] = modelo
        state.tables["system/routerboard"][0]["model"] = modelo
        state.tables["system/routerboard"][0]["serial-number"] = f"D563{indice}A2B7C8D"
        _poblar_clientes(state, clientes, f"cl{indice}")
        server = await FakeRouterOSBinaryServer(state).start()
        print(f"[demo] {nombre}: RouterOS simulado en 127.0.0.1:{server.port} ({clientes} clientes)")
        devices.append(
            NetworkDevice(
                name=nombre,
                host="127.0.0.1",
                api_mode=MikrotikApiMode.API,
                api_port=server.port,
                api_use_tls=False,
                username=ROUTER_USER,
                password_encrypted=encrypt_secret(ROUTER_PASSWORD),
                site=sitio,
                location=ubicacion,
            )
        )

    # Un equipo caído a propósito: así se ve cómo reporta la plataforma un sitio
    # sin conexión en lugar de fingir que todo está bien.
    devices.append(
        NetworkDevice(
            name="Los Limones",
            host="127.0.0.1",
            api_mode=MikrotikApiMode.API,
            api_port=9,
            api_use_tls=False,
            username=ROUTER_USER,
            password_encrypted=encrypt_secret(ROUTER_PASSWORD),
            site="Los Limones",
            location="Los Limones",
        )
    )

    async with Session() as session:
        session.add_all(
            [
                User(
                    email=DEMO_ADMIN[0],
                    full_name="Olga Administradora",
                    hashed_password=hash_password(DEMO_ADMIN[1]),
                    role=UserRole.ADMIN,
                ),
                User(
                    email=DEMO_COBRANZA[0],
                    full_name="Equipo de Cobranza",
                    hashed_password=hash_password(DEMO_COBRANZA[1]),
                    role=UserRole.COBRANZA,
                ),
                *devices,
            ]
        )
        await session.commit()

    async def override_session():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    # El monitoreo tiene que muestrear contra SQLite, no contra PostgreSQL.
    app.state.session_factory = Session
    print(f"[demo] Login: {DEMO_ADMIN[0]} / {DEMO_ADMIN[1]}")


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bootstrap())
    config = uvicorn.Config(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8000)), loop="none")
    server = uvicorn.Server(config)
    loop.run_until_complete(server.serve())


if __name__ == "__main__":
    main()

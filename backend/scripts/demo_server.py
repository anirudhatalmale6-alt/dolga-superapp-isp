"""Levanta la SuperApp en modo demostración, sin PostgreSQL ni router real.

Arranca:
  * un RouterOS simulado (mismo protocolo que un MikroTik de verdad),
  * la API de FastAPI sobre SQLite en memoria,
  * un usuario admin y el router ya registrado.

    python -m scripts.demo_server            # http://127.0.0.1:8000

Sirve para ver el frontend funcionando antes de tener acceso a la red del ISP.
Al conectar el equipo real solo cambia el registro del router en /devices.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Debe fijarse antes de importar la configuración: evita que el arranque intente
# crear las tablas contra el PostgreSQL de desarrollo.
os.environ["ENVIRONMENT"] = "demo"

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


async def bootstrap() -> None:
    state = FakeRouterOSState(ROUTER_USER, ROUTER_PASSWORD)
    server = await FakeRouterOSBinaryServer(state).start()
    print(f"[demo] RouterOS simulado escuchando en 127.0.0.1:{server.port}")

    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

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
                NetworkDevice(
                    name="Borde Principal",
                    host="127.0.0.1",
                    api_mode=MikrotikApiMode.API,
                    api_port=server.port,
                    api_use_tls=False,
                    username=ROUTER_USER,
                    password_encrypted=encrypt_secret(ROUTER_PASSWORD),
                    site="Estacion Centro",
                ),
                NetworkDevice(
                    name="Torre Norte (fuera de linea)",
                    host="127.0.0.1",
                    api_mode=MikrotikApiMode.API,
                    api_port=9,
                    api_use_tls=False,
                    username=ROUTER_USER,
                    password_encrypted=encrypt_secret(ROUTER_PASSWORD),
                    site="Torre Norte",
                ),
            ]
        )
        await session.commit()

    async def override_session():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
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

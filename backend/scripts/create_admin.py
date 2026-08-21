"""Crea el primer usuario administrador de la SuperApp.

    python -m scripts.create_admin admin@dolga.net "Olga Admin" MiClaveSegura
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine
from app.models.network import NetworkDevice  # noqa: F401
from app.models.user import User, UserRole


async def main(email: str, full_name: str, password: str) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        existing = await session.execute(select(User).where(User.email == email.lower()))
        if existing.scalar_one_or_none() is not None:
            print(f"El usuario {email} ya existe.")
            return
        session.add(
            User(
                email=email.lower(),
                full_name=full_name,
                hashed_password=hash_password(password),
                role=UserRole.ADMIN,
            )
        )
        await session.commit()
    print(f"Administrador creado: {email}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        raise SystemExit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3]))

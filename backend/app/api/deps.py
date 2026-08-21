from __future__ import annotations

from typing import AsyncGenerator, Iterable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.core.security import decode_token
from app.db.session import get_session
from app.models.network import MikrotikApiMode, NetworkDevice
from app.models.user import User, UserRole
from app.services.mikrotik.client import MikrotikClient

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    payload = decode_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = await session.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario inactivo")
    return user


def require_roles(*roles: UserRole):
    allowed: Iterable[UserRole] = roles

    async def _checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para ejecutar esta acción",
            )
        return user

    return _checker


async def get_device(
    device_id: int,
    session: AsyncSession = Depends(get_session),
    # La autenticación va primero a propósito: sin esta dependencia, un usuario
    # sin token recibiría 404/200 y podría deducir qué equipos existen.
    _: User = Depends(get_current_user),
) -> NetworkDevice:
    device = await session.get(NetworkDevice, device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Equipo no encontrado")
    return device


def build_client(device: NetworkDevice) -> MikrotikClient:
    """Construye el cliente descifrando la clave en memoria, nunca antes."""
    return MikrotikClient(
        host=device.host,
        username=device.username,
        password=decrypt_secret(device.password_encrypted),
        mode=device.api_mode.value if isinstance(device.api_mode, MikrotikApiMode) else str(device.api_mode),
        rest_port=device.rest_port,
        rest_use_tls=device.rest_use_tls,
        api_port=device.api_port,
        api_use_tls=device.api_use_tls,
        verify_tls=device.verify_tls,
        timeout=settings.MIKROTIK_TIMEOUT,
    )


async def get_mikrotik_client(
    device: NetworkDevice = Depends(get_device),
    _: User = Depends(get_current_user),
) -> AsyncGenerator[MikrotikClient, None]:
    client = build_client(device)
    try:
        yield client
    finally:
        await client.close()

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    audit_trail,
    build_client,
    get_current_user,
    get_device,
    require_roles,
)
from app.services.audit import AuditTrail
from app.core.crypto import encrypt_secret
from app.db.session import get_session
from app.models.network import NetworkDevice
from app.models.user import User, UserRole
from app.schemas.network import ConnectionTestResult, DeviceCreate, DeviceOut, DeviceUpdate
from app.services.mikrotik.exceptions import MikrotikError

router = APIRouter(prefix="/devices", tags=["equipos"])


@router.get("", response_model=list[DeviceOut], summary="Listar equipos administrados")
async def list_devices(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> list[NetworkDevice]:
    result = await session.execute(select(NetworkDevice).order_by(NetworkDevice.name))
    return list(result.scalars().all())


@router.post(
    "",
    response_model=DeviceOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(UserRole.ADMIN))],
    summary="Registrar un router (la clave se guarda cifrada)",
)
async def create_device(
    payload: DeviceCreate, session: AsyncSession = Depends(get_session)
) -> NetworkDevice:
    data = payload.model_dump(exclude={"password"})
    device = NetworkDevice(**data, password_encrypted=encrypt_secret(payload.password))
    session.add(device)
    await session.commit()
    await session.refresh(device)
    return device


@router.get("/{device_id}", response_model=DeviceOut)
async def get_one(device: NetworkDevice = Depends(get_device), _: User = Depends(get_current_user)):
    return device


@router.patch(
    "/{device_id}",
    response_model=DeviceOut,
    dependencies=[Depends(require_roles(UserRole.ADMIN))],
)
async def update_device(
    payload: DeviceUpdate,
    device: NetworkDevice = Depends(get_device),
    session: AsyncSession = Depends(get_session),
) -> NetworkDevice:
    updates = payload.model_dump(exclude_unset=True)
    password = updates.pop("password", None)
    for field, value in updates.items():
        setattr(device, field, value)
    if password:
        device.password_encrypted = encrypt_secret(password)
    await session.commit()
    await session.refresh(device)
    return device


@router.delete(
    "/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles(UserRole.ADMIN))],
)
async def delete_device(
    device: NetworkDevice = Depends(get_device), session: AsyncSession = Depends(get_session)
) -> None:
    await session.delete(device)
    await session.commit()


@router.post(
    "/{device_id}/test-connection",
    response_model=ConnectionTestResult,
    summary="Probar credenciales y transporte contra el equipo real",
)
async def test_connection(
    device: NetworkDevice = Depends(get_device),
    session: AsyncSession = Depends(get_session),
    trail: AuditTrail = Depends(audit_trail),
    _: User = Depends(get_current_user),
) -> ConnectionTestResult:
    trail.target_id = device.id
    trail.target_name = device.name
    client = build_client(device, trail)
    try:
        info = await client.ping_check()
    except MikrotikError as exc:
        device.last_error = str(exc)
        await session.commit()
        return ConnectionTestResult(ok=False, error=str(exc))
    finally:
        await client.close()

    device.last_seen_at = datetime.now(timezone.utc)
    device.last_error = None
    device.routeros_version = info.get("version")
    device.board_name = info.get("board_name")
    await session.commit()
    return ConnectionTestResult(**info)

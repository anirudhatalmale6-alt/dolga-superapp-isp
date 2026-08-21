"""Endpoints Ubiquiti/UISP: React -> FastAPI -> UISP.

Mismo principio que MikroTik: el navegador solo conoce el id del controlador.
La App Key vive cifrada en PostgreSQL y se descifra en memoria para cada
consulta.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_roles
from app.core.config import settings
from app.core.crypto import encrypt_secret
from app.db.session import get_session
from app.models.uisp import UispController
from app.models.user import User, UserRole
from app.schemas.uisp import (
    ControllerCreate,
    ControllerOut,
    ControllerTestResult,
    ControllerUpdate,
)
from app.services.uisp.client import UispClient
from app.services.uisp.exceptions import (
    UispAuthError,
    UispConnectionError,
    UispError,
    UispRequestError,
)
from app.services.uisp.service import UispService
from app.services.uisp.session import build_uisp_client

router = APIRouter(prefix="/uisp", tags=["ubiquiti"])


# ----------------------------------------------------------------- dependencias


async def get_controller(
    controller_id: int,
    session: AsyncSession = Depends(get_session),
    # La autenticación va primero a propósito: sin token no se debe poder
    # deducir qué controladores existen.
    _: User = Depends(get_current_user),
) -> UispController:
    controller = await session.get(UispController, controller_id)
    if controller is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Controlador UISP no encontrado"
        )
    return controller


async def get_uisp_client(
    controller: UispController = Depends(get_controller),
) -> AsyncGenerator[UispClient, None]:
    client = build_uisp_client(controller)
    try:
        yield client
    finally:
        await client.close()


def _svc(client: UispClient = Depends(get_uisp_client)) -> UispService:
    return UispService(client)


def _translate(exc: UispError) -> HTTPException:
    if isinstance(exc, UispAuthError):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    if isinstance(exc, UispConnectionError):
        return HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    if isinstance(exc, UispRequestError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


async def _guard(coro):
    try:
        return await coro
    except UispError as exc:
        raise _translate(exc) from exc


# ------------------------------------------------------- alta de controladores


@router.get("/controllers", response_model=list[ControllerOut], summary="Listar controladores UISP")
async def list_controllers(
    session: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)
) -> list[UispController]:
    result = await session.execute(select(UispController).order_by(UispController.name))
    return list(result.scalars().all())


@router.post(
    "/controllers",
    response_model=ControllerOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(UserRole.ADMIN))],
    summary="Registrar UISP (la App Key se guarda cifrada)",
)
async def create_controller(
    payload: ControllerCreate, session: AsyncSession = Depends(get_session)
) -> UispController:
    data = payload.model_dump(exclude={"token"})
    controller = UispController(**data, token_encrypted=encrypt_secret(payload.token))
    session.add(controller)
    await session.commit()
    await session.refresh(controller)
    return controller


@router.patch(
    "/controllers/{controller_id}",
    response_model=ControllerOut,
    dependencies=[Depends(require_roles(UserRole.ADMIN))],
)
async def update_controller(
    payload: ControllerUpdate,
    controller: UispController = Depends(get_controller),
    session: AsyncSession = Depends(get_session),
) -> UispController:
    updates = payload.model_dump(exclude_unset=True)
    token = updates.pop("token", None)
    for field, value in updates.items():
        setattr(controller, field, value)
    if token:
        controller.token_encrypted = encrypt_secret(token)
    await session.commit()
    await session.refresh(controller)
    return controller


@router.delete(
    "/controllers/{controller_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles(UserRole.ADMIN))],
)
async def delete_controller(
    controller: UispController = Depends(get_controller),
    session: AsyncSession = Depends(get_session),
) -> None:
    await session.delete(controller)
    await session.commit()


@router.post(
    "/controllers/{controller_id}/test-connection",
    response_model=ControllerTestResult,
    summary="Probar la URL y la App Key contra el UISP real",
)
async def test_connection(
    controller: UispController = Depends(get_controller),
    session: AsyncSession = Depends(get_session),
) -> ControllerTestResult:
    client = build_uisp_client(controller)
    try:
        info = await UispService(client).ping_check()
    except UispError as exc:
        controller.last_error = str(exc)
        await session.commit()
        return ControllerTestResult(ok=False, error=str(exc))
    finally:
        await client.close()

    controller.last_seen_at = datetime.now(timezone.utc)
    controller.last_error = None
    await session.commit()
    return ControllerTestResult(**info)


# ------------------------------------------------------------------- consultas


@router.get("/{controller_id}/overview", summary="Totales de la red inalámbrica")
async def overview(svc: UispService = Depends(_svc), _: User = Depends(get_current_user)):
    return await _guard(svc.overview())


@router.get("/{controller_id}/sites", summary="Sitios / torres")
async def sites(svc: UispService = Depends(_svc), _: User = Depends(get_current_user)):
    return await _guard(svc.sites())


@router.get("/{controller_id}/devices", summary="Todos los equipos adoptados en UISP")
async def devices(
    svc: UispService = Depends(_svc),
    role: str | None = Query(default=None, description="ap, station, router, switch…"),
    _: User = Depends(get_current_user),
):
    rows: List[Dict[str, Any]] = await _guard(svc.devices())
    if role:
        rows = [d for d in rows if d["role"] == role.lower()]
    return rows


@router.get("/{controller_id}/topology", summary="Sectores con sus estaciones asociadas")
async def topology(svc: UispService = Depends(_svc), _: User = Depends(get_current_user)):
    return await _guard(svc.topology())


@router.get("/{controller_id}/sectors/{ap_id}/stations", summary="Clientes de un sector")
async def sector_stations(
    ap_id: str, svc: UispService = Depends(_svc), _: User = Depends(get_current_user)
):
    return await _guard(svc.stations_of(ap_id))


@router.get("/{controller_id}/devices/{device_id}", summary="Detalle de un equipo")
async def device_detail(
    device_id: str, svc: UispService = Depends(_svc), _: User = Depends(get_current_user)
):
    return await _guard(svc.device(device_id))


@router.get("/{controller_id}/devices/{device_id}/interfaces", summary="Interfaces de un equipo")
async def device_interfaces(
    device_id: str, svc: UispService = Depends(_svc), _: User = Depends(get_current_user)
):
    return await _guard(svc.interfaces(device_id))


@router.get(
    "/{controller_id}/devices/{device_id}/statistics",
    summary="Histórico que ya guarda UISP (señal, CPU, tráfico)",
)
async def device_statistics(
    device_id: str,
    interval: str = Query(default="hour", pattern="^(hour|day|week|month)$"),
    svc: UispService = Depends(_svc),
    _: User = Depends(get_current_user),
):
    return await _guard(svc.statistics(device_id, interval))

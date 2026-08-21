"""Endpoints MikroTik: React -> FastAPI -> RouterOS.

Ninguna respuesta de este módulo contiene credenciales del equipo. El frontend
solo conoce el `device_id`; usuario y clave viven cifrados en PostgreSQL y se
descifran en memoria para cada llamada.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user, get_mikrotik_client, require_roles
from app.models.user import User, UserRole
from app.schemas.network import ActionResult, RestoreRequest, SuspendRequest
from app.services.mikrotik.client import MikrotikClient
from app.services.mikrotik.exceptions import (
    MikrotikAuthError,
    MikrotikCommandError,
    MikrotikConnectionError,
    MikrotikError,
)
from app.services.mikrotik.service import MikrotikService

router = APIRouter(prefix="/mikrotik/{device_id}", tags=["mikrotik"])


def _svc(client: MikrotikClient = Depends(get_mikrotik_client)) -> MikrotikService:
    return MikrotikService(client)


def _translate(exc: MikrotikError) -> HTTPException:
    """Convierte errores del equipo en respuestas HTTP claras para el frontend."""
    if isinstance(exc, MikrotikAuthError):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    if isinstance(exc, MikrotikConnectionError):
        return HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc))
    if isinstance(exc, MikrotikCommandError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


async def _guard(coro):
    try:
        return await coro
    except MikrotikError as exc:
        raise _translate(exc) from exc


# --------------------------------------------------------------------- lectura


@router.get("/resource", summary="CPU, memoria, disco, uptime y versión")
async def resource(svc: MikrotikService = Depends(_svc), _: User = Depends(get_current_user)):
    return await _guard(svc.system_resource())


@router.get("/routerboard", summary="Modelo, serie y firmware")
async def routerboard(svc: MikrotikService = Depends(_svc), _: User = Depends(get_current_user)):
    return await _guard(svc.routerboard())


@router.get("/interfaces", summary="Interfaces con estado y contadores")
async def interfaces(svc: MikrotikService = Depends(_svc), _: User = Depends(get_current_user)):
    return await _guard(svc.interfaces())


@router.get("/interfaces/{interface}/traffic", summary="Tráfico instantáneo de una interfaz")
async def interface_traffic(
    interface: str, svc: MikrotikService = Depends(_svc), _: User = Depends(get_current_user)
):
    return await _guard(svc.interface_traffic(interface))


@router.get("/ip-addresses", summary="Direcciones IP configuradas")
async def ip_addresses(svc: MikrotikService = Depends(_svc), _: User = Depends(get_current_user)):
    return await _guard(svc.ip_addresses())


@router.get("/pppoe/active", summary="Sesiones PPPoE activas")
async def pppoe_active(svc: MikrotikService = Depends(_svc), _: User = Depends(get_current_user)):
    return await _guard(svc.pppoe_active())


@router.get("/pppoe/secrets", summary="Clientes PPPoE configurados")
async def pppoe_secrets(svc: MikrotikService = Depends(_svc), _: User = Depends(get_current_user)):
    return await _guard(svc.pppoe_secrets())


@router.get("/pppoe/profiles", summary="Perfiles PPP (planes en el router)")
async def ppp_profiles(svc: MikrotikService = Depends(_svc), _: User = Depends(get_current_user)):
    return await _guard(svc.ppp_profiles())


@router.get("/pppoe/overview", summary="Clientes PPPoE con estado online/offline/suspendido")
async def pppoe_overview(
    svc: MikrotikService = Depends(_svc),
    search: str | None = Query(default=None, description="Filtra por usuario o comentario"),
    _: User = Depends(get_current_user),
):
    data: Dict[str, Any] = await _guard(svc.pppoe_overview())
    if search:
        needle = search.lower()
        clients: List[Dict[str, Any]] = [
            c
            for c in data["clients"]
            if needle in (c.get("username") or "").lower()
            or needle in (c.get("comment") or "").lower()
        ]
        data = {**data, "clients": clients}
    return data


@router.get("/dashboard", summary="Resumen del router para el dashboard")
async def dashboard(svc: MikrotikService = Depends(_svc), _: User = Depends(get_current_user)):
    return await _guard(svc.dashboard())


# --------------------------------------------------------------------- acciones


@router.post(
    "/pppoe/suspend",
    response_model=ActionResult,
    dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.COBRANZA))],
    summary="Suspender un cliente PPPoE por falta de pago",
)
async def suspend(payload: SuspendRequest, svc: MikrotikService = Depends(_svc)) -> ActionResult:
    try:
        result = await svc.suspend_customer(
            username=payload.username,
            method=payload.method,
            suspended_profile=payload.suspended_profile,
            address_list=payload.address_list,
            comment=payload.comment,
        )
    except MikrotikError as exc:
        raise _translate(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ActionResult(**result)


@router.post(
    "/pppoe/restore",
    response_model=ActionResult,
    dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.COBRANZA))],
    summary="Reactivar un cliente PPPoE suspendido",
)
async def restore(payload: RestoreRequest, svc: MikrotikService = Depends(_svc)) -> ActionResult:
    try:
        result = await svc.restore_customer(
            username=payload.username,
            method=payload.method,
            active_profile=payload.active_profile,
            address_list=payload.address_list,
            comment=payload.comment,
        )
    except MikrotikError as exc:
        raise _translate(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ActionResult(**result)


@router.post(
    "/pppoe/{username}/kick",
    dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.COBRANZA, UserRole.SOPORTE))],
    summary="Cerrar la sesión activa de un cliente (forzar reconexión)",
)
async def kick(username: str, svc: MikrotikService = Depends(_svc)):
    closed = await _guard(svc.kick_session(username))
    return {"username": username, "session_closed": closed}

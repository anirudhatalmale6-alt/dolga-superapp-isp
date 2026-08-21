"""Endpoints MikroTik: React -> FastAPI -> RouterOS.

Ninguna respuesta de este módulo contiene credenciales del equipo. El frontend
solo conoce el `device_id`; usuario y clave viven cifrados en PostgreSQL y se
descifran en memoria para cada llamada.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user, get_device, get_mikrotik_client, require_roles
from app.models.network import NetworkDevice
from app.models.user import User, UserRole
from app.schemas.network import ActionResult, RestoreRequest, ShutdownRequest, SuspendRequest
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


@router.get(
    "/health",
    summary="Temperatura y voltaje (solo en placas con sensor)",
    description=(
        "Devuelve `available: false` cuando el equipo no trae sensor de "
        "temperatura (RB750Gr3, RB2011, etc). Eso no es un error: es un dato "
        "que ese hardware no puede entregar, y la interfaz debe mostrar 'n/d' "
        "en lugar de un número inventado."
    ),
)
async def health(svc: MikrotikService = Depends(_svc), _: User = Depends(get_current_user)):
    return await _guard(svc.health())


@router.get("/wan", summary="Interfaces hacia la calle con IP, gateway y velocidad")
async def wan(svc: MikrotikService = Depends(_svc), _: User = Depends(get_current_user)):
    return await _guard(svc.wan_interfaces())


@router.get("/snapshot", summary="Lectura compacta del equipo (usada por la tabla de flota)")
async def snapshot(svc: MikrotikService = Depends(_svc), _: User = Depends(get_current_user)):
    return await _guard(svc.snapshot())


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


# ----------------------------------------------------------------- mantenimiento


@router.get(
    "/updates",
    dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.SOPORTE))],
    summary="Revisar si hay una versión nueva de RouterOS (no instala nada)",
)
async def updates(svc: MikrotikService = Depends(_svc)):
    return await _guard(svc.check_updates())


@router.post(
    "/reboot",
    dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.SOPORTE))],
    summary="Reiniciar el equipo",
)
async def reboot(svc: MikrotikService = Depends(_svc)):
    await _guard(svc.reboot())
    return {
        "action": "reboot",
        "ok": True,
        "message": "Reinicio enviado. El equipo vuelve solo en uno o dos minutos.",
    }


@router.post(
    "/shutdown",
    dependencies=[Depends(require_roles(UserRole.ADMIN))],
    summary="Apagar el equipo (requiere confirmar escribiendo su nombre)",
    description=(
        "Apagar un MikroTik remoto significa que NO vuelve solo: hay que ir al "
        "sitio a darle corriente. Por eso el servidor exige que `confirm_name` "
        "coincida exactamente con el nombre del equipo; no alcanza con "
        "confirmar en el navegador."
    ),
)
async def shutdown(
    payload: ShutdownRequest,
    device: NetworkDevice = Depends(get_device),
    svc: MikrotikService = Depends(_svc),
):
    # La confirmación se valida en el servidor, no solo en la interfaz: si solo
    # la revisara el navegador no sería una salvaguarda, sería decoración.
    if payload.confirm_name.strip() != device.name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Para apagar el equipo hay que escribir su nombre exacto. "
                f"Se esperaba '{device.name}'."
            ),
        )
    await _guard(svc.shutdown())
    return {
        "action": "shutdown",
        "ok": True,
        "message": (
            f"'{device.name}' fue apagado. No volverá solo: requiere corte y "
            "restitución de corriente en el sitio."
        ),
    }

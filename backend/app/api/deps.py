from __future__ import annotations

from typing import AsyncGenerator, Iterable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.core.security import decode_token
from app.db.session import get_session
from app.models.network import MikrotikApiMode, NetworkDevice
from app.models.user import User, UserRole
from app.services.audit import AuditTrail
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


def build_client(device: NetworkDevice, trail: "AuditTrail | None" = None) -> MikrotikClient:
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
        on_exchange=trail.record if trail is not None else None,
    )


# Nombres legibles de cada operación, para que la bitácora se pueda leer sin
# saber cómo se llaman las rutas por dentro. La clave lleva el tipo de equipo
# delante porque los dos módulos tienen funciones con el mismo nombre
# (`reboot`, `interfaces`, `test_connection`...) y sin el prefijo una
# operación sobre una antena se leería como si fuera sobre un router.
OPERATION_NAMES: dict[str, str] = {
    # MikroTik
    "mikrotik:resource": "Consultar recursos del router",
    "mikrotik:routerboard": "Consultar datos de la RouterBOARD",
    "mikrotik:health": "Consultar salud del router",
    "mikrotik:interfaces": "Listar interfaces",
    "mikrotik:interface_traffic": "Medir tráfico de una interfaz",
    "mikrotik:ip_addresses": "Listar direcciones IP",
    "mikrotik:wan": "Detectar la WAN",
    "mikrotik:snapshot": "Consultar estado general del router",
    "mikrotik:dashboard": "Abrir tablero del router",
    "mikrotik:pppoe_overview": "Consultar clientes PPPoE",
    "mikrotik:pppoe_active": "Listar sesiones PPPoE activas",
    "mikrotik:pppoe_secrets": "Listar secretos PPPoE",
    "mikrotik:ppp_profiles": "Listar perfiles PPP",
    "mikrotik:kick": "Cortar sesión PPPoE",
    "mikrotik:suspend": "Suspender cliente",
    "mikrotik:restore": "Reactivar cliente",
    "mikrotik:reboot": "Reiniciar router",
    "mikrotik:shutdown": "Apagar router",
    "mikrotik:updates": "Buscar actualizaciones de RouterOS",
    "mikrotik:test_connection": "Probar conexión con el router",
    # Ubiquiti / UISP
    "uisp:overview": "Consultar resumen de la red Ubiquiti",
    "uisp:sites": "Listar sitios / torres",
    "uisp:devices": "Listar equipos Ubiquiti",
    "uisp:topology": "Consultar sectores y clientes",
    "uisp:sector_stations": "Listar clientes de un sector",
    "uisp:device_detail": "Consultar detalle de un equipo Ubiquiti",
    "uisp:device_interfaces": "Listar interfaces de un equipo Ubiquiti",
    "uisp:device_statistics": "Consultar histórico de un equipo",
    "uisp:reboot_device": "Reiniciar equipo Ubiquiti",
    "uisp:locate_device": "Localizar equipo (parpadeo de LED)",
    "uisp:upgrade_device": "Actualizar firmware",
    "uisp:test_connection": "Probar conexión con UISP",
}

# Operaciones que cambian algo en el equipo. Se conservan mucho más tiempo que
# las consultas, porque son las que alguien puede tener que justificar.
ACTIONS = {
    "mikrotik:kick",
    "mikrotik:suspend",
    "mikrotik:restore",
    "mikrotik:reboot",
    "mikrotik:shutdown",
    "uisp:reboot_device",
    "uisp:locate_device",
    "uisp:upgrade_device",
}


async def audit_trail(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> AsyncGenerator[AuditTrail, None]:
    """Abre la bitácora de esta petición y la guarda al terminar.

    El `finally` corre pase lo que pase: si el equipo no contestó, la fila
    igual queda escrita con el error. Un intento fallido es justo lo que
    después hace falta poder mirar.
    """
    route = request.scope.get("route")
    path = request.url.path
    kind = "uisp" if "/uisp/" in path else "mikrotik"
    clave = f"{kind}:{getattr(route, 'name', '') or path}"
    trail = AuditTrail(
        operation=OPERATION_NAMES.get(clave, clave),
        target_kind=kind,
        endpoint=f"{request.method} {path}",
        is_action=clave in ACTIONS,
        user_id=user.id,
        user_email=user.email,
        user_role=user.role.value if hasattr(user.role, "value") else str(user.role),
        source_ip=request.client.host if request.client else None,
    )
    try:
        yield trail
    finally:
        await trail.flush(session)


async def get_mikrotik_client(
    device: NetworkDevice = Depends(get_device),
    trail: AuditTrail = Depends(audit_trail),
    _: User = Depends(get_current_user),
) -> AsyncGenerator[MikrotikClient, None]:
    trail.target_id = device.id
    trail.target_name = device.name
    client = build_client(device, trail)
    try:
        yield client
    finally:
        await client.close()

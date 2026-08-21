"""Construcción del cliente de UISP a partir de la fila cifrada.

Vive aparte para que tanto la API como el servicio de monitoreo lo usen igual,
sin duplicar el descifrado.
"""

from __future__ import annotations

from typing import Optional

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.models.uisp import UispController
from app.services.audit import AuditTrail
from app.services.uisp.client import UispClient


def build_uisp_client(
    controller: UispController, trail: Optional[AuditTrail] = None
) -> UispClient:
    """La App Key se descifra aquí, en memoria, y nunca antes."""
    return UispClient(
        base_url=controller.base_url,
        token=decrypt_secret(controller.token_encrypted),
        verify_tls=controller.verify_tls,
        timeout=settings.UISP_TIMEOUT,
        on_exchange=trail.record if trail is not None else None,
    )

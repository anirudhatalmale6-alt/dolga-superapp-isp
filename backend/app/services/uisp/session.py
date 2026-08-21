"""Construcción del cliente de UISP a partir de la fila cifrada.

Vive aparte para que tanto la API como el servicio de monitoreo lo usen igual,
sin duplicar el descifrado.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.models.uisp import UispController
from app.services.uisp.client import UispClient


def build_uisp_client(controller: UispController) -> UispClient:
    """La App Key se descifra aquí, en memoria, y nunca antes."""
    return UispClient(
        base_url=controller.base_url,
        token=decrypt_secret(controller.token_encrypted),
        verify_tls=controller.verify_tls,
        timeout=settings.UISP_TIMEOUT,
    )

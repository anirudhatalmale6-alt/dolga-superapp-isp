"""Cifrado simétrico de credenciales de equipos.

Las claves de MikroTik / UISP / OLT nunca se guardan en claro en PostgreSQL ni
salen hacia el frontend: se cifran con Fernet (AES-128-CBC + HMAC-SHA256) usando
DEVICE_SECRET_KEY y solo se descifran dentro del proceso de FastAPI, justo antes
de abrir la conexión con el equipo.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class DecryptionError(RuntimeError):
    """La credencial almacenada no puede descifrarse con la clave actual."""


def _fernet() -> Fernet:
    key = (settings.DEVICE_SECRET_KEY or "").strip()
    if not key:
        # Fallback determinista para entornos de desarrollo/tests: deriva una
        # clave Fernet válida desde SECRET_KEY. En producción hay que definir
        # DEVICE_SECRET_KEY explícitamente.
        digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(plain: str) -> str:
    if plain is None:
        raise ValueError("No se puede cifrar un valor nulo")
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:  # pragma: no cover - depende del entorno
        raise DecryptionError(
            "No se pudo descifrar la credencial: DEVICE_SECRET_KEY no coincide "
            "con la clave usada al guardarla."
        ) from exc


def mask_secret(plain: str, keep: int = 2) -> str:
    """Representación segura para logs y respuestas de API."""
    if not plain:
        return ""
    if len(plain) <= keep:
        return "*" * len(plain)
    return plain[:keep] + "*" * (len(plain) - keep)

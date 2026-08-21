"""Cliente asíncrono de la API binaria de RouterOS (puerto 8728 / 8729 con TLS).

Implementa el protocolo completo de MikroTik:

* Palabras con longitud codificada (1 a 5 bytes) y sentencias terminadas en
  palabra vacía.
* Login moderno (RouterOS >= 6.43): `/login` con `=name=` y `=password=`.
* Login legacy (RouterOS < 6.43): reto MD5 `/login` -> `=ret=` -> respuesta
  `00` + md5(chr(0) + password + challenge).
* Lectura de respuestas `!re`, `!done`, `!trap` y `!fatal`.

Se usa cuando el equipo corre RouterOS 6.x (sin REST) o cuando el cliente
prefiere no exponer el servicio www-ssl.
"""

from __future__ import annotations

import asyncio
import hashlib
import ssl
from typing import Any, Dict, Iterable, List, Optional

from .exceptions import (
    MikrotikAuthError,
    MikrotikCommandError,
    MikrotikConnectionError,
    MikrotikError,
)


def encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    if length < 0x4000:
        length |= 0x8000
        return length.to_bytes(2, "big")
    if length < 0x200000:
        length |= 0xC00000
        return length.to_bytes(3, "big")
    if length < 0x10000000:
        length |= 0xE0000000
        return length.to_bytes(4, "big")
    return b"\xf0" + length.to_bytes(4, "big")


def encode_word(word: str) -> bytes:
    payload = word.encode("utf-8")
    return encode_length(len(payload)) + payload


class RouterOSBinaryClient:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 8728,
        use_tls: bool = False,
        verify_tls: bool = False,
        timeout: float = 8.0,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.use_tls = use_tls
        self.verify_tls = verify_tls
        self.timeout = timeout
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ conexión

    def _ssl_context(self) -> Optional[ssl.SSLContext]:
        if not self.use_tls:
            return None
        ctx = ssl.create_default_context()
        if not self.verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        # RouterOS genera certificados con cifrados antiguos por defecto.
        ctx.set_ciphers("DEFAULT@SECLEVEL=0")
        return ctx

    async def connect(self) -> None:
        if self._writer is not None:
            return
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port, ssl=self._ssl_context()),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError as exc:
            raise MikrotikConnectionError(
                f"Timeout conectando a {self.host}:{self.port} (API binaria)"
            ) from exc
        except OSError as exc:
            raise MikrotikConnectionError(
                f"No se pudo conectar a {self.host}:{self.port}: {exc}"
            ) from exc
        await self._login()

    async def close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:  # pragma: no cover - cierre best-effort
                pass
        self._reader = None
        self._writer = None

    async def __aenter__(self) -> "RouterOSBinaryClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    # -------------------------------------------------------------- E/S de bajo nivel

    async def _write_sentence(self, words: Iterable[str]) -> None:
        assert self._writer is not None
        buffer = b"".join(encode_word(word) for word in words) + b"\x00"
        self._writer.write(buffer)
        await self._writer.drain()

    async def _read_exactly(self, count: int) -> bytes:
        assert self._reader is not None
        try:
            return await asyncio.wait_for(self._reader.readexactly(count), timeout=self.timeout)
        except asyncio.IncompleteReadError as exc:
            raise MikrotikConnectionError("El equipo cerró la conexión inesperadamente") from exc
        except asyncio.TimeoutError as exc:
            raise MikrotikConnectionError("Timeout leyendo la respuesta del equipo") from exc

    async def _read_length(self) -> int:
        first = (await self._read_exactly(1))[0]
        if first < 0x80:
            return first
        if first < 0xC0:
            rest = await self._read_exactly(1)
            return ((first << 8) + rest[0]) & ~0x8000
        if first < 0xE0:
            rest = await self._read_exactly(2)
            return ((first << 16) + int.from_bytes(rest, "big")) & ~0xC00000
        if first < 0xF0:
            rest = await self._read_exactly(3)
            return ((first << 24) + int.from_bytes(rest, "big")) & ~0xE0000000
        if first == 0xF0:
            rest = await self._read_exactly(4)
            return int.from_bytes(rest, "big")
        raise MikrotikError(f"Longitud de palabra inválida en la respuesta: 0x{first:02x}")

    async def _read_word(self) -> str:
        length = await self._read_length()
        if length == 0:
            return ""
        return (await self._read_exactly(length)).decode("utf-8", errors="replace")

    async def _read_sentence(self) -> List[str]:
        words: List[str] = []
        while True:
            word = await self._read_word()
            if word == "":
                return words
            words.append(word)

    # ------------------------------------------------------------------- comandos

    @staticmethod
    def _parse_sentence(words: List[str]) -> tuple[str, Dict[str, str]]:
        if not words:
            return "", {}
        reply = words[0]
        attrs: Dict[str, str] = {}
        for word in words[1:]:
            if word.startswith("="):
                key, _, value = word[1:].partition("=")
                attrs[key] = value
            elif word.startswith(".tag="):
                attrs[".tag"] = word[5:]
        return reply, attrs

    async def _talk(self, words: List[str]) -> List[Dict[str, str]]:
        """Envía una sentencia y devuelve las filas `!re` hasta recibir `!done`."""
        await self._write_sentence(words)
        rows: List[Dict[str, str]] = []
        while True:
            reply, attrs = self._parse_sentence(await self._read_sentence())
            if reply == "!re":
                rows.append(attrs)
            elif reply == "!done":
                if attrs:
                    rows.append(attrs)
                return rows
            elif reply == "!trap":
                raise MikrotikCommandError(
                    attrs.get("message", "error desconocido de RouterOS"),
                    command=words[0] if words else None,
                    category=attrs.get("category"),
                )
            elif reply == "!fatal":
                await self.close()
                raise MikrotikError(attrs.get("message") or "conexión terminada por RouterOS")
            elif reply == "":
                continue
            else:  # pragma: no cover - respuesta no documentada
                raise MikrotikError(f"Respuesta inesperada de RouterOS: {reply}")

    async def _login(self) -> None:
        try:
            rows = await self._talk(["/login", f"=name={self.username}", f"=password={self.password}"])
        except MikrotikCommandError as exc:
            raise MikrotikAuthError(
                f"RouterOS rechazó el login del usuario '{self.username}': {exc.message}"
            ) from exc

        challenge = next((row.get("ret") for row in rows if row.get("ret")), None)
        if not challenge:
            return  # RouterOS >= 6.43: login en un solo paso

        # RouterOS < 6.43: respuesta al reto MD5
        try:
            challenge_bytes = bytes.fromhex(challenge)
        except ValueError as exc:  # pragma: no cover
            raise MikrotikError(f"Reto de login inválido: {challenge!r}") from exc

        digest = hashlib.md5(b"\x00" + self.password.encode() + challenge_bytes).hexdigest()
        try:
            await self._talk(
                ["/login", f"=name={self.username}", f"=response=00{digest}"]
            )
        except MikrotikCommandError as exc:
            raise MikrotikAuthError(
                f"RouterOS rechazó el login del usuario '{self.username}': {exc.message}"
            ) from exc

    # ------------------------------------------------------------------- API pública

    async def command(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        queries: Optional[List[str]] = None,
        proplist: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        """Ejecuta un comando de RouterOS.

        path      -> "/system/resource/print"
        params    -> {"numbers": "*1", "disabled": "yes"}  (palabras `=k=v`)
        queries   -> ["?disabled=false"]                    (filtros del lado del router)
        proplist  -> ["name", "running"]                    (=.proplist=)
        """
        words = [path if path.startswith("/") else "/" + path]
        if proplist:
            words.append("=.proplist=" + ",".join(proplist))
        for key, value in (params or {}).items():
            if isinstance(value, bool):
                value = "yes" if value else "no"
            words.append(f"={key}={value}")
        words.extend(queries or [])
        # Una sola sentencia a la vez por conexión: el protocolo no multiplexa
        # si no se usan tags, y `connect()` debe entrar en la misma sección
        # crítica para no abrir dos sockets en paralelo.
        async with self._lock:
            await self.connect()
            return await self._talk(words)

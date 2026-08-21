"""Registro de operaciones: qué se le pidió al equipo y qué contestó.

La pieza central es `AuditTrail`. Se crea una por petición HTTP, se le pasa a
los conectores como callback y va anotando cada intercambio real con el equipo.
Al terminar la petición se guarda una fila en `operation_logs`.

Lo que se anota de cada intercambio es deliberadamente lo verificable:

    GET https://uisp.midominio.net/nms/api/v2.1/devices -> 200 en 312 ms (47 filas)
    /ppp/active/print -> ok en 41 ms (18 filas)     [API binaria]

Nunca la App Key, la clave del router ni las cabeceras de autenticación.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import OperationLog

logger = logging.getLogger(__name__)

# Cuánto texto de una respuesta o de un error se guarda. Suficiente para
# entender qué pasó, no tanto como para engordar la tabla.
MAX_DETAIL = 500


@dataclass
class Exchange:
    """Una llamada concreta que salió del servidor hacia un equipo."""

    target: str  # "uisp", "routeros-rest", "routeros-api"
    request: str  # URL o sentencia, tal cual se envió
    ok: bool
    duration_ms: int
    http_status: Optional[int] = None
    rows: Optional[int] = None
    error: Optional[str] = None

    def as_line(self) -> str:
        estado = f"HTTP {self.http_status}" if self.http_status else ("ok" if self.ok else "error")
        extra = f", {self.rows} fila(s)" if self.rows is not None else ""
        if self.error:
            extra = f" — {self.error[:MAX_DETAIL]}"
        return f"[{self.target}] {self.request} -> {estado} en {self.duration_ms} ms{extra}"


@dataclass
class AuditTrail:
    """Acumula los intercambios de una petición y los persiste al final."""

    operation: str
    target_kind: str
    endpoint: Optional[str] = None
    is_action: bool = False
    target_id: Optional[int] = None
    target_name: Optional[str] = None
    user_id: Optional[int] = None
    user_email: Optional[str] = None
    user_role: Optional[str] = None
    source_ip: Optional[str] = None
    detail: Optional[str] = None
    ok: bool = True
    exchanges: List[Exchange] = field(default_factory=list)
    _started: float = field(default_factory=time.perf_counter)

    # ------------------------------------------------------------- recolección

    def record(self, exchange: Exchange) -> None:
        """Callback que reciben los conectores. Nunca debe romper la llamada."""
        self.exchanges.append(exchange)
        if not exchange.ok:
            self.ok = False

    def describe(self, operation: str, *, is_action: bool = False) -> None:
        self.operation = operation
        self.is_action = is_action

    def fail(self, message: str) -> None:
        self.ok = False
        self.detail = message[:MAX_DETAIL]

    def succeed(self, message: str) -> None:
        self.detail = message[:MAX_DETAIL]

    # ------------------------------------------------------------- persistencia

    def to_row(self) -> OperationLog:
        payload: List[Dict[str, Any]] = [asdict(exchange) for exchange in self.exchanges]
        detail = self.detail
        if detail is None and self.exchanges:
            fallidos = [e for e in self.exchanges if not e.ok]
            detail = fallidos[0].error if fallidos else None
        return OperationLog(
            user_id=self.user_id,
            user_email=self.user_email,
            user_role=self.user_role,
            source_ip=self.source_ip,
            target_kind=self.target_kind,
            target_id=self.target_id,
            target_name=self.target_name,
            operation=self.operation,
            endpoint=self.endpoint,
            is_action=self.is_action,
            ok=self.ok,
            detail=(detail or "")[:MAX_DETAIL] or None,
            duration_ms=int((time.perf_counter() - self._started) * 1000),
            upstream=json.dumps(payload, ensure_ascii=False) if payload else None,
            upstream_calls=len(payload),
        )

    async def flush(self, session: AsyncSession) -> None:
        """Guarda la fila. Si esto falla, la operación ya ocurrió igual.

        Por eso el error se traga y se registra en el log del servidor: no
        tendría sentido devolverle un 500 al operador porque no se pudo
        escribir la bitácora de algo que sí se ejecutó.
        """
        # Si la petición no llegó a hablar con ningún equipo y no era una
        # acción, no hay nada que auditar: listar los routers guardados no es
        # una operación sobre la red.
        if not self.exchanges and not self.is_action:
            return
        try:
            session.add(self.to_row())
            await session.commit()
        except Exception:  # pragma: no cover - defensivo
            logger.exception("No se pudo guardar la bitácora de %s", self.operation)
            try:
                await session.rollback()
            except Exception:
                pass


async def purge_old_logs(
    session: AsyncSession, *, read_retention_days: int, action_retention_days: int
) -> int:
    """Borra bitácora vencida y devuelve cuántas filas se eliminaron.

    Las consultas caducan rápido: a los pocos días ya no le dicen nada a nadie.
    Las acciones (suspender, reiniciar, actualizar firmware) se guardan mucho
    más tiempo porque son las que alguien puede tener que justificar.
    """
    from datetime import datetime, timedelta, timezone

    ahora = datetime.now(timezone.utc)
    corte_lecturas = ahora - timedelta(days=read_retention_days)
    corte_acciones = ahora - timedelta(days=action_retention_days)

    result = await session.execute(
        delete(OperationLog).where(
            or_(
                (OperationLog.is_action.is_(False)) & (OperationLog.occurred_at < corte_lecturas),
                (OperationLog.is_action.is_(True)) & (OperationLog.occurred_at < corte_acciones),
            )
        )
    )
    await session.commit()
    return result.rowcount or 0

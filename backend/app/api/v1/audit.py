"""Bitácora de operaciones: qué se le pidió a cada equipo y qué contestó.

Sirve para auditar (quién suspendió a quién) y para comprobar que el dato que
muestra la pantalla salió de verdad del equipo: cada fila guarda las llamadas
que se hicieron, con su URL o su sentencia, su código de respuesta y su demora.

La lectura queda para administración y soporte. Un técnico de campo no tiene
por qué ver la actividad de sus compañeros; si más adelante hace falta, se
abre, pero se empieza cerrado.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.db.session import get_session
from app.models.audit import OperationLog
from app.models.user import UserRole

router = APIRouter(prefix="/audit", tags=["bitácora"])

LECTORES = require_roles(UserRole.ADMIN, UserRole.SOPORTE)


def _row(log: OperationLog) -> Dict[str, Any]:
    try:
        upstream = json.loads(log.upstream) if log.upstream else []
    except ValueError:  # pragma: no cover - defensivo
        upstream = []
    return {
        "id": log.id,
        "occurred_at": log.occurred_at,
        "user_email": log.user_email,
        "user_role": log.user_role,
        "source_ip": log.source_ip,
        "target_kind": log.target_kind,
        "target_id": log.target_id,
        "target_name": log.target_name,
        "operation": log.operation,
        "endpoint": log.endpoint,
        "is_action": log.is_action,
        "ok": log.ok,
        "detail": log.detail,
        "duration_ms": log.duration_ms,
        "upstream_calls": log.upstream_calls,
        # Esto es lo que prueba que el dato es real: las llamadas que
        # efectivamente salieron del servidor hacia el equipo.
        "upstream": upstream,
    }


@router.get(
    "/operations",
    dependencies=[Depends(LECTORES)],
    summary="Últimas operaciones ejecutadas contra los equipos",
)
async def operations(
    session: AsyncSession = Depends(get_session),
    target_kind: Optional[str] = Query(default=None, pattern="^(mikrotik|uisp)$"),
    target_id: Optional[int] = Query(default=None),
    only_actions: bool = Query(default=False, description="Ocultar las consultas"),
    only_errors: bool = Query(default=False),
    hours: int = Query(default=24, ge=1, le=24 * 90),
    limit: int = Query(default=200, ge=1, le=1000),
) -> List[Dict[str, Any]]:
    desde = datetime.now(timezone.utc) - timedelta(hours=hours)
    consulta = select(OperationLog).where(OperationLog.occurred_at >= desde)
    if target_kind:
        consulta = consulta.where(OperationLog.target_kind == target_kind)
    if target_id is not None:
        consulta = consulta.where(OperationLog.target_id == target_id)
    if only_actions:
        consulta = consulta.where(OperationLog.is_action.is_(True))
    if only_errors:
        consulta = consulta.where(OperationLog.ok.is_(False))
    consulta = consulta.order_by(OperationLog.occurred_at.desc(), OperationLog.id.desc()).limit(limit)

    result = await session.execute(consulta)
    return [_row(log) for log in result.scalars().all()]


@router.get(
    "/summary",
    dependencies=[Depends(LECTORES)],
    summary="Totales de la bitácora en las últimas horas",
)
async def summary(
    session: AsyncSession = Depends(get_session),
    hours: int = Query(default=24, ge=1, le=24 * 90),
) -> Dict[str, Any]:
    desde = datetime.now(timezone.utc) - timedelta(hours=hours)
    base = select(func.count()).select_from(OperationLog).where(OperationLog.occurred_at >= desde)

    total = (await session.execute(base)).scalar_one()
    acciones = (await session.execute(base.where(OperationLog.is_action.is_(True)))).scalar_one()
    fallidas = (await session.execute(base.where(OperationLog.ok.is_(False)))).scalar_one()
    llamadas = (
        await session.execute(
            select(func.coalesce(func.sum(OperationLog.upstream_calls), 0)).where(
                OperationLog.occurred_at >= desde
            )
        )
    ).scalar_one()
    ultima = (
        await session.execute(select(func.max(OperationLog.occurred_at)))
    ).scalar_one()

    return {
        "hours": hours,
        "operations": total,
        "actions": acciones,
        "failed": fallidas,
        # Suma de peticiones reales a equipos. Es el número que hay que mirar
        # para saber si la plataforma está hablando con la red o no.
        "upstream_calls": int(llamadas or 0),
        "last_operation_at": ultima,
    }

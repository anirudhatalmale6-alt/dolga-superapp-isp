from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine
from app.models import monitoring as monitoring_models  # noqa: F401  (registra las tablas)
from app.models import network, user  # noqa: F401
from app.services.monitoring import monitor_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # En desarrollo creamos las tablas al arrancar; en producción manda Alembic.
    if settings.ENVIRONMENT == "development":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # El monitoreo usa la fábrica de sesiones que esté activa (los tests y el
    # modo demo la reemplazan por una sobre SQLite).
    task = None
    if settings.MONITOR_ENABLED:
        factory = getattr(app.state, "session_factory", None) or AsyncSessionLocal
        task = asyncio.create_task(monitor_loop(factory))

    yield

    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.2.0",
    description=(
        "Backend de D' OLGA SUPERAPP. Toda la comunicación con MikroTik, UISP y "
        "OLT ocurre aquí: el frontend nunca recibe credenciales de los equipos."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/health", tags=["infra"])
async def health():
    return {"status": "ok", "environment": settings.ENVIRONMENT}

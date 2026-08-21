from fastapi import APIRouter

from app.api.v1 import auth, devices, mikrotik, monitoring

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(devices.router)
api_router.include_router(monitoring.router)
api_router.include_router(mikrotik.router)

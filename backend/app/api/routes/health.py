from typing import Any

import httpx
from fastapi import APIRouter, Depends
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.redis import get_redis_client

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/health/ready")
async def ready(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    status = "ready"

    try:
        db.execute(text("select 1"))
        checks["postgresql"] = {"status": "ok"}
    except Exception as exc:
        checks["postgresql"] = {"status": "error", "detail": type(exc).__name__}
        status = "degraded"

    redis = get_redis_client()
    try:
        if redis is None:
            raise RedisError("client unavailable")
        redis.ping()
        checks["redis"] = {"status": "ok"}
    except Exception as exc:
        checks["redis"] = {"status": "error", "detail": type(exc).__name__}
        if settings.redis_required:
            status = "degraded"

    checks["scheduler"] = {"status": "ok"}

    async with httpx.AsyncClient(timeout=2.0) as client:
        if settings.ai_api_key and settings.ai_chat_model:
            try:
                await client.get(f"{settings.ai_base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {settings.ai_api_key}"})
                checks["ai_endpoint"] = {"status": "ok"}
            except Exception as exc:
                checks["ai_endpoint"] = {"status": "error", "detail": type(exc).__name__}
                status = "degraded"
        else:
            checks["ai_endpoint"] = {"status": "not_configured"}

        checks["openwa"] = {"status": "configured" if settings.openwa_api_base_url else "not_configured"}
        checks["n8n"] = {"status": "configured" if settings.n8n_email_webhook_url else "not_configured"}

    return {"status": status, "checks": checks}

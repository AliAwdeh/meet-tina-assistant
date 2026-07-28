from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.entities import Email, N8nRequest
from app.schemas.integrations import N8nCallback
from app.security.signatures import SignatureParts, verify_hmac
from app.services.audit import write_audit
from app.services.replay import claim_replay_key

router = APIRouter()


@router.post("/callback")
async def callback(
    request: Request,
    x_request_id: str | None = Header(default=None, alias="X-Request-ID"),
    x_timestamp: str | None = Header(default=None, alias="X-Timestamp"),
    x_signature: str | None = Header(default=None, alias="X-Signature"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(status_code=415, detail="Expected application/json")
    if not x_request_id or not x_timestamp:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing callback signature headers")
    body = await request.body()
    parts = SignatureParts("POST", request.url.path, x_timestamp, x_request_id, body)
    if not verify_hmac(settings.n8n_callback_secret, parts, x_signature, settings.n8n_replay_window_seconds):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid callback signature")
    if not claim_replay_key(db, source="n8n", key=x_request_id, ttl_seconds=settings.n8n_replay_window_seconds):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Duplicate callback request id")
    payload = N8nCallback.model_validate_json(body)
    record = db.scalar(select(N8nRequest).where(N8nRequest.request_id == payload.request_id))
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown n8n request")
    record.status = payload.status
    record.response = payload.model_dump(mode="json")
    email = db.scalar(select(Email).where(Email.n8n_request_id == payload.request_id))
    if email is not None:
        email.status = payload.status
    write_audit(
        db,
        actor_type="n8n",
        actor_id=None,
        action="n8n_callback",
        entity_type="n8n_request",
        entity_id=record.id,
        safe_metadata={"status": payload.status},
    )
    db.commit()
    return {"status": "accepted"}

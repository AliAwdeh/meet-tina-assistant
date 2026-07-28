import time
import uuid
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.entities import N8nRequest
from app.schemas.integrations import N8nEmailPayload
from app.security.signatures import SignatureParts, hmac_signature


class N8nClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send_email(self, db: Session, payload: N8nEmailPayload) -> N8nRequest:
        if not self.settings.n8n_email_webhook_url:
            request = N8nRequest(
                request_id=payload.request_id,
                operation=payload.operation,
                status="queued",
                payload=payload.model_dump(mode="json"),
                response={"skipped": "N8N_EMAIL_WEBHOOK_URL not configured"},
                idempotency_key=payload.idempotency_key,
            )
            db.add(request)
            db.flush()
            return request

        body = payload.model_dump_json().encode("utf-8")
        request_id = str(uuid.uuid4())
        timestamp = str(int(time.time()))
        parsed = urlparse(self.settings.n8n_email_webhook_url)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        parts = SignatureParts("POST", path, timestamp, request_id, body)
        headers = {
            "Authorization": f"Bearer {self.settings.n8n_outbound_token}",
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
            "X-Timestamp": timestamp,
            "X-Signature": hmac_signature(self.settings.n8n_outbound_token, parts),
        }
        record = N8nRequest(
            request_id=payload.request_id,
            operation=payload.operation,
            status="queued",
            payload=payload.model_dump(mode="json"),
            idempotency_key=payload.idempotency_key,
        )
        db.add(record)
        db.flush()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(self.settings.n8n_email_webhook_url, content=body, headers=headers)
            record.status = "sent_to_n8n"
            record.response = {"status_code": response.status_code, "body": response.text[:2000]}
            response.raise_for_status()
        db.flush()
        return record

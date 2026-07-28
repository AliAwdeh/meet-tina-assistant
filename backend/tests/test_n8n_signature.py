import json
import time

from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.entities import N8nRequest
from app.security.signatures import SignatureParts, hmac_signature


def test_n8n_rejects_forged_callback(client: TestClient) -> None:
    response = client.post("/api/integrations/n8n/callback", json={"request_id": "x"}, headers={"X-Signature": "bad"})
    assert response.status_code == 401


def test_n8n_accepts_signed_callback(client: TestClient) -> None:
    with SessionLocal() as db:
        db.add(
            N8nRequest(
                request_id="email-1",
                operation="send_email",
                status="sent_to_n8n",
                payload={"operation": "send_email"},
                idempotency_key="email-key-1",
            )
        )
        db.commit()

    body = json.dumps({"request_id": "email-1", "operation": "send_email", "status": "failed"}).encode()
    timestamp = str(int(time.time()))
    request_id = "callback-1"
    parts = SignatureParts("POST", "/api/integrations/n8n/callback", timestamp, request_id, body)
    response = client.post(
        "/api/integrations/n8n/callback",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
            "X-Timestamp": timestamp,
            "X-Signature": hmac_signature("test-callback", parts),
        },
    )
    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}

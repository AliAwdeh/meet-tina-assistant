from fastapi.testclient import TestClient


def _event(message_id: str = "msg-1") -> dict[str, object]:
    return {
        "id": message_id,
        "chatId": "96170000000@c.us",
        "from": "+96170000000",
        "body": "Remind me tomorrow to call Sami",
        "type": "chat",
        "sessionId": "session",
    }


def test_openwa_accepts_general_greeting(client: TestClient) -> None:
    event = _event("msg-greeting")
    event["body"] = "Hi"
    response = client.post("/webhooks/openwa", json=event, headers={"X-OpenWA-Token": "test-openwa"})
    assert response.status_code == 200
    assert response.json()["status"] == "processed"


def test_openwa_rejects_bad_token(client: TestClient) -> None:
    response = client.post("/webhooks/openwa", json=_event(), headers={"X-OpenWA-Token": "wrong"})
    assert response.status_code == 401


def test_openwa_accepts_query_token(client: TestClient) -> None:
    response = client.post("/webhooks/openwa?token=test-openwa", json=_event("msg-query-token"))
    assert response.status_code == 200
    assert response.json()["status"] == "processed"


def test_openwa_duplicate_event_is_idempotent(client: TestClient) -> None:
    headers = {"X-OpenWA-Token": "test-openwa"}
    first = client.post("/webhooks/openwa", json=_event(), headers=headers)
    second = client.post("/webhooks/openwa", json=_event(), headers=headers)
    assert first.status_code == 200
    assert first.json()["status"] == "processed"
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"

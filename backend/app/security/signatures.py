import hashlib
import hmac
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class SignatureParts:
    method: str
    request_path: str
    timestamp: str
    request_id: str
    body: bytes


def sha256_body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_string(parts: SignatureParts) -> str:
    return "\n".join(
        [
            parts.method.upper(),
            parts.request_path,
            parts.timestamp,
            parts.request_id,
            sha256_body_hash(parts.body),
        ]
    )


def hmac_signature(secret: str, parts: SignatureParts) -> str:
    digest = hmac.new(secret.encode("utf-8"), canonical_string(parts).encode("utf-8"), hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_hmac(secret: str, parts: SignatureParts, received: str | None, window_seconds: int) -> bool:
    if not secret or not received:
        return False
    try:
        timestamp = int(parts.timestamp)
    except ValueError:
        return False
    if abs(int(time.time()) - timestamp) > window_seconds:
        return False
    expected = hmac_signature(secret, parts)
    return hmac.compare_digest(expected, received)

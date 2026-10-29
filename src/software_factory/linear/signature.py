import hashlib
import hmac


def verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    """Valida o header `Linear-Signature` (HMAC-SHA256 hex do corpo bruto)."""
    if not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def is_fresh(timestamp_ms: int, now_ms: int, tolerance_s: int = 60) -> bool:
    """Rejeita entregas antigas (proteção contra replay)."""
    return abs(now_ms - timestamp_ms) <= tolerance_s * 1000

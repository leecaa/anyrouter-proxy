import hmac
import hashlib
import time

COOKIE_NAME = "bridge_session"
MAX_AGE = 86400


def create_session_token(secret: str) -> str:
    ts = format(int(time.time()), 'x')
    sig = hmac.new(secret.encode(), ts.encode(), hashlib.sha256).hexdigest()
    return f"{ts}:{sig}"


def verify_session_token(token: str, secret: str, max_age: int = MAX_AGE) -> bool:
    if not token or ':' not in token:
        return False
    parts = token.split(':', 1)
    if len(parts) != 2:
        return False
    ts_hex, sig = parts
    try:
        ts = int(ts_hex, 16)
    except ValueError:
        return False
    if time.time() - ts > max_age:
        return False
    expected = hmac.new(secret.encode(), ts_hex.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def verify_password(provided: str, stored: str) -> bool:
    return hmac.compare_digest(provided.encode(), stored.encode())

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings

_security = HTTPBasic(auto_error=False)

_ADMIN_COOKIE_NAME = "azs_admin_session"
_ADMIN_SESSION_TTL_SECONDS = 60 * 60 * 12  # 12 часов


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * ((4 - (len(text) % 4)) % 4)
    return base64.urlsafe_b64decode((text + pad).encode("ascii"))


def _sign(data: bytes, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), data, hashlib.sha256).digest()
    return _b64url_encode(mac)


def make_admin_session_token(*, username: str, secret: str) -> str:
    payload = {
        "u": username,
        "ts": int(time.time()),
    }
    payload_b = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_enc = _b64url_encode(payload_b)
    sig = _sign(payload_enc.encode("ascii"), secret)
    return f"{payload_enc}.{sig}"


def verify_admin_session_token(*, token: str, secret: str) -> bool:
    try:
        payload_enc, sig = token.split(".", 1)
    except ValueError:
        return False

    expected_sig = _sign(payload_enc.encode("ascii"), secret)
    if not secrets.compare_digest(sig, expected_sig):
        return False

    try:
        payload_b = _b64url_decode(payload_enc)
        payload = json.loads(payload_b.decode("utf-8"))
    except Exception:
        return False

    ts = int(payload.get("ts") or 0)
    if ts <= 0:
        return False
    if int(time.time()) - ts > _ADMIN_SESSION_TTL_SECONDS:
        return False

    return True


def is_admin_authenticated(request: Request) -> bool:
    expected_username = (settings.ADMIN_USERNAME or "").strip()
    expected_password = settings.ADMIN_PASSWORD or ""
    if not expected_username or not expected_password:
        return False

    cookie_token = request.cookies.get(_ADMIN_COOKIE_NAME)
    if cookie_token and verify_admin_session_token(token=cookie_token, secret=settings.SECRET_KEY):
        return True

    return False


def require_admin(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_security),
) -> bool:
    expected_username = (settings.ADMIN_USERNAME or "").strip()
    expected_password = settings.ADMIN_PASSWORD or ""

    if not expected_username or not expected_password:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin credentials are not configured",
        )

    # 1) Cookie session
    if is_admin_authenticated(request):
        return True

    # 2) Basic Auth fallback
    if credentials is not None:
        ok_user = secrets.compare_digest(credentials.username, expected_username)
        ok_pass = secrets.compare_digest(credentials.password, expected_password)
        if ok_user and ok_pass:
            return True

    # По умолчанию: 401
    # (оставляем WWW-Authenticate для совместимости; модалка использует /api/admin/login и cookie)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
        headers={"WWW-Authenticate": "Basic"},
    )

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from jose import JWTError, jwt

from app.config import settings


def create_access_token(subject: str) -> str:
    expire = datetime.now(timezone.utc).timestamp() + settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

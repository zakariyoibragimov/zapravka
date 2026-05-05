from __future__ import annotations

from typing import Optional

from passlib.context import CryptContext

from app.services.auth import create_access_token, decode_token

CASHIER_SESSION_COOKIE = "azs_cashier_session"


_pwd_context = CryptContext(schemes=["pbkdf2_sha256", "bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _pwd_context.verify(password, password_hash)


def create_cashier_token(*, cashier_id: int) -> str:
    return create_access_token(f"cashier:{cashier_id}")


def decode_cashier_token(token: str) -> Optional[int]:
    sub = decode_token(token)
    if not sub or not isinstance(sub, str):
        return None
    if not sub.startswith("cashier:"):
        return None
    try:
        return int(sub.split(":", 1)[1])
    except Exception:
        return None

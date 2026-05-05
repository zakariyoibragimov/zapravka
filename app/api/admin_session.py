from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.services.admin_auth import is_admin_authenticated, make_admin_session_token, require_admin
from app.services.action_logs import log_action


router = APIRouter(prefix="/api/admin", tags=["admin-auth"])


class AdminLoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
async def admin_login(payload: AdminLoginIn, response: Response, db: AsyncSession = Depends(get_db)):
    expected_username = (settings.ADMIN_USERNAME or "").strip()
    expected_password = settings.ADMIN_PASSWORD or ""

    if not expected_username or not expected_password:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin credentials are not configured",
        )

    ok_user = secrets.compare_digest(payload.username.strip(), expected_username)
    ok_pass = secrets.compare_digest(payload.password, expected_password)
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    token = make_admin_session_token(username=expected_username, secret=settings.SECRET_KEY)
    response.set_cookie(
        key="azs_admin_session",
        value=token,
        httponly=True,
        secure=False,  # локально http
        samesite="lax",
        max_age=60 * 60 * 12,
        path="/",
    )
    await log_action(
        db,
        actor_type="admin",
        actor_name=expected_username,
        action="admin_login",
        entity_type="session",
    )
    await db.commit()
    return {"ok": True}


@router.post("/logout")
async def admin_logout(response: Response, db: AsyncSession = Depends(get_db)):
    response.delete_cookie(key="azs_admin_session", path="/")
    await log_action(
        db,
        actor_type="admin",
        actor_name=(settings.ADMIN_USERNAME or "admin").strip() or "admin",
        action="admin_logout",
        entity_type="session",
    )
    await db.commit()
    return {"ok": True}


@router.get("/ping")
async def admin_ping(request: Request):
    if not is_admin_authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return {"ok": True}

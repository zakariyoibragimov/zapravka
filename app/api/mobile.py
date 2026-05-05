from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ActionLog, Client, FuelPrice, NewsItem, Transaction
from app.db.session import get_db
from app.config import settings
from app.services.auth import create_access_token, decode_token
from app.services.action_logs import log_action
from app.services.bonus_expiry import expire_bonuses
from app.services.cashier_auth import hash_password, verify_password
from app.services.client_cards import build_client_card_payload
from app.services.sms import force_issue_one_time_code, verify_code
from app.services.qr import generate_qr_base64, make_client_qr_payload
from app.services.transactions import PHONE_VALIDATION_ERROR, get_or_create_client, normalize_phone

router = APIRouter(prefix="/api/mobile", tags=["mobile"])
security = HTTPBearer()
PHOTO_DATA_URL_MAX_LENGTH = 2_500_000
CLIENT_PASSWORD_MIN_LENGTH = 6


def _normalize_profile_text(value: Optional[str], *, max_length: int) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:max_length]


def _compose_client_name(client: Client) -> Optional[str]:
    parts = [client.last_name, client.first_name, client.patronymic]
    normalized_parts = [part.strip() for part in parts if part and part.strip()]
    return " ".join(normalized_parts) or None


def _validate_photo_data_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if not cleaned.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="Фотография должна быть изображением")
    if len(cleaned) > PHOTO_DATA_URL_MAX_LENGTH:
        raise HTTPException(status_code=400, detail="Фотография слишком большая")
    return cleaned


def _mobile_profile_payload(client: Client) -> dict:
    full_name = _compose_client_name(client) or client.name
    return {
        "id": client.id,
        "phone": client.phone,
        "phone_verified": bool(client.phone_verified),
        "has_password": bool(client.password_hash),
        "name": full_name,
        "first_name": client.first_name,
        "last_name": client.last_name,
        "patronymic": client.patronymic,
        "photo_data_url": client.photo_data_url,
        "bonus_balance": str(client.bonus_balance),
        "total_spent": str(client.total_spent),
        "birth_date": client.birth_date,
        "reg_date": client.reg_date,
    }


# ---- Auth ------------------------------------------------------------------ #

class SendCodeRequest(BaseModel):
    phone: str


class VerifyCodeRequest(BaseModel):
    phone: str
    code: str


class PasswordLoginRequest(BaseModel):
    phone: str
    password: str


class PasswordSetRequest(BaseModel):
    password: str = Field(min_length=CLIENT_PASSWORD_MIN_LENGTH, max_length=128)


class PasswordResetRequest(BaseModel):
    phone: str
    code: str
    password: str = Field(min_length=CLIENT_PASSWORD_MIN_LENGTH, max_length=128)


def _validate_client_password(password: str) -> str:
    normalized = password.strip()
    if len(normalized) < CLIENT_PASSWORD_MIN_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Пароль должен быть не короче {CLIENT_PASSWORD_MIN_LENGTH} символов",
        )
    return normalized


async def _get_client_by_phone(db: AsyncSession, phone: str) -> Client | None:
    return (await db.execute(select(Client).where(Client.phone == phone))).scalar_one_or_none()


def _mobile_code_response(detail: str = "Одноразовый код можно получить у кассира") -> dict:
    return {"detail": detail}


async def _has_self_issued_mobile_code(db: AsyncSession, client_id: int) -> bool:
    stmt = select(ActionLog.id).where(
        ActionLog.entity_type == "client",
        ActionLog.entity_id == client_id,
        ActionLog.action == "mobile_code_self_issued",
    ).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none() is not None


def _mobile_password_redirect_response() -> dict:
    return {
        "detail": "Введите пароль",
        "redirect_to_password": True,
        "has_password": True,
    }


def _invalid_code_detail(result) -> str:
    if result.locked:
        return "Неверный код. Попытки закончились, попросите кассира выдать новый код"
    if result.remaining_attempts > 0:
        return f"Неверный код. Осталось попыток: {result.remaining_attempts}"
    return "Код истек или недействителен. Попросите кассира выдать новый код"


@router.post("/auth/send-code")
async def mobile_send_code(body: SendCodeRequest, db: AsyncSession = Depends(get_db)):
    try:
        phone = normalize_phone(body.phone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=PHONE_VALIDATION_ERROR) from exc

    existing_client = await _get_client_by_phone(db, phone)
    if existing_client is not None and existing_client.phone_verified and existing_client.password_hash:
        return _mobile_password_redirect_response()

    client = await get_or_create_client(db, phone)
    if await _has_self_issued_mobile_code(db, client.id):
        await db.commit()
        raise HTTPException(
            status_code=403,
            detail="Повторный код можно получить только у кассира",
        )

    code = await force_issue_one_time_code(phone)
    await log_action(
        db,
        actor_type="client",
        actor_name=phone,
        action="mobile_code_self_issued",
        entity_type="client",
        entity_id=client.id,
        details={"phone": phone},
    )
    await db.commit()
    return {
        "detail": "Код подтверждения выдан",
        "code": code,
        "issued_once": True,
    }


@router.post("/auth/verify")
async def mobile_verify(body: VerifyCodeRequest, db: AsyncSession = Depends(get_db)):
    try:
        phone = normalize_phone(body.phone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=PHONE_VALIDATION_ERROR) from exc

    result = await verify_code(phone, body.code, max_attempts=settings.MOBILE_AUTH_MAX_CODE_ATTEMPTS)
    if not result.ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_invalid_code_detail(result))

    client = await get_or_create_client(db, phone)
    client.phone_verified = True
    db.add(client)
    await db.commit()

    token = create_access_token(phone)
    return {
        "access_token": token,
        "token_type": "bearer",
        "password_required": not bool(client.password_hash),
    }


@router.post("/auth/login-password")
async def mobile_login_password(body: PasswordLoginRequest, db: AsyncSession = Depends(get_db)):
    try:
        phone = normalize_phone(body.phone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=PHONE_VALIDATION_ERROR) from exc

    client = await _get_client_by_phone(db, phone)
    if client is None or not client.phone_verified or not client.password_hash:
        raise HTTPException(status_code=403, detail="Сначала подтвердите номер одноразовым кодом и создайте пароль")
    if not verify_password(body.password, client.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный номер или пароль")

    token = create_access_token(phone)
    return {"access_token": token, "token_type": "bearer", "password_required": False}


@router.post("/auth/request-password-reset")
async def mobile_request_password_reset(body: SendCodeRequest, db: AsyncSession = Depends(get_db)):
    try:
        phone = normalize_phone(body.phone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=PHONE_VALIDATION_ERROR) from exc

    client = await _get_client_by_phone(db, phone)
    if client is None or not client.phone_verified or not client.password_hash:
        raise HTTPException(status_code=404, detail="Для этого номера пароль еще не настроен")

    return _mobile_code_response("Одноразовый код для сброса можно получить у кассира")


@router.post("/auth/reset-password")
async def mobile_reset_password(body: PasswordResetRequest, db: AsyncSession = Depends(get_db)):
    try:
        phone = normalize_phone(body.phone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=PHONE_VALIDATION_ERROR) from exc

    client = await _get_client_by_phone(db, phone)
    if client is None or not client.phone_verified:
        raise HTTPException(status_code=404, detail="Клиент не найден")

    result = await verify_code(phone, body.code, max_attempts=settings.MOBILE_AUTH_MAX_CODE_ATTEMPTS)
    if not result.ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_invalid_code_detail(result))

    client.password_hash = hash_password(_validate_client_password(body.password))
    db.add(client)
    await db.commit()

    token = create_access_token(phone)
    return {"access_token": token, "token_type": "bearer", "password_required": False}


# ---- Helpers --------------------------------------------------------------- #

async def _get_current_client(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Client:
    phone = decode_token(credentials.credentials)
    if phone is None:
        raise HTTPException(status_code=401, detail="Недействительный токен")
    try:
        phone = normalize_phone(phone)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Недействительный токен") from exc
    stmt = select(Client).where(Client.phone == phone)
    result = await db.execute(stmt)
    client = result.scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    return client


@router.post("/auth/set-password")
async def mobile_set_password(
    body: PasswordSetRequest,
    client: Client = Depends(_get_current_client),
    db: AsyncSession = Depends(get_db),
):
    client.phone_verified = True
    client.password_hash = hash_password(_validate_client_password(body.password))
    db.add(client)
    await db.commit()
    return {"detail": "Пароль сохранен"}


# ---- Profile --------------------------------------------------------------- #

@router.get("/profile")
async def mobile_profile(client: Client = Depends(_get_current_client), db: AsyncSession = Depends(get_db)):
    await expire_bonuses(db, client_id=client.id, commit=True)
    return _mobile_profile_payload(client)


@router.get("/transactions")
async def mobile_transactions(
    client: Client = Depends(_get_current_client),
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
):
    stmt = (
        select(Transaction)
        .where(Transaction.client_id == client.id)
        .order_by(Transaction.ts.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    txs = result.scalars().all()
    return [
        {
            "id": t.id,
            "ts": t.ts,
            "type": t.type,
            "amount_bonus": str(t.amount_bonus),
            "purchase_amount": str(t.purchase_amount) if t.purchase_amount else None,
            "location": t.location,
        }
        for t in txs
    ]


@router.get("/client-card")
async def mobile_client_card(
    client: Client = Depends(_get_current_client),
    db: AsyncSession = Depends(get_db),
):
    payload = await build_client_card_payload(db, client=client)
    payload["name"] = _compose_client_name(client) or client.name or ""
    return payload


@router.get("/qrcode")
async def mobile_qrcode(client: Client = Depends(_get_current_client)):
    img_b64 = generate_qr_base64(make_client_qr_payload(client.phone))
    return {"qr_base64": img_b64, "phone": client.phone}


@router.get("/news")
async def mobile_news(
    _client: Client = Depends(_get_current_client),
    db: AsyncSession = Depends(get_db),
    limit: int = 20,
):
    def normalize_news_signature(value: str, *, multiline: bool) -> str:
        text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
        if multiline:
            text = "\n".join(line.strip() for line in text.split("\n") if line.strip())
        else:
            text = re.sub(r"\s+", " ", text)
        return text.strip().casefold()

    limit = max(1, min(limit, 50))
    result = await db.execute(
        select(NewsItem)
        .where(NewsItem.is_active.is_(True))
        .order_by(NewsItem.published_at.desc(), NewsItem.id.desc())
        .limit(limit)
    )
    items = result.scalars().all()
    seen_signatures: set[tuple[str, str]] = set()
    payload: list[dict] = []
    for item in items:
        title = re.sub(r"\s+", " ", (item.title or "")).strip()
        body = (item.body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(title) < 3 or len(body) < 3:
            continue
        signature = (
            normalize_news_signature(title, multiline=False),
            normalize_news_signature(body, multiline=True),
        )
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        payload.append(
            {
                "id": item.id,
                "title": title,
                "body": body,
                "published_at": item.published_at,
            }
        )
    return payload


class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    first_name: Optional[str] = Field(default=None, max_length=100)
    last_name: Optional[str] = Field(default=None, max_length=100)
    patronymic: Optional[str] = Field(default=None, max_length=100)
    photo_data_url: Optional[str] = None
    birth_date: Optional[str] = None  # YYYY-MM-DD or empty string


@router.patch("/profile")
async def mobile_update_profile(
    body: ProfileUpdateRequest,
    client: Client = Depends(_get_current_client),
    db: AsyncSession = Depends(get_db),
):
    from datetime import date
    updated_fields = body.model_fields_set

    if "first_name" in updated_fields:
        client.first_name = _normalize_profile_text(body.first_name, max_length=100)
    if "last_name" in updated_fields:
        client.last_name = _normalize_profile_text(body.last_name, max_length=100)
    if "patronymic" in updated_fields:
        client.patronymic = _normalize_profile_text(body.patronymic, max_length=100)
    if "photo_data_url" in updated_fields:
        client.photo_data_url = _validate_photo_data_url(body.photo_data_url)

    if "name" in updated_fields and not any(
        field in updated_fields for field in ("first_name", "last_name", "patronymic")
    ):
        legacy_name = _normalize_profile_text(body.name, max_length=200)
        client.first_name = legacy_name
        client.last_name = None
        client.patronymic = None

    if "birth_date" in updated_fields:
        if body.birth_date.strip():
            try:
                client.birth_date = date.fromisoformat(body.birth_date.strip())
            except ValueError:
                raise HTTPException(status_code=400, detail="Неверный формат даты")
        else:
            client.birth_date = None

    client.name = _compose_client_name(client)
    db.add(client)
    await db.commit()
    await db.refresh(client)
    return _mobile_profile_payload(client)


@router.get("/rules")
async def mobile_rules(db: AsyncSession = Depends(get_db)):
    from app.db.models import AccrualRule
    result = await db.execute(select(AccrualRule))
    rules = result.scalars().all()
    return [
        {
            "id": r.id,
            "location": r.location,
            "accrual_type": r.accrual_type,
            "accrual_value": str(r.accrual_value),
            "min_purchase": str(r.min_purchase),
            "active_from": r.active_from,
            "active_to": r.active_to,
        }
        for r in rules
    ]


@router.get("/fuel-prices")
async def mobile_fuel_prices(
    _client: Client = Depends(_get_current_client),
    db: AsyncSession = Depends(get_db),
):
    # _client нужен для авторизации (проверяем токен)
    result = await db.execute(
        select(FuelPrice)
        .where(FuelPrice.is_active.is_(True))
        .order_by(FuelPrice.id)
    )
    fuels = result.scalars().all()
    return [
        {
            "id": f.id,
            "code": f.code,
            "name": f.name,
            "price_per_liter": str(f.price_per_liter),
            "updated_at": f.updated_at,
        }
        for f in fuels
    ]

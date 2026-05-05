from __future__ import annotations

from datetime import date
from decimal import Decimal
import re
from typing import Optional

from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, not_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AccrualRule,
    AccrualType,
    Campaign,
    Cashier,
    CashierShift,
    Client,
    ClientLevel,
    FuelSale,
    Location,
    NewsItem,
    Transaction,
)
from app.db.session import get_db
from app.services.action_logs import log_action
from app.services.admin_auth import require_admin
from app.services.app_settings import BONUS_EXPIRY_MONTHS_KEY, get_bonus_expiry_months, get_setting_entry, set_setting
from app.services.cashier_auth import hash_password
from app.services.suspicious_activity import build_suspicious_activity_alerts
from app.services.transactions import cancel_receipt
from app.config import settings

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _admin_actor_name() -> str:
    return (settings.ADMIN_USERNAME or "admin").strip() or "admin"


class BonusExpirySettingsIn(BaseModel):
    months: int = Field(ge=1, le=60)


class CancelReceiptRequest(BaseModel):
    check_id: str = Field(min_length=1, max_length=100)
    reason: Optional[str] = Field(default=None, max_length=500)


def _bonus_expiry_settings_payload(*, months: int, source: str, updated_at=None) -> dict:
    preview_date = date.today() + relativedelta(months=months)
    return {
        "months": months,
        "source": source,
        "applies_to": "new_accruals_only",
        "preview_expiry_date": preview_date.isoformat(),
        "preview_expiry_date_label": preview_date.strftime("%d.%m.%Y"),
        "updated_at": updated_at.isoformat() if updated_at else None,
        "updated_at_label": updated_at.strftime("%d.%m.%Y %H:%M") if updated_at else None,
    }


# ---- News ----------------------------------------------------------------- #

class NewsCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=3, max_length=2000)
    is_active: bool = True


class NewsStatusUpdate(BaseModel):
    is_active: bool


def _normalize_news_text(value: str, *, multiline: bool) -> str:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    if multiline:
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
        normalized_lines: list[str] = []
        blank_pending = False
        for line in lines:
            if not line:
                if normalized_lines:
                    blank_pending = True
                continue
            if blank_pending:
                normalized_lines.append("")
                blank_pending = False
            normalized_lines.append(line)
        return "\n".join(normalized_lines).strip()
    return re.sub(r"\s+", " ", text).strip()


def _news_public(item: NewsItem) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "body": item.body,
        "is_active": item.is_active,
        "created_at": item.created_at,
        "published_at": item.published_at,
    }


def _news_public_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "body": row["body"],
        "is_active": row["is_active"],
        "created_at": row["created_at"],
        "published_at": row["published_at"],
    }


@router.get("/news")
async def list_news(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(
            NewsItem.id,
            NewsItem.title,
            NewsItem.body,
            NewsItem.is_active,
            NewsItem.created_at,
            NewsItem.published_at,
        ).order_by(NewsItem.published_at.desc(), NewsItem.id.desc())
    )
    return [_news_public_row(row) for row in result.mappings().all()]


@router.post("/news", status_code=201)
async def create_news(body: NewsCreate, db: AsyncSession = Depends(get_db)):
    title = _normalize_news_text(body.title, multiline=False)
    news_body = _normalize_news_text(body.body, multiline=True)
    if len(title) < 3 or len(news_body) < 3:
        raise HTTPException(422, "Заголовок и текст новости должны содержать смысловой текст")
    item = NewsItem(
        title=title,
        body=news_body,
        is_active=body.is_active,
    )
    db.add(item)
    await db.flush()
    await log_action(
        db,
        actor_type="admin",
        actor_name=_admin_actor_name(),
        action="news_created",
        entity_type="news",
        entity_id=item.id,
        details={"title": item.title},
    )
    await db.commit()
    await db.refresh(item)
    return _news_public(item)


@router.put("/news/{news_id}")
async def update_news(news_id: int, body: NewsCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(NewsItem).where(NewsItem.id == news_id))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "Новость не найдена")

    title = _normalize_news_text(body.title, multiline=False)
    news_body = _normalize_news_text(body.body, multiline=True)
    if len(title) < 3 or len(news_body) < 3:
        raise HTTPException(422, "Заголовок и текст новости должны содержать смысловой текст")

    item.title = title
    item.body = news_body
    item.is_active = body.is_active
    await log_action(
        db,
        actor_type="admin",
        actor_name=_admin_actor_name(),
        action="news_updated",
        entity_type="news",
        entity_id=item.id,
        details={"title": item.title, "is_active": item.is_active},
    )
    await db.commit()
    await db.refresh(item)
    return _news_public(item)


@router.patch("/news/{news_id}/status")
async def update_news_status(news_id: int, body: NewsStatusUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(NewsItem).where(NewsItem.id == news_id))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "Новость не найдена")

    item.is_active = body.is_active
    await log_action(
        db,
        actor_type="admin",
        actor_name=_admin_actor_name(),
        action="news_status_updated",
        entity_type="news",
        entity_id=item.id,
        details={"title": item.title, "is_active": item.is_active},
    )
    await db.commit()
    await db.refresh(item)
    return _news_public(item)


@router.delete("/news/{news_id}", status_code=204)
async def delete_news(news_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(NewsItem).where(NewsItem.id == news_id))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "Новость не найдена")
    await log_action(
        db,
        actor_type="admin",
        actor_name=_admin_actor_name(),
        action="news_deleted",
        entity_type="news",
        entity_id=item.id,
        details={"title": item.title},
    )
    await db.delete(item)
    await db.commit()


@router.get("/suspicious-activity")
async def suspicious_activity(db: AsyncSession = Depends(get_db)):
    alerts = await build_suspicious_activity_alerts(db)
    return {"items": alerts, "count": len(alerts)}


@router.get("/settings/bonus-expiry")
async def get_bonus_expiry_settings(db: AsyncSession = Depends(get_db)):
    months = await get_bonus_expiry_months(db)
    entry = await get_setting_entry(db, BONUS_EXPIRY_MONTHS_KEY)
    return _bonus_expiry_settings_payload(
        months=months,
        source="admin" if entry is not None else "default",
        updated_at=entry.updated_at if entry is not None else None,
    )


@router.put("/settings/bonus-expiry")
async def update_bonus_expiry_settings(body: BonusExpirySettingsIn, db: AsyncSession = Depends(get_db)):
    entry = await set_setting(db, BONUS_EXPIRY_MONTHS_KEY, str(body.months))
    await log_action(
        db,
        actor_type="admin",
        actor_name=_admin_actor_name(),
        action="bonus_expiry_updated",
        entity_type="setting",
        details={"months": body.months},
    )
    await db.commit()
    await db.refresh(entry)
    return _bonus_expiry_settings_payload(months=body.months, source="admin", updated_at=entry.updated_at)


@router.post("/cancel-receipt")
async def admin_cancel_receipt(body: CancelReceiptRequest, db: AsyncSession = Depends(get_db)):
    try:
        summary = await cancel_receipt(
            db,
            check_id=body.check_id,
            cashier_id=None,
            cashier_name=_admin_actor_name(),
            reason=body.reason,
        )
        await log_action(
            db,
            actor_type="admin",
            actor_name=_admin_actor_name(),
            action="receipt_cancelled",
            entity_type="receipt",
            check_id=body.check_id.strip(),
            details=summary | {"reason": (body.reason or "").strip() or None},
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return summary


# ---- Accrual Rules --------------------------------------------------------- #

class RuleCreate(BaseModel):
    location: Location
    accrual_type: AccrualType
    accrual_value: Decimal
    min_purchase: Decimal = Decimal("0")
    active_from: date
    active_to: Optional[date] = None


def _rule_public_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "location": row["location"],
        "client_level": row["client_level"],
        "accrual_type": row["accrual_type"],
        "accrual_value": row["accrual_value"],
        "min_purchase": row["min_purchase"],
        "active_from": row["active_from"],
        "active_to": row["active_to"],
    }


@router.get("/rules")
async def list_rules(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(
            AccrualRule.id,
            AccrualRule.location,
            AccrualRule.client_level,
            AccrualRule.accrual_type,
            AccrualRule.accrual_value,
            AccrualRule.min_purchase,
            AccrualRule.active_from,
            AccrualRule.active_to,
        ).order_by(AccrualRule.id)
    )
    return [_rule_public_row(row) for row in result.mappings().all()]


@router.post("/rules", status_code=201)
async def create_rule(body: RuleCreate, db: AsyncSession = Depends(get_db)):
    data = body.model_dump()
    data["client_level"] = ClientLevel.bronze
    rule = AccrualRule(**data)
    db.add(rule)
    await db.flush()
    await log_action(
        db,
        actor_type="admin",
        actor_name=_admin_actor_name(),
        action="rule_created",
        entity_type="rule",
        entity_id=rule.id,
        details={"location": str(rule.location.value), "type": str(rule.accrual_type.value)},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put("/rules/{rule_id}")
async def update_rule(rule_id: int, body: RuleCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AccrualRule).where(AccrualRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "Правило не найдено")
    data = body.model_dump()
    data["client_level"] = ClientLevel.bronze
    for k, v in data.items():
        setattr(rule, k, v)
    await log_action(
        db,
        actor_type="admin",
        actor_name=_admin_actor_name(),
        action="rule_updated",
        entity_type="rule",
        entity_id=rule.id,
        details={"location": str(rule.location.value), "type": str(rule.accrual_type.value)},
    )
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AccrualRule).where(AccrualRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "Правило не найдено")
    await log_action(
        db,
        actor_type="admin",
        actor_name=_admin_actor_name(),
        action="rule_deleted",
        entity_type="rule",
        entity_id=rule.id,
        details={"location": str(rule.location.value), "type": str(rule.accrual_type.value)},
    )
    await db.delete(rule)
    await db.commit()


# ---- Campaigns ------------------------------------------------------------- #

class CampaignCreate(BaseModel):
    name: str
    multiplier: Decimal
    start_date: date
    end_date: Optional[date] = None
    days_of_week: Optional[str] = None
    hours_start: Optional[int] = None
    hours_end: Optional[int] = None


@router.get("/campaigns")
async def list_campaigns(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).order_by(Campaign.id))
    return result.scalars().all()


@router.post("/campaigns", status_code=201)
async def create_campaign(body: CampaignCreate, db: AsyncSession = Depends(get_db)):
    c = Campaign(**body.model_dump())
    db.add(c)
    await db.flush()
    await log_action(
        db,
        actor_type="admin",
        actor_name=_admin_actor_name(),
        action="campaign_created",
        entity_type="campaign",
        entity_id=c.id,
        details={"name": c.name},
    )
    await db.commit()
    await db.refresh(c)
    return c


@router.put("/campaigns/{campaign_id}")
async def update_campaign(campaign_id: int, body: CampaignCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Акция не найдена")
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    await log_action(
        db,
        actor_type="admin",
        actor_name=_admin_actor_name(),
        action="campaign_updated",
        entity_type="campaign",
        entity_id=c.id,
        details={"name": c.name},
    )
    await db.commit()
    await db.refresh(c)
    return c


@router.delete("/campaigns/{campaign_id}", status_code=204)
async def delete_campaign(campaign_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Акция не найдена")
    await log_action(
        db,
        actor_type="admin",
        actor_name=_admin_actor_name(),
        action="campaign_deleted",
        entity_type="campaign",
        entity_id=c.id,
        details={"name": c.name},
    )
    await db.delete(c)
    await db.commit()


# ---- Cashiers -------------------------------------------------------------- #

class CashierCreate(BaseModel):
    name: str
    azs_id: Optional[int] = None
    username: Optional[str] = Field(default=None, min_length=1, max_length=64)
    password: Optional[str] = Field(default=None, min_length=4, max_length=200)


class CashierUpdate(BaseModel):
    name: Optional[str] = None
    azs_id: Optional[int] = None
    is_active: Optional[bool] = None
    username: Optional[str] = Field(default=None, min_length=1, max_length=64)
    password: Optional[str] = Field(default=None, min_length=4, max_length=200)


class ClientPasswordUpdate(BaseModel):
    password: str = Field(min_length=6, max_length=128)


def _cashier_public(c: Cashier) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "username": c.username,
        "azs_id": c.azs_id,
        "is_active": c.is_active,
    }


def _cashier_public_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "username": row["username"],
        "azs_id": row["azs_id"],
        "is_active": row["is_active"],
    }


def _normalize_username(username: Optional[str]) -> Optional[str]:
    if username is None:
        return None
    username = username.strip()
    return username or None


def _is_cashier_archived(c: Cashier) -> bool:
    return not c.is_active and c.username is None and c.password_hash is None


async def _ensure_username_is_unique(
    db: AsyncSession,
    username: Optional[str],
    *,
    exclude_cashier_id: Optional[int] = None,
) -> None:
    if username is None:
        return

    stmt = select(Cashier.id).where(Cashier.username == username)
    if exclude_cashier_id is not None:
        stmt = stmt.where(Cashier.id != exclude_cashier_id)

    existing_cashier_id = (await db.execute(stmt.limit(1))).scalar_one_or_none()
    if existing_cashier_id is not None:
        raise HTTPException(400, "Логин уже занят")


async def _cashier_has_history(db: AsyncSession, cashier_id: int) -> bool:
    refs = (
        select(CashierShift.id).where(CashierShift.cashier_id == cashier_id).limit(1),
        select(Transaction.id).where(Transaction.cashier_id == cashier_id).limit(1),
        select(FuelSale.id).where(FuelSale.cashier_id == cashier_id).limit(1),
    )
    for stmt in refs:
        if (await db.execute(stmt)).scalar_one_or_none() is not None:
            return True
    return False


@router.get("/cashiers")
async def list_cashiers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(
            Cashier.id,
            Cashier.name,
            Cashier.username,
            Cashier.azs_id,
            Cashier.is_active,
        )
        .where(
            not_(
                and_(
                    Cashier.is_active.is_(False),
                    Cashier.username.is_(None),
                    Cashier.password_hash.is_(None),
                )
            )
        )
        .order_by(Cashier.id)
    )
    return [_cashier_public_row(row) for row in result.mappings().all()]


@router.post("/cashiers", status_code=201)
async def create_cashier(body: CashierCreate, db: AsyncSession = Depends(get_db)):
    data = body.model_dump(exclude={"password"})
    data["username"] = _normalize_username(data.get("username"))
    password = body.password
    if password:
        data["password_hash"] = hash_password(password)
    await _ensure_username_is_unique(db, data.get("username"))
    c = Cashier(**data)
    db.add(c)
    try:
        await db.flush()
        await log_action(
            db,
            actor_type="admin",
            actor_name=_admin_actor_name(),
            action="cashier_created",
            entity_type="cashier",
            entity_id=c.id,
            details={"name": c.name, "username": c.username},
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(400, "Не удалось создать кассира")
    await db.refresh(c)
    return _cashier_public(c)


@router.put("/cashiers/{cashier_id}")
async def update_cashier(cashier_id: int, body: CashierUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Cashier).where(Cashier.id == cashier_id))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Кассир не найден")

    data = body.model_dump(exclude_unset=True, exclude={"password"})
    if "username" in data:
        data["username"] = _normalize_username(data.get("username"))
        await _ensure_username_is_unique(db, data.get("username"), exclude_cashier_id=cashier_id)
    for k, v in data.items():
        setattr(c, k, v)
    if body.password:
        c.password_hash = hash_password(body.password)
    try:
        await log_action(
            db,
            actor_type="admin",
            actor_name=_admin_actor_name(),
            action="cashier_updated",
            entity_type="cashier",
            entity_id=c.id,
            details={"name": c.name, "username": c.username, "is_active": c.is_active},
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(400, "Не удалось сохранить кассира")
    await db.refresh(c)
    return _cashier_public(c)


@router.delete("/cashiers/{cashier_id}")
async def delete_cashier(cashier_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Cashier).where(Cashier.id == cashier_id))
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Кассир не найден")

    if await _cashier_has_history(db, cashier_id):
        c.is_active = False
        c.username = None
        c.password_hash = None
        await log_action(
            db,
            actor_type="admin",
            actor_name=_admin_actor_name(),
            action="cashier_archived",
            entity_type="cashier",
            entity_id=c.id,
            details={"name": c.name},
        )
        await db.commit()
        return {"ok": True, "mode": "archived"}

    await log_action(
        db,
        actor_type="admin",
        actor_name=_admin_actor_name(),
        action="cashier_deleted",
        entity_type="cashier",
        entity_id=c.id,
        details={"name": c.name},
    )
    await db.delete(c)
    await db.commit()
    return {"ok": True, "mode": "deleted"}


@router.post("/clients/{client_id}/password")
async def admin_set_client_password(client_id: int, body: ClientPasswordUpdate, db: AsyncSession = Depends(get_db)):
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if client is None:
        raise HTTPException(404, "Клиент не найден")

    password = body.password.strip()
    if len(password) < 6:
        raise HTTPException(400, "Пароль должен быть не короче 6 символов")

    client.phone_verified = True
    client.password_hash = hash_password(password)
    await log_action(
        db,
        actor_type="admin",
        actor_name=_admin_actor_name(),
        action="client_password_updated",
        entity_type="client",
        entity_id=client.id,
        details={"phone": client.phone},
    )
    await db.commit()
    return {"ok": True, "detail": "Пароль клиента обновлен", "password": password}

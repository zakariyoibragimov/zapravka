from __future__ import annotations

from datetime import datetime
import logging
from typing import NamedTuple, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Cashier, CashierShift, FuelSale, Transaction, TransactionType
from app.db.session import get_db
from app.services.action_logs import log_action
from app.services.cashier_auth import CASHIER_SESSION_COOKIE, create_cashier_token, decode_cashier_token, verify_password
from app.utils.datetime import local_now


router = APIRouter(prefix="/api/cashiers", tags=["cashiers"])
security = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)


class CashierLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class CashierLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    cashier: dict


class CashierShiftContext(NamedTuple):
    cashier: Cashier
    shift: CashierShift


def _normalize_dt(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.replace(tzinfo=None)


def _current_shift_dt() -> datetime:
    return local_now()


def _shift_operation_key(check_id: Optional[str], fallback: str) -> str:
    raw = (check_id or '').strip()
    if not raw:
        return fallback
    for suffix in ('-R', '-C-A', '-C-R', '-C-F'):
        if raw.endswith(suffix):
            return raw[:-len(suffix)]
    return raw


def _serialize_shift(shift: CashierShift) -> dict:
    return {
        "id": shift.id,
        "cashier_id": shift.cashier_id,
        "started_at": shift.started_at.isoformat() if shift.started_at else None,
        "ended_at": shift.ended_at.isoformat() if shift.ended_at else None,
    }


def _empty_shift_summary() -> dict:
    return {
        "transactions_count": 0,
        "accrual_transactions_count": 0,
        "redemption_transactions_count": 0,
        "accrued_bonus_total": 0.0,
        "redeemed_bonus_total": 0.0,
        "fuel_sales_count": 0,
        "fuel_sales_total": 0.0,
        "fuel_liters_total": 0.0,
    }


async def get_active_cashier_shift(db: AsyncSession, cashier_id: int) -> Optional[CashierShift]:
    stmt = (
        select(CashierShift)
        .where(CashierShift.cashier_id == cashier_id, CashierShift.ended_at.is_(None))
        .order_by(CashierShift.started_at.desc(), CashierShift.id.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def build_cashier_shift_summary(db: AsyncSession, shift: CashierShift) -> dict:
    shift_start_db = shift.started_at
    shift_end_db = shift.ended_at or _current_shift_dt()
    shift_start = _normalize_dt(shift_start_db)
    shift_end = _normalize_dt(shift_end_db)
    summary = _empty_shift_summary()
    if shift_start is None or shift_end is None:
        return summary

    operation_keys: set[str] = set()

    txs = (
        await db.execute(
            select(Transaction)
            .where(
                Transaction.cashier_id == shift.cashier_id,
                Transaction.ts >= shift_start_db,
                Transaction.ts <= shift_end_db,
            )
            .order_by(Transaction.ts, Transaction.id)
        )
    ).scalars().all()
    for tx in txs:
        tx_ts = _normalize_dt(tx.ts)
        if tx_ts is None or tx_ts < shift_start or tx_ts > shift_end:
            continue
        operation_keys.add(_shift_operation_key(tx.check_id, f"tx:{tx.id}"))
        if tx.type == TransactionType.accrual:
            summary["accrual_transactions_count"] += 1
            summary["accrued_bonus_total"] += float(tx.amount_bonus or 0)
        elif tx.type == TransactionType.redemption:
            summary["redemption_transactions_count"] += 1
            summary["redeemed_bonus_total"] += float(tx.amount_bonus or 0)

    fuel_sales = (
        await db.execute(
            select(FuelSale)
            .where(
                FuelSale.cashier_id == shift.cashier_id,
                FuelSale.sale_date >= shift_start_db,
                FuelSale.sale_date <= shift_end_db,
            )
            .order_by(FuelSale.sale_date, FuelSale.id)
        )
    ).scalars().all()
    for sale in fuel_sales:
        sale_ts = _normalize_dt(sale.sale_date)
        if sale_ts is None or sale_ts < shift_start or sale_ts > shift_end:
            continue
        operation_keys.add(_shift_operation_key(sale.check_id, f"sale:{sale.id}"))
        summary["fuel_sales_count"] += 1
        summary["fuel_sales_total"] += float(sale.total_rub or 0)
        summary["fuel_liters_total"] += float(sale.liters or 0)

    summary["transactions_count"] = len(operation_keys)

    return summary


async def get_current_cashier(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Cashier:
    token: Optional[str] = None
    if credentials is not None:
        token = credentials.credentials
    elif request.cookies.get(CASHIER_SESSION_COOKIE):
        token = request.cookies.get(CASHIER_SESSION_COOKIE)

    if token is None:
        raise HTTPException(status_code=401, detail="Нет токена кассира")
    cashier_id = decode_cashier_token(token)
    if cashier_id is None:
        raise HTTPException(status_code=401, detail="Недействительный токен кассира")
    cashier = (await db.execute(select(Cashier).where(Cashier.id == cashier_id))).scalar_one_or_none()
    if cashier is None or not cashier.is_active:
        raise HTTPException(status_code=401, detail="Кассир не найден или отключён")
    return cashier


async def require_active_cashier_shift(
    cashier: Cashier = Depends(get_current_cashier),
    db: AsyncSession = Depends(get_db),
) -> CashierShiftContext:
    shift = await get_active_cashier_shift(db, cashier.id)
    if shift is None:
        raise HTTPException(status_code=409, detail="Смена не открыта. Откройте смену перед проведением операций.")
    return CashierShiftContext(cashier=cashier, shift=shift)


@router.post("/login", response_model=CashierLoginResponse)
async def cashier_login(body: CashierLoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    stmt = select(Cashier).where(Cashier.username == body.username)
    cashier = (await db.execute(stmt)).scalar_one_or_none()
    if cashier is None or not cashier.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный логин или пароль")
    if not cashier.password_hash:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="У кассира не задан пароль")
    if not verify_password(body.password, cashier.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный логин или пароль")

    now = _current_shift_dt()

    # Закрыть предыдущие незакрытые смены (на случай если кассир не нажал "Выйти")
    await db.execute(
        update(CashierShift)
        .where(CashierShift.cashier_id == cashier.id, CashierShift.ended_at.is_(None))
        .values(ended_at=now)
    )
    await log_action(
        db,
        actor_type="cashier",
        actor_id=cashier.id,
        actor_name=cashier.name,
        action="cashier_login",
        entity_type="session",
    )
    await db.commit()

    token = create_cashier_token(cashier_id=cashier.id)
    response.set_cookie(
        key=CASHIER_SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=60 * 60 * 12,
        path="/",
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "cashier": {
            "id": cashier.id,
            "name": cashier.name,
            "username": cashier.username,
            "azs_id": cashier.azs_id,
        },
    }


@router.post("/logout")
async def cashier_logout(
    response: Response,
    cashier: Cashier = Depends(get_current_cashier),
    db: AsyncSession = Depends(get_db),
):
    now = _current_shift_dt()
    shift = await get_active_cashier_shift(db, cashier.id)
    summary = None
    if shift is not None:
        shift.ended_at = now
        await db.flush()
        summary = await build_cashier_shift_summary(db, shift)
    await log_action(
        db,
        actor_type="cashier",
        actor_id=cashier.id,
        actor_name=cashier.name,
        action="cashier_logout",
        entity_type="session",
        details=summary,
    )
    await db.commit()
    response.delete_cookie(key=CASHIER_SESSION_COOKIE, path="/")
    return {"detail": "ok", "shift_closed": shift is not None, "summary": summary}


@router.get("/shift")
async def cashier_shift_status(
    cashier: Cashier = Depends(get_current_cashier),
    db: AsyncSession = Depends(get_db),
):
    shift = await get_active_cashier_shift(db, cashier.id)
    if shift is None:
        return {"has_active_shift": False, "shift": None, "summary": None}
    try:
        summary = await build_cashier_shift_summary(db, shift)
    except Exception:
        logger.exception("Failed to build active shift summary", extra={"cashier_id": cashier.id, "shift_id": shift.id})
        summary = _empty_shift_summary()
    return {
        "has_active_shift": True,
        "shift": _serialize_shift(shift),
        "summary": summary,
    }


@router.post("/shift/open")
async def cashier_open_shift(
    cashier: Cashier = Depends(get_current_cashier),
    db: AsyncSession = Depends(get_db),
):
    active_shift = await get_active_cashier_shift(db, cashier.id)
    if active_shift is not None:
        raise HTTPException(status_code=409, detail="Смена уже открыта")

    shift = CashierShift(cashier_id=cashier.id, started_at=_current_shift_dt())
    db.add(shift)
    await db.flush()
    await log_action(
        db,
        actor_type="cashier",
        actor_id=cashier.id,
        actor_name=cashier.name,
        action="shift_opened",
        entity_type="cashier_shift",
        entity_id=shift.id,
    )
    await db.commit()
    await db.refresh(shift)
    return {
        "has_active_shift": True,
        "shift": _serialize_shift(shift),
        "summary": await build_cashier_shift_summary(db, shift),
    }


@router.post("/shift/close")
async def cashier_close_shift(
    cashier: Cashier = Depends(get_current_cashier),
    db: AsyncSession = Depends(get_db),
):
    shift = await get_active_cashier_shift(db, cashier.id)
    if shift is None:
        raise HTTPException(status_code=409, detail="Смена уже закрыта")

    shift.ended_at = _current_shift_dt()
    await db.flush()
    summary = await build_cashier_shift_summary(db, shift)
    await log_action(
        db,
        actor_type="cashier",
        actor_id=cashier.id,
        actor_name=cashier.name,
        action="shift_closed",
        entity_type="cashier_shift",
        entity_id=shift.id,
        details=summary,
    )
    await db.commit()
    return {
        "has_active_shift": False,
        "shift": _serialize_shift(shift),
        "summary": summary,
    }


@router.get("/me")
async def cashier_me(cashier: Cashier = Depends(get_current_cashier)):
    return {
        "id": cashier.id,
        "name": cashier.name,
        "username": cashier.username,
        "azs_id": cashier.azs_id,
    }

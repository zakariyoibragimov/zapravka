from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cashiers import CashierShiftContext, require_active_cashier_shift
from app.config import settings
from app.db.models import ActionLog, Client, FuelSale, Location, TransactionType
from app.db.session import get_db
from app.services.action_logs import log_action
from app.services.bonus_expiry import expire_bonuses
from app.services.client_cards import build_client_card_payload
from app.services.qr import extract_phone_from_qr_data, generate_qr_base64, make_client_qr_payload
from app.services.sms import force_issue_one_time_code
from app.services.transactions import PHONE_VALIDATION_ERROR, accrue, cancel_receipt, get_or_create_client, normalize_phone, redeem
from app.services.transactions import Transaction
from app.utils.datetime import local_now

router = APIRouter(prefix="/api/cash", tags=["cash"])


class ClientUpdateRequest(BaseModel):
    name: Optional[str] = None
    birth_date: Optional[str] = None   # ISO date string YYYY-MM-DD or empty



class AccrueRequest(BaseModel):
    phone: str
    purchase_amount: Decimal = Field(gt=0)  # полная сумма чека (для min_purchase и записи)
    cash_amount: Optional[Decimal] = Field(default=None, ge=0)   # сумма оплаченная деньгами (для % расчёта)
    fuel_liters: Optional[Decimal] = None
    cashier_id: Optional[int] = None
    check_id: str
    location: Location


class RedeemRequest(BaseModel):
    phone: str
    purchase_amount: Decimal = Field(gt=0)
    bonus_to_redeem: Decimal = Field(gt=0)
    cashier_id: Optional[int] = None
    check_id: str
    location: Location


class ClientInfoRequest(BaseModel):
    phone: Optional[str] = None
    qr_data: Optional[str] = None


class ClientOneTimeCodeRequest(BaseModel):
    phone: str


class FuelSaleRequest(BaseModel):
    total_rub: Decimal = Field(gt=0)
    fuel_type: str = Field(min_length=1, max_length=100)
    liters: Decimal = Field(gt=0)
    price_per_liter: Decimal = Field(gt=0)
    cashier_name: Optional[str] = None
    phone: Optional[str] = None
    sale_date: Optional[datetime] = None
    check_id: Optional[str] = None


class CancelReceiptRequest(BaseModel):
    check_id: str = Field(min_length=1, max_length=100)
    reason: Optional[str] = Field(default=None, max_length=500)


class ReceiptProcessedRequest(BaseModel):
    check_id: str = Field(min_length=1, max_length=100)
    purchase_amount: Decimal = Field(gt=0)
    location: Location
    has_client: bool = False
    redeem_bonus: Decimal = Field(default=Decimal("0"), ge=0)
    accrued_bonus: Decimal = Field(default=Decimal("0"), ge=0)
    fuel_liters: Optional[Decimal] = Field(default=None, ge=0)


def _tx_response(tx: Transaction) -> dict:
    return {
        "transaction_id": tx.id,
        "client_id": tx.client_id,
        "type": tx.type,
        "amount_bonus": str(tx.amount_bonus),
        "check_id": tx.check_id,
    }


def _client_public_payload(client: Client) -> dict:
    qr_payload = make_client_qr_payload(client.phone)
    return {
        "id": client.id,
        "phone": client.phone,
        "name": client.name,
        "photo_data_url": client.photo_data_url,
        "bonus_balance": str(client.bonus_balance),
        "total_spent": str(client.total_spent),
        "phone_verified": bool(client.phone_verified),
        "has_password": bool(client.password_hash),
        "qr_payload": qr_payload,
        "qr_base64": generate_qr_base64(qr_payload),
    }


def _client_search_statement(search: str):
    query = (search or "").strip()
    stmt = select(Client)
    if not query:
        return stmt.order_by(Client.id.desc())

    like = f"%{query}%"
    compact = "".join(ch for ch in query if ch not in " -()")
    if compact.isdigit() and len(compact) <= 4:
        phone_filter = Client.phone.ilike(f"%{compact}")
    else:
        phone_filter = Client.phone.ilike(like)

    return stmt.where(
        or_(phone_filter, Client.name.ilike(like))
    ).order_by(Client.id.desc())


@router.post("/accrue")
async def cash_accrue(
    body: AccrueRequest,
    db: AsyncSession = Depends(get_db),
    shift_ctx: CashierShiftContext = Depends(require_active_cashier_shift),
):
    try:
        tx = await accrue(
            db,
            phone=body.phone,
            purchase_amount=body.purchase_amount,
            cash_amount=body.cash_amount,
            fuel_liters=body.fuel_liters,
            cashier_id=shift_ctx.cashier.id,
            check_id=body.check_id,
            location=body.location,
        )
        await log_action(
            db,
            actor_type="cashier",
            actor_id=shift_ctx.cashier.id,
            actor_name=shift_ctx.cashier.name,
            action="receipt_accrued",
            entity_type="transaction",
            entity_id=tx.id,
            check_id=body.check_id,
            details={
                "amount_bonus": str(tx.amount_bonus),
                "purchase_amount": str(body.purchase_amount),
                "location": body.location.value if hasattr(body.location, "value") else str(body.location),
            },
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _tx_response(tx)


@router.post("/redeem")
async def cash_redeem(
    body: RedeemRequest,
    db: AsyncSession = Depends(get_db),
    shift_ctx: CashierShiftContext = Depends(require_active_cashier_shift),
):
    try:
        tx = await redeem(
            db,
            phone=body.phone,
            purchase_amount=body.purchase_amount,
            bonus_to_redeem=body.bonus_to_redeem,
            cashier_id=shift_ctx.cashier.id,
            check_id=body.check_id,
            location=body.location,
        )
        await log_action(
            db,
            actor_type="cashier",
            actor_id=shift_ctx.cashier.id,
            actor_name=shift_ctx.cashier.name,
            action="receipt_redeemed",
            entity_type="transaction",
            entity_id=tx.id,
            check_id=body.check_id,
            details={
                "amount_bonus": str(tx.amount_bonus),
                "purchase_amount": str(body.purchase_amount),
                "location": body.location.value if hasattr(body.location, "value") else str(body.location),
            },
        )
        await db.commit()
    except ValueError as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return _tx_response(tx)


@router.post("/fuel-sale")
async def cash_log_fuel_sale(
    body: FuelSaleRequest,
    db: AsyncSession = Depends(get_db),
    shift_ctx: CashierShiftContext = Depends(require_active_cashier_shift),
):
    """Логирование продажи топлива для отчётности (в т.ч. без клиента)."""
    from sqlalchemy import select
    from app.db.models import Client

    try:
        client_id: Optional[int] = None
        if body.phone:
            try:
                phone = normalize_phone(body.phone)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=PHONE_VALIDATION_ERROR) from exc
            client = (await db.execute(select(Client).where(Client.phone == phone))).scalar_one_or_none()
            if client is not None:
                client_id = client.id

        cashier_id: Optional[int] = shift_ctx.cashier.id
        cashier_name: Optional[str] = shift_ctx.cashier.name

        sale_kwargs = {
            "cashier_name": cashier_name,
            "cashier_id": cashier_id,
            "fuel_type": body.fuel_type,
            "liters": body.liters,
            "price_per_liter": body.price_per_liter,
            "total_rub": body.total_rub,
            "client_id": client_id,
            "check_id": (body.check_id or "").strip() or None,
            "sale_date": body.sale_date or local_now(),
        }
        sale = FuelSale(**sale_kwargs)
        db.add(sale)
        await db.flush()
        await log_action(
            db,
            actor_type="cashier",
            actor_id=shift_ctx.cashier.id,
            actor_name=shift_ctx.cashier.name,
            action="fuel_sale_logged",
            entity_type="fuel_sale",
            entity_id=sale.id,
            check_id=sale.check_id,
            details={
                "fuel_type": sale.fuel_type,
                "liters": str(sale.liters),
                "total_rub": str(sale.total_rub),
            },
        )
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise

    return {"id": sale.id}


@router.post("/client-info")
async def cash_client_info(body: ClientInfoRequest, db: AsyncSession = Depends(get_db)):
    client: Client | None = None
    if body.phone:
        query = body.phone.strip()
        if not query:
            raise HTTPException(status_code=400, detail="Укажите phone или qr_data")
        try:
            phone = normalize_phone(query)
        except ValueError:
            phone = None

        if phone is not None:
            client = (await db.execute(select(Client).where(Client.phone == phone))).scalar_one_or_none()
        if client is None:
            matches = (await db.execute(_client_search_statement(query).limit(6))).scalars().all()
            if not matches:
                raise HTTPException(status_code=404, detail="Клиент не найден")
            if len(matches) > 1:
                raise HTTPException(
                    status_code=409,
                    detail="Найдено несколько клиентов. Уточните имя или последние 4 цифры телефона.",
                )
            client = matches[0]
    elif body.qr_data:
        try:
            phone = extract_phone_from_qr_data(body.qr_data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Некорректные данные QR-кода") from exc
        client = (await db.execute(select(Client).where(Client.phone == phone))).scalar_one_or_none()
    else:
        raise HTTPException(status_code=400, detail="Укажите phone или qr_data")

    if client is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")

    await expire_bonuses(db, client_id=client.id, commit=True)

    return _client_public_payload(client)


@router.post("/issue-mobile-code")
async def cash_issue_mobile_code(
    body: ClientOneTimeCodeRequest,
    db: AsyncSession = Depends(get_db),
    shift_ctx: CashierShiftContext = Depends(require_active_cashier_shift),
):
    try:
        phone = normalize_phone(body.phone.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=PHONE_VALIDATION_ERROR) from exc

    client = (await db.execute(select(Client).where(Client.phone == phone))).scalar_one_or_none()
    if client is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")

    code = await force_issue_one_time_code(phone)

    await log_action(
        db,
        actor_type="cashier",
        actor_id=shift_ctx.cashier.id,
        actor_name=shift_ctx.cashier.name,
        action="mobile_code_issued_by_cashier",
        entity_type="client",
        entity_id=client.id,
        details={"phone": client.phone},
    )
    await db.commit()

    return {
        "detail": "Одноразовый код готов",
        "phone": client.phone,
        "code": code,
        "expires_in": settings.SMS_CODE_TTL_SECONDS,
        "has_password": bool(client.password_hash),
        "phone_verified": bool(client.phone_verified),
        "issued_by": shift_ctx.cashier.name,
    }


@router.get("/clients")
async def cash_clients_list(
    db: AsyncSession = Depends(get_db),
    search: str = "",
    limit: int = 100,
    offset: int = 0,
):
    stmt = _client_search_statement(search).limit(limit).offset(offset)
    result = await db.execute(stmt)
    clients = result.scalars().all()
    return [
        {
            "id": c.id,
            "phone": c.phone,
            "name": c.name or "",
            "photo_data_url": c.photo_data_url,
            "bonus_balance": str(c.bonus_balance),
            "total_spent": str(c.total_spent),
            "reg_date": str(c.reg_date) if c.reg_date else None,
        }
        for c in clients
    ]


@router.get("/clients/{client_id}")
async def cash_client_detail(client_id: int, db: AsyncSession = Depends(get_db)):
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if client is None:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Клиент не найден")
    return await build_client_card_payload(db, client=client)


class ClientRegisterRequest(BaseModel):
    phone: str
    name: Optional[str] = None


@router.get("/preview-accrual")
async def cash_preview_accrual(
    location: Location,
    purchase_amount: Decimal,
    fuel_liters: Optional[Decimal] = None,
    db: AsyncSession = Depends(get_db),
):
    """Предпросмотр начисления бонусов без сохранения."""
    from datetime import datetime
    from app.services.accrual import calculate_accrual
    amount_bonus = await calculate_accrual(
        db,
        location=location,
        purchase_amount=purchase_amount,
        fuel_liters=fuel_liters,
        now=local_now(),
    )
    return {"amount_bonus": str(amount_bonus)}


@router.post("/register")
async def cash_register_client(
    body: ClientRegisterRequest,
    db: AsyncSession = Depends(get_db),
    _: CashierShiftContext = Depends(require_active_cashier_shift),
):
    """Быстрая регистрация нового клиента с кассы."""
    from sqlalchemy import select
    from app.db.models import Client
    import datetime

    try:
        phone = normalize_phone(body.phone.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=PHONE_VALIDATION_ERROR) from exc
    try:
        existing = (await db.execute(select(Client).where(Client.phone == phone))).scalar_one_or_none()
        if existing:
            # Обновим имя если передано
            if body.name:
                existing.name = body.name.strip()
            client = existing
        else:
            client = Client(
                phone=phone,
                name=body.name.strip() if body.name else None,
                reg_date=datetime.date.today(),
            )
            db.add(client)
            await db.flush()
        await log_action(
            db,
            actor_type="cashier",
            actor_id=_.cashier.id,
            actor_name=_.cashier.name,
            action="client_registered_cashier",
            entity_type="client",
            entity_id=client.id,
            details={"phone": client.phone, "name": client.name or ""},
        )
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    return _client_public_payload(client)


@router.post("/cancel-receipt")
async def cash_cancel_receipt(
    body: CancelReceiptRequest,
    db: AsyncSession = Depends(get_db),
    shift_ctx: CashierShiftContext = Depends(require_active_cashier_shift),
):
    try:
        summary = await cancel_receipt(
            db,
            check_id=body.check_id,
            cashier_id=shift_ctx.cashier.id,
            cashier_name=shift_ctx.cashier.name,
            reason=body.reason,
        )
        await log_action(
            db,
            actor_type="cashier",
            actor_id=shift_ctx.cashier.id,
            actor_name=shift_ctx.cashier.name,
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


@router.post("/receipt-processed")
async def cash_receipt_processed(
    body: ReceiptProcessedRequest,
    db: AsyncSession = Depends(get_db),
    shift_ctx: CashierShiftContext = Depends(require_active_cashier_shift),
):
    existing = (
        await db.execute(
            select(ActionLog).where(
                ActionLog.action == "receipt_processed",
                ActionLog.check_id == body.check_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {"status": "ok", "duplicate": True}

    actual_redeem_bonus = body.redeem_bonus
    actual_accrued_bonus = body.accrued_bonus
    if body.has_client:
        redeem_tx = (
            await db.execute(
                select(Transaction.amount_bonus).where(
                    Transaction.check_id == f"{body.check_id}-R",
                    Transaction.type == TransactionType.redemption,
                )
            )
        ).scalar_one_or_none()
        accrue_tx = (
            await db.execute(
                select(Transaction.amount_bonus).where(
                    Transaction.check_id == body.check_id,
                    Transaction.type == TransactionType.accrual,
                )
            )
        ).scalar_one_or_none()
        if redeem_tx is not None:
            actual_redeem_bonus = redeem_tx
        if accrue_tx is not None:
            actual_accrued_bonus = accrue_tx

    await log_action(
        db,
        actor_type="cashier",
        actor_id=shift_ctx.cashier.id,
        actor_name=shift_ctx.cashier.name,
        action="receipt_processed",
        entity_type="receipt",
        check_id=body.check_id,
        details={
            "purchase_amount": str(body.purchase_amount),
            "location": body.location.value if hasattr(body.location, "value") else str(body.location),
            "has_client": body.has_client,
            "redeem_bonus": str(actual_redeem_bonus),
            "accrued_bonus": str(actual_accrued_bonus),
            "fuel_liters": str(body.fuel_liters) if body.fuel_liters is not None else None,
        },
    )
    await db.commit()
    return {
        "status": "ok",
        "duplicate": False,
        "redeem_bonus": str(actual_redeem_bonus),
        "accrued_bonus": str(actual_accrued_bonus),
    }


@router.patch("/clients/{client_id}")
async def cash_client_update(client_id: int, body: ClientUpdateRequest, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from app.db.models import Client
    import datetime

    async with db.begin():
        client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
        if client is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Клиент не найден")
        if body.name is not None:
            client.name = body.name.strip() or None
        if body.birth_date is not None:
            if body.birth_date.strip():
                try:
                    client.birth_date = datetime.date.fromisoformat(body.birth_date.strip())
                except ValueError:
                    raise HTTPException(status_code=400, detail="Неверный формат даты (YYYY-MM-DD)")
            else:
                client.birth_date = None
    return {
        "id": client.id,
        "phone": client.phone,
        "name": client.name or "",
        "birth_date": str(client.birth_date) if client.birth_date else None,
    }

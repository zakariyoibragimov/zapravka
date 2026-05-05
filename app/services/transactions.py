from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Client, FuelSale, Location, ReceiptCancellation, Transaction, TransactionType
from app.services.accrual import _get_client_level, calculate_accrual
from app.services.bonus_expiry import add_bonus_expiry, deduct_bonus_expiry_fifo, expire_bonuses
from app.utils.datetime import local_now


PHONE_VALIDATION_ERROR = "Поддерживаются номера России (+7) и Таджикистана (+992)"


def normalize_phone(phone: str) -> str:
    """Normalize supported phone numbers to E.164-like RU/TJ format."""
    digits = "".join(c for c in str(phone or "") if c.isdigit())
    if digits.startswith("00"):
        digits = digits[2:]

    if len(digits) == 10:
        return "+7" + digits
    if len(digits) == 11 and digits[0] in ("7", "8"):
        return "+7" + digits[1:]
    if len(digits) == 9:
        return "+992" + digits
    if len(digits) == 12 and digits.startswith("992"):
        return "+" + digits

    raise ValueError(PHONE_VALIDATION_ERROR)


async def get_or_create_client(session: AsyncSession, phone: str, name: Optional[str] = None) -> Client:
    phone = normalize_phone(phone)
    stmt = select(Client).where(Client.phone == phone).with_for_update()
    result = await session.execute(stmt)
    client = result.scalar_one_or_none()
    if client is None:
        client = Client(phone=phone, name=name)
        session.add(client)
        await session.flush()
    return client


async def accrue(
    session: AsyncSession,
    *,
    phone: str,
    purchase_amount: Decimal,
    cash_amount: Optional[Decimal] = None,
    fuel_liters: Optional[Decimal],
    cashier_id: Optional[int],
    check_id: str,
    location: Location,
    now: Optional[datetime] = None,
) -> Transaction:
    """Начислить бонусы по чеку. Идемпотентно по check_id."""
    if now is None:
        now = local_now()

    # Идемпотентность
    existing_stmt = select(Transaction).where(Transaction.check_id == check_id)
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    client = await get_or_create_client(session, phone)

    # cash_amount — сколько оплачено деньгами (без бонусов)
    # используется для расчёта % начисления; min_purchase проверяется по полной сумме
    effective_cash = cash_amount if cash_amount is not None else purchase_amount

    amount_bonus = await calculate_accrual(
        session,
        location=location,
        purchase_amount=purchase_amount,
        cash_amount=effective_cash,
        fuel_liters=fuel_liters,
        now=now,
    )

    tx = Transaction(
        client_id=client.id,
        ts=now,
        type=TransactionType.accrual,
        amount_bonus=amount_bonus,
        purchase_amount=purchase_amount,
        location=location,
        fuel_liters=fuel_liters,
        cashier_id=cashier_id,
        check_id=check_id,
    )
    session.add(tx)

    client.bonus_balance += amount_bonus
    client.total_spent += purchase_amount
    client.level = _get_client_level(client.total_spent)

    if amount_bonus > 0:
        await add_bonus_expiry(session, client, amount_bonus)

    await session.flush()
    return tx


async def redeem(
    session: AsyncSession,
    *,
    phone: str,
    purchase_amount: Decimal,
    bonus_to_redeem: Decimal,
    cashier_id: Optional[int],
    check_id: str,
    location: Location,
    now: Optional[datetime] = None,
) -> Transaction:
    """Списать бонусы. Не более MAX_REDEMPTION_PERCENT % чека."""
    if now is None:
        now = local_now()

    # Идемпотентность
    existing_stmt = select(Transaction).where(Transaction.check_id == check_id)
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    client_stmt = select(Client).where(Client.phone == phone).with_for_update()
    result = await session.execute(client_stmt)
    client = result.scalar_one_or_none()
    if client is None:
        raise ValueError("Клиент не найден")

    await expire_bonuses(session, client_id=client.id, commit=True)

    max_allowed = (purchase_amount * settings.MAX_REDEMPTION_PERCENT / 100).quantize(Decimal("0.01"))
    bonus_to_redeem = min(bonus_to_redeem, max_allowed, client.bonus_balance)

    if bonus_to_redeem <= 0:
        raise ValueError("Нет доступных бонусов для списания")

    tx = Transaction(
        client_id=client.id,
        ts=now,
        type=TransactionType.redemption,
        amount_bonus=bonus_to_redeem,
        purchase_amount=purchase_amount,
        location=location,
        cashier_id=cashier_id,
        check_id=check_id,
    )
    session.add(tx)

    client.bonus_balance -= bonus_to_redeem
    await deduct_bonus_expiry_fifo(session, client, bonus_to_redeem)

    await session.flush()
    return tx


async def cancel_receipt(
    session: AsyncSession,
    *,
    check_id: str,
    cashier_id: Optional[int],
    cashier_name: Optional[str] = None,
    reason: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict:
    if now is None:
        now = local_now()

    original_check_id = (check_id or "").strip()
    if not original_check_id:
        raise ValueError("Укажите номер чека")

    existing_cancel = (
        await session.execute(
            select(ReceiptCancellation).where(ReceiptCancellation.original_check_id == original_check_id)
        )
    ).scalar_one_or_none()
    if existing_cancel is not None:
        raise ValueError("Чек уже отменён")

    accrual_tx = (
        await session.execute(select(Transaction).where(Transaction.check_id == original_check_id))
    ).scalar_one_or_none()
    redemption_tx = (
        await session.execute(select(Transaction).where(Transaction.check_id == f"{original_check_id}-R"))
    ).scalar_one_or_none()
    fuel_sales = (
        await session.execute(select(FuelSale).where(FuelSale.check_id == original_check_id).order_by(FuelSale.id))
    ).scalars().all()

    if accrual_tx is None and redemption_tx is None and not fuel_sales:
        raise ValueError("Чек не найден")

    client_id: Optional[int] = None
    if accrual_tx is not None:
        client_id = accrual_tx.client_id
    elif redemption_tx is not None:
        client_id = redemption_tx.client_id
    elif fuel_sales:
        client_id = fuel_sales[0].client_id

    client: Optional[Client] = None
    if client_id is not None:
        client = (
            await session.execute(select(Client).where(Client.id == client_id).with_for_update())
        ).scalar_one_or_none()

    original_accrual_bonus = Decimal(accrual_tx.amount_bonus or 0) if accrual_tx is not None else Decimal("0")
    original_redeemed_bonus = Decimal(redemption_tx.amount_bonus or 0) if redemption_tx is not None else Decimal("0")

    if client is not None and client.bonus_balance + original_redeemed_bonus < original_accrual_bonus:
        raise ValueError("Нельзя отменить чек: у клиента недостаточно бонусов для обратного списания")

    original_purchase_amount = Decimal("0")
    for candidate in (
        accrual_tx.purchase_amount if accrual_tx is not None else None,
        redemption_tx.purchase_amount if redemption_tx is not None else None,
        fuel_sales[0].total_rub if fuel_sales else None,
    ):
        if candidate is not None:
            original_purchase_amount = Decimal(candidate)
            break

    reversed_transactions: list[Transaction] = []
    purchase_amount_reversed = False
    pending_expiry_deduction = Decimal("0")

    if accrual_tx is not None:
        accrual_reversal = Transaction(
            client_id=accrual_tx.client_id,
            ts=now,
            type=TransactionType.accrual,
            amount_bonus=-Decimal(accrual_tx.amount_bonus or 0),
            purchase_amount=(-original_purchase_amount if original_purchase_amount and not purchase_amount_reversed else Decimal("0")),
            location=accrual_tx.location,
            fuel_liters=(-Decimal(accrual_tx.fuel_liters) if accrual_tx.fuel_liters is not None else None),
            cashier_id=accrual_tx.cashier_id,
            check_id=f"{original_check_id}-C-A",
        )
        session.add(accrual_reversal)
        reversed_transactions.append(accrual_reversal)
        purchase_amount_reversed = purchase_amount_reversed or bool(original_purchase_amount)
        if client is not None:
            client.bonus_balance += accrual_reversal.amount_bonus
            if original_accrual_bonus > 0:
                pending_expiry_deduction += original_accrual_bonus

    if redemption_tx is not None:
        redemption_reversal = Transaction(
            client_id=redemption_tx.client_id,
            ts=now,
            type=TransactionType.redemption,
            amount_bonus=-Decimal(redemption_tx.amount_bonus or 0),
            purchase_amount=(-original_purchase_amount if original_purchase_amount and not purchase_amount_reversed else Decimal("0")),
            location=redemption_tx.location,
            fuel_liters=(-Decimal(redemption_tx.fuel_liters) if redemption_tx.fuel_liters is not None else None),
            cashier_id=redemption_tx.cashier_id,
            check_id=f"{original_check_id}-C-R",
        )
        session.add(redemption_reversal)
        reversed_transactions.append(redemption_reversal)
        purchase_amount_reversed = purchase_amount_reversed or bool(original_purchase_amount)
        if client is not None:
            client.bonus_balance -= redemption_reversal.amount_bonus
            if original_redeemed_bonus > 0:
                await add_bonus_expiry(session, client, original_redeemed_bonus)

    if client is not None and pending_expiry_deduction > 0:
        await deduct_bonus_expiry_fifo(session, client, pending_expiry_deduction)

    reversed_sales: list[FuelSale] = []
    for index, sale in enumerate(fuel_sales, start=1):
        sale_reversal = FuelSale(
            sale_date=now,
            cashier_name=sale.cashier_name or cashier_name,
            cashier_id=sale.cashier_id,
            fuel_type=sale.fuel_type,
            liters=-Decimal(sale.liters or 0),
            price_per_liter=sale.price_per_liter,
            total_rub=-Decimal(sale.total_rub or 0),
            client_id=sale.client_id,
            check_id=f"{original_check_id}-C-F{index if len(fuel_sales) > 1 else ''}",
        )
        session.add(sale_reversal)
        reversed_sales.append(sale_reversal)

    if client is not None and original_purchase_amount > 0:
        client.total_spent = max(Decimal("0"), Decimal(client.total_spent or 0) - original_purchase_amount)
        client.level = _get_client_level(client.total_spent)

    cancellation = ReceiptCancellation(
        original_check_id=original_check_id,
        canceled_check_id=f"{original_check_id}-C",
        cashier_id=cashier_id,
        client_id=client_id,
        reason=(reason or "").strip() or None,
    )
    session.add(cancellation)
    await session.flush()

    return {
        "original_check_id": original_check_id,
        "canceled_check_id": cancellation.canceled_check_id,
        "client_id": client_id,
        "client_phone": client.phone if client is not None else None,
        "purchase_amount": str(original_purchase_amount),
        "reversed_transactions": len(reversed_transactions),
        "reversed_fuel_sales": len(reversed_sales),
        "restored_bonus": str(original_redeemed_bonus),
        "deducted_bonus": str(original_accrual_bonus),
    }

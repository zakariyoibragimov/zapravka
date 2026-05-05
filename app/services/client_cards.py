from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BonusExpiry, Client, FuelSale, Location, ReceiptCancellation, Transaction
from app.services.bonus_expiry import expire_bonuses


def _match_fuel_sale(tx: Transaction, fuel_sales: list[FuelSale]) -> FuelSale | None:
    if tx.location != Location.fuel or tx.purchase_amount is None or tx.ts is None:
        return None
    for sale in fuel_sales:
        if tx.check_id and sale.check_id and sale.check_id == tx.check_id:
            return sale
        if sale.total_rub != tx.purchase_amount or sale.sale_date is None:
            continue
        delta = abs((sale.sale_date.replace(tzinfo=None) - tx.ts.replace(tzinfo=None)).total_seconds())
        if delta <= 600:
            return sale
    return None


def _serialize_transaction(tx: Transaction, fuel_sales: list[FuelSale]) -> dict:
    matched_sale = _match_fuel_sale(tx, fuel_sales)
    price_per_liter = None
    fuel_liters = None
    fuel_type = None
    if matched_sale is not None:
        fuel_liters = str(matched_sale.liters)
        price_per_liter = str(matched_sale.price_per_liter)
        fuel_type = matched_sale.fuel_type
    else:
        fuel_liters = str(tx.fuel_liters) if tx.fuel_liters is not None else None
        if tx.purchase_amount is not None and tx.fuel_liters not in (None, 0):
            price_per_liter = str((tx.purchase_amount / tx.fuel_liters).quantize(Decimal("0.01")))

    tx_type = str(tx.type.value) if hasattr(tx.type, "value") else str(tx.type)
    location = str(tx.location.value) if tx.location and hasattr(tx.location, "value") else str(tx.location or "")
    title = {
        "accrual": "Начисление бонусов",
        "redemption": "Списание бонусов",
        "expire": "Сгорание бонусов",
    }.get(tx_type, tx_type)

    return {
        "id": tx.id,
        "ts": tx.ts.isoformat() if tx.ts else None,
        "ts_label": tx.ts.strftime("%d.%m.%Y %H:%M") if tx.ts else "",
        "type": tx_type,
        "title": title,
        "amount_bonus": str(tx.amount_bonus),
        "purchase_amount": str(tx.purchase_amount) if tx.purchase_amount is not None else "0",
        "fuel_liters": fuel_liters,
        "price_per_liter": price_per_liter,
        "fuel_type": fuel_type,
        "check_id": tx.check_id or "",
        "location": location,
        "entry_kind": "transaction",
    }


def _serialize_cancellation(item: ReceiptCancellation) -> dict:
    return {
        "id": item.id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "created_at_label": item.created_at.strftime("%d.%m.%Y %H:%M") if item.created_at else "",
        "original_check_id": item.original_check_id,
        "canceled_check_id": item.canceled_check_id,
        "reason": item.reason,
        "entry_kind": "cancellation",
        "title": "Отмена чека",
    }


def _serialize_expiry(item: BonusExpiry) -> dict:
    return {
        "id": item.id,
        "bonus_amount": str(item.bonus_amount),
        "remaining": str(item.remaining),
        "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
        "expiry_date_label": item.expiry_date.strftime("%d.%m.%Y") if item.expiry_date else "",
    }


async def build_client_card_payload(
    db: AsyncSession,
    *,
    client: Client,
    transaction_limit: int = 100,
    cancellation_limit: int = 50,
) -> dict:
    client_id = client.id
    await expire_bonuses(db, client_id=client_id, commit=True)

    txs_result = await db.execute(
        select(Transaction)
        .where(Transaction.client_id == client_id)
        .order_by(Transaction.ts.desc())
        .limit(transaction_limit)
    )
    txs = txs_result.scalars().all()

    fuel_sales_result = await db.execute(
        select(FuelSale)
        .where(FuelSale.client_id == client_id)
        .order_by(FuelSale.sale_date.desc())
        .limit(max(transaction_limit * 2, 100))
    )
    fuel_sales = fuel_sales_result.scalars().all()

    cancellations_result = await db.execute(
        select(ReceiptCancellation)
        .where(ReceiptCancellation.client_id == client_id)
        .order_by(ReceiptCancellation.created_at.desc())
        .limit(cancellation_limit)
    )
    cancellations = cancellations_result.scalars().all()

    expiries_result = await db.execute(
        select(BonusExpiry)
        .where(BonusExpiry.client_id == client_id, BonusExpiry.remaining > 0, BonusExpiry.expiry_date > date.today())
        .order_by(BonusExpiry.expiry_date.asc(), BonusExpiry.id.asc())
    )
    expiries = expiries_result.scalars().all()

    serialized_transactions = [_serialize_transaction(tx, fuel_sales) for tx in txs]
    serialized_cancellations = [_serialize_cancellation(item) for item in cancellations]
    serialized_expiries = [_serialize_expiry(item) for item in expiries]
    nearest_expiry = serialized_expiries[0] if serialized_expiries else None
    nearest_expiry_total_remaining = None
    if expiries:
        nearest_expiry_date = expiries[0].expiry_date
        nearest_expiry_total_remaining = str(
            sum(
                (item.remaining for item in expiries if item.expiry_date == nearest_expiry_date),
                Decimal("0"),
            )
        )

    history = [*serialized_transactions]
    history.extend(
        {
            "id": item["id"],
            "ts": item["created_at"],
            "ts_label": item["created_at_label"],
            "type": "cancellation",
            "title": item["title"],
            "amount_bonus": None,
            "purchase_amount": None,
            "check_id": item["original_check_id"],
            "location": None,
            "reason": item["reason"],
            "original_check_id": item["original_check_id"],
            "canceled_check_id": item["canceled_check_id"],
            "entry_kind": "cancellation",
        }
        for item in serialized_cancellations
    )
    history.sort(key=lambda item: item.get("ts") or "", reverse=True)

    total_accrued = sum((tx.amount_bonus for tx in txs if str(tx.type.value) == "accrual"), Decimal("0"))
    total_redeemed = sum((abs(tx.amount_bonus) for tx in txs if str(tx.type.value) == "redemption"), Decimal("0"))

    return {
        "id": client.id,
        "phone": client.phone,
        "name": client.name or "",
        "has_password": bool(client.password_hash),
        "first_name": client.first_name,
        "last_name": client.last_name,
        "patronymic": client.patronymic,
        "photo_data_url": client.photo_data_url,
        "bonus_balance": str(client.bonus_balance),
        "total_spent": str(client.total_spent),
        "birth_date": str(client.birth_date) if client.birth_date else None,
        "reg_date": str(client.reg_date) if client.reg_date else None,
        "summary": {
            "transactions_count": len(serialized_transactions),
            "cancellations_count": len(serialized_cancellations),
            "active_expiry_count": len(serialized_expiries),
            "total_accrued_bonus": str(total_accrued),
            "total_redeemed_bonus": str(total_redeemed),
            "nearest_expiry_date": nearest_expiry["expiry_date"] if nearest_expiry else None,
            "nearest_expiry_date_label": nearest_expiry["expiry_date_label"] if nearest_expiry else None,
            "nearest_expiry_remaining": nearest_expiry_total_remaining,
        },
        "transactions": serialized_transactions,
        "cancellations": serialized_cancellations,
        "bonus_expiry": serialized_expiries,
        "history": history,
    }
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BonusExpiry, Client
from app.services.app_settings import get_bonus_expiry_months


async def _recalculate_client_balance(session: AsyncSession, client_id: int) -> None:
    client_result = await session.execute(select(Client).where(Client.id == client_id).with_for_update())
    client = client_result.scalar_one_or_none()
    if client is None:
        return

    today = date.today()
    balance_result = await session.execute(
        select(BonusExpiry).where(
            BonusExpiry.client_id == client_id,
            BonusExpiry.remaining > 0,
            BonusExpiry.expiry_date > today,
        )
    )
    active = balance_result.scalars().all()
    client.bonus_balance = sum((entry.remaining for entry in active), Decimal("0.00"))


async def add_bonus_expiry(
    session: AsyncSession,
    client: Client,
    amount: Decimal,
) -> BonusExpiry:
    """Создаёт запись о сроке сгорания бонусов."""
    from datetime import date
    months = await get_bonus_expiry_months(session)
    try:
        from dateutil.relativedelta import relativedelta
        expiry = date.today() + relativedelta(months=months)
    except ImportError:
        expiry = date.today() + timedelta(days=30 * months)

    entry = BonusExpiry(
        client_id=client.id,
        bonus_amount=amount,
        expiry_date=expiry,
        remaining=amount,
    )
    session.add(entry)
    return entry


async def deduct_bonus_expiry_fifo(
    session: AsyncSession,
    client: Client,
    amount: Decimal,
) -> None:
    """Списывает бонусы по FIFO из bonus_expiry."""
    today = date.today()
    stmt = (
        select(BonusExpiry)
        .where(
            BonusExpiry.client_id == client.id,
            BonusExpiry.remaining > 0,
            BonusExpiry.expiry_date > today,
        )
        .order_by(BonusExpiry.expiry_date.asc())
        .with_for_update()
    )
    result = await session.execute(stmt)
    entries = result.scalars().all()

    left = amount
    for entry in entries:
        if left <= 0:
            break
        deduct = min(entry.remaining, left)
        entry.remaining -= deduct
        left -= deduct

    if left > 0:
        raise ValueError("Нет доступных бонусов для списания")


async def expire_bonuses(
    session: AsyncSession,
    *,
    client_id: int | None = None,
    commit: bool = True,
) -> int:
    """Сгорание просроченных бонусов. Возвращает число обработанных клиентов."""
    today = date.today()
    stmt = (
        select(BonusExpiry)
        .where(
            BonusExpiry.expiry_date <= today,
            BonusExpiry.remaining > 0,
        )
        .with_for_update()
    )
    if client_id is not None:
        stmt = stmt.where(BonusExpiry.client_id == client_id)
    result = await session.execute(stmt)
    expired = result.scalars().all()

    clients_affected: set[int] = set()
    for entry in expired:
        clients_affected.add(entry.client_id)
        entry.remaining = Decimal("0")

    # Пересчитать баланс клиентов
    for cid in clients_affected:
        await _recalculate_client_balance(session, cid)

    if client_id is not None and client_id not in clients_affected:
        await _recalculate_client_balance(session, client_id)

    if commit:
        await session.commit()
    return len(clients_affected)

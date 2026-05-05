from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AccrualRule, AccrualType, Campaign, ClientLevel, Location
from app.utils.datetime import local_now


def _get_client_level(total_spent: Decimal) -> ClientLevel:
    if total_spent > 15000:
        return ClientLevel.gold
    elif total_spent > 5000:
        return ClientLevel.silver
    return ClientLevel.bronze


async def get_active_multiplier(
    session: AsyncSession,
    now: datetime,
) -> Decimal:
    """Возвращает максимальный множитель из активных акций."""
    today = now.date()
    day_of_week = str(now.weekday())
    hour = now.hour

    stmt = select(Campaign).where(
        Campaign.start_date <= today,
        (Campaign.end_date >= today) | (Campaign.end_date.is_(None)),
    )
    result = await session.execute(stmt)
    campaigns = result.scalars().all()

    multiplier = Decimal("1")
    for c in campaigns:
        if c.days_of_week:
            allowed_days = c.days_of_week.split(",")
            if day_of_week not in allowed_days:
                continue
        if c.hours_start is not None and c.hours_end is not None:
            if not (c.hours_start <= hour < c.hours_end):
                continue
        if c.multiplier > multiplier:
            multiplier = c.multiplier

    return multiplier


async def calculate_accrual(
    session: AsyncSession,
    location: Location,
    purchase_amount: Decimal,
    cash_amount: Optional[Decimal] = None,
    fuel_liters: Optional[Decimal] = None,
    now: Optional[datetime] = None,
) -> Decimal:
    """Рассчитывает количество бонусов для начисления.

    purchase_amount — полная сумма чека (для проверки min_purchase и записи)
    cash_amount — сумма оплаченная деньгами (для % расчёта; если None = purchase_amount)
    """
    if now is None:
        now = local_now()
    if cash_amount is None:
        cash_amount = purchase_amount
    today = now.date()

    stmt = select(AccrualRule).where(
        AccrualRule.location == location,
        AccrualRule.active_from <= today,
        (AccrualRule.active_to >= today) | (AccrualRule.active_to.is_(None)),
        AccrualRule.min_purchase <= purchase_amount,  # min_purchase по полной сумме чека
    )
    result = await session.execute(stmt)
    rules = result.scalars().all()

    if not rules:
        return Decimal("0")

    selected_rule = sorted(
        rules,
        key=lambda rule: (
            rule.active_from,
            Decimal(rule.min_purchase or 0),
            int(rule.id or 0),
        ),
        reverse=True,
    )[0]

    bonuses = Decimal("0")
    if selected_rule.accrual_type == AccrualType.percent:
        # % считается только от суммы оплаченной деньгами
        bonuses = cash_amount * selected_rule.accrual_value / 100
    elif selected_rule.accrual_type == AccrualType.bonus_per_liter:
        # бонус/литр не зависит от способа оплаты
        if fuel_liters:
            bonuses = fuel_liters * selected_rule.accrual_value
    elif selected_rule.accrual_type == AccrualType.fixed:
        bonuses = selected_rule.accrual_value

    multiplier = await get_active_multiplier(session, now)
    return (bonuses * multiplier).quantize(Decimal("0.01"))

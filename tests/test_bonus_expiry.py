"""Тесты сервиса сгорания бонусов."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from dateutil.relativedelta import relativedelta
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BonusExpiry, Client
from app.services.app_settings import BONUS_EXPIRY_MONTHS_KEY, set_setting
from app.services.bonus_expiry import add_bonus_expiry, expire_bonuses


class TestBonusExpiry:
    async def test_expire_removes_balance(self, db_session: AsyncSession):
        client = Client(phone="+79008880001", bonus_balance=Decimal("100"), total_spent=Decimal("0"))
        db_session.add(client)
        await db_session.flush()

        # Просроченный бонус
        entry = BonusExpiry(
            client_id=client.id,
            bonus_amount=Decimal("100"),
            expiry_date=date.today() - timedelta(days=1),
            remaining=Decimal("100"),
        )
        db_session.add(entry)
        await db_session.flush()

        count = await expire_bonuses(db_session)
        assert count >= 1

        await db_session.refresh(client)
        assert client.bonus_balance == Decimal("0")

    async def test_future_bonus_not_expired(self, db_session: AsyncSession):
        client = Client(phone="+79008880002", bonus_balance=Decimal("50"), total_spent=Decimal("0"))
        db_session.add(client)
        await db_session.flush()

        entry = BonusExpiry(
            client_id=client.id,
            bonus_amount=Decimal("50"),
            expiry_date=date.today() + timedelta(days=30),
            remaining=Decimal("50"),
        )
        db_session.add(entry)
        await db_session.flush()

        await expire_bonuses(db_session)

        await db_session.refresh(entry)
        assert entry.remaining == Decimal("50")

    async def test_bonus_expires_on_expiry_date(self, db_session: AsyncSession):
        client = Client(phone="+79008880004", bonus_balance=Decimal("40"), total_spent=Decimal("0"))
        db_session.add(client)
        await db_session.flush()

        entry = BonusExpiry(
            client_id=client.id,
            bonus_amount=Decimal("40"),
            expiry_date=date.today(),
            remaining=Decimal("40"),
        )
        db_session.add(entry)
        await db_session.flush()

        count = await expire_bonuses(db_session)

        assert count >= 1
        await db_session.refresh(entry)
        await db_session.refresh(client)
        assert entry.remaining == Decimal("0")
        assert client.bonus_balance == Decimal("0")

    async def test_add_bonus_expiry_uses_admin_setting(self, db_session: AsyncSession):
        client = Client(phone="+79008880003", bonus_balance=Decimal("0"), total_spent=Decimal("0"))
        db_session.add(client)
        await db_session.flush()

        await set_setting(db_session, BONUS_EXPIRY_MONTHS_KEY, "3")
        entry = await add_bonus_expiry(db_session, client, Decimal("25"))

        assert entry.expiry_date == date.today() + relativedelta(months=3)

"""Тесты бизнес-логики начисления/списания бонусов."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import AccrualRule, AccrualType, Client, ClientLevel, Location
from app.services.transactions import accrue, get_or_create_client, normalize_phone, redeem


class TestNormalizePhone:
    def test_normalize_russian_local_number(self):
        assert normalize_phone("9991234567") == "+79991234567"

    def test_normalize_tajik_local_number(self):
        assert normalize_phone("901234567") == "+992901234567"

    def test_reject_unsupported_number(self):
        with pytest.raises(ValueError, match="России"):
            normalize_phone("+998901234567")


async def _seed_rule(session: AsyncSession, location: Location, level: ClientLevel,
                     atype: AccrualType, value: Decimal):
    rule = AccrualRule(
        location=location,
        client_level=level,
        accrual_type=atype,
        accrual_value=value,
        min_purchase=Decimal("0"),
        active_from=date(2000, 1, 1),
        active_to=None,
    )
    session.add(rule)
    await session.flush()
    return rule


class TestAccrual:
    async def test_accrue_percent(self, db_session: AsyncSession):
        await _seed_rule(db_session, Location.base, ClientLevel.bronze, AccrualType.percent, Decimal("5"))
        tx = await accrue(
            db_session,
            phone="+79001111111",
            purchase_amount=Decimal("1000"),
            fuel_liters=None,
            cashier_id=None,
            check_id="CHK-001",
            location=Location.base,
        )
        assert tx.amount_bonus == Decimal("50.00")

    async def test_accrue_respects_min_purchase_threshold(self, db_session: AsyncSession):
        rule = AccrualRule(
            location=Location.base,
            client_level=ClientLevel.bronze,
            accrual_type=AccrualType.percent,
            accrual_value=Decimal("1"),
            min_purchase=Decimal("100"),
            active_from=date(2000, 1, 1),
            active_to=None,
        )
        db_session.add(rule)
        await db_session.flush()

        below_threshold = await accrue(
            db_session,
            phone="+79001111116",
            purchase_amount=Decimal("99"),
            fuel_liters=None,
            cashier_id=None,
            check_id="CHK-MIN-099",
            location=Location.base,
        )
        at_threshold = await accrue(
            db_session,
            phone="+79001111117",
            purchase_amount=Decimal("100"),
            fuel_liters=None,
            cashier_id=None,
            check_id="CHK-MIN-100",
            location=Location.base,
        )

        assert below_threshold.amount_bonus == Decimal("0.00")
        assert at_threshold.amount_bonus == Decimal("1.00")

    async def test_accrue_per_liter(self, db_session: AsyncSession):
        await _seed_rule(db_session, Location.fuel, ClientLevel.bronze, AccrualType.bonus_per_liter, Decimal("1"))
        tx = await accrue(
            db_session,
            phone="+79001111112",
            purchase_amount=Decimal("1500"),
            fuel_liters=Decimal("30"),
            cashier_id=None,
            check_id="CHK-002",
            location=Location.fuel,
        )
        assert tx.amount_bonus == Decimal("30.00")

    async def test_accrue_fixed(self, db_session: AsyncSession):
        await _seed_rule(db_session, Location.base, ClientLevel.bronze, AccrualType.fixed, Decimal("25"))
        tx = await accrue(
            db_session,
            phone="+79001111118",
            purchase_amount=Decimal("1000"),
            fuel_liters=None,
            cashier_id=None,
            check_id="CHK-FIXED-001",
            location=Location.base,
        )
        assert tx.amount_bonus == Decimal("25.00")

    async def test_latest_active_rule_overrides_older_rule(self, db_session: AsyncSession):
        await _seed_rule(db_session, Location.fuel, ClientLevel.bronze, AccrualType.percent, Decimal("5"))
        newer_rule = AccrualRule(
            location=Location.fuel,
            client_level=ClientLevel.bronze,
            accrual_type=AccrualType.bonus_per_liter,
            accrual_value=Decimal("1.5"),
            min_purchase=Decimal("0"),
            active_from=date(2000, 1, 2),
            active_to=None,
        )
        db_session.add(newer_rule)
        await db_session.flush()

        tx = await accrue(
            db_session,
            phone="+79001111119",
            purchase_amount=Decimal("1000"),
            fuel_liters=Decimal("20"),
            cashier_id=None,
            check_id="CHK-OVERRIDE-001",
            location=Location.fuel,
        )

        assert tx.amount_bonus == Decimal("30.00")

    async def test_accrue_percent_uses_cash_amount_after_redemption(self, db_session: AsyncSession):
        await _seed_rule(db_session, Location.base, ClientLevel.bronze, AccrualType.percent, Decimal("10"))
        tx = await accrue(
            db_session,
            phone="+79001111115",
            purchase_amount=Decimal("1000"),
            cash_amount=Decimal("700"),
            fuel_liters=None,
            cashier_id=None,
            check_id="CHK-CASH-ONLY",
            location=Location.base,
        )
        assert tx.amount_bonus == Decimal("70.00")

    async def test_idempotent_check_id(self, db_session: AsyncSession):
        """Повторное начисление с тем же check_id не должно удваивать бонусы."""
        await _seed_rule(db_session, Location.base, ClientLevel.bronze, AccrualType.percent, Decimal("10"))
        kwargs = dict(
            phone="+79001111113",
            purchase_amount=Decimal("500"),
            fuel_liters=None,
            cashier_id=None,
            check_id="CHK-IDEM",
            location=Location.base,
        )
        tx1 = await accrue(db_session, **kwargs)
        tx2 = await accrue(db_session, **kwargs)
        assert tx1.id == tx2.id

    async def test_client_level_upgrade(self, db_session: AsyncSession):
        await _seed_rule(db_session, Location.base, ClientLevel.bronze, AccrualType.percent, Decimal("1"))
        # Накопить > 5000 руб
        for i in range(6):
            await accrue(
                db_session,
                phone="+79001111114",
                purchase_amount=Decimal("1000"),
                fuel_liters=None,
                cashier_id=None,
                check_id=f"LEVEL-{i}",
                location=Location.base,
            )
        stmt = select(Client).where(Client.phone == "+79001111114")
        client = (await db_session.execute(stmt)).scalar_one()
        assert client.level == ClientLevel.silver


class TestRedeem:
    async def test_redeem_basic(self, db_session: AsyncSession):
        await _seed_rule(db_session, Location.base, ClientLevel.bronze, AccrualType.percent, Decimal("50"))
        await accrue(
            db_session,
            phone="+79001222222",
            purchase_amount=Decimal("1000"),
            fuel_liters=None,
            cashier_id=None,
            check_id="CHK-R1",
            location=Location.base,
        )
        tx = await redeem(
            db_session,
            phone="+79001222222",
            purchase_amount=Decimal("1000"),
            bonus_to_redeem=Decimal("100"),
            cashier_id=None,
            check_id="CHK-R2",
            location=Location.base,
        )
        assert tx.amount_bonus == Decimal("100.00")

    async def test_redeem_max_30_percent(self, db_session: AsyncSession):
        """Списание ограничено текущей настройкой MAX_REDEMPTION_PERCENT."""
        await _seed_rule(db_session, Location.base, ClientLevel.bronze, AccrualType.percent, Decimal("100"))
        await accrue(
            db_session,
            phone="+79001333333",
            purchase_amount=Decimal("10000"),
            fuel_liters=None,
            cashier_id=None,
            check_id="CHK-M1",
            location=Location.base,
        )
        tx = await redeem(
            db_session,
            phone="+79001333333",
            purchase_amount=Decimal("1000"),
            bonus_to_redeem=Decimal("9999"),
            cashier_id=None,
            check_id="CHK-M2",
            location=Location.base,
        )
        expected = (Decimal("1000") * Decimal(settings.MAX_REDEMPTION_PERCENT) / Decimal("100")).quantize(Decimal("0.01"))
        assert tx.amount_bonus == expected

    async def test_redeem_no_client(self, db_session: AsyncSession):
        with pytest.raises(ValueError, match="Клиент не найден"):
            await redeem(
                db_session,
                phone="+79009999999",
                purchase_amount=Decimal("100"),
                bonus_to_redeem=Decimal("10"),
                cashier_id=None,
                check_id="CHK-NOONE",
                location=Location.base,
            )

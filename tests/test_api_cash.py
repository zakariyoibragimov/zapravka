"""Тесты HTTP API кассира."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AccrualRule, AccrualType, ActionLog, BonusExpiry, Cashier, Client, ClientLevel, FuelSale, Location, ReceiptCancellation, Transaction
from app.services.qr import make_client_qr_payload
from app.services.cashier_auth import hash_password


async def _seed_rule(session: AsyncSession):
    rule = AccrualRule(
        location=Location.base,
        client_level=ClientLevel.bronze,
        accrual_type=AccrualType.percent,
        accrual_value=Decimal("10"),
        min_purchase=Decimal("0"),
        active_from=date(2000, 1, 1),
    )
    session.add(rule)
    await session.commit()


async def _cashier_headers(client: AsyncClient, session: AsyncSession) -> dict[str, str]:
    cashier = Cashier(
        name="Тестовый кассир",
        username="cashier1",
        password_hash=hash_password("secret123"),
        is_active=True,
    )
    session.add(cashier)
    await session.commit()

    login_resp = await client.post(
        "/api/cashiers/login",
        json={"username": "cashier1", "password": "secret123"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    open_resp = await client.post("/api/cashiers/shift/open", headers=headers)
    assert open_resp.status_code == 200
    return headers


class TestCashAPI:
    async def test_accrue_creates_client(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)
        headers = await _cashier_headers(client, db_session)
        resp = await client.post("/api/cash/accrue", json={
            "phone": "+79005550001",
            "purchase_amount": 1000,
            "location": "base",
            "check_id": "API-001",
        }, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "accrual"
        assert float(data["amount_bonus"]) == 100.0

    async def test_client_info(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)
        headers = await _cashier_headers(client, db_session)
        await client.post("/api/cash/accrue", json={
            "phone": "+79005550002",
            "purchase_amount": 500,
            "location": "base",
            "check_id": "API-INFO-001",
        }, headers=headers)
        db_client = (await db_session.execute(select(Client).where(Client.phone == "+79005550002"))).scalar_one()
        db_client.photo_data_url = "data:image/png;base64,AAAA"
        await db_session.commit()
        resp = await client.post("/api/cash/client-info", json={"phone": "+79005550002"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["phone"] == "+79005550002"
        assert float(data["bonus_balance"]) == 50.0
        assert data["photo_data_url"] == "data:image/png;base64,AAAA"

    async def test_client_info_can_search_by_last_four_digits(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)
        headers = await _cashier_headers(client, db_session)
        await client.post("/api/cash/accrue", json={
            "phone": "+79005554321",
            "purchase_amount": 500,
            "location": "base",
            "check_id": "API-INFO-LAST4-001",
        }, headers=headers)

        resp = await client.post("/api/cash/client-info", json={"phone": "4321"})
        assert resp.status_code == 200
        assert resp.json()["phone"] == "+79005554321"

    async def test_client_info_can_search_by_name(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)
        headers = await _cashier_headers(client, db_session)
        await client.post("/api/cash/accrue", json={
            "phone": "+79005550022",
            "purchase_amount": 500,
            "location": "base",
            "check_id": "API-INFO-NAME-001",
        }, headers=headers)
        db_client = (await db_session.execute(select(Client).where(Client.phone == "+79005550022"))).scalar_one()
        db_client.name = "Салим"
        await db_session.commit()

        resp = await client.post("/api/cash/client-info", json={"phone": "Салим"})
        assert resp.status_code == 200
        assert resp.json()["phone"] == "+79005550022"

    async def test_client_info_returns_conflict_for_ambiguous_quick_search(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)
        headers = await _cashier_headers(client, db_session)
        await client.post("/api/cash/accrue", json={
            "phone": "+79005550111",
            "purchase_amount": 500,
            "location": "base",
            "check_id": "API-INFO-MULTI-001",
        }, headers=headers)
        await client.post("/api/cash/accrue", json={
            "phone": "+79005550222",
            "purchase_amount": 500,
            "location": "base",
            "check_id": "API-INFO-MULTI-002",
        }, headers=headers)
        first = (await db_session.execute(select(Client).where(Client.phone == "+79005550111"))).scalar_one()
        second = (await db_session.execute(select(Client).where(Client.phone == "+79005550222"))).scalar_one()
        first.name = "Али"
        second.name = "Али"
        await db_session.commit()

        resp = await client.post("/api/cash/client-info", json={"phone": "Али"})
        assert resp.status_code == 409

    async def test_clients_list_and_detail_include_photo(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)
        headers = await _cashier_headers(client, db_session)
        await client.post("/api/cash/accrue", json={
            "phone": "+79005550123",
            "purchase_amount": 500,
            "location": "base",
            "check_id": "API-LIST-001",
        }, headers=headers)
        db_client = (await db_session.execute(select(Client).where(Client.phone == "+79005550123"))).scalar_one()
        db_client.photo_data_url = "data:image/png;base64,CCCC"
        await db_session.commit()

        list_resp = await client.get("/api/cash/clients")
        assert list_resp.status_code == 200
        listed = next(item for item in list_resp.json() if item["phone"] == "+79005550123")
        assert listed["photo_data_url"] == "data:image/png;base64,CCCC"

        detail_resp = await client.get(f"/api/cash/clients/{db_client.id}")
        assert detail_resp.status_code == 200
        assert detail_resp.json()["photo_data_url"] == "data:image/png;base64,CCCC"

    async def test_client_detail_includes_transaction_date_and_fuel_meta(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)
        headers = await _cashier_headers(client, db_session)
        await client.post("/api/cash/accrue", json={
            "phone": "+79005550444",
            "purchase_amount": 600,
            "location": "fuel",
            "fuel_liters": 20,
            "check_id": "API-FUEL-DETAIL-001",
        }, headers=headers)
        sale_resp = await client.post("/api/cash/fuel-sale", json={
            "phone": "+79005550444",
            "total_rub": 600,
            "fuel_type": "АИ-92",
            "liters": 20,
            "price_per_liter": 30,
        }, headers=headers)
        assert sale_resp.status_code == 200

        db_client = (await db_session.execute(select(Client).where(Client.phone == "+79005550444"))).scalar_one()
        detail_resp = await client.get(f"/api/cash/clients/{db_client.id}")
        assert detail_resp.status_code == 200
        tx = detail_resp.json()["transactions"][0]
        assert tx["ts"]
        assert tx["ts_label"]
        assert tx["fuel_liters"] == "20.000"
        assert tx["price_per_liter"] == "30.00"

    async def test_client_detail_includes_cancellations_and_bonus_expiry(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)
        headers = await _cashier_headers(client, db_session)

        first = await client.post("/api/cash/accrue", json={
            "phone": "+79005550777",
            "purchase_amount": 500,
            "location": "base",
            "check_id": "API-CARD-001",
        }, headers=headers)
        assert first.status_code == 200

        second = await client.post("/api/cash/accrue", json={
            "phone": "+79005550777",
            "purchase_amount": 700,
            "location": "base",
            "check_id": "API-CARD-002",
        }, headers=headers)
        assert second.status_code == 200

        cancel_resp = await client.post(
            "/api/cash/cancel-receipt",
            json={"check_id": "API-CARD-001", "reason": "Ошибка кассира"},
            headers=headers,
        )
        assert cancel_resp.status_code == 200

        db_client = (await db_session.execute(select(Client).where(Client.phone == "+79005550777"))).scalar_one()
        detail_resp = await client.get(f"/api/cash/clients/{db_client.id}")
        assert detail_resp.status_code == 200

        payload = detail_resp.json()
        assert payload["summary"]["cancellations_count"] == 1
        assert payload["cancellations"][0]["original_check_id"] == "API-CARD-001"
        assert payload["cancellations"][0]["reason"] == "Ошибка кассира"
        assert payload["bonus_expiry"]
        assert payload["bonus_expiry"][0]["remaining"] == "70.00"
        assert any(item["type"] == "cancellation" for item in payload["history"])

    async def test_redeem_rejects_bonus_expiring_today(self, client: AsyncClient, db_session: AsyncSession):
        headers = await _cashier_headers(client, db_session)
        db_client = Client(phone="+79005550999", bonus_balance=Decimal("50.00"), total_spent=Decimal("0"))
        db_session.add(db_client)
        await db_session.flush()
        db_session.add(
            BonusExpiry(
                client_id=db_client.id,
                bonus_amount=Decimal("50.00"),
                remaining=Decimal("50.00"),
                expiry_date=date.today(),
            )
        )
        await db_session.commit()

        redeem_resp = await client.post(
            "/api/cash/redeem",
            json={
                "phone": "+79005550999",
                "purchase_amount": 100,
                "bonus_to_redeem": 10,
                "check_id": "API-EXPIRE-TODAY-001",
                "location": "base",
            },
            headers=headers,
        )
        assert redeem_resp.status_code == 400
        assert "Нет доступных бонусов" in redeem_resp.json()["detail"]

        await db_session.refresh(db_client)
        assert db_client.bonus_balance == Decimal("0.00")

    async def test_receipt_processed_creates_deduplicated_action_log(self, client: AsyncClient, db_session: AsyncSession):
        headers = await _cashier_headers(client, db_session)

        first_resp = await client.post(
            "/api/cash/receipt-processed",
            json={
                "check_id": "API-RECEIPT-001",
                "purchase_amount": 900,
                "location": "base",
                "has_client": False,
                "redeem_bonus": 0,
                "accrued_bonus": 0,
            },
            headers=headers,
        )
        assert first_resp.status_code == 200
        assert first_resp.json()["duplicate"] is False

        duplicate_resp = await client.post(
            "/api/cash/receipt-processed",
            json={
                "check_id": "API-RECEIPT-001",
                "purchase_amount": 900,
                "location": "base",
                "has_client": False,
                "redeem_bonus": 0,
                "accrued_bonus": 0,
            },
            headers=headers,
        )
        assert duplicate_resp.status_code == 200
        assert duplicate_resp.json()["duplicate"] is True

        logs = (await db_session.execute(select(ActionLog).where(ActionLog.action == "receipt_processed"))).scalars().all()
        assert len(logs) == 1
        assert logs[0].check_id == "API-RECEIPT-001"
        assert '"has_client":false' in (logs[0].details or "")

    async def test_receipt_processed_reconciles_actual_bonus_amounts(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)
        headers = await _cashier_headers(client, db_session)

        seed_resp = await client.post(
            "/api/cash/accrue",
            json={
                "phone": "+79005550010",
                "purchase_amount": 300,
                "location": "base",
                "check_id": "API-RECON-SEED-001",
            },
            headers=headers,
        )
        assert seed_resp.status_code == 200

        redeem_resp = await client.post(
            "/api/cash/redeem",
            json={
                "phone": "+79005550010",
                "purchase_amount": 100,
                "bonus_to_redeem": 999,
                "location": "base",
                "check_id": "API-RECON-001-R",
            },
            headers=headers,
        )
        assert redeem_resp.status_code == 200
        assert Decimal(redeem_resp.json()["amount_bonus"]) == Decimal("30.00")

        accrue_resp = await client.post(
            "/api/cash/accrue",
            json={
                "phone": "+79005550010",
                "purchase_amount": 100,
                "cash_amount": 70,
                "location": "base",
                "check_id": "API-RECON-001",
            },
            headers=headers,
        )
        assert accrue_resp.status_code == 200
        assert Decimal(accrue_resp.json()["amount_bonus"]) == Decimal("7.00")

        receipt_resp = await client.post(
            "/api/cash/receipt-processed",
            json={
                "check_id": "API-RECON-001",
                "purchase_amount": 100,
                "location": "base",
                "has_client": True,
                "redeem_bonus": 999,
                "accrued_bonus": 999,
            },
            headers=headers,
        )
        assert receipt_resp.status_code == 200
        assert receipt_resp.json()["redeem_bonus"] == "30.00"
        assert receipt_resp.json()["accrued_bonus"] == "7.00"

        receipt_log = (
            await db_session.execute(
                select(ActionLog).where(ActionLog.action == "receipt_processed", ActionLog.check_id == "API-RECON-001")
            )
        ).scalar_one()
        assert '"redeem_bonus":"30.00"' in (receipt_log.details or "")
        assert '"accrued_bonus":"7.00"' in (receipt_log.details or "")

    async def test_client_info_by_qr_payload(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)
        headers = await _cashier_headers(client, db_session)
        await client.post("/api/cash/accrue", json={
            "phone": "+79005550012",
            "purchase_amount": 500,
            "location": "base",
            "check_id": "API-QR-001",
        }, headers=headers)
        resp = await client.post("/api/cash/client-info", json={"qr_data": make_client_qr_payload("+79005550012")})
        assert resp.status_code == 200
        data = resp.json()
        assert data["phone"] == "+79005550012"
        assert float(data["bonus_balance"]) == 50.0
        assert data["qr_payload"] == make_client_qr_payload("+79005550012")
        assert data["qr_base64"]

    async def test_cash_register_returns_client_qr(self, client: AsyncClient, db_session: AsyncSession):
        headers = await _cashier_headers(client, db_session)

        resp = await client.post(
            "/api/cash/register",
            json={"phone": "+79005550014", "name": "Клиент QR"},
            headers=headers,
        )

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["phone"] == "+79005550014"
        assert payload["name"] == "Клиент QR"
        assert payload["qr_payload"] == make_client_qr_payload("+79005550014")
        assert payload["qr_base64"]

    async def test_client_info_by_tel_qr_payload(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)
        headers = await _cashier_headers(client, db_session)
        await client.post("/api/cash/accrue", json={
            "phone": "+79005550013",
            "purchase_amount": 500,
            "location": "base",
            "check_id": "API-QR-TEL-001",
        }, headers=headers)

        resp = await client.post("/api/cash/client-info", json={"qr_data": "tel:+79005550013"})
        assert resp.status_code == 200
        assert resp.json()["phone"] == "+79005550013"

    async def test_preview_accrual_for_fuel_per_liter(self, client: AsyncClient, db_session: AsyncSession):
        rule = AccrualRule(
            location=Location.fuel,
            client_level=ClientLevel.bronze,
            accrual_type=AccrualType.bonus_per_liter,
            accrual_value=Decimal("1.5"),
            min_purchase=Decimal("0"),
            active_from=date(2000, 1, 1),
        )
        db_session.add(rule)
        await db_session.commit()

        resp = await client.get(
            "/api/cash/preview-accrual",
            params={"location": "fuel", "purchase_amount": "1500", "fuel_liters": "20"},
        )
        assert resp.status_code == 200
        assert Decimal(resp.json()["amount_bonus"]) == Decimal("30.00")

    async def test_preview_accrual_for_fixed_bonus_rule(self, client: AsyncClient, db_session: AsyncSession):
        rule = AccrualRule(
            location=Location.base,
            client_level=ClientLevel.bronze,
            accrual_type=AccrualType.fixed,
            accrual_value=Decimal("15"),
            min_purchase=Decimal("0"),
            active_from=date(2000, 1, 1),
        )
        db_session.add(rule)
        await db_session.commit()

        resp = await client.get(
            "/api/cash/preview-accrual",
            params={"location": "base", "purchase_amount": "1500"},
        )
        assert resp.status_code == 200
        assert Decimal(resp.json()["amount_bonus"]) == Decimal("15.00")

    async def test_client_info_not_found(self, client: AsyncClient):
        resp = await client.post("/api/cash/client-info", json={"phone": "+79000000000"})
        assert resp.status_code == 404

    async def test_redeem_via_api(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)
        headers = await _cashier_headers(client, db_session)
        await client.post("/api/cash/accrue", json={
            "phone": "+79005550003",
            "purchase_amount": 10000,
            "location": "base",
            "check_id": "API-ACC-003",
        }, headers=headers)
        resp = await client.post("/api/cash/redeem", json={
            "phone": "+79005550003",
            "purchase_amount": 1000,
            "bonus_to_redeem": 200,
            "location": "base",
            "check_id": "API-RED-003",
        }, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "redemption"

    async def test_cash_operations_require_open_shift(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)
        cashier = Cashier(
            name="Тестовый кассир",
            username="cashier1",
            password_hash=hash_password("secret123"),
            is_active=True,
        )
        db_session.add(cashier)
        await db_session.commit()

        login_resp = await client.post(
            "/api/cashiers/login",
            json={"username": "cashier1", "password": "secret123"},
        )
        assert login_resp.status_code == 200
        headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

        status_resp = await client.get("/api/cashiers/shift", headers=headers)
        assert status_resp.status_code == 200
        assert status_resp.json()["has_active_shift"] is False

        accrue_resp = await client.post("/api/cash/accrue", json={
            "phone": "+79005559999",
            "purchase_amount": 1000,
            "location": "base",
            "check_id": "API-SHIFT-001",
        }, headers=headers)
        assert accrue_resp.status_code == 409

        open_resp = await client.post("/api/cashiers/shift/open", headers=headers)
        assert open_resp.status_code == 200
        assert open_resp.json()["has_active_shift"] is True

        accrue_resp = await client.post("/api/cash/accrue", json={
            "phone": "+79005559999",
            "purchase_amount": 1000,
            "location": "base",
            "check_id": "API-SHIFT-002",
        }, headers=headers)
        assert accrue_resp.status_code == 200

        close_resp = await client.post("/api/cashiers/shift/close", headers=headers)
        assert close_resp.status_code == 200
        assert close_resp.json()["has_active_shift"] is False
        assert close_resp.json()["summary"]["transactions_count"] == 1

        second_accrue_resp = await client.post("/api/cash/accrue", json={
            "phone": "+79005559998",
            "purchase_amount": 500,
            "location": "base",
            "check_id": "API-SHIFT-003",
        }, headers=headers)
        assert second_accrue_resp.status_code == 409

    async def test_cancel_receipt_reverts_bonus_and_fuel_sale(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)
        headers = await _cashier_headers(client, db_session)

        accrue_resp = await client.post("/api/cash/accrue", json={
            "phone": "+79005557777",
            "purchase_amount": 1000,
            "location": "base",
            "check_id": "API-CANCEL-001",
        }, headers=headers)
        assert accrue_resp.status_code == 200

        redeem_resp = await client.post("/api/cash/redeem", json={
            "phone": "+79005557777",
            "purchase_amount": 1000,
            "bonus_to_redeem": 50,
            "location": "base",
            "check_id": "API-CANCEL-001-R",
        }, headers=headers)
        assert redeem_resp.status_code == 200

        sale_resp = await client.post("/api/cash/fuel-sale", json={
            "phone": "+79005557777",
            "total_rub": 1000,
            "fuel_type": "АИ-95",
            "liters": 20,
            "price_per_liter": 50,
            "check_id": "API-CANCEL-001",
        }, headers=headers)
        assert sale_resp.status_code == 200

        cancel_resp = await client.post("/api/cash/cancel-receipt", json={
            "check_id": "API-CANCEL-001",
            "reason": "Ошибочный чек",
        }, headers=headers)
        assert cancel_resp.status_code == 200
        payload = cancel_resp.json()
        assert payload["original_check_id"] == "API-CANCEL-001"
        assert payload["reversed_transactions"] == 2
        assert payload["reversed_fuel_sales"] == 1

        db_client = (await db_session.execute(select(Client).where(Client.phone == "+79005557777"))).scalar_one()
        assert float(db_client.bonus_balance) == 0.0
        assert float(db_client.total_spent) == 0.0

        cancellation = (
            await db_session.execute(
                select(ReceiptCancellation).where(ReceiptCancellation.original_check_id == "API-CANCEL-001")
            )
        ).scalar_one()
        assert cancellation.reason == "Ошибочный чек"

        tx_ids = {
            tx.check_id
            for tx in (
                await db_session.execute(
                    select(Transaction).where(Transaction.check_id.like("API-CANCEL-001%"))
                )
            ).scalars().all()
        }
        assert "API-CANCEL-001-C-A" in tx_ids
        assert "API-CANCEL-001-C-R" in tx_ids

        fuel_checks = {
            sale.check_id
            for sale in (
                await db_session.execute(select(FuelSale).where(FuelSale.check_id.like("API-CANCEL-001%")))
            ).scalars().all()
        }
        assert "API-CANCEL-001" in fuel_checks
        assert "API-CANCEL-001-C-F" in fuel_checks

        action = (
            await db_session.execute(
                select(ActionLog).where(ActionLog.action == "receipt_cancelled", ActionLog.check_id == "API-CANCEL-001")
            )
        ).scalar_one()
        assert action.actor_type == "cashier"

    async def test_full_receipt_flow_updates_client_card_and_receipt_log(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)
        headers = await _cashier_headers(client, db_session)

        seed_resp = await client.post("/api/cash/accrue", json={
            "phone": "+79005550888",
            "purchase_amount": 1000,
            "location": "base",
            "check_id": "API-FLOW-SEED-001",
        }, headers=headers)
        assert seed_resp.status_code == 200
        assert Decimal(seed_resp.json()["amount_bonus"]) == Decimal("100.00")

        qr_resp = await client.post("/api/cash/client-info", json={"qr_data": make_client_qr_payload("+79005550888")})
        assert qr_resp.status_code == 200
        assert qr_resp.json()["phone"] == "+79005550888"

        redeem_resp = await client.post("/api/cash/redeem", json={
            "phone": "+79005550888",
            "purchase_amount": 500,
            "bonus_to_redeem": 50,
            "location": "base",
            "check_id": "API-FLOW-001-R",
        }, headers=headers)
        assert redeem_resp.status_code == 200
        assert redeem_resp.json()["type"] == "redemption"

        accrue_resp = await client.post("/api/cash/accrue", json={
            "phone": "+79005550888",
            "purchase_amount": 500,
            "cash_amount": 450,
            "location": "base",
            "check_id": "API-FLOW-001",
        }, headers=headers)
        assert accrue_resp.status_code == 200
        assert Decimal(accrue_resp.json()["amount_bonus"]) == Decimal("45.00")

        receipt_resp = await client.post("/api/cash/receipt-processed", json={
            "check_id": "API-FLOW-001",
            "purchase_amount": 500,
            "location": "base",
            "has_client": True,
            "redeem_bonus": 50,
            "accrued_bonus": 45,
        }, headers=headers)
        assert receipt_resp.status_code == 200
        assert receipt_resp.json()["duplicate"] is False

        db_client = (await db_session.execute(select(Client).where(Client.phone == "+79005550888"))).scalar_one()
        detail_resp = await client.get(f"/api/cash/clients/{db_client.id}")
        assert detail_resp.status_code == 200

        payload = detail_resp.json()
        assert payload["bonus_balance"] == "95.00"
        assert payload["summary"]["transactions_count"] == 3
        assert payload["summary"]["total_accrued_bonus"] == "145.00"
        assert payload["summary"]["total_redeemed_bonus"] == "50.00"
        assert {item["type"] for item in payload["transactions"]} == {"accrual", "redemption"}

        receipt_log = (
            await db_session.execute(
                select(ActionLog).where(ActionLog.action == "receipt_processed", ActionLog.check_id == "API-FLOW-001")
            )
        ).scalar_one()
        assert '"has_client":true' in (receipt_log.details or "")
        assert '"redeem_bonus":"50.00"' in (receipt_log.details or "")
        assert '"accrued_bonus":"45.00"' in (receipt_log.details or "")

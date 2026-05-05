from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.api.reports import _dt_label
from app.db.models import ActionLog, BonusExpiry, Cashier, Client, FuelSale, Location, Transaction, TransactionType
from app.services.cashier_auth import hash_password
from tests.test_api_cash import _cashier_headers, _seed_rule


class TestReportsAPI:
    def test_report_datetime_label_uses_business_timezone(self):
        assert _dt_label(datetime(2026, 5, 3, 12, 30, tzinfo=timezone.utc)) == "03.05.2026 17:30"

    async def test_closed_shifts_report_and_action_log_for_cashier(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)
        headers = await _cashier_headers(client, db_session)

        accrue_resp = await client.post("/api/cash/accrue", json={
            "phone": "+79005558888",
            "purchase_amount": 1200,
            "location": "base",
            "check_id": "API-REPORT-001",
        }, headers=headers)
        assert accrue_resp.status_code == 200

        close_resp = await client.post("/api/cashiers/shift/close", headers=headers)
        assert close_resp.status_code == 200

        today = date.today().isoformat()
        shifts_resp = await client.get(f"/api/reports/closed-shifts?date_from={today}&date_to={today}", headers=headers)
        assert shifts_resp.status_code == 200
        shift_rows = shifts_resp.json()
        assert shift_rows
        assert any(row["transactions_count"] >= 1 for row in shift_rows)

        logs_resp = await client.get(f"/api/reports/action-log?date_from={today}&date_to={today}", headers=headers)
        assert logs_resp.status_code == 200
        log_rows = logs_resp.json()
        actions = [row["action"] for row in log_rows]
        assert "Начисление по чеку" in actions
        assert "Закрытие смены" in actions

        accrued_row = next(row for row in log_rows if row["action"] == "Начисление по чеку")
        assert accrued_row["actor_display"].startswith("Кассир:")
        assert accrued_row["entity_display"] == "Чек API-REPORT-001"
        assert "Сумма чека:" in accrued_row["details"]
        assert "Начислено бонусов:" in accrued_row["details"]

        close_row = next(row for row in log_rows if row["action"] == "Закрытие смены")
        assert close_row["entity_display"].startswith("Смена #")
        assert "Операций:" in close_row["details"]

    async def test_action_log_includes_admin_actions(self, client: AsyncClient):
        login_resp = await client.post(
            "/api/admin/login",
            json={"username": settings.ADMIN_USERNAME, "password": settings.ADMIN_PASSWORD},
        )
        assert login_resp.status_code == 200

        news_resp = await client.post(
            "/api/admin/news",
            json={"title": "Тестовая новость", "body": "Проверка журнала", "is_active": True},
        )
        assert news_resp.status_code == 201

        today = date.today().isoformat()
        logs_resp = await client.get(f"/api/reports/action-log?date_from={today}&date_to={today}")
        assert logs_resp.status_code == 200
        actions = [row["action"] for row in logs_resp.json()]
        assert "Вход админа" in actions
        assert "Создание новости" in actions

    async def test_action_log_supports_limit_and_offset(self, client: AsyncClient, db_session: AsyncSession):
        login_resp = await client.post(
            "/api/admin/login",
            json={"username": settings.ADMIN_USERNAME, "password": settings.ADMIN_PASSWORD},
        )
        assert login_resp.status_code == 200

        base_time = datetime.now(timezone.utc).replace(microsecond=0)
        db_session.add_all(
            [
                ActionLog(
                    actor_type="admin",
                    actor_name="admin",
                    action=f"admin_test_{index}",
                    entity_type="session",
                    entity_id=index,
                    created_at=base_time + timedelta(seconds=index),
                )
                for index in range(5)
            ]
        )
        await db_session.commit()

        today = date.today().isoformat()
        logs_resp = await client.get(f"/api/reports/action-log?date_from={today}&date_to={today}&limit=2&offset=1")
        assert logs_resp.status_code == 200
        rows = logs_resp.json()
        assert len(rows) == 2
        assert rows[0]["action"] == "admin_test_4"
        assert rows[1]["action"] == "admin_test_3"

    async def test_admin_summary_report_is_admin_only_and_supports_month_and_year(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        may_first = datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc)
        may_second = datetime(2026, 5, 20, 12, 30, tzinfo=timezone.utc)
        june_sale = datetime(2026, 6, 2, 9, 15, tzinfo=timezone.utc)

        client_row = Client(phone="+79001112233", name="Админ отчет")
        db_session.add(client_row)
        await db_session.flush()

        db_session.add_all(
            [
                FuelSale(
                    sale_date=may_first,
                    fuel_type="АИ-95",
                    liters=20,
                    price_per_liter=50,
                    total_rub=1000,
                    client_id=client_row.id,
                    check_id="ADMIN-SUMMARY-FUEL-1",
                ),
                FuelSale(
                    sale_date=may_second,
                    fuel_type="ДТ",
                    liters=15,
                    price_per_liter=60,
                    total_rub=900,
                    client_id=client_row.id,
                    check_id="ADMIN-SUMMARY-FUEL-2",
                ),
                FuelSale(
                    sale_date=june_sale,
                    fuel_type="АИ-92",
                    liters=10,
                    price_per_liter=48,
                    total_rub=480,
                    client_id=client_row.id,
                    check_id="ADMIN-SUMMARY-FUEL-3",
                ),
                Transaction(
                    client_id=client_row.id,
                    ts=may_first,
                    type=TransactionType.accrual,
                    amount_bonus=120,
                    purchase_amount=1000,
                    location=Location.base,
                    check_id="ADMIN-SUMMARY-TX-1",
                ),
                Transaction(
                    client_id=client_row.id,
                    ts=may_second,
                    type=TransactionType.redemption,
                    amount_bonus=30,
                    purchase_amount=300,
                    location=Location.base,
                    check_id="ADMIN-SUMMARY-TX-2",
                ),
                Transaction(
                    client_id=client_row.id,
                    ts=june_sale,
                    type=TransactionType.accrual,
                    amount_bonus=50,
                    purchase_amount=480,
                    location=Location.fuel,
                    check_id="ADMIN-SUMMARY-TX-3",
                ),
            ]
        )
        await db_session.commit()

        unauthorized_resp = await client.get("/api/reports/admin-summary?period_kind=month&year=2026&month=5")
        assert unauthorized_resp.status_code == 401

        login_resp = await client.post(
            "/api/admin/login",
            json={"username": settings.ADMIN_USERNAME, "password": settings.ADMIN_PASSWORD},
        )
        assert login_resp.status_code == 200

        month_resp = await client.get("/api/reports/admin-summary?period_kind=month&year=2026&month=5")
        assert month_resp.status_code == 200
        month_payload = month_resp.json()
        assert month_payload["period_kind"] == "month"
        assert month_payload["period_label"] == "Май 2026"
        assert month_payload["totals"]["fuel_types_count"] == 2
        assert month_payload["totals"]["total_liters"] == 35.0
        assert month_payload["totals"]["accrued_bonus_total"] == 120.0
        assert month_payload["totals"]["redeemed_bonus_total"] == 30.0
        assert [row["fuel_type"] for row in month_payload["fuel_rows"]] == ["АИ-95", "ДТ"]

        year_resp = await client.get("/api/reports/admin-summary?period_kind=year&year=2026")
        assert year_resp.status_code == 200
        year_payload = year_resp.json()
        assert year_payload["period_kind"] == "year"
        assert year_payload["period_label"] == "2026 год"
        assert year_payload["totals"]["fuel_types_count"] == 3
        assert year_payload["totals"]["total_liters"] == 45.0
        assert year_payload["totals"]["accrued_bonus_total"] == 170.0
        assert year_payload["totals"]["redeemed_bonus_total"] == 30.0

    async def test_admin_can_cancel_receipt_and_reports_exclude_it_everywhere(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        cashier = Cashier(name="Кассир отчета", username="report_cancel_admin", password_hash="hash", is_active=True)
        db_session.add(cashier)
        await db_session.flush()

        client_row = Client(
            phone="+79001115566",
            name="Клиент отмены",
            bonus_balance=Decimal("100.00"),
            total_spent=Decimal("1000.00"),
        )
        db_session.add(client_row)
        await db_session.flush()

        sale_dt = datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc)
        db_session.add(
            FuelSale(
                sale_date=sale_dt,
                cashier_name=cashier.name,
                cashier_id=cashier.id,
                fuel_type="АИ-95",
                liters=Decimal("20.000"),
                price_per_liter=Decimal("50.00"),
                total_rub=Decimal("1000.00"),
                client_id=client_row.id,
                check_id="ADMIN-CANCEL-001",
            )
        )
        db_session.add(
            Transaction(
                client_id=client_row.id,
                ts=sale_dt,
                type=TransactionType.accrual,
                amount_bonus=Decimal("100.00"),
                purchase_amount=Decimal("1000.00"),
                location=Location.fuel,
                fuel_liters=Decimal("20.000"),
                cashier_id=cashier.id,
                check_id="ADMIN-CANCEL-001",
            )
        )
        db_session.add(
            BonusExpiry(
                client_id=client_row.id,
                bonus_amount=Decimal("100.00"),
                remaining=Decimal("100.00"),
                expiry_date=date(2026, 12, 31),
            )
        )
        await db_session.commit()

        login_resp = await client.post(
            "/api/admin/login",
            json={"username": settings.ADMIN_USERNAME, "password": settings.ADMIN_PASSWORD},
        )
        assert login_resp.status_code == 200

        cancel_resp = await client.post(
            "/api/admin/cancel-receipt",
            json={"check_id": "ADMIN-CANCEL-001", "reason": "Ошибочный чек"},
        )
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["original_check_id"] == "ADMIN-CANCEL-001"

        month_resp = await client.get("/api/reports/admin-summary?period_kind=month&year=2026&month=5")
        assert month_resp.status_code == 200
        month_payload = month_resp.json()
        assert month_payload["totals"]["total_liters"] == 0.0
        assert month_payload["totals"]["accrued_bonus_total"] == 0.0
        assert month_payload["fuel_rows"] == []

        fuel_type_resp = await client.get("/api/reports/fuel-by-type?date_from=2026-05-01&date_to=2026-05-31")
        assert fuel_type_resp.status_code == 200
        assert fuel_type_resp.json() == []

        cashier_sales_resp = await client.get("/api/reports/cashier-sales?date_from=2026-05-01&date_to=2026-05-31")
        assert cashier_sales_resp.status_code == 200
        assert cashier_sales_resp.json() == []

        bonus_resp = await client.get("/api/reports/bonus-movement?date_from=2026-05-01&date_to=2026-05-31")
        assert bonus_resp.status_code == 200
        assert bonus_resp.json() == []

        action = (
            await db_session.execute(
                select(ActionLog).where(ActionLog.action == "receipt_cancelled", ActionLog.check_id == "ADMIN-CANCEL-001")
            )
        ).scalar_one()
        assert action.actor_type == "admin"

    async def test_fuel_reports_and_closed_shift_summary_include_fuel_metrics(self, client: AsyncClient, db_session: AsyncSession):
        headers = await _cashier_headers(client, db_session)

        register_resp = await client.post(
            "/api/cash/register",
            json={"phone": "+79005556661", "name": "Водитель"},
            headers=headers,
        )
        assert register_resp.status_code == 200

        sale_resp = await client.post(
            "/api/cash/fuel-sale",
            json={
                "phone": "+79005556661",
                "total_rub": 1200,
                "fuel_type": "АИ-95",
                "liters": 24,
                "price_per_liter": 50,
                "check_id": "API-REPORT-FUEL-001",
            },
            headers=headers,
        )
        assert sale_resp.status_code == 200

        second_sale_resp = await client.post(
            "/api/cash/fuel-sale",
            json={
                "phone": "+79005556661",
                "total_rub": 700,
                "fuel_type": "ДТ",
                "liters": 10,
                "price_per_liter": 70,
                "check_id": "API-REPORT-FUEL-002",
                "sale_date": (datetime.now() + timedelta(minutes=5)).isoformat(),
            },
            headers=headers,
        )
        assert second_sale_resp.status_code == 200

        close_resp = await client.post("/api/cashiers/shift/close", headers=headers)
        assert close_resp.status_code == 200

        today = date.today().isoformat()

        shifts_resp = await client.get(f"/api/reports/closed-shifts?date_from={today}&date_to={today}", headers=headers)
        assert shifts_resp.status_code == 200
        shift_row = next(row for row in shifts_resp.json() if row["fuel_sales_count"] == 1)
        assert shift_row["fuel_sales_total"] == 1200.0
        assert shift_row["fuel_liters_total"] == 24.0

        sales_resp = await client.get(f"/api/reports/fuel-sales?date_from={today}&date_to={today}", headers=headers)
        assert sales_resp.status_code == 200
        sale_row = next(row for row in sales_resp.json() if row["fuel_type"] == "АИ-95")
        assert sale_row["total_rub"] == 1200.0
        assert sale_row["liters"] == 24.0
        assert sale_row["client_phone"] == "+79005556661"
        assert sale_row["client_name"] == "Водитель"

        fuel_type_resp = await client.get(f"/api/reports/fuel-by-type?date_from={today}&date_to={today}", headers=headers)
        assert fuel_type_resp.status_code == 200
        fuel_rows = fuel_type_resp.json()
        fuel_row = next(row for row in fuel_rows if row["fuel_type"] == "АИ-95")
        assert fuel_row["sales_count"] == 1
        assert fuel_row["total_liters"] == 24.0
        assert fuel_row["total_rub"] == 1200.0
        assert fuel_row["avg_price_per_liter"] == 50.0
        assert fuel_row["last_sale_at"]
        assert fuel_rows[0]["fuel_type"] == "ДТ"

    async def test_cashier_sales_report_includes_bonus_totals(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)
        headers = await _cashier_headers(client, db_session)

        register_resp = await client.post(
            "/api/cash/register",
            json={"phone": "+79005557771", "name": "Клиент отчета"},
            headers=headers,
        )
        assert register_resp.status_code == 200

        accrue_resp = await client.post(
            "/api/cash/accrue",
            json={
                "phone": "+79005557771",
                "purchase_amount": 1000,
                "location": "base",
                "check_id": "API-CASHIER-REPORT-001",
            },
            headers=headers,
        )
        assert accrue_resp.status_code == 200

        redeem_resp = await client.post(
            "/api/cash/redeem",
            json={
                "phone": "+79005557771",
                "purchase_amount": 500,
                "bonus_to_redeem": 50,
                "location": "base",
                "check_id": "API-CASHIER-REPORT-002",
            },
            headers=headers,
        )
        assert redeem_resp.status_code == 200

        sale_resp = await client.post(
            "/api/cash/fuel-sale",
            json={
                "phone": "+79005557771",
                "total_rub": 1500,
                "fuel_type": "ДТ",
                "liters": 30,
                "price_per_liter": 50,
                "check_id": "API-CASHIER-REPORT-003",
            },
            headers=headers,
        )
        assert sale_resp.status_code == 200

        today = date.today().isoformat()
        cashier_sales_resp = await client.get(
            f"/api/reports/cashier-sales?date_from={today}&date_to={today}",
            headers=headers,
        )
        assert cashier_sales_resp.status_code == 200

        rows = cashier_sales_resp.json()
        assert rows
        cashier_row = next(row for row in rows if row["cashier_name"])
        assert cashier_row["transactions_count"] == 2
        assert cashier_row["accrual_count"] == 1
        assert cashier_row["redemption_count"] == 1
        assert cashier_row["accrued_bonus_total"] > 0
        assert cashier_row["redeemed_bonus_total"] == 50.0
        assert cashier_row["bonus_net_total"] == cashier_row["accrued_bonus_total"] - cashier_row["redeemed_bonus_total"]
        assert cashier_row["sales_count"] == 1
        assert cashier_row["total_rub"] == 1500.0
        assert cashier_row["total_liters"] == 30.0

    async def test_bonus_reports_can_be_filtered_into_accruals_and_redemptions(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)
        headers = await _cashier_headers(client, db_session)

        register_resp = await client.post(
            "/api/cash/register",
            json={"phone": "+79005558881", "name": "Клиент бонусов"},
            headers=headers,
        )
        assert register_resp.status_code == 200

        accrue_resp = await client.post(
            "/api/cash/accrue",
            json={
                "phone": "+79005558881",
                "purchase_amount": 1000,
                "location": "base",
                "check_id": "API-BONUS-FILTER-001",
            },
            headers=headers,
        )
        assert accrue_resp.status_code == 200

        redeem_resp = await client.post(
            "/api/cash/redeem",
            json={
                "phone": "+79005558881",
                "purchase_amount": 400,
                "bonus_to_redeem": 40,
                "location": "base",
                "check_id": "API-BONUS-FILTER-002-R",
            },
            headers=headers,
        )
        assert redeem_resp.status_code == 200

        today = date.today().isoformat()
        accruals_resp = await client.get(
            f"/api/reports/bonus-movement?date_from={today}&date_to={today}&movement_type=accrual",
            headers=headers,
        )
        assert accruals_resp.status_code == 200
        accrual_rows = accruals_resp.json()
        assert accrual_rows
        assert all(row["type"] == "Начисление" for row in accrual_rows)

        redemptions_resp = await client.get(
            f"/api/reports/bonus-movement?date_from={today}&date_to={today}&movement_type=redemption",
            headers=headers,
        )
        assert redemptions_resp.status_code == 200
        redemption_rows = redemptions_resp.json()
        assert redemption_rows
        assert all(row["type"] == "Списание" for row in redemption_rows)

    async def test_closed_shifts_and_cashier_sales_can_be_filtered_by_cashier(self, client: AsyncClient, db_session: AsyncSession):
        await _seed_rule(db_session)

        first_headers = await _cashier_headers(client, db_session)
        first_cashier = (await db_session.execute(select(Cashier).where(Cashier.username == "cashier1"))).scalar_one()

        second_cashier = Cashier(
            name="Второй кассир",
            username="cashier2",
            password_hash=hash_password("secret456"),
            is_active=True,
        )
        db_session.add(second_cashier)
        await db_session.commit()

        second_login = await client.post(
            "/api/cashiers/login",
            json={"username": "cashier2", "password": "secret456"},
        )
        assert second_login.status_code == 200
        second_headers = {"Authorization": f"Bearer {second_login.json()['access_token']}"}
        second_open = await client.post("/api/cashiers/shift/open", headers=second_headers)
        assert second_open.status_code == 200

        first_accrue = await client.post(
            "/api/cash/accrue",
            json={
                "phone": "+79009990001",
                "purchase_amount": 900,
                "location": "base",
                "check_id": "SHIFT-FILTER-001",
            },
            headers=first_headers,
        )
        assert first_accrue.status_code == 200

        second_accrue = await client.post(
            "/api/cash/accrue",
            json={
                "phone": "+79009990002",
                "purchase_amount": 700,
                "location": "base",
                "check_id": "SHIFT-FILTER-002",
            },
            headers=second_headers,
        )
        assert second_accrue.status_code == 200

        first_close = await client.post("/api/cashiers/shift/close", headers=first_headers)
        assert first_close.status_code == 200
        second_close = await client.post("/api/cashiers/shift/close", headers=second_headers)
        assert second_close.status_code == 200

        today = date.today().isoformat()
        shifts_resp = await client.get(
            f"/api/reports/closed-shifts?date_from={today}&date_to={today}&cashier_id={first_cashier.id}",
            headers=first_headers,
        )
        assert shifts_resp.status_code == 200
        shift_rows = shifts_resp.json()
        assert shift_rows
        assert all(row["cashier_name"] == first_cashier.name for row in shift_rows)
        assert sum(float(row["accrued_bonus_total"]) for row in shift_rows) > 0

        cashier_sales_resp = await client.get(
            f"/api/reports/cashier-sales?date_from={today}&date_to={today}&cashier_id={first_cashier.id}",
            headers=first_headers,
        )
        assert cashier_sales_resp.status_code == 200
        sales_rows = cashier_sales_resp.json()
        assert len(sales_rows) == 1
        assert sales_rows[0]["cashier_name"] == first_cashier.name
        assert sales_rows[0]["accrued_bonus_total"] > 0

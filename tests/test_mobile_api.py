from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import BonusExpiry, Client, FuelSale, Location, ReceiptCancellation, Transaction, TransactionType
from app.services.cashier_auth import verify_password
from tests.test_api_cash import _cashier_headers


async def _request_mobile_code(
    client: AsyncClient,
    phone: str,
) -> str:
    send_resp = await client.post("/api/mobile/auth/send-code", json={"phone": phone})
    assert send_resp.status_code == 200
    return send_resp.json()["code"]


async def _request_cashier_mobile_code(
    client: AsyncClient,
    db_session: AsyncSession,
    phone: str,
    *,
    headers: dict[str, str] | None = None,
) -> str:
    if headers is None:
        headers = await _cashier_headers(client, db_session)
    code_resp = await client.post(
        "/api/cash/issue-mobile-code",
        json={"phone": phone},
        headers=headers,
    )
    assert code_resp.status_code == 200
    return code_resp.json()["code"]


@pytest.mark.parametrize(
    ("input_phone", "expected_phone"),
    [
        ("9991234567", "+79991234567"),
        ("901234567", "+992901234567"),
    ],
)
async def test_mobile_auth_accepts_russian_and_tajik_numbers(
    client: AsyncClient,
    input_phone: str,
    expected_phone: str,
):
    code = await _request_mobile_code(client, input_phone)

    verify_resp = await client.post(
        "/api/mobile/auth/verify",
        json={"phone": input_phone, "code": code},
    )
    assert verify_resp.status_code == 200
    token = verify_resp.json()["access_token"]

    profile_resp = await client.get(
        "/api/mobile/profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert profile_resp.status_code == 200
    assert profile_resp.json()["phone"] == expected_phone


async def test_mobile_send_code_returns_code_for_first_self_request(client: AsyncClient):
    send_resp = await client.post("/api/mobile/auth/send-code", json={"phone": "9991234599"})

    assert send_resp.status_code == 200
    assert send_resp.json()["detail"] == "Код подтверждения выдан"
    assert send_resp.json()["code"]
    assert send_resp.json()["issued_once"] is True


async def test_mobile_second_self_request_requires_cashier(client: AsyncClient):
    first_resp = await client.post("/api/mobile/auth/send-code", json={"phone": "9991234588"})
    second_resp = await client.post("/api/mobile/auth/send-code", json={"phone": "9991234588"})

    assert first_resp.status_code == 200
    assert first_resp.json()["code"]
    assert second_resp.status_code == 403
    assert second_resp.json()["detail"] == "Повторный код можно получить только у кассира"


async def test_cashier_can_reissue_mobile_code_after_self_request(client: AsyncClient, db_session: AsyncSession):
    send_resp = await client.post("/api/mobile/auth/send-code", json={"phone": "9991234501"})
    assert send_resp.status_code == 200

    headers = await _cashier_headers(client, db_session)
    issue_resp = await client.post("/api/cash/issue-mobile-code", json={"phone": "9991234501"}, headers=headers)

    assert issue_resp.status_code == 200
    payload = issue_resp.json()
    assert payload["code"]
    assert payload["phone"] == "+79991234501"


async def test_mobile_verify_limits_code_attempts_to_two(client: AsyncClient):
    code = await _request_mobile_code(client, "9991234502")

    first = await client.post("/api/mobile/auth/verify", json={"phone": "9991234502", "code": "0000"})
    second = await client.post("/api/mobile/auth/verify", json={"phone": "9991234502", "code": "1111"})
    third = await client.post("/api/mobile/auth/verify", json={"phone": "9991234502", "code": code})

    assert first.status_code == 401
    assert "Осталось попыток: 1" in first.json()["detail"]
    assert second.status_code == 401
    assert "Попытки закончились" in second.json()["detail"]
    assert third.status_code == 401


async def test_mobile_verify_without_active_code_tells_to_contact_cashier(client: AsyncClient):
    verify_resp = await client.post(
        "/api/mobile/auth/verify",
        json={"phone": "9991234591", "code": "1234"},
    )

    assert verify_resp.status_code == 401
    assert verify_resp.json()["detail"] == "Код истек или недействителен. Попросите кассира выдать новый код"


async def test_admin_can_update_client_password(client: AsyncClient, db_session: AsyncSession):
    code = await _request_mobile_code(client, "9991234503")
    verify_resp = await client.post(
        "/api/mobile/auth/verify",
        json={"phone": "9991234503", "code": code},
    )
    assert verify_resp.status_code == 200

    db_client = (await db_session.execute(select(Client).where(Client.phone == "+79991234503"))).scalar_one()

    admin_login = await client.post(
        "/api/admin/login",
        json={"username": settings.ADMIN_USERNAME, "password": settings.ADMIN_PASSWORD},
    )
    assert admin_login.status_code == 200

    update_resp = await client.post(
        f"/api/admin/clients/{db_client.id}/password",
        json={"password": "admin-reset-123"},
    )

    assert update_resp.status_code == 200
    assert update_resp.json()["password"] == "admin-reset-123"

    refreshed = (await db_session.execute(select(Client).where(Client.id == db_client.id))).scalar_one()
    assert verify_password("admin-reset-123", refreshed.password_hash)


async def test_mobile_send_code_redirects_to_password_when_client_has_password(client: AsyncClient, db_session: AsyncSession):
    code = await _request_mobile_code(client, "9991234504")
    verify_resp = await client.post(
        "/api/mobile/auth/verify",
        json={"phone": "9991234504", "code": code},
    )
    assert verify_resp.status_code == 200
    token = verify_resp.json()["access_token"]

    set_password_resp = await client.post(
        "/api/mobile/auth/set-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"password": "has-password-123"},
    )
    assert set_password_resp.status_code == 200

    next_send_resp = await client.post("/api/mobile/auth/send-code", json={"phone": "9991234504"})

    assert next_send_resp.status_code == 200
    assert next_send_resp.json()["redirect_to_password"] is True
    assert next_send_resp.json()["has_password"] is True
    assert "code" not in next_send_resp.json()


async def test_mobile_client_can_set_password_and_relogin_without_sms(client: AsyncClient):
    code = await _request_mobile_code(client, "9991234511")

    verify_resp = await client.post(
        "/api/mobile/auth/verify",
        json={"phone": "9991234511", "code": code},
    )

    assert verify_resp.status_code == 200
    assert verify_resp.json()["password_required"] is True
    token = verify_resp.json()["access_token"]

    set_password_resp = await client.post(
        "/api/mobile/auth/set-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"password": "secret123"},
    )
    assert set_password_resp.status_code == 200

    login_resp = await client.post(
        "/api/mobile/auth/login-password",
        json={"phone": "9991234511", "password": "secret123"},
    )
    assert login_resp.status_code == 200
    assert login_resp.json()["password_required"] is False


async def test_mobile_client_can_reset_password_via_cashier_code(client: AsyncClient, db_session: AsyncSession):
    headers = await _cashier_headers(client, db_session)
    first_code = await _request_mobile_code(client, "9991234512")
    verify_resp = await client.post(
        "/api/mobile/auth/verify",
        json={"phone": "9991234512", "code": first_code},
    )
    token = verify_resp.json()["access_token"]
    await client.post(
        "/api/mobile/auth/set-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"password": "secret123"},
    )

    reset_request = await client.post("/api/mobile/auth/request-password-reset", json={"phone": "9991234512"})
    assert reset_request.status_code == 200
    assert reset_request.json() == {"detail": "Одноразовый код для сброса можно получить у кассира"}
    reset_code = await _request_cashier_mobile_code(client, db_session, "9991234512", headers=headers)

    reset_resp = await client.post(
        "/api/mobile/auth/reset-password",
        json={"phone": "9991234512", "code": reset_code, "password": "newsecret123"},
    )
    assert reset_resp.status_code == 200

    login_resp = await client.post(
        "/api/mobile/auth/login-password",
        json={"phone": "9991234512", "password": "newsecret123"},
    )
    assert login_resp.status_code == 200


async def test_mobile_password_login_requires_password_setup(client: AsyncClient):
    code = await _request_mobile_code(client, "9991234513")
    await client.post(
        "/api/mobile/auth/verify",
        json={"phone": "9991234513", "code": code},
    )

    login_resp = await client.post(
        "/api/mobile/auth/login-password",
        json={"phone": "9991234513", "password": "secret123"},
    )
    assert login_resp.status_code == 403


async def test_mobile_profile_updates_full_name_photo_and_birth_date(client: AsyncClient):
    code = await _request_mobile_code(client, "9991234567")

    verify_resp = await client.post(
        "/api/mobile/auth/verify",
        json={"phone": "9991234567", "code": code},
    )
    assert verify_resp.status_code == 200
    token = verify_resp.json()["access_token"]

    patch_resp = await client.patch(
        "/api/mobile/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "first_name": "Али",
            "last_name": "Саидов",
            "patronymic": "Каримович",
            "birth_date": "2001-05-17",
            "photo_data_url": "data:image/png;base64,AAAA",
        },
    )

    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["name"] == "Саидов Али Каримович"
    assert data["first_name"] == "Али"
    assert data["last_name"] == "Саидов"
    assert data["patronymic"] == "Каримович"
    assert data["birth_date"] == "2001-05-17"
    assert data["photo_data_url"] == "data:image/png;base64,AAAA"

    profile_resp = await client.get(
        "/api/mobile/profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert profile_resp.status_code == 200
    persisted = profile_resp.json()
    assert persisted["name"] == "Саидов Али Каримович"
    assert persisted["photo_data_url"] == "data:image/png;base64,AAAA"


async def test_mobile_profile_can_clear_photo(client: AsyncClient):
    code = await _request_mobile_code(client, "9991234567")
    verify_resp = await client.post(
        "/api/mobile/auth/verify",
        json={"phone": "9991234567", "code": code},
    )
    token = verify_resp.json()["access_token"]

    first_patch = await client.patch(
        "/api/mobile/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"photo_data_url": "data:image/png;base64,BBBB"},
    )
    assert first_patch.status_code == 200
    assert first_patch.json()["photo_data_url"] == "data:image/png;base64,BBBB"

    clear_patch = await client.patch(
        "/api/mobile/profile",
        headers={"Authorization": f"Bearer {token}"},
        json={"photo_data_url": ""},
    )
    assert clear_patch.status_code == 200
    assert clear_patch.json()["photo_data_url"] is None


async def test_mobile_client_card_includes_history_cancellations_and_expiry(client: AsyncClient, db_session: AsyncSession):
    code = await _request_mobile_code(client, "9991234567")
    verify_resp = await client.post(
        "/api/mobile/auth/verify",
        json={"phone": "9991234567", "code": code},
    )
    token = verify_resp.json()["access_token"]

    db_client = (await db_session.execute(select(Client).where(Client.phone == "+79991234567"))).scalar_one()
    db_session.add_all(
        [
            Transaction(
                client_id=db_client.id,
                ts=datetime.now(),
                type=TransactionType.accrual,
                amount_bonus=Decimal("40.00"),
                purchase_amount=Decimal("400.00"),
                location=Location.base,
                check_id="MOBILE-CARD-001",
            ),
            Transaction(
                client_id=db_client.id,
                ts=datetime.now(),
                type=TransactionType.redemption,
                amount_bonus=Decimal("20.00"),
                purchase_amount=Decimal("100.00"),
                location=Location.base,
                check_id="MOBILE-CARD-002",
            ),
            ReceiptCancellation(
                original_check_id="MOBILE-CARD-001",
                canceled_check_id="MOBILE-CANCEL-001",
                client_id=db_client.id,
                reason="Тестовая отмена",
                created_at=datetime.now(),
            ),
            BonusExpiry(
                client_id=db_client.id,
                bonus_amount=Decimal("40.00"),
                remaining=Decimal("20.00"),
                expiry_date=date.today() + timedelta(days=1),
            ),
        ]
    )
    await db_session.commit()

    card_resp = await client.get(
        "/api/mobile/client-card",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert card_resp.status_code == 200
    payload = card_resp.json()
    assert payload["bonus_expiry"][0]["remaining"] == "20.00"
    assert payload["cancellations"][0]["reason"] == "Тестовая отмена"
    assert payload["summary"]["nearest_expiry_remaining"] == "20.00"
    assert any(item["type"] == "cancellation" for item in payload["history"])


async def test_mobile_client_card_history_includes_fuel_type_for_receipt(client: AsyncClient, db_session: AsyncSession):
    code = await _request_mobile_code(client, "9991234570")
    verify_resp = await client.post(
        "/api/mobile/auth/verify",
        json={"phone": "9991234570", "code": code},
    )
    token = verify_resp.json()["access_token"]

    db_client = (await db_session.execute(select(Client).where(Client.phone == "+79991234570"))).scalar_one()
    db_session.add(
        Transaction(
            client_id=db_client.id,
            ts=datetime.now(),
            type=TransactionType.accrual,
            amount_bonus=Decimal("30.00"),
            purchase_amount=Decimal("600.00"),
            location=Location.fuel,
            fuel_liters=Decimal("20.000"),
            check_id="MOBILE-FUEL-001",
        )
    )
    await db_session.flush()
    db_session.add(
        FuelSale(
            client_id=db_client.id,
            sale_date=datetime.now(),
            fuel_type="АИ-92",
            liters=Decimal("20.000"),
            price_per_liter=Decimal("30.00"),
            total_rub=Decimal("600.00"),
            check_id="MOBILE-FUEL-001",
        )
    )
    await db_session.commit()

    card_resp = await client.get(
        "/api/mobile/client-card",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert card_resp.status_code == 200
    payload = card_resp.json()
    tx = next(item for item in payload["history"] if item["check_id"] == "MOBILE-FUEL-001")
    assert tx["fuel_type"] == "АИ-92"
    assert tx["fuel_liters"] == "20.000"
    assert tx["price_per_liter"] == "30.00"


async def test_mobile_profile_excludes_bonus_expiring_today(client: AsyncClient, db_session: AsyncSession):
    code = await _request_mobile_code(client, "9991234568")
    verify_resp = await client.post(
        "/api/mobile/auth/verify",
        json={"phone": "9991234568", "code": code},
    )
    token = verify_resp.json()["access_token"]

    db_client = (await db_session.execute(select(Client).where(Client.phone == "+79991234568"))).scalar_one()
    db_client.bonus_balance = Decimal("30.00")
    db_session.add(
        BonusExpiry(
            client_id=db_client.id,
            bonus_amount=Decimal("30.00"),
            remaining=Decimal("30.00"),
            expiry_date=date.today(),
        )
    )
    await db_session.commit()

    profile_resp = await client.get(
        "/api/mobile/profile",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert profile_resp.status_code == 200
    assert profile_resp.json()["bonus_balance"] == "0.00"


async def test_mobile_client_card_sums_bonus_expiry_for_same_nearest_date(client: AsyncClient, db_session: AsyncSession):
    code = await _request_mobile_code(client, "9991234569")
    verify_resp = await client.post(
        "/api/mobile/auth/verify",
        json={"phone": "9991234569", "code": code},
    )
    token = verify_resp.json()["access_token"]

    db_client = (await db_session.execute(select(Client).where(Client.phone == "+79991234569"))).scalar_one()
    db_client.bonus_balance = Decimal("50.00")
    db_session.add_all(
        [
            BonusExpiry(
                client_id=db_client.id,
                bonus_amount=Decimal("20.00"),
                remaining=Decimal("20.00"),
                expiry_date=date.today() + timedelta(days=1),
            ),
            BonusExpiry(
                client_id=db_client.id,
                bonus_amount=Decimal("30.00"),
                remaining=Decimal("30.00"),
                expiry_date=date.today() + timedelta(days=1),
            ),
        ]
    )
    await db_session.commit()

    card_resp = await client.get(
        "/api/mobile/client-card",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert card_resp.status_code == 200
    payload = card_resp.json()
    assert payload["bonus_balance"] == "50.00"
    assert payload["summary"]["nearest_expiry_remaining"] == "50.00"
    assert len(payload["bonus_expiry"]) == 2
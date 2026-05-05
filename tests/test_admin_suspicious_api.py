from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.db.models import Cashier, Client
from app.main import app
from app.services.action_logs import log_action
from app.services.admin_auth import require_admin


@pytest.fixture
def admin_override():
    app.dependency_overrides[require_admin] = lambda: True
    yield
    app.dependency_overrides.pop(require_admin, None)


async def test_admin_suspicious_activity_returns_daily_alerts(client: AsyncClient, db_session, admin_override):
    cashier = Cashier(name="Кассир контроля", username="audit_cashier", password_hash="stub", is_active=True)
    client_record = Client(phone="+79001112233", name="Клиент контроля")
    db_session.add(cashier)
    db_session.add(client_record)
    await db_session.flush()

    for idx in range(3):
        await log_action(
            db_session,
            actor_type="cashier",
            actor_id=cashier.id,
            actor_name=cashier.name,
            action="receipt_cancelled",
            entity_type="receipt",
            check_id=f"CANCEL-{idx}",
            details={"reason": "Проверка"},
        )

    for idx in range(5):
        await log_action(
            db_session,
            actor_type="cashier",
            actor_id=cashier.id,
            actor_name=cashier.name,
            action="receipt_processed",
            entity_type="receipt",
            check_id=f"NOCLIENT-{idx}",
            details={"has_client": False, "purchase_amount": "500.00"},
        )

    for idx in range(4):
        await log_action(
            db_session,
            actor_type="cashier",
            actor_id=cashier.id,
            actor_name=cashier.name,
            action="client_registered_cashier",
            entity_type="client",
            entity_id=client_record.id,
            check_id=f"REG-{idx}",
            details={"phone": f"+7900000{idx}"},
        )

    await db_session.commit()

    resp = await client.get("/api/admin/suspicious-activity")
    assert resp.status_code == 200
    payload = resp.json()
    codes = {item["code"] for item in payload["items"]}
    assert payload["count"] == 3
    assert "many_cancellations" in codes
    assert "many_receipts_without_client" in codes
    assert "many_manual_registrations" in codes

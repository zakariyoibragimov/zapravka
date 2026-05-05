from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.main import app
from app.services.admin_auth import require_admin
from app.services.app_settings import get_bonus_expiry_months


@pytest.fixture
def admin_override():
    app.dependency_overrides[require_admin] = lambda: True
    yield
    app.dependency_overrides.pop(require_admin, None)


async def test_admin_bonus_expiry_settings_default(client: AsyncClient, db_session, admin_override):
    resp = await client.get("/api/admin/settings/bonus-expiry")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["months"] >= 1
    assert payload["preview_expiry_date_label"]
    assert payload["applies_to"] == "new_accruals_only"


async def test_admin_bonus_expiry_settings_update(client: AsyncClient, db_session, admin_override):
    resp = await client.put("/api/admin/settings/bonus-expiry", json={"months": 6})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["months"] == 6
    assert payload["source"] == "admin"
    assert payload["preview_expiry_date_label"]
    assert await get_bonus_expiry_months(db_session) == 6

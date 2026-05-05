from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.main import app
from app.services.admin_auth import require_admin


@pytest.fixture
def admin_override():
    app.dependency_overrides[require_admin] = lambda: True
    yield
    app.dependency_overrides.pop(require_admin, None)


async def test_admin_can_create_cashier_and_cashier_can_login(client: AsyncClient, admin_override):
    create_resp = await client.post(
        "/api/admin/cashiers",
        json={
            "name": "Новый кассир",
            "username": "cashier_created",
            "password": "secret123",
            "azs_id": 1,
        },
    )

    assert create_resp.status_code == 201
    created = create_resp.json()
    assert created["name"] == "Новый кассир"
    assert created["username"] == "cashier_created"
    assert created["azs_id"] == 1
    assert created["is_active"] is True

    login_resp = await client.post(
        "/api/cashiers/login",
        json={"username": "cashier_created", "password": "secret123"},
    )

    assert login_resp.status_code == 200
    payload = login_resp.json()
    assert payload["access_token"]
    assert payload["cashier"]["username"] == "cashier_created"
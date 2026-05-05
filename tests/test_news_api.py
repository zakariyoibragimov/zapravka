from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.services.admin_auth import require_admin


@pytest.fixture
def admin_override():
    app.dependency_overrides[require_admin] = lambda: True
    yield
    app.dependency_overrides.pop(require_admin, None)


async def _mobile_token(client: AsyncClient, db_session: AsyncSession, phone: str) -> str:
    send_resp = await client.post("/api/mobile/auth/send-code", json={"phone": phone})
    assert send_resp.status_code == 200
    code = send_resp.json()["code"]

    verify_resp = await client.post(
        "/api/mobile/auth/verify",
        json={"phone": phone, "code": code},
    )
    assert verify_resp.status_code == 200
    return verify_resp.json()["access_token"]


class TestNewsAPI:
    async def test_admin_can_create_and_mobile_can_read_news(self, client: AsyncClient, db_session: AsyncSession, admin_override):
        create_resp = await client.post(
            "/api/admin/news",
            json={
                "title": "Новая акция",
                "body": "С пятницы двойные бонусы на АИ-95.",
                "is_active": True,
            },
        )
        assert create_resp.status_code == 201
        created = create_resp.json()
        assert created["title"] == "Новая акция"

        token = await _mobile_token(client, db_session, "+79005550111")

        news_resp = await client.get(
            "/api/mobile/news",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert news_resp.status_code == 200
        payload = news_resp.json()
        assert len(payload) == 1
        assert payload[0]["title"] == "Новая акция"
        assert payload[0]["body"] == "С пятницы двойные бонусы на АИ-95."

    async def test_mobile_news_hides_inactive_items(self, client: AsyncClient, db_session: AsyncSession, admin_override):
        resp = await client.post(
            "/api/admin/news",
            json={
                "title": "Черновик",
                "body": "Этого клиент видеть не должен.",
                "is_active": False,
            },
        )
        assert resp.status_code == 201

        token = await _mobile_token(client, db_session, "+79005550112")

        news_resp = await client.get(
            "/api/mobile/news",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert news_resp.status_code == 200
        assert news_resp.json() == []

    async def test_admin_can_update_news_and_keep_it_visible(self, client: AsyncClient, db_session: AsyncSession, admin_override):
        create_resp = await client.post(
            "/api/admin/news",
            json={
                "title": "Старая новость",
                "body": "Старый текст.",
                "is_active": True,
            },
        )
        news_id = create_resp.json()["id"]

        update_resp = await client.put(
            f"/api/admin/news/{news_id}",
            json={
                "title": "Обновленная новость",
                "body": "Новый текст для клиентов.",
                "is_active": True,
            },
        )
        assert update_resp.status_code == 200
        updated = update_resp.json()
        assert updated["title"] == "Обновленная новость"
        assert updated["body"] == "Новый текст для клиентов."
        assert updated["is_active"] is True

        token = await _mobile_token(client, db_session, "+79005550113")

        news_resp = await client.get(
            "/api/mobile/news",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert news_resp.status_code == 200
        payload = news_resp.json()
        assert len(payload) == 1
        assert payload[0]["title"] == "Обновленная новость"

    async def test_admin_can_toggle_news_visibility(self, client: AsyncClient, db_session: AsyncSession, admin_override):
        create_resp = await client.post(
            "/api/admin/news",
            json={
                "title": "Временная новость",
                "body": "Скрываем без удаления.",
                "is_active": True,
            },
        )
        news_id = create_resp.json()["id"]

        toggle_resp = await client.patch(
            f"/api/admin/news/{news_id}/status",
            json={"is_active": False},
        )
        assert toggle_resp.status_code == 200
        assert toggle_resp.json()["is_active"] is False

        token = await _mobile_token(client, db_session, "+79005550114")

        news_resp = await client.get(
            "/api/mobile/news",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert news_resp.status_code == 200
        assert news_resp.json() == []

    async def test_admin_news_normalizes_extra_whitespace_and_blank_lines(self, client: AsyncClient, admin_override):
        create_resp = await client.post(
            "/api/admin/news",
            json={
                "title": "  Большая    акция   ",
                "body": "  Первый   абзац  \n\n\n   Второй    абзац   ",
                "is_active": True,
            },
        )
        assert create_resp.status_code == 201
        created = create_resp.json()
        assert created["title"] == "Большая акция"
        assert created["body"] == "Первый абзац\n\nВторой абзац"

    async def test_mobile_news_filters_duplicate_items(self, client: AsyncClient, db_session: AsyncSession, admin_override):
        first_resp = await client.post(
            "/api/admin/news",
            json={
                "title": "Скидка дня",
                "body": "Только сегодня двойные бонусы.",
                "is_active": True,
            },
        )
        assert first_resp.status_code == 201

        second_resp = await client.post(
            "/api/admin/news",
            json={
                "title": "  Скидка   дня ",
                "body": "Только сегодня   двойные бонусы.\n",
                "is_active": True,
            },
        )
        assert second_resp.status_code == 201

        token = await _mobile_token(client, db_session, "+79005550115")

        news_resp = await client.get(
            "/api/mobile/news",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert news_resp.status_code == 200
        payload = news_resp.json()
        assert len(payload) == 1
        assert payload[0]["title"] == "Скидка дня"
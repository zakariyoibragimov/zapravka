from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Cashier
from app.services.cashier_auth import CASHIER_SESSION_COOKIE, hash_password


class TestFrontendStaffAccess:
    async def test_cashier_page_redirects_to_staff_login_when_unauthorized(self, client: AsyncClient):
        response = await client.get('/cashier')

        assert response.status_code == 303
        assert response.headers['location'] == '/staff-login?next=%2Fcashier&role=staff'

    async def test_reports_page_redirects_to_staff_login_when_unauthorized(self, client: AsyncClient):
        response = await client.get('/reports')

        assert response.status_code == 303
        assert response.headers['location'] == '/staff-login?next=%2Freports&role=staff'

    async def test_cashier_login_sets_session_cookie_and_opens_cashier_page(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        cashier = Cashier(
            name='Кассир 1',
            username='cashier1',
            password_hash=hash_password('secret123'),
            is_active=True,
        )
        db_session.add(cashier)
        await db_session.commit()

        login_response = await client.post(
            '/api/cashiers/login',
            json={'username': 'cashier1', 'password': 'secret123'},
        )

        assert login_response.status_code == 200
        assert CASHIER_SESSION_COOKIE in login_response.headers.get('set-cookie', '')

        page_response = await client.get('/cashier')
        assert page_response.status_code == 200
        assert 'Касса' in page_response.text
        assert '/static/cashier-runtime.js' in page_response.text

    async def test_admin_pages_open_only_after_admin_login(self, client: AsyncClient):
        blocked = await client.get('/admin')
        assert blocked.status_code == 303
        assert blocked.headers['location'] == '/staff-login?next=%2Fadmin&role=admin'

        login_response = await client.post(
            '/api/admin/login',
            json={'username': settings.ADMIN_USERNAME, 'password': settings.ADMIN_PASSWORD},
        )
        assert login_response.status_code == 200

        dashboard_response = await client.get('/admin')
        assert dashboard_response.status_code == 200
        assert 'Дашборд' in dashboard_response.text

        reports_response = await client.get('/reports')
        assert reports_response.status_code == 200
        assert 'Отчёты' in reports_response.text

    async def test_staff_login_redirects_authenticated_admin_to_requested_page(self, client: AsyncClient):
        login_response = await client.post(
            '/api/admin/login',
            json={'username': settings.ADMIN_USERNAME, 'password': settings.ADMIN_PASSWORD},
        )
        assert login_response.status_code == 200

        response = await client.get('/staff-login?next=/reports&role=staff')

        assert response.status_code == 303
        assert response.headers['location'] == '/reports'

    async def test_staff_login_redirects_authenticated_admin_to_requested_fuel_prices_page(self, client: AsyncClient):
        login_response = await client.post(
            '/api/admin/login',
            json={'username': settings.ADMIN_USERNAME, 'password': settings.ADMIN_PASSWORD},
        )
        assert login_response.status_code == 200

        response = await client.get('/staff-login?next=/fuel-prices&role=admin')

        assert response.status_code == 303
        assert response.headers['location'] == '/fuel-prices'

    async def test_staff_login_redirects_authenticated_cashier_to_cashier_page(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        cashier = Cashier(
            name='Кассир 2',
            username='cashier2',
            password_hash=hash_password('secret123'),
            is_active=True,
        )
        db_session.add(cashier)
        await db_session.commit()

        login_response = await client.post(
            '/api/cashiers/login',
            json={'username': 'cashier2', 'password': 'secret123'},
        )
        assert login_response.status_code == 200

        response = await client.get('/staff-login?next=/reports&role=staff')

        assert response.status_code == 303
        assert response.headers['location'] == '/cashier'

    async def test_admin_can_open_cashier_page_after_admin_login(self, client: AsyncClient):
        login_response = await client.post(
            '/api/admin/login',
            json={'username': settings.ADMIN_USERNAME, 'password': settings.ADMIN_PASSWORD},
        )
        assert login_response.status_code == 200

        response = await client.get('/cashier')

        assert response.status_code == 200
        assert 'Касса' in response.text

    async def test_admin_can_open_fuel_prices_page_after_admin_login(self, client: AsyncClient):
        login_response = await client.post(
            '/api/admin/login',
            json={'username': settings.ADMIN_USERNAME, 'password': settings.ADMIN_PASSWORD},
        )
        assert login_response.status_code == 200

        response = await client.get('/fuel-prices')

        assert response.status_code == 200
        assert 'Цены на топливо' in response.text

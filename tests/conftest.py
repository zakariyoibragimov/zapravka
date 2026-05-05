from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.services.sms import reset_sms_state

# SQLite в памяти для тестов
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(autouse=True)
async def reset_sms_fixture():
    await reset_sms_state()
    yield
    await reset_sms_state()


@pytest.fixture(autouse=True)
def force_stub_sms_provider(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "test")
    monkeypatch.setattr(settings, "SMS_PROVIDER", "stub")
    monkeypatch.setattr(settings, "SMS_STUB_EXPOSE_CODE", True)
    monkeypatch.setattr(settings, "SMS_PROVIDER_API_KEY", "")
    monkeypatch.setattr(settings, "SMS_PROVIDER_SENDER", "")
    monkeypatch.setattr(settings, "SMS_PROVIDER_URL", "https://sms.ru/sms/send")


@pytest.fixture(scope="session")
def engine():
    return create_async_engine(TEST_DB_URL, echo=False)


@pytest_asyncio.fixture(scope="session")
async def init_db(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session(engine, init_db) -> AsyncSession:
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as session:
        for table in reversed(Base.metadata.sorted_tables):
            await session.execute(delete(table))
        await session.commit()
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncClient:
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()

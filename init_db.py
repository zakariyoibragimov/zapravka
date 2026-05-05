"""
Скрипт инициализации БД для локального запуска.
Создаёт все таблицы через SQLAlchemy (минуя Alembic).
"""
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from app.db.base import Base
from app.db import models  # noqa: F401 — регистрирует все модели
from app.config import settings


async def init():
    engine = create_async_engine(settings.DATABASE_URL, echo=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print("✓ База данных инициализирована:", settings.DATABASE_URL)


if __name__ == "__main__":
    asyncio.run(init())

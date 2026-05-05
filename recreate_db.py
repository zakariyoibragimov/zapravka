import os
import sys
import asyncio

# Явно переходим в папку проекта
os.chdir(r"C:\Users\user\Desktop\Zapravka")
sys.path.insert(0, r"C:\Users\user\Desktop\Zapravka")

# Удаляем старую БД
db_path = r"C:\Users\user\Desktop\Zapravka\azs_bonus.db"
if os.path.exists(db_path):
    os.remove(db_path)
    print(f"Удалена старая БД: {db_path}")
else:
    print("Старая БД не найдена")

from sqlalchemy.ext.asyncio import create_async_engine
from app.db.base import Base
from app.db import models  # noqa: F401
from app.config import settings

async def init():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print("✓ БД создана:", settings.DATABASE_URL)

asyncio.run(init())

# Проверка схемы
import sqlite3
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='accrual_rules'")
row = cur.fetchone()
print("Схема accrual_rules:")
print(row[0] if row else "Таблица не найдена")

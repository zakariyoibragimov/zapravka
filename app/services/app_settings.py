from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import AppSetting

BONUS_EXPIRY_MONTHS_KEY = "bonus_expiry_months"


async def ensure_app_settings_table(session: AsyncSession) -> None:
    conn = await session.connection()
    await conn.run_sync(lambda sync_conn: AppSetting.__table__.create(sync_conn, checkfirst=True))


async def get_setting(session: AsyncSession, key: str) -> str | None:
    await ensure_app_settings_table(session)
    result = await session.execute(select(AppSetting).where(AppSetting.key == key))
    item = result.scalar_one_or_none()
    return item.value if item else None


async def get_setting_entry(session: AsyncSession, key: str) -> AppSetting | None:
    await ensure_app_settings_table(session)
    result = await session.execute(select(AppSetting).where(AppSetting.key == key))
    return result.scalar_one_or_none()


async def set_setting(session: AsyncSession, key: str, value: str) -> AppSetting:
    await ensure_app_settings_table(session)
    result = await session.execute(select(AppSetting).where(AppSetting.key == key))
    item = result.scalar_one_or_none()
    if item is None:
        item = AppSetting(key=key, value=value)
        session.add(item)
        await session.flush()
        return item
    item.value = value
    await session.flush()
    return item


async def get_bonus_expiry_months(session: AsyncSession) -> int:
    raw = await get_setting(session, BONUS_EXPIRY_MONTHS_KEY)
    if raw is None:
        return max(1, int(settings.BONUS_EXPIRY_MONTHS or 12))
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return max(1, int(settings.BONUS_EXPIRY_MONTHS or 12))

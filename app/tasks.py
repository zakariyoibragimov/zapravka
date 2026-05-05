from __future__ import annotations

import asyncio
import logging

from app.celery_app import celery_app
from app.db.session import AsyncSessionLocal
from app.services.bonus_expiry import expire_bonuses

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.expire_bonuses_task", bind=True, max_retries=3)
def expire_bonuses_task(self):
    """Ежедневное сгорание просроченных бонусов."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    async def _run():
        async with AsyncSessionLocal() as session:
            count = await expire_bonuses(session)
            logger.info("Сгорело бонусов у %d клиентов", count)
            return count

    return loop.run_until_complete(_run())

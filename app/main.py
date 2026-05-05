from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.admin import router as admin_router
from app.api.admin_session import router as admin_session_router
from app.api.cash import router as cash_router
from app.api.fuel_prices import router as fuel_prices_router
from app.api.mobile import router as mobile_router
from app.api.reports import router as reports_router
from app.config import settings
from app.db.session import AsyncSessionLocal
from app.services.bonus_expiry import expire_bonuses
from app.views.frontend import router as frontend_router
from app.api.cashiers import router as cashiers_router

logger = logging.getLogger(__name__)
_bonus_expiry_worker_task: asyncio.Task | None = None


async def _bonus_expiry_worker() -> None:
    """Fallback scheduler for bonus expiry when Celery beat is not running."""
    while True:
        try:
            async with AsyncSessionLocal() as session:
                expired_clients = await expire_bonuses(session, commit=True)
                if expired_clients:
                    logger.info("Auto expiry worker processed %d client(s)", expired_clients)
        except Exception:
            logger.exception("Auto expiry worker failed")
        await asyncio.sleep(60 * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bonus_expiry_worker_task
    if settings.APP_ENV.lower() != "test":
        _bonus_expiry_worker_task = asyncio.create_task(_bonus_expiry_worker())
    try:
        yield
    finally:
        if _bonus_expiry_worker_task is not None:
            _bonus_expiry_worker_task.cancel()
            try:
                await _bonus_expiry_worker_task
            except asyncio.CancelledError:
                pass
            _bonus_expiry_worker_task = None


app = FastAPI(
    title="АЗС Бонус",
    version="1.0.0",
    description="Бонусная система для сети АЗС и магазинов",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# API
app.include_router(cash_router)
app.include_router(cashiers_router)
app.include_router(fuel_prices_router)
app.include_router(mobile_router)
app.include_router(admin_session_router)
app.include_router(admin_router)
app.include_router(reports_router)

# Frontend
app.include_router(frontend_router)


@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/cashier")

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FuelPrice
from app.db.session import get_db

router = APIRouter(prefix="/api/fuel-prices", tags=["fuel-prices"])


class FuelPriceUpdate(BaseModel):
    price_per_liter: Decimal = Field(gt=0, decimal_places=2)
    is_active: Optional[bool] = None


class FuelPriceCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=100)
    price_per_liter: Decimal = Field(gt=0, decimal_places=2)


def _row(fp: FuelPrice) -> dict:
    updated_at = None
    if fp.updated_at is not None:
        if hasattr(fp.updated_at, "strftime"):
            updated_at = fp.updated_at.strftime("%d.%m.%Y %H:%M")
        else:
            # на всякий случай (если драйвер/ORM вернул не datetime)
            updated_at = str(fp.updated_at)
    return {
        "id": fp.id,
        "code": fp.code,
        "name": fp.name,
        "price_per_liter": str(fp.price_per_liter),
        "is_active": fp.is_active,
        "updated_at": updated_at,
    }


@router.get("")
async def list_fuel_prices(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FuelPrice).order_by(FuelPrice.id))
    return [_row(fp) for fp in result.scalars().all()]


@router.patch("/{fuel_id}")
async def update_fuel_price(
    fuel_id: int,
    body: FuelPriceUpdate,
    db: AsyncSession = Depends(get_db),
):
    async with db.begin():
        fp = (await db.execute(select(FuelPrice).where(FuelPrice.id == fuel_id))).scalar_one_or_none()
        if fp is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Вид топлива не найден")
        fp.price_per_liter = body.price_per_liter
        if body.is_active is not None:
            fp.is_active = body.is_active
        await db.flush()
        # Подтянуть server-side updated_at (и тип) до формирования ответа
        await db.refresh(fp)
        return _row(fp)


@router.post("")
async def create_fuel_price(body: FuelPriceCreate, db: AsyncSession = Depends(get_db)):
    async with db.begin():
        existing = (await db.execute(select(FuelPrice).where(FuelPrice.code == body.code))).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=400, detail="Вид топлива с таким кодом уже существует")
        fp = FuelPrice(code=body.code, name=body.name, price_per_liter=body.price_per_liter)
        db.add(fp)
        await db.flush()
        await db.refresh(fp)
        return _row(fp)


@router.delete("/{fuel_id}")
async def delete_fuel_price(fuel_id: int, db: AsyncSession = Depends(get_db)):
    async with db.begin():
        fp = (await db.execute(select(FuelPrice).where(FuelPrice.id == fuel_id))).scalar_one_or_none()
        if fp is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Вид топлива не найден")
        await db.delete(fp)
    return {"ok": True}

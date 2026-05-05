from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ClientLevel(str, enum.Enum):
    bronze = "bronze"
    silver = "silver"
    gold = "gold"


class TransactionType(str, enum.Enum):
    accrual = "accrual"
    redemption = "redemption"


class Location(str, enum.Enum):
    fuel = "fuel"
    base = "base"


class AccrualType(str, enum.Enum):
    percent = "percent"
    bonus_per_liter = "bonus_per_liter"
    fixed = "fixed"


# --------------------------------------------------------------------------- #
#  Client
# --------------------------------------------------------------------------- #
class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255))
    name: Mapped[Optional[str]] = mapped_column(String(200))
    first_name: Mapped[Optional[str]] = mapped_column(String(100))
    last_name: Mapped[Optional[str]] = mapped_column(String(100))
    patronymic: Mapped[Optional[str]] = mapped_column(String(100))
    photo_data_url: Mapped[Optional[str]] = mapped_column(Text)
    bonus_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    level: Mapped[ClientLevel] = mapped_column(
        Enum(ClientLevel, name="client_level_enum"), default=ClientLevel.bronze
    )
    total_spent: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    birth_date: Mapped[Optional[date]] = mapped_column(Date)
    reg_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="client")
    bonus_expiries: Mapped[list["BonusExpiry"]] = relationship(back_populates="client")
    fuel_sales: Mapped[list["FuelSale"]] = relationship(back_populates="client")


# --------------------------------------------------------------------------- #
#  News
# --------------------------------------------------------------------------- #
class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(String(2000), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


# --------------------------------------------------------------------------- #
#  AppSetting
# --------------------------------------------------------------------------- #
class AppSetting(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    value: Mapped[str] = mapped_column(String(500), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), index=True
    )


# --------------------------------------------------------------------------- #
#  ReceiptCancellation
# --------------------------------------------------------------------------- #
class ReceiptCancellation(Base):
    __tablename__ = "receipt_cancellations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    original_check_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    canceled_check_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    cashier_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("cashiers.id"), index=True)
    client_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("clients.id"), index=True)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


# --------------------------------------------------------------------------- #
#  ActionLog
# --------------------------------------------------------------------------- #
class ActionLog(Base):
    __tablename__ = "action_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_type: Mapped[str] = mapped_column(String(20), index=True)
    actor_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    actor_name: Mapped[str] = mapped_column(String(200), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(50), index=True)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    check_id: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    details: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


# --------------------------------------------------------------------------- #
#  Transaction
# --------------------------------------------------------------------------- #
class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    type: Mapped[TransactionType] = mapped_column(Enum(TransactionType, name="transaction_type_enum"))
    amount_bonus: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    purchase_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    location: Mapped[Location] = mapped_column(Enum(Location, name="location_enum"))
    fuel_liters: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 3))
    cashier_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("cashiers.id"))
    check_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True, index=True)

    client: Mapped["Client"] = relationship(back_populates="transactions")
    cashier: Mapped[Optional["Cashier"]] = relationship()


# --------------------------------------------------------------------------- #
#  AccrualRule
# --------------------------------------------------------------------------- #
class AccrualRule(Base):
    __tablename__ = "accrual_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    location: Mapped[Location] = mapped_column(Enum(Location, name="location_enum2"))
    client_level: Mapped[ClientLevel] = mapped_column(Enum(ClientLevel, name="client_level_enum2"))
    accrual_type: Mapped[AccrualType] = mapped_column(Enum(AccrualType, name="accrual_type_enum"))
    accrual_value: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    min_purchase: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=0)
    active_from: Mapped[date] = mapped_column(Date)
    active_to: Mapped[Optional[date]] = mapped_column(Date)


# --------------------------------------------------------------------------- #
#  Campaign
# --------------------------------------------------------------------------- #
class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    multiplier: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=1)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    days_of_week: Mapped[Optional[str]] = mapped_column(String(20))  # "0,1,2,3,4,5,6" (Mon=0)
    hours_start: Mapped[Optional[int]] = mapped_column(Integer)
    hours_end: Mapped[Optional[int]] = mapped_column(Integer)


# --------------------------------------------------------------------------- #
#  BonusExpiry
# --------------------------------------------------------------------------- #
class BonusExpiry(Base):
    __tablename__ = "bonus_expiry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("clients.id"), nullable=False, index=True)
    bonus_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    expiry_date: Mapped[date] = mapped_column(Date, index=True)
    remaining: Mapped[Decimal] = mapped_column(Numeric(12, 2))

    client: Mapped["Client"] = relationship(back_populates="bonus_expiries")


# --------------------------------------------------------------------------- #
#  FuelSale
# --------------------------------------------------------------------------- #
class FuelSale(Base):
    __tablename__ = "fuel_sales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sale_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    cashier_name: Mapped[Optional[str]] = mapped_column(String(200))
    cashier_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("cashiers.id"), index=True)
    fuel_type: Mapped[Optional[str]] = mapped_column(String(50))
    liters: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    price_per_liter: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    total_rub: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    client_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("clients.id"))
    check_id: Mapped[Optional[str]] = mapped_column(String(100), index=True)

    client: Mapped[Optional["Client"]] = relationship(back_populates="fuel_sales")
    cashier: Mapped[Optional["Cashier"]] = relationship()


# --------------------------------------------------------------------------- #
#  Cashier
# --------------------------------------------------------------------------- #
class Cashier(Base):
    __tablename__ = "cashiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255))
    azs_id: Mapped[Optional[int]] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    shifts: Mapped[list["CashierShift"]] = relationship(back_populates="cashier")


# --------------------------------------------------------------------------- #
#  CashierShift
# --------------------------------------------------------------------------- #
class CashierShift(Base):
    __tablename__ = "cashier_shifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cashier_id: Mapped[int] = mapped_column(Integer, ForeignKey("cashiers.id"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)

    cashier: Mapped["Cashier"] = relationship(back_populates="shifts")


# --------------------------------------------------------------------------- #
#  FuelPrice
# --------------------------------------------------------------------------- #
class FuelPrice(Base):
    __tablename__ = "fuel_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)   # ai92, ai95, ai98, diesel, gas
    name: Mapped[str] = mapped_column(String(100), nullable=False)               # АИ-92, АИ-95 …
    price_per_liter: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

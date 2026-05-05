from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import random
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.db.models import ActionLog, AccrualRule, AccrualType, Cashier, CashierShift, Client, ClientLevel, FuelSale, Location, NewsItem, Transaction, TransactionType
from app.services.cashier_auth import hash_password


USER_TABLES = [
    "action_logs",
    "accrual_rules",
    "app_settings",
    "bonus_expiry",
    "campaigns",
    "cashier_shifts",
    "cashiers",
    "clients",
    "fuel_prices",
    "fuel_sales",
    "news_items",
    "receipt_cancellations",
    "transactions",
]


@dataclass
class BenchResult:
    path: str
    calls: int
    mean_ms: float
    median_ms: float
    p95_ms: float
    min_ms: float
    max_ms: float
    bytes_mean: float


def _chunked(items: list[dict], size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _measure_summary(values: list[float], bytes_values: list[int], path: str) -> BenchResult:
    ordered = sorted(values)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return BenchResult(
        path=path,
        calls=len(values),
        mean_ms=round(statistics.fmean(values), 2),
        median_ms=round(statistics.median(values), 2),
        p95_ms=round(ordered[p95_index], 2),
        min_ms=round(min(values), 2),
        max_ms=round(max(values), 2),
        bytes_mean=round(statistics.fmean(bytes_values), 2),
    )


async def truncate_user_tables(engine) -> None:
    table_list = ", ".join(f'"public"."{name}"' for name in USER_TABLES)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {table_list} RESTART IDENTITY CASCADE"))


async def seed_data(args) -> None:
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    rng = random.Random(args.seed)
    today = datetime.now().replace(microsecond=0)
    period_start = today - timedelta(days=args.days)

    try:
        await truncate_user_tables(engine)

        async with session_factory() as session:
            news_rows = [
                {
                    "title": f"Benchmark news #{index}",
                    "body": f"Synthetic admin news body #{index}",
                    "is_active": index % 5 != 0,
                    "created_at": period_start + timedelta(minutes=index),
                    "published_at": period_start + timedelta(minutes=index),
                }
                for index in range(1, args.news + 1)
            ]
            for chunk in _chunked(news_rows, 1000):
                await session.execute(insert(NewsItem), chunk)

            rule_rows = []
            for index in range(1, args.rules + 1):
                rule_rows.append(
                    {
                        "location": Location.fuel if index % 2 else Location.base,
                        "client_level": ClientLevel.bronze,
                        "accrual_type": [AccrualType.percent, AccrualType.fixed, AccrualType.bonus_per_liter][index % 3],
                        "accrual_value": Decimal("2.5") if index % 3 == 0 else Decimal("5.0"),
                        "min_purchase": Decimal(str((index % 10) * 100)),
                        "active_from": (date.today() - timedelta(days=index % max(args.days, 1))),
                        "active_to": None if index % 4 else (date.today() + timedelta(days=30)),
                    }
                )
            for chunk in _chunked(rule_rows, 1000):
                await session.execute(insert(AccrualRule), chunk)

            cashier_rows = []
            for index in range(1, args.cashiers + 1):
                cashier_rows.append(
                    {
                        "name": f"Cashier {index}",
                        "username": f"bench_cashier_{index}",
                        "password_hash": hash_password("secret123"),
                        "azs_id": (index % 5) + 1,
                        "is_active": True,
                    }
                )
            archived_count = max(1, args.cashiers // 10)
            for index in range(1, archived_count + 1):
                cashier_rows.append(
                    {
                        "name": f"Archived Cashier {index}",
                        "username": None,
                        "password_hash": None,
                        "azs_id": None,
                        "is_active": False,
                    }
                )
            await session.execute(insert(Cashier), cashier_rows)

            client_rows = []
            for index in range(1, args.clients + 1):
                client_rows.append(
                    {
                        "phone": f"+992900{index:06d}",
                        "phone_verified": True,
                        "name": f"Client {index}",
                        "bonus_balance": Decimal(str((index % 40) * 5)),
                        "level": ClientLevel.bronze,
                        "total_spent": Decimal(str((index % 500) * 10)),
                        "reg_date": period_start + timedelta(minutes=index % max(args.days * 24 * 60, 1)),
                    }
                )
            for chunk in _chunked(client_rows, 2000):
                await session.execute(insert(Client), chunk)

            shift_rows = []
            shift_summaries: dict[int, dict[str, float]] = {}
            shift_id = 1
            for cashier_id in range(1, args.cashiers + 1):
                for shift_no in range(args.shifts_per_cashier):
                    start = period_start + timedelta(hours=(cashier_id * 3 + shift_no * 9) % max(args.days * 24, 1))
                    duration_hours = 8 + (shift_no % 3)
                    end = start + timedelta(hours=duration_hours)
                    shift_rows.append(
                        {
                            "id": shift_id,
                            "cashier_id": cashier_id,
                            "started_at": start,
                            "ended_at": end,
                        }
                    )
                    shift_summaries[shift_id] = {
                        "transactions_count": float(60 + (shift_no % 20)),
                        "accrual_transactions_count": float(35 + (shift_no % 10)),
                        "redemption_transactions_count": float(25 + (shift_no % 10)),
                        "accrued_bonus_total": float(800 + (cashier_id % 7) * 20),
                        "redeemed_bonus_total": float(250 + (cashier_id % 5) * 10),
                        "fuel_sales_count": float(40 + (shift_no % 15)),
                        "fuel_sales_total": float(12000 + shift_no * 75),
                        "fuel_liters_total": float(300 + shift_no * 4),
                    }
                    shift_id += 1
            for chunk in _chunked(shift_rows, 1000):
                await session.execute(insert(CashierShift), chunk)

            tx_rows = []
            receipt_logs = []
            for index in range(1, args.transactions + 1):
                tx_type = TransactionType.accrual if index % 3 else TransactionType.redemption
                cashier_id = (index % args.cashiers) + 1
                client_id = (index % args.clients) + 1
                tx_time = period_start + timedelta(seconds=index * 37 % max(args.days * 24 * 3600, 1))
                amount_bonus = Decimal(str((index % 30) + 1))
                purchase_amount = Decimal(str(300 + (index % 400) * 5))
                check_id = f"BENCH-TX-{index:07d}"
                tx_rows.append(
                    {
                        "client_id": client_id,
                        "ts": tx_time,
                        "type": tx_type,
                        "amount_bonus": amount_bonus,
                        "purchase_amount": purchase_amount,
                        "location": Location.base if index % 4 else Location.fuel,
                        "fuel_liters": Decimal(str(round((index % 90) / 3, 3))),
                        "cashier_id": cashier_id,
                        "check_id": check_id,
                    }
                )
                receipt_logs.append(
                    {
                        "actor_type": "cashier",
                        "actor_id": cashier_id,
                        "actor_name": f"Cashier {cashier_id}",
                        "action": "receipt_processed",
                        "entity_type": "receipt",
                        "entity_id": index,
                        "check_id": check_id,
                        "details": json.dumps({"client_id": client_id, "purchase_amount": float(purchase_amount)}),
                        "created_at": tx_time,
                    }
                )
            for chunk in _chunked(tx_rows, 2000):
                await session.execute(insert(Transaction), chunk)

            fuel_types = ["АИ-92", "АИ-95", "АИ-98", "ДТ"]
            sale_rows = []
            for index in range(1, args.fuel_sales + 1):
                cashier_id = (index % args.cashiers) + 1
                client_id = (index % args.clients) + 1
                price = Decimal(str(10 + (index % 8)))
                liters = Decimal(str(round(20 + (index % 70) * 0.5, 3)))
                sale_time = period_start + timedelta(seconds=index * 53 % max(args.days * 24 * 3600, 1))
                sale_rows.append(
                    {
                        "sale_date": sale_time,
                        "cashier_name": f"Cashier {cashier_id}",
                        "cashier_id": cashier_id,
                        "fuel_type": fuel_types[index % len(fuel_types)],
                        "liters": liters,
                        "price_per_liter": price,
                        "total_rub": (liters * price).quantize(Decimal("0.01")),
                        "client_id": client_id,
                        "check_id": f"BENCH-FUEL-{index:07d}",
                    }
                )
            for chunk in _chunked(sale_rows, 2000):
                await session.execute(insert(FuelSale), chunk)

            shift_log_rows = []
            for row in shift_rows:
                summary = shift_summaries[row["id"]]
                shift_log_rows.append(
                    {
                        "actor_type": "cashier",
                        "actor_id": row["cashier_id"],
                        "actor_name": f"Cashier {row['cashier_id']}",
                        "action": "shift_closed",
                        "entity_type": "cashier_shift",
                        "entity_id": row["id"],
                        "check_id": None,
                        "details": json.dumps(summary),
                        "created_at": row["ended_at"],
                    }
                )

            action_log_rows = receipt_logs + shift_log_rows
            extra_logs = max(args.action_logs - len(action_log_rows), 0)
            for index in range(1, extra_logs + 1):
                cashier_id = (index % args.cashiers) + 1
                created_at = period_start + timedelta(seconds=index * 71 % max(args.days * 24 * 3600, 1))
                action_log_rows.append(
                    {
                        "actor_type": "admin" if index % 7 == 0 else "cashier",
                        "actor_id": cashier_id,
                        "actor_name": "admin" if index % 7 == 0 else f"Cashier {cashier_id}",
                        "action": "settings_viewed" if index % 7 == 0 else "receipt_processed",
                        "entity_type": "setting" if index % 7 == 0 else "receipt",
                        "entity_id": index,
                        "check_id": None if index % 7 == 0 else f"BENCH-LOG-{index:07d}",
                        "details": json.dumps({"seq": index, "noise": rng.randint(1, 1000)}),
                        "created_at": created_at,
                    }
                )
            for chunk in _chunked(action_log_rows, 2000):
                await session.execute(insert(ActionLog), chunk)

            await session.commit()
    finally:
        await engine.dispose()


async def benchmark_http(args) -> list[BenchResult]:
    today = date.today().isoformat()
    period_start = (date.today() - timedelta(days=max(args.days - 1, 0))).isoformat()
    endpoints = [
        f"/api/admin/news",
        f"/api/admin/rules",
        f"/api/admin/cashiers",
        f"/api/admin/suspicious-activity",
        f"/api/reports/action-log?date_from={period_start}&date_to={today}",
        f"/api/reports/fuel-sales?date_from={period_start}&date_to={today}",
        f"/api/reports/fuel-by-type?date_from={period_start}&date_to={today}",
        f"/api/reports/cashier-sales?date_from={period_start}&date_to={today}",
        f"/api/reports/closed-shifts?date_from={period_start}&date_to={today}",
    ]

    results: list[BenchResult] = []
    timeout = httpx.Timeout(120.0, connect=10.0)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=timeout) as client:
        login_response = await client.post(
            "/api/admin/login",
            json={
                "username": settings.ADMIN_USERNAME,
                "password": settings.ADMIN_PASSWORD,
            },
        )
        login_response.raise_for_status()
        for path in endpoints:
            timings: list[float] = []
            bytes_values: list[int] = []
            for _ in range(args.repeats):
                started = time.perf_counter()
                response = await client.get(path)
                elapsed_ms = (time.perf_counter() - started) * 1000
                if response.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        f"Request failed for {path} with status {response.status_code}: {response.text[:400]}",
                        request=response.request,
                        response=response,
                    )
                timings.append(elapsed_ms)
                bytes_values.append(len(response.content))
            results.append(_measure_summary(timings, bytes_values, path))
    return results


def print_seed_summary(args) -> None:
    print("Seed complete:")
    print(f"  news={args.news}")
    print(f"  rules={args.rules}")
    print(f"  cashiers={args.cashiers}")
    print(f"  clients={args.clients}")
    print(f"  shifts={args.cashiers * args.shifts_per_cashier}")
    print(f"  transactions={args.transactions}")
    print(f"  fuel_sales={args.fuel_sales}")
    print(f"  action_logs={max(args.action_logs, args.transactions + args.cashiers * args.shifts_per_cashier)}")


def print_bench_summary(results: list[BenchResult]) -> None:
    print("\nBenchmark results:")
    print("path | calls | mean_ms | median_ms | p95_ms | min_ms | max_ms | avg_bytes")
    for item in results:
        print(
            f"{item.path} | {item.calls} | {item.mean_ms:.2f} | {item.median_ms:.2f} | {item.p95_ms:.2f} | {item.min_ms:.2f} | {item.max_ms:.2f} | {item.bytes_mean:.0f}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed benchmark data and measure key admin/report endpoints.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--news", type=int, default=300)
    parser.add_argument("--rules", type=int, default=200)
    parser.add_argument("--cashiers", type=int, default=40)
    parser.add_argument("--clients", type=int, default=5000)
    parser.add_argument("--shifts-per-cashier", type=int, default=8)
    parser.add_argument("--transactions", type=int, default=30000)
    parser.add_argument("--fuel-sales", type=int, default=20000)
    parser.add_argument("--action-logs", type=int, default=40000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--skip-seed", action="store_true")
    return parser


async def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not args.skip_seed:
        await seed_data(args)
        print_seed_summary(args)
    results = await benchmark_http(args)
    print_bench_summary(results)


if __name__ == "__main__":
    asyncio.run(main())
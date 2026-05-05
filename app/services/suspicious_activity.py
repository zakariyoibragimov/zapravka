from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ActionLog
from app.utils.datetime import local_now

SUSPICIOUS_CANCELS_THRESHOLD = 3
SUSPICIOUS_NO_CLIENT_THRESHOLD = 5
SUSPICIOUS_MANUAL_REGISTRATIONS_THRESHOLD = 4


def _day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = now or local_now()
    day_start = datetime.combine(current.date(), time.min)
    return day_start, day_start + timedelta(days=1)


def _parse_details(details: str | None) -> dict[str, Any]:
    if not details:
        return {}
    try:
        parsed = json.loads(details)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _severity(level_value: int, warning_threshold: int, danger_threshold: int) -> str:
    if level_value >= danger_threshold:
        return "danger"
    if level_value >= warning_threshold:
        return "warning"
    return "info"


async def _count_logs(
    db: AsyncSession,
    *,
    action: str,
    start: datetime,
    end: datetime,
    extra_clause=None,
) -> int:
    stmt = select(func.count(ActionLog.id)).where(
        ActionLog.action == action,
        ActionLog.created_at >= start,
        ActionLog.created_at < end,
    )
    if extra_clause is not None:
        stmt = stmt.where(extra_clause)
    return int((await db.execute(stmt)).scalar_one() or 0)


async def _fetch_examples(
    db: AsyncSession,
    *,
    action: str,
    start: datetime,
    end: datetime,
    limit: int,
    extra_clause=None,
) -> list[tuple[str, str | None, datetime | None, str | None]]:
    stmt = (
        select(ActionLog.actor_name, ActionLog.check_id, ActionLog.created_at, ActionLog.details)
        .where(
            ActionLog.action == action,
            ActionLog.created_at >= start,
            ActionLog.created_at < end,
        )
        .order_by(ActionLog.created_at.desc(), ActionLog.id.desc())
        .limit(limit)
    )
    if extra_clause is not None:
        stmt = stmt.where(extra_clause)
    return [
        (row.actor_name, row.check_id, row.created_at, row.details)
        for row in (await db.execute(stmt)).all()
    ]


async def build_suspicious_activity_alerts(db: AsyncSession) -> list[dict[str, Any]]:
    start, end = _day_bounds()
    alerts: list[dict[str, Any]] = []

    cancel_count = await _count_logs(db, action="receipt_cancelled", start=start, end=end)
    if cancel_count >= SUSPICIOUS_CANCELS_THRESHOLD:
        cancel_logs = await _fetch_examples(
            db,
            action="receipt_cancelled",
            start=start,
            end=end,
            limit=5,
        )
        alerts.append(
            {
                "code": "many_cancellations",
                "severity": _severity(cancel_count, SUSPICIOUS_CANCELS_THRESHOLD, SUSPICIOUS_CANCELS_THRESHOLD + 2),
                "title": "Слишком много отмен за день",
                "description": f"За сегодня проведено {cancel_count} отмен чеков.",
                "metric": cancel_count,
                "threshold": SUSPICIOUS_CANCELS_THRESHOLD,
                "examples": [
                    {
                        "actor_name": actor_name,
                        "check_id": check_id,
                        "created_at": created_at.isoformat() if created_at else None,
                    }
                    for actor_name, check_id, created_at, _details in cancel_logs
                ],
            }
        )

    no_client_clause = or_(
        ActionLog.details.is_(None),
        ~ActionLog.details.like('%"has_client":true%'),
    )
    no_client_count = await _count_logs(
        db,
        action="receipt_processed",
        start=start,
        end=end,
        extra_clause=no_client_clause,
    )
    if no_client_count >= SUSPICIOUS_NO_CLIENT_THRESHOLD:
        receipt_logs = await _fetch_examples(
            db,
            action="receipt_processed",
            start=start,
            end=end,
            limit=5,
            extra_clause=no_client_clause,
        )
        no_client_receipts = []
        for actor_name, check_id, created_at, details_raw in receipt_logs:
            details = _parse_details(details_raw)
            no_client_receipts.append(
                {
                    "actor_name": actor_name,
                    "check_id": check_id,
                    "purchase_amount": details.get("purchase_amount"),
                    "created_at": created_at.isoformat() if created_at else None,
                }
            )
        alerts.append(
            {
                "code": "many_receipts_without_client",
                "severity": _severity(no_client_count, SUSPICIOUS_NO_CLIENT_THRESHOLD, SUSPICIOUS_NO_CLIENT_THRESHOLD + 3),
                "title": "Слишком много чеков без клиента",
                "description": f"За сегодня проведено {no_client_count} чеков без привязки клиента.",
                "metric": no_client_count,
                "threshold": SUSPICIOUS_NO_CLIENT_THRESHOLD,
                "examples": no_client_receipts,
            }
        )

    registration_count = await _count_logs(db, action="client_registered_cashier", start=start, end=end)
    if registration_count >= SUSPICIOUS_MANUAL_REGISTRATIONS_THRESHOLD:
        registration_logs = await _fetch_examples(
            db,
            action="client_registered_cashier",
            start=start,
            end=end,
            limit=5,
        )
        alerts.append(
            {
                "code": "many_manual_registrations",
                "severity": _severity(
                    registration_count,
                    SUSPICIOUS_MANUAL_REGISTRATIONS_THRESHOLD,
                    SUSPICIOUS_MANUAL_REGISTRATIONS_THRESHOLD + 3,
                ),
                "title": "Слишком частые ручные регистрации клиентов",
                "description": f"За сегодня с кассы вручную зарегистрировано {registration_count} клиентов.",
                "metric": registration_count,
                "threshold": SUSPICIOUS_MANUAL_REGISTRATIONS_THRESHOLD,
                "examples": [
                    {
                        "actor_name": actor_name,
                        "created_at": created_at.isoformat() if created_at else None,
                    }
                    for actor_name, _check_id, created_at, _details in registration_logs
                ],
            }
        )

    alerts.sort(key=lambda item: ({"danger": 0, "warning": 1, "info": 2}.get(item["severity"], 3), -int(item.get("metric") or 0)))
    return alerts
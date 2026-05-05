from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ActionLog
from app.utils.datetime import local_now


def _serialize_details(details: Optional[dict[str, Any] | list[Any] | str]) -> Optional[str]:
    if details in (None, "", {}, []):
        return None
    if isinstance(details, str):
        return details
    return json.dumps(details, ensure_ascii=False, default=str, separators=(",", ":"))


async def log_action(
    session: AsyncSession,
    *,
    actor_type: str,
    actor_name: str,
    action: str,
    actor_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    check_id: Optional[str] = None,
    details: Optional[dict[str, Any] | list[Any] | str] = None,
) -> ActionLog:
    entry = ActionLog(
        actor_type=actor_type,
        actor_id=actor_id,
        actor_name=actor_name,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        check_id=check_id,
        details=_serialize_details(details),
        created_at=local_now(),
    )
    session.add(entry)
    await session.flush()
    return entry
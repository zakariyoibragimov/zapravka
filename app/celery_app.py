from __future__ import annotations

try:
    from celery import Celery
    from celery.schedules import crontab
    from app.config import settings

    celery_app = Celery(
        "azs_bonus",
        broker=settings.REDIS_URL,
        backend=settings.REDIS_URL,
    )

    celery_app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone=settings.APP_TIMEZONE,
        enable_utc=True,
        beat_schedule={
            "expire-bonuses-daily": {
                "task": "app.tasks.expire_bonuses_task",
                "schedule": crontab(hour=2, minute=0),
            },
        },
    )

    celery_app.autodiscover_tasks(["app.tasks"])

except ImportError:
    celery_app = None  # type: ignore[assignment]

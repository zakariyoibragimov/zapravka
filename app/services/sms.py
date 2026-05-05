from __future__ import annotations

import logging
import random
import string
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# In-memory хранилище для локального режима и тестов.
_memory_store: dict[str, tuple[str, float]] = {}

try:
    import redis.asyncio as aioredis
    _redis_available = True
except ImportError:
    _redis_available = False

_redis_client = None


class SMSRateLimitError(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = max(1, retry_after)
        super().__init__(f"Повторная отправка доступна через {self.retry_after} сек.")


class SMSDeliveryError(Exception):
    pass


def _get_redis():
    global _redis_client
    if _redis_available and _redis_client is None:
        _redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


def _code_key(phone: str) -> str:
    return f"sms_code:{phone}"


def _cooldown_key(phone: str) -> str:
    return f"sms_cooldown:{phone}"


def _attempts_key(phone: str) -> str:
    return f"sms_attempts:{phone}"


@dataclass(frozen=True)
class VerifyCodeResult:
    ok: bool
    remaining_attempts: int = 0
    locked: bool = False


def _memory_get(key: str) -> str | None:
    item = _memory_store.get(key)
    if item is None:
        return None
    value, expires_at = item
    if expires_at <= time.time():
        _memory_store.pop(key, None)
        return None
    return value


def _memory_ttl(key: str) -> int:
    item = _memory_store.get(key)
    if item is None:
        return -2
    _, expires_at = item
    remaining = int(expires_at - time.time())
    if remaining <= 0:
        _memory_store.pop(key, None)
        return -2
    return remaining


def _memory_setex(key: str, ttl_seconds: int, value: str) -> None:
    _memory_store[key] = (value, time.time() + ttl_seconds)


def _memory_delete(*keys: str) -> None:
    for key in keys:
        _memory_store.pop(key, None)


async def _store_get(key: str) -> str | None:
    redis_client = _get_redis()
    if redis_client is not None:
        try:
            return await redis_client.get(key)
        except Exception:
            pass
    return _memory_get(key)


async def _store_ttl(key: str) -> int:
    redis_client = _get_redis()
    if redis_client is not None:
        try:
            return await redis_client.ttl(key)
        except Exception:
            pass
    return _memory_ttl(key)


async def _store_setex(key: str, ttl_seconds: int, value: str) -> None:
    redis_client = _get_redis()
    if redis_client is not None:
        try:
            await redis_client.setex(key, ttl_seconds, value)
            return
        except Exception:
            pass
    _memory_setex(key, ttl_seconds, value)


async def _store_delete(*keys: str) -> None:
    redis_client = _get_redis()
    if redis_client is not None:
        try:
            await redis_client.delete(*keys)
            return
        except Exception:
            pass
    _memory_delete(*keys)


def _generate_code() -> str:
    code_length = min(8, max(4, settings.SMS_CODE_LENGTH))
    return "".join(random.choices(string.digits, k=code_length))


def _is_international_phone(phone: str) -> bool:
    normalized = (phone or "").strip()
    return normalized.startswith("+") and not normalized.startswith("+7")


def _compose_message(phone: str, code: str) -> str:
    if _is_international_phone(phone):
        return f"Code {code}"
    return f"Код подтверждения AZS Bonus: {code}. Никому его не сообщайте."


def _resolve_sender(phone: str) -> str | None:
    if _is_international_phone(phone):
        sender = (settings.SMS_PROVIDER_INTERNATIONAL_SENDER or "").strip()
        return sender or None
    sender = (settings.SMS_PROVIDER_SENDER or "").strip()
    return sender or None


def _get_provider_url(provider: str) -> str:
    configured_url = (settings.SMS_PROVIDER_URL or "").strip()
    provider_specific_urls = {
        "sms_ru": (settings.SMS_PROVIDER_SMS_RU_URL or "").strip(),
        "bytehand": (settings.SMS_PROVIDER_BYTEHAND_URL or "").strip(),
        "smsc": (settings.SMS_PROVIDER_SMSC_URL or "").strip(),
    }
    specific_url = provider_specific_urls.get(provider, "")
    if specific_url:
        return specific_url
    if provider == "bytehand":
        if not configured_url or configured_url == "https://sms.ru/sms/send":
            return "https://api.bytehand.com/v2/sms/messages"
        return configured_url
    if provider == "smsc":
        if not configured_url or configured_url == "https://sms.ru/sms/send":
            return "https://smsc.ru/sys/send.php"
        return configured_url
    return configured_url or "https://sms.ru/sms/send"


def _get_primary_provider() -> str:
    return (settings.SMS_PROVIDER or "stub").strip().lower()


def _get_fallback_provider() -> str:
    return (settings.SMS_PROVIDER_FALLBACK or "").strip().lower()


async def _send_via_sms_ru(phone: str, message: str) -> None:
    if not settings.SMS_PROVIDER_API_KEY.strip():
        raise SMSDeliveryError("Не настроен SMS_PROVIDER_API_KEY")

    payload: dict[str, Any] = {
        "api_id": settings.SMS_PROVIDER_API_KEY,
        "to": phone,
        "msg": message,
        "json": 1,
    }
    if settings.SMS_PROVIDER_SENDER.strip():
        payload["from"] = settings.SMS_PROVIDER_SENDER.strip()

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(_get_provider_url("sms_ru"), data=payload)
            response.raise_for_status()
            provider_payload = response.json()
    except httpx.HTTPError as exc:
        raise SMSDeliveryError("SMS-провайдер недоступен") from exc
    except ValueError as exc:
        raise SMSDeliveryError("SMS-провайдер вернул некорректный ответ") from exc

    if provider_payload.get("status") != "OK":
        raise SMSDeliveryError(provider_payload.get("status_text") or "SMS не отправлена")

    sms_result = provider_payload.get("sms") or {}
    current_phone_result = sms_result.get(phone)
    if current_phone_result is None and sms_result:
        current_phone_result = next(iter(sms_result.values()))
    if isinstance(current_phone_result, dict) and current_phone_result.get("status") != "OK":
        raise SMSDeliveryError(current_phone_result.get("status_text") or "SMS не отправлена")


async def _send_via_bytehand(phone: str, message: str) -> None:
    if not settings.SMS_PROVIDER_API_KEY.strip():
        raise SMSDeliveryError("Не настроен SMS_PROVIDER_API_KEY")
    sender = _resolve_sender(phone)
    if sender is None and not _is_international_phone(phone):
        raise SMSDeliveryError("Не настроен SMS_PROVIDER_SENDER")

    payload = {
        "receiver": phone,
        "text": message,
    }
    if sender:
        payload["sender"] = sender
    headers = {
        "X-Service-Key": settings.SMS_PROVIDER_API_KEY.strip(),
        "Content-Type": "application/json;charset=UTF-8",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(_get_provider_url("bytehand"), json=payload, headers=headers)
            response.raise_for_status()
            provider_payload = response.json()
    except httpx.HTTPError as exc:
        raise SMSDeliveryError("BYTEHAND недоступен") from exc
    except ValueError as exc:
        raise SMSDeliveryError("BYTEHAND вернул некорректный ответ") from exc

    if provider_payload.get("result") != "created":
        raise SMSDeliveryError(provider_payload.get("description") or "BYTEHAND не принял SMS")


async def _send_via_smsc(phone: str, message: str) -> None:
    if not settings.SMS_PROVIDER_API_KEY.strip():
        raise SMSDeliveryError("Не настроен SMS_PROVIDER_API_KEY")

    payload: dict[str, Any] = {
        "apikey": settings.SMS_PROVIDER_API_KEY.strip(),
        "phones": phone,
        "mes": message,
        "fmt": 3,
        "charset": "utf-8",
    }
    sender = _resolve_sender(phone)
    if sender:
        payload["sender"] = sender

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(_get_provider_url("smsc"), data=payload)
            response.raise_for_status()
            provider_payload = response.json()
    except httpx.HTTPError as exc:
        raise SMSDeliveryError("SMSC недоступен") from exc
    except ValueError as exc:
        raise SMSDeliveryError("SMSC вернул некорректный ответ") from exc

    if provider_payload.get("error"):
        raise SMSDeliveryError(provider_payload.get("error") or "SMSC не отправил SMS")
    if not provider_payload.get("id") and not provider_payload.get("cnt"):
        raise SMSDeliveryError("SMSC не подтвердил отправку SMS")


async def _send_with_provider(provider: str, phone: str, message: str) -> None:
    if provider == "bytehand":
        await _send_via_bytehand(phone, message)
        return
    if provider == "sms_ru":
        await _send_via_sms_ru(phone, message)
        return
    if provider == "smsc":
        await _send_via_smsc(phone, message)
        return
    raise SMSDeliveryError(f"Неподдерживаемый SMS_PROVIDER: {provider}")


async def _dispatch_code(phone: str, code: str, *, provider_override: str | None = None) -> None:
    provider = (provider_override or _get_primary_provider() or "stub").strip().lower()
    if provider == "stub":
        if settings.APP_ENV.strip().lower() == "production":
            raise SMSDeliveryError("Stub SMS отключен в production")
        logger.info("SMS stub for %s: %s", phone, code)
        return

    message = _compose_message(phone, code)
    try:
        await _send_with_provider(provider, phone, message)
        return
    except SMSDeliveryError:
        fallback_provider = _get_fallback_provider()
        if not fallback_provider or fallback_provider == provider:
            raise
        logger.warning("SMS provider %s failed for %s, trying fallback %s", provider, phone, fallback_provider)
        await _send_with_provider(fallback_provider, phone, message)


async def issue_one_time_code(phone: str) -> str:
    retry_after = await _store_ttl(_cooldown_key(phone))
    if retry_after > 0:
        raise SMSRateLimitError(retry_after)

    code = _generate_code()
    code_key = _code_key(phone)
    cooldown_key = _cooldown_key(phone)
    attempts_key = _attempts_key(phone)
    await _store_setex(code_key, settings.SMS_CODE_TTL_SECONDS, code)
    await _store_setex(cooldown_key, settings.SMS_RESEND_INTERVAL_SECONDS, "1")
    await _store_setex(attempts_key, settings.SMS_CODE_TTL_SECONDS, "0")

    return code


async def force_issue_one_time_code(phone: str) -> str:
    code = _generate_code()
    code_key = _code_key(phone)
    cooldown_key = _cooldown_key(phone)
    attempts_key = _attempts_key(phone)
    await _store_setex(code_key, settings.SMS_CODE_TTL_SECONDS, code)
    await _store_setex(cooldown_key, settings.SMS_RESEND_INTERVAL_SECONDS, "1")
    await _store_setex(attempts_key, settings.SMS_CODE_TTL_SECONDS, "0")
    return code


async def send_code(phone: str, *, provider_override: str | None = None) -> str:
    code_key = _code_key(phone)
    cooldown_key = _cooldown_key(phone)
    attempts_key = _attempts_key(phone)
    code = await issue_one_time_code(phone)

    try:
        await _dispatch_code(phone, code, provider_override=provider_override)
    except Exception:
        await _store_delete(code_key, cooldown_key, attempts_key)
        raise

    return code


async def verify_code(phone: str, code: str, *, max_attempts: int | None = None) -> VerifyCodeResult:
    code_key = _code_key(phone)
    cooldown_key = _cooldown_key(phone)
    attempts_key = _attempts_key(phone)
    stored = await _store_get(code_key)
    if not stored:
        return VerifyCodeResult(ok=False, remaining_attempts=0, locked=False)
    if stored == code:
        await _store_delete(code_key, cooldown_key, attempts_key)
        return VerifyCodeResult(ok=True, remaining_attempts=max(0, int(max_attempts or 0)))

    if max_attempts is None or max_attempts <= 0:
        return VerifyCodeResult(ok=False, remaining_attempts=0, locked=False)

    current_attempts = int((await _store_get(attempts_key)) or "0") + 1
    code_ttl = await _store_ttl(code_key)
    if code_ttl > 0:
        await _store_setex(attempts_key, code_ttl, str(current_attempts))

    remaining_attempts = max(0, max_attempts - current_attempts)
    if remaining_attempts == 0:
        await _store_delete(code_key, cooldown_key, attempts_key)
        return VerifyCodeResult(ok=False, remaining_attempts=0, locked=True)
    return VerifyCodeResult(ok=False, remaining_attempts=remaining_attempts, locked=False)


async def reset_sms_state() -> None:
    _memory_store.clear()
    redis_client = _get_redis()
    if redis_client is not None:
        try:
            keys = await redis_client.keys("sms_*")
            if keys:
                await redis_client.delete(*keys)
        except Exception:
            pass

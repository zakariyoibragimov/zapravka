from __future__ import annotations

import pytest

from app.config import settings
from app.services import sms as sms_service


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    calls: list[tuple[str, dict]] = []

    def __init__(self, *, timeout: int):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, **kwargs):
        _FakeAsyncClient.calls.append((url, kwargs))
        if "data" in kwargs:
            data = kwargs["data"]
            if "to" in data:
                return _FakeResponse(
                    {
                        "status": "OK",
                        "sms": {
                            data["to"]: {
                                "status": "OK",
                                "status_code": 100,
                            }
                        },
                    }
                )
            return _FakeResponse(
                {
                    "id": "170000001",
                    "cnt": 1,
                }
            )
        return _FakeResponse(
            {
                "result": "created",
                "id": "164030947974825423",
                "count": 1,
            }
        )


@pytest.mark.asyncio
async def test_send_code_uses_sms_ru_provider(monkeypatch):
    _FakeAsyncClient.calls.clear()
    monkeypatch.setattr(settings, "SMS_PROVIDER", "sms_ru")
    monkeypatch.setattr(settings, "SMS_PROVIDER_API_KEY", "real-api-key")
    monkeypatch.setattr(settings, "SMS_PROVIDER_SENDER", "AZSBonus")
    monkeypatch.setattr(settings, "SMS_PROVIDER_SMS_RU_URL", "https://sms.example.test/send")
    monkeypatch.setattr(sms_service.httpx, "AsyncClient", _FakeAsyncClient)

    code = await sms_service.send_code("+79991234567")

    assert len(code) == settings.SMS_CODE_LENGTH
    assert _FakeAsyncClient.calls
    url, request_kwargs = _FakeAsyncClient.calls[0]
    data = request_kwargs["data"]
    assert url == "https://sms.example.test/send"
    assert data["api_id"] == "real-api-key"
    assert data["from"] == "AZSBonus"
    assert data["to"] == "+79991234567"
    assert code in data["msg"]


@pytest.mark.asyncio
async def test_send_code_uses_bytehand_provider(monkeypatch):
    _FakeAsyncClient.calls.clear()
    monkeypatch.setattr(settings, "SMS_PROVIDER", "bytehand")
    monkeypatch.setattr(settings, "SMS_PROVIDER_API_KEY", "bytehand-key")
    monkeypatch.setattr(settings, "SMS_PROVIDER_SENDER", "SMS-INFO")
    monkeypatch.setattr(settings, "SMS_PROVIDER_INTERNATIONAL_SENDER", "")
    monkeypatch.setattr(settings, "SMS_PROVIDER_BYTEHAND_URL", "https://api.bytehand.test/v2/sms/messages")
    monkeypatch.setattr(sms_service.httpx, "AsyncClient", _FakeAsyncClient)

    code = await sms_service.send_code("+79991234567")

    assert len(code) == settings.SMS_CODE_LENGTH
    assert _FakeAsyncClient.calls
    url, request_kwargs = _FakeAsyncClient.calls[0]
    assert url == "https://api.bytehand.test/v2/sms/messages"
    assert request_kwargs["headers"]["X-Service-Key"] == "bytehand-key"
    assert request_kwargs["headers"]["Content-Type"] == "application/json;charset=UTF-8"
    assert request_kwargs["json"]["sender"] == "SMS-INFO"
    assert request_kwargs["json"]["receiver"] == "+79991234567"
    assert code in request_kwargs["json"]["text"]


@pytest.mark.asyncio
async def test_send_code_uses_bytehand_without_sender_for_international(monkeypatch):
    _FakeAsyncClient.calls.clear()
    monkeypatch.setattr(settings, "SMS_PROVIDER", "bytehand")
    monkeypatch.setattr(settings, "SMS_PROVIDER_API_KEY", "bytehand-key")
    monkeypatch.setattr(settings, "SMS_PROVIDER_SENDER", "SMS-INFO")
    monkeypatch.setattr(settings, "SMS_PROVIDER_INTERNATIONAL_SENDER", "")
    monkeypatch.setattr(settings, "SMS_PROVIDER_BYTEHAND_URL", "https://api.bytehand.test/v2/sms/messages")
    monkeypatch.setattr(sms_service.httpx, "AsyncClient", _FakeAsyncClient)

    code = await sms_service.send_code("+992927882299")

    assert _FakeAsyncClient.calls
    url, request_kwargs = _FakeAsyncClient.calls[0]
    assert url == "https://api.bytehand.test/v2/sms/messages"
    assert request_kwargs["json"]["receiver"] == "+992927882299"
    assert "sender" not in request_kwargs["json"]
    assert request_kwargs["json"]["text"] == f"Code {code}"


@pytest.mark.asyncio
async def test_send_code_uses_smsc_provider(monkeypatch):
    _FakeAsyncClient.calls.clear()
    monkeypatch.setattr(settings, "SMS_PROVIDER", "smsc")
    monkeypatch.setattr(settings, "SMS_PROVIDER_API_KEY", "smsc-api-key")
    monkeypatch.setattr(settings, "SMS_PROVIDER_SENDER", "AZSBonus")
    monkeypatch.setattr(settings, "SMS_PROVIDER_INTERNATIONAL_SENDER", "")
    monkeypatch.setattr(settings, "SMS_PROVIDER_SMSC_URL", "https://smsc.example.test/sys/send.php")
    monkeypatch.setattr(sms_service.httpx, "AsyncClient", _FakeAsyncClient)

    code = await sms_service.send_code("+992927882299")

    assert _FakeAsyncClient.calls
    url, request_kwargs = _FakeAsyncClient.calls[0]
    assert url == "https://smsc.example.test/sys/send.php"
    data = request_kwargs["data"]
    assert data["apikey"] == "smsc-api-key"
    assert data["phones"] == "+992927882299"
    assert data["mes"] == f"Code {code}"
    assert data["fmt"] == 3
    assert data["charset"] == "utf-8"
    assert "sender" not in data


@pytest.mark.asyncio
async def test_send_code_uses_fallback_provider(monkeypatch):
    class _FailingBytehandClient(_FakeAsyncClient):
        first_call_failed = False

        async def post(self, url: str, **kwargs):
            if not _FailingBytehandClient.first_call_failed:
                _FailingBytehandClient.first_call_failed = True
                _FakeAsyncClient.calls.append((url, kwargs))
                raise sms_service.httpx.ConnectError("boom")
            return await super().post(url, **kwargs)

    _FakeAsyncClient.calls.clear()
    _FailingBytehandClient.first_call_failed = False
    monkeypatch.setattr(settings, "SMS_PROVIDER", "bytehand")
    monkeypatch.setattr(settings, "SMS_PROVIDER_FALLBACK", "smsc")
    monkeypatch.setattr(settings, "SMS_PROVIDER_API_KEY", "shared-api-key")
    monkeypatch.setattr(settings, "SMS_PROVIDER_SENDER", "SMS-INFO")
    monkeypatch.setattr(settings, "SMS_PROVIDER_INTERNATIONAL_SENDER", "")
    monkeypatch.setattr(settings, "SMS_PROVIDER_BYTEHAND_URL", "https://api.bytehand.test/v2/sms/messages")
    monkeypatch.setattr(settings, "SMS_PROVIDER_SMSC_URL", "https://smsc.example.test/sys/send.php")
    monkeypatch.setattr(sms_service.httpx, "AsyncClient", _FailingBytehandClient)

    code = await sms_service.send_code("+992927882299")

    assert len(_FakeAsyncClient.calls) == 2
    first_url, first_kwargs = _FakeAsyncClient.calls[0]
    second_url, second_kwargs = _FakeAsyncClient.calls[1]
    assert first_url == "https://api.bytehand.test/v2/sms/messages"
    assert first_kwargs["json"]["text"] == f"Code {code}"
    assert second_url == "https://smsc.example.test/sys/send.php"
    assert second_kwargs["data"]["mes"] == f"Code {code}"


@pytest.mark.asyncio
async def test_stub_sms_is_blocked_in_production(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "SMS_PROVIDER", "stub")

    with pytest.raises(sms_service.SMSDeliveryError, match="Stub SMS отключен в production"):
        await sms_service.send_code("+79991234567")


@pytest.mark.asyncio
async def test_send_code_can_force_stub_mode(monkeypatch):
    async def fail_dispatch(*args, **kwargs):
        raise AssertionError("real provider should not be used")

    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "SMS_PROVIDER", "bytehand")
    monkeypatch.setattr(sms_service, "_send_with_provider", fail_dispatch)

    code = await sms_service.send_code("+79991230001", provider_override="stub")

    assert len(code) == settings.SMS_CODE_LENGTH


@pytest.mark.asyncio
async def test_verify_code_locks_after_two_failed_attempts(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "SMS_PROVIDER", "stub")

    code = await sms_service.send_code("+79991230002")

    first = await sms_service.verify_code("+79991230002", "0000", max_attempts=2)
    second = await sms_service.verify_code("+79991230002", "1111", max_attempts=2)
    third = await sms_service.verify_code("+79991230002", code, max_attempts=2)

    assert first.ok is False
    assert first.remaining_attempts == 1
    assert first.locked is False
    assert second.ok is False
    assert second.remaining_attempts == 0
    assert second.locked is True
    assert third.ok is False

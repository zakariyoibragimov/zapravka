from __future__ import annotations

from httpx import AsyncClient

from app.config import settings


class TestPWA:
    async def test_manifest_available(self, client: AsyncClient):
        resp = await client.get('/manifest.webmanifest')
        assert resp.status_code == 200
        data = resp.json()
        assert data['id'] == '/mobile'
        assert data['start_url'] == '/mobile'
        assert data['display'] == 'standalone'
        assert data['icons']

    async def test_service_worker_available(self, client: AsyncClient):
        resp = await client.get('/sw.js')
        assert resp.status_code == 200
        assert 'CACHE_NAME' in resp.text
        assert 'API_CACHE_NAME' in resp.text
        assert '/api/mobile/' in resp.text

    async def test_offline_helper_available(self, client: AsyncClient):
        resp = await client.get('/static/offline.js')
        assert resp.status_code == 200
        assert 'QUEUE_STORAGE_KEY' in resp.text
        assert 'offlineQueue' in resp.text

    async def test_mobile_page_links_manifest(self, client: AsyncClient):
        resp = await client.get('/mobile')
        assert resp.status_code == 200
        assert '/manifest.webmanifest' in resp.text
        assert '/sw.js' in resp.text
        assert '/static/offline.js' in resp.text

    async def test_privacy_page_available(self, client: AsyncClient):
        resp = await client.get('/privacy')
        assert resp.status_code == 200
        assert 'политик' in resp.text.lower()
        assert 'remix_993@mail.ru' in resp.text
        assert 'замените этот блок' not in resp.text.lower()

    async def test_assetlinks_returns_empty_list_without_fingerprints(self, client: AsyncClient, monkeypatch):
        monkeypatch.setattr(settings, 'ANDROID_SHA256_CERT_FINGERPRINTS', '')
        resp = await client.get('/.well-known/assetlinks.json')
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_assetlinks_returns_android_target_when_configured(self, client: AsyncClient, monkeypatch):
        monkeypatch.setattr(settings, 'ANDROID_APP_PACKAGE', 'com.azsbonus.app')
        monkeypatch.setattr(settings, 'ANDROID_SHA256_CERT_FINGERPRINTS', 'AA:BB:CC,DD:EE:FF')
        resp = await client.get('/.well-known/assetlinks.json')
        assert resp.status_code == 200
        payload = resp.json()
        assert payload[0]['target']['package_name'] == 'com.azsbonus.app'
        assert payload[0]['target']['sha256_cert_fingerprints'] == ['AA:BB:CC', 'DD:EE:FF']
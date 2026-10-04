import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from .cloud.config import CloudConfig
from .cloud.repository import CloudAuthError, CloudRepository
from .modules.cloud_account import repository as client_repository
from .modules.cloud_account import router, service


@pytest.fixture
def stores(tmp_path, monkeypatch):
    cloud = CloudRepository(CloudConfig(
        database_path=tmp_path / 'cloud.sqlite3', public_url='http://127.0.0.1:8010',
        secret='test-secret', allowed_origins=(), dev_mode=True,
    ))
    monkeypatch.setattr(client_repository, 'CLIENT_PATH', tmp_path / 'client.sqlite3')
    monkeypatch.setattr(client_repository, 'KEY_PATH', tmp_path / 'client.key')
    monkeypatch.setattr(client_repository, '_initialized_path', None)
    return cloud


@pytest.mark.parametrize('expired', [True, False])
def test_concurrent_requests_rotate_once_without_revoking_session(stores, monkeypatch, expired):
    cloud = stores
    payload = cloud.register('reader@example.com', 'secure test password', 'Reader', 'device', 'Device')
    client_repository.save_account('local-reader', payload)
    if expired:
        with client_repository._connect() as connection:
            connection.execute('UPDATE cloud_accounts SET access_expires_at=?', (time.time() - 1,))

    async def scenario():
        refresh_calls = 0
        stale_requests = 0
        all_stale = asyncio.Event()

        async def request(method, path, **kwargs):
            nonlocal refresh_calls, stale_requests
            if path == '/v1/auth/refresh':
                refresh_calls += 1
                await asyncio.sleep(0.03)
                try:
                    result = cloud.refresh(kwargs['json']['refresh_token'])
                    return httpx.Response(200, json=result)
                except CloudAuthError as error:
                    return httpx.Response(401, json={'detail': str(error)})
            token = kwargs['headers']['Authorization'].removeprefix('Bearer ')
            if not expired and token == payload['access_token']:
                stale_requests += 1
                if stale_requests == 8:
                    all_stale.set()
                await all_stale.wait()
                return httpx.Response(401, json={'detail': 'expired access token'})
            try:
                user, _ = cloud.authenticate(token)
                return httpx.Response(200, json={'id': user['id']})
            except CloudAuthError as error:
                return httpx.Response(401, json={'detail': str(error)})

        monkeypatch.setattr(service, '_plain_request', request)
        results = await asyncio.wait_for(asyncio.gather(*[
            service._authorized_request('local-reader', 'GET', '/v1/auth/me') for _ in range(8)
        ]), timeout=5)
        assert refresh_calls == 1
        assert all(result.json()['id'] == payload['user']['id'] for result in results)
        credentials = client_repository.account_credentials('local-reader')
        assert cloud.authenticate(credentials['access_token'])[0]['id'] == payload['user']['id']
        # The new refresh token must remain usable, not just its access token.
        assert cloud.refresh(credentials['refresh_token'])['user']['id'] == payload['user']['id']

    asyncio.run(scenario())


def test_revoked_session_requires_login_and_admin_route_preserves_401(stores, monkeypatch):
    payload = stores.register('reader@example.com', 'secure test password', 'Reader', 'device', 'Device')
    client_repository.save_account('local-reader', payload)
    stores.logout(payload['access_token'])
    with client_repository._connect() as connection:
        connection.execute('UPDATE cloud_accounts SET access_expires_at=?', (time.time() - 1,))

    async def request(method, path, **kwargs):
        try:
            return httpx.Response(200, json=stores.refresh(kwargs['json']['refresh_token']))
        except CloudAuthError as error:
            return httpx.Response(401, json={'detail': str(error)})

    monkeypatch.setattr(service, '_plain_request', request)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(router.admin_users(query='', limit=50, offset=0, context=SimpleNamespace(user_id='local-reader')))
    assert caught.value.status_code == 401
    # Keep the account binding so logging back in can restore the same data space.
    assert client_repository.account_status('local-reader')['cloud_user_id'] == payload['user']['id']

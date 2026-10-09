import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from .app import app
from .cloud.config import CloudConfig
from .cloud.repository import CloudRepository
from .modules.cloud_account import repository as client_store, service
from .modules.identity import repository as identity_store, router as identity_router


@pytest.fixture
def cloud_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr(identity_store, 'IDENTITY_PATH', tmp_path / 'identity.sqlite3')
    monkeypatch.setattr(identity_store, 'PUBLIC_MODE', True)
    monkeypatch.setattr(identity_router, 'AVATAR_DIR', tmp_path / 'users')
    monkeypatch.setattr(service, 'PUBLIC_MODE', True)
    monkeypatch.setattr(client_store, 'CLIENT_PATH', tmp_path / 'client.sqlite3')
    monkeypatch.setattr(client_store, 'KEY_PATH', tmp_path / 'client.key')
    monkeypatch.setattr(client_store, '_initialized_path', None)
    cloud = CloudRepository(CloudConfig(
        database_path=tmp_path / 'cloud.sqlite3', public_url='http://127.0.0.1:8010',
        secret='test-secret', allowed_origins=(), dev_mode=True,
    ))
    users = {}
    for name in ['a', 'b']:
        users[name] = cloud.register(name + '@example.com', 'secure test password', name.upper(), name, name)

    async def request(method, path, **kwargs):
        value = kwargs['json']
        if path == '/v1/auth/login':
            return httpx.Response(200, json=cloud.login(value['email'], value['password'], value['device_id'], value['device_name']))
        return httpx.Response(200, json=users['a'])

    monkeypatch.setattr(service, '_plain_request', request)
    return users


def login(client, name):
    result = client.post('/api/cloud/login', json={'email': name + '@example.com', 'password': 'secure test password'})
    assert result.status_code == 200, result.text
    assert '_identity' not in result.json()
    return client.get('/api/me').json()


def test_cloud_accounts_keep_avatars_when_switching_and_reopening_browser(cloud_profiles):
    client = TestClient(app)
    # Login without a prior /me request checks that bootstrap middleware does not overwrite the new cookie.
    first = login(client, 'a')
    assert first['auth_provider'] == 'cloud' and not first['is_guest']
    image_a = client.post('/api/me/avatar', files={'file': ('a.png', b'avatar-A', 'image/png')}).json()['avatar_url']
    assert client.get(image_a).content == b'avatar-A'
    second = login(client, 'b')
    assert first['user_id'] != second['user_id']
    assert second['avatar_url'] is None
    assert client.get(image_a).status_code == 404
    image_b = client.post('/api/me/avatar', files={'file': ('b.png', b'avatar-B', 'image/png')}).json()['avatar_url']
    for _ in range(3):
        assert login(client, 'a')['user_id'] == first['user_id']
        assert client.get(image_a).content == b'avatar-A'
        assert client.get(image_b).status_code == 404
        assert login(client, 'b')['user_id'] == second['user_id']
        assert client.get(image_b).content == b'avatar-B'
    assert client.get(image_b).headers['cache-control'] == 'private, no-store'
    # Logging out must not delete the persistent mapping to the profile.
    client.post('/api/auth/logout')
    new_browser = TestClient(app)
    assert login(new_browser, 'a')['user_id'] == first['user_id']
    assert new_browser.get(image_a).content == b'avatar-A'


def test_legacy_binding_keeps_existing_profile_and_avatar(cloud_profiles):
    legacy = identity_store.resolve_or_bootstrap_session(None, None)
    identity_store.update_user_profile(legacy.user_id, avatar_filename='existing.png')
    client_store.save_account(legacy.user_id, cloud_profiles['a'])
    result = asyncio.run(service.login(legacy.user_id, legacy.device_id, 'a@example.com', 'secure test password'))
    assert result['_identity'].user_id == legacy.user_id
    assert identity_store.avatar_filename(legacy.user_id) == 'existing.png'
    other = asyncio.run(service.login(legacy.user_id, legacy.device_id, 'b@example.com', 'secure test password'))
    assert other['_identity'].user_id != legacy.user_id
    assert identity_store.avatar_filename(other['_identity'].user_id) is None
    assert client_store.account_status(legacy.user_id)['cloud_user_id'] == cloud_profiles['a']['user']['id']


def test_remote_issuer_is_part_of_identity(cloud_profiles):
    user = cloud_profiles['a']['user']
    first = identity_store.create_cloud_session('https://one.example', user, 'device')
    other = identity_store.create_cloud_session('https://two.example', user, 'device', first.user_id)
    assert first.user_id != other.user_id
    assert identity_store.create_cloud_session('https://one.example', user, 'device').user_id == first.user_id

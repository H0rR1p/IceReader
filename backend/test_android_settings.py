import json
from backend import settings_store
from backend.models import LocalAiSettingsInput


def test_android_credentials_are_private_and_scoped(tmp_path, monkeypatch):
    class Secrets:
        values = {}
        def read(self, key): return self.values.get(key, '')
        def write(self, key, value): self.values[key] = value
    secrets = Secrets()
    monkeypatch.setattr(settings_store, 'SETTINGS_PATH', tmp_path / 'settings.json')
    monkeypatch.setattr(settings_store, '_secret_store', secrets)
    settings_store.save_settings('alice', LocalAiSettingsInput(api_key='private-alice', base_url='https://example.com', model='test'))
    settings_store.save_settings('bob', LocalAiSettingsInput(api_key='private-bob', base_url='https://example.com', model='test'))
    assert settings_store.resolve_settings('alice', None, '', '')[0] == 'private-alice'
    assert settings_store.resolve_settings('bob', None, '', '')[0] == 'private-bob'
    assert 'api_key' not in json.loads(settings_store._path_for_user('alice').read_text())
    assert 'private-alice' not in settings_store._path_for_user('alice').read_text()


def test_legacy_android_key_migrates_once(tmp_path, monkeypatch):
    class Secrets:
        values = {}
        def read(self, key): return self.values.get(key, '')
        def write(self, key, value): self.values[key] = value
    secrets = Secrets()
    monkeypatch.setattr(settings_store, 'SETTINGS_PATH', tmp_path / 'settings.json')
    monkeypatch.setattr(settings_store, '_secret_store', secrets)
    path = settings_store._path_for_user('alice'); path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'api_key': 'legacy-secret', 'model': 'test'}))
    assert settings_store.get_settings_status('alice').has_api_key
    assert 'api_key' not in json.loads(path.read_text())
    assert secrets.values['api-key:alice'] == 'legacy-secret'

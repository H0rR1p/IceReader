from backend.modules.cloud_account import repository

def test_fresh_android_cloud_settings_use_public_https(tmp_path, monkeypatch):
    monkeypatch.setattr(repository, 'CLIENT_PATH', tmp_path / 'cloud.sqlite3')
    monkeypatch.setattr(repository, '_initialized_path', None)
    assert repository.get_base_url() == 'https://8-139-254-69.sslip.io'

def test_explicit_cloud_server_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(repository, 'CLIENT_PATH', tmp_path / 'cloud.sqlite3')
    monkeypatch.setattr(repository, '_initialized_path', None)
    repository.set_base_url('https://example.com')
    monkeypatch.setattr(repository, '_initialized_path', None)
    assert repository.get_base_url() == 'https://example.com'

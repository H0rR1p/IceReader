import pytest
from fastapi.testclient import TestClient

from .app import app
from .core.errors import AuthenticationError, ConflictError
from .modules.identity import repository as identity_store


def test_bootstrap_identity_is_stable_and_session_can_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(identity_store, "IDENTITY_PATH", tmp_path / "identity.sqlite3")

    first_user = identity_store.initialize_store()
    assert identity_store.initialize_store() == first_user

    created = identity_store.resolve_or_bootstrap_session(None, None)
    resumed = identity_store.resolve_or_bootstrap_session(created.token, created.device_id)
    assert resumed.user_id == first_user
    assert resumed.session_id == created.session_id
    assert resumed.device_id == created.device_id
    assert resumed.token is None


def test_local_accounts_have_distinct_stable_user_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(identity_store, "IDENTITY_PATH", tmp_path / "identity.sqlite3")

    first = identity_store.create_local_user("甲", "reader-a", "password-a")
    second = identity_store.create_local_user("乙", "reader-b", "password-b")

    assert first.user_id != second.user_id
    assert identity_store.login_local_user("reader-a", "password-a", first.device_id).user_id == first.user_id
    with pytest.raises(AuthenticationError):
        identity_store.login_local_user("reader-a", "wrong-password", first.device_id)
    with pytest.raises(ConflictError):
        identity_store.create_local_user("重复", "reader-a", "password-c")


def test_revoked_session_is_not_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(identity_store, "IDENTITY_PATH", tmp_path / "identity.sqlite3")
    session = identity_store.resolve_or_bootstrap_session(None, None)
    identity_store.revoke_session(session.session_id, session.user_id)

    replacement = identity_store.resolve_or_bootstrap_session(session.token, session.device_id)
    assert replacement.user_id == session.user_id
    assert replacement.session_id != session.session_id
    assert replacement.token


def test_identity_api_switches_browser_session(tmp_path, monkeypatch):
    monkeypatch.setattr(identity_store, "IDENTITY_PATH", tmp_path / "identity.sqlite3")
    client = TestClient(app)
    default_user = client.get("/api/me").json()
    assert default_user["is_guest"] is True
    assert default_user["username"] is None

    registered = client.post("/api/auth/local/register", json={
        "display_name": "学习者", "username": "reader", "password": "password-1",
    })
    assert registered.status_code == 200
    current = client.get("/api/me").json()
    assert current["display_name"] == "学习者"
    assert current["username"] == "reader"
    assert current["is_guest"] is False
    assert current["user_id"] != default_user["user_id"]

    other_browser = TestClient(app)
    logged_in = other_browser.post("/api/auth/local/login", json={
        "username": "reader", "password": "password-1",
    })
    assert logged_in.status_code == 200
    assert other_browser.get("/api/me").json()["user_id"] == current["user_id"]

    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/me").json()["user_id"] == default_user["user_id"]


def test_profile_nickname_and_avatar_are_user_scoped(tmp_path, monkeypatch):
    from .modules.identity import router as identity_router

    monkeypatch.setattr(identity_store, "IDENTITY_PATH", tmp_path / "identity.sqlite3")
    monkeypatch.setattr(identity_router, "AVATAR_DIR", tmp_path / "users")
    client = TestClient(app)

    current = client.get("/api/me").json()
    renamed = client.patch("/api/me", json={"display_name": "冰读学习者"})
    assert renamed.status_code == 200
    assert renamed.json()["display_name"] == "冰读学习者"
    assert renamed.json()["user_id"] == current["user_id"]

    uploaded = client.post(
        "/api/me/avatar",
        files={"file": ("avatar.png", b"avatar-image-data", "image/png")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["avatar_url"].startswith("/api/me/avatar?v=")
    avatar = client.get("/api/me/avatar")
    assert avatar.status_code == 200
    assert avatar.content == b"avatar-image-data"

    other_browser = TestClient(app)
    assert other_browser.post("/api/auth/local/register", json={
        "display_name": "另一位用户", "username": "other-reader", "password": "password-2",
    }).status_code == 200
    assert other_browser.get("/api/me/avatar").status_code == 404

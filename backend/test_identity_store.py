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

    first = identity_store.create_local_user("甲", "reader-a")
    second = identity_store.create_local_user("乙", "reader-b")

    assert first.user_id != second.user_id
    assert identity_store.login_local_user("reader-a", first.device_id).user_id == first.user_id
    assert identity_store.switch_local_profile(second.user_id, first.device_id).user_id == second.user_id
    assert {row["user_id"] for row in identity_store.list_local_profiles()} >= {first.user_id, second.user_id}
    with pytest.raises(AuthenticationError):
        identity_store.login_local_user("missing", first.device_id)
    with pytest.raises(ConflictError):
        identity_store.create_local_user("重复", "reader-a")


def test_revoked_session_is_not_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(identity_store, "IDENTITY_PATH", tmp_path / "identity.sqlite3")
    session = identity_store.resolve_or_bootstrap_session(None, None)
    identity_store.revoke_session(session.session_id, session.user_id)

    replacement = identity_store.resolve_or_bootstrap_session(session.token, session.device_id)
    assert replacement.user_id == session.user_id
    assert replacement.session_id != session.session_id
    assert replacement.token


def test_public_mode_gives_each_new_browser_an_isolated_guest(tmp_path, monkeypatch):
    monkeypatch.setattr(identity_store, "IDENTITY_PATH", tmp_path / "identity.sqlite3")
    monkeypatch.setattr(identity_store, "PUBLIC_MODE", True)

    first = identity_store.resolve_or_bootstrap_session(None, None)
    second = identity_store.resolve_or_bootstrap_session(None, None)
    resumed = identity_store.resolve_or_bootstrap_session(first.token, first.device_id)

    assert first.user_id != second.user_id
    assert resumed.user_id == first.user_id
    assert first.auth_provider == "guest"
    assert identity_store.user_profile(first.user_id)["is_guest"] is True


def test_identity_api_switches_browser_session(tmp_path, monkeypatch):
    monkeypatch.setattr(identity_store, "IDENTITY_PATH", tmp_path / "identity.sqlite3")
    client = TestClient(app)
    default_user = client.get("/api/me").json()
    assert default_user["is_guest"] is True
    assert default_user["username"] is None

    registered = client.post("/api/auth/local/register", json={
        "display_name": "学习者", "username": "reader",
    })
    assert registered.status_code == 200
    current = client.get("/api/me").json()
    assert current["display_name"] == "学习者"
    assert current["username"] == "reader"
    assert current["is_guest"] is False
    assert current["user_id"] != default_user["user_id"]

    other_browser = TestClient(app)
    logged_in = other_browser.post("/api/auth/local/login", json={
        "username": "reader",
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
        "display_name": "另一位用户", "username": "other-reader",
    }).status_code == 200
    assert other_browser.get("/api/me/avatar").status_code == 404


def test_passwordless_profile_switch_and_session_management(tmp_path, monkeypatch):
    monkeypatch.setattr(identity_store, "IDENTITY_PATH", tmp_path / "identity.sqlite3")
    first = identity_store.create_local_user("学习者", "reader")
    second = identity_store.login_local_user("reader", None)
    sessions = identity_store.list_user_sessions(first.user_id)
    assert {row["id"] for row in sessions} == {first.session_id, second.session_id}

    assert identity_store.revoke_other_session(first.user_id, second.session_id, first.session_id) is True
    assert all(row["id"] != second.session_id for row in identity_store.list_user_sessions(first.user_id))


def test_active_sessions_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(identity_store, "IDENTITY_PATH", tmp_path / "identity.sqlite3")
    monkeypatch.setattr(identity_store, "MAX_ACTIVE_SESSIONS_PER_USER", 3)
    first = identity_store.create_local_user("学习者", "reader")
    for _ in range(5):
        identity_store.login_local_user("reader", None)

    sessions = identity_store.list_user_sessions(first.user_id)
    assert len(sessions) == 3
    assert first.session_id not in {row["id"] for row in sessions}


def test_local_api_rejects_cross_site_writes_and_sets_security_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(identity_store, "IDENTITY_PATH", tmp_path / "identity.sqlite3")
    client = TestClient(app)
    rejected = client.post("/api/auth/logout", headers={"Origin": "https://attacker.example"})
    assert rejected.status_code == 403
    assert rejected.json()["code"] == "invalid_origin"

    hostile_host = client.get("/api/health", headers={"Host": "attacker.example"})
    assert hostile_host.status_code == 400
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]

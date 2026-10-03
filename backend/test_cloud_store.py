import pytest

from .cloud.config import CloudConfig
from .cloud.repository import CloudAuthError, CloudRepository


def _store(tmp_path):
    repository = CloudRepository(CloudConfig(
        database_path=tmp_path / "cloud.sqlite3", public_url="http://127.0.0.1:8010",
        secret="test-secret", allowed_origins=(), dev_mode=True,
    ))
    repository.initialize()
    return repository


def test_cloud_account_verification_reset_and_refresh_replay(tmp_path):
    repository = _store(tmp_path)
    registered = repository.register("reader@example.com", "correct horse battery", "读者", "device-a", "电脑 A")
    user_id = registered["user"]["id"]
    assert repository.authenticate(registered["access_token"])[0]["id"] == user_id

    verification = repository.request_action_token("reader@example.com", "verify_email")
    assert verification and repository.verify_email(verification[1])["email_verified"] is True
    rotated = repository.refresh(registered["refresh_token"])
    assert rotated["refresh_token"] != registered["refresh_token"]
    with pytest.raises(CloudAuthError, match="重复使用"):
        repository.refresh(registered["refresh_token"])
    with pytest.raises(CloudAuthError):
        repository.refresh(rotated["refresh_token"])

    reset = repository.request_action_token("reader@example.com", "reset_password")
    assert reset and repository.reset_password(reset[1], "a newer secure password")["id"] == user_id
    assert repository.login("reader@example.com", "a newer secure password", "device-b", "电脑 B")["user"]["id"] == user_id


def test_cloud_sync_is_owner_scoped_and_conflicts_can_be_resolved(tmp_path):
    repository = _store(tmp_path)
    user = repository.register("one@example.com", "long enough password", "一", "a", "A")["user"]
    other = repository.register("two@example.com", "long enough password", "二", "x", "X")["user"]
    change = {"change_id":"c1","entity_type":"note","entity_id":"n1","payload":{"gloss":"猫"},"updated_at":1}
    assert repository.push_changes(user["id"], "a", [change])["accepted"]
    assert repository.pull_changes(other["id"], "x", 0, 100)["changes"] == []
    fork = {"change_id":"c2","entity_type":"note","entity_id":"n1","payload":{"gloss":"猫科"},"updated_at":2,"base_version":0}
    assert repository.push_changes(user["id"], "b", [fork])["conflicts"]
    groups = repository.conflicts(user["id"])
    assert len(groups) == 1 and len(groups[0]["versions"]) == 2
    winner = groups[0]["versions"][1]["entity_id"]
    assert repository.resolve_conflict(user["id"], "a", groups[0]["id"], winner)["resolved"] is True
    assert repository.conflicts(user["id"]) == []


def test_admin_bootstrap_lists_and_disables_accounts(tmp_path):
    repository = CloudRepository(CloudConfig(
        database_path=tmp_path / "cloud.sqlite3", public_url="http://127.0.0.1:8010",
        secret="test-secret", allowed_origins=(), dev_mode=True,
        admin_email="admin@example.com", admin_password="secure admin password",
    ))
    repository.initialize()
    admin = repository.ensure_admin()
    assert admin and admin["role"] == "admin" and admin["email_verified"] is True
    reader = repository.register("reader@example.com", "correct horse battery", "读者", "device-a", "电脑 A")
    page = repository.list_users("reader")
    assert page["total"] == 1 and page["items"][0]["email"] == "reader@example.com"

    disabled = repository.set_user_disabled(admin["id"], reader["user"]["id"], True)
    assert disabled["disabled"] is True
    with pytest.raises(CloudAuthError):
        repository.authenticate(reader["access_token"])
    with pytest.raises(ValueError, match="不能禁用"):
        repository.set_user_disabled(admin["id"], admin["id"], True)


def test_admin_bootstrap_promotes_existing_account_and_applies_configured_password(tmp_path):
    database_path = tmp_path / "cloud.sqlite3"
    regular = CloudRepository(CloudConfig(
        database_path=database_path, public_url="http://127.0.0.1:8010",
        secret="test-secret", allowed_origins=(), dev_mode=True,
    ))
    regular.initialize()
    regular.register("owner@example.com", "previous user password", "站长", "device-a", "电脑 A")

    repository = CloudRepository(CloudConfig(
        database_path=database_path, public_url="http://127.0.0.1:8010",
        secret="test-secret", allowed_origins=(), dev_mode=True,
        admin_email="owner@example.com", admin_password="configured admin password",
    ))
    repository.initialize()
    admin = repository.ensure_admin()

    assert admin and admin["role"] == "admin" and admin["email_verified"] is True
    with pytest.raises(CloudAuthError):
        repository.login("owner@example.com", "previous user password", "old-device", "旧设备")
    logged_in = repository.login("owner@example.com", "configured admin password", "admin-device", "管理设备")
    assert logged_in["user"]["role"] == "admin"


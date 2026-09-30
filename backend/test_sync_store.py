from .modules.sync import repository as sync_store


def _isolated_store(tmp_path,monkeypatch):
    path=tmp_path / "sync.sqlite3"
    monkeypatch.setattr(sync_store,"SYNC_PATH",path)
    monkeypatch.setattr(sync_store,"_initialized_path",None)
    sync_store.initialize_store(); return path


def _change(change_id,entity_type,entity_id,payload,updated_at,base_version=None,deleted_at=None):
    value={"change_id":change_id,"entity_type":entity_type,"entity_id":entity_id,"payload":payload,"updated_at":updated_at}
    if base_version is not None: value["base_version"]=base_version
    if deleted_at is not None: value["deleted_at"]=deleted_at
    return value


def test_two_devices_incrementally_merge_and_retry_idempotently(tmp_path,monkeypatch):
    _isolated_store(tmp_path,monkeypatch)
    first=sync_store.push_changes("user-a","device-a",[_change("change-1","bookmark","bookmark-1",{"text":"猫"},1)])
    assert first["accepted"][0]["version"] == 1
    duplicate=sync_store.push_changes("user-a","device-a",[_change("change-1","bookmark","bookmark-1",{"text":"猫"},1)])
    assert duplicate["skipped"][0]["reason"] == "duplicate"
    pulled=sync_store.pull_changes("user-a","device-b",0)
    assert pulled["changes"][0]["payload"] == {"text":"猫"}
    second=sync_store.push_changes("user-a","device-b",[_change("change-2","bookmark","bookmark-1",{"text":"猫です"},2,1)])
    assert second["accepted"][0]["version"] == 2
    assert sync_store.pull_changes("user-a","device-a",pulled["cursor"])["changes"][0]["source_device_id"] == "device-b"
    assert sync_store.pull_changes("user-b","device-x",0)["changes"] == []
    status=sync_store.sync_status("user-a")
    assert status["cursor"] == 2 and status["entities"] == 1 and status["devices"] == 2


def test_note_conflict_forks_and_tombstone_is_incremental(tmp_path,monkeypatch):
    _isolated_store(tmp_path,monkeypatch)
    sync_store.push_changes("user-a","device-a",[_change("change-1","note","note-1",{"gloss":"猫"},10)])
    conflict=sync_store.push_changes("user-a","device-b",[_change("change-2","note","note-1",{"gloss":"猫科动物"},11,0)])
    assert len(conflict["conflicts"]) == 1
    variants=sync_store.list_conflicts("user-a")
    assert len(variants) == 2
    assert {row["payload"]["gloss"] for row in variants} == {"猫","猫科动物"}
    deleted=sync_store.push_changes("user-a","device-a",[_change("change-3","bookmark","bookmark-2",{},12,deleted_at=12)])
    pulled=sync_store.pull_changes("user-a","device-b",0)
    tombstone=[row for row in pulled["changes"] if row["change_id"]=="change-3"][0]
    assert deleted["accepted"] and tombstone["operation"] == "delete" and tombstone["deleted_at"] == 12


def test_append_only_conflicts_and_identity_binding_are_owner_scoped(tmp_path,monkeypatch):
    _isolated_store(tmp_path,monkeypatch)
    sync_store.push_changes("user-a","device-a",[_change("change-1","review_log","review-1",{"rating":"good"},1)])
    conflict=sync_store.push_changes("user-a","device-b",[_change("change-2","review_log","review-1",{"rating":"again"},2)])
    assert conflict["conflicts"]
    binding=sync_store.bind_identity("user-a","cloud","subject-a")
    assert binding["user_id"] == "user-a"
    assert sync_store.list_bindings("user-a")[0]["provider_subject"] == "subject-a"

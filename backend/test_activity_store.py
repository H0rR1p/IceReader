from datetime import date

from .modules.activity import repository as activity_store
from .modules.learning import repository as learning_store


def _isolated_store(tmp_path, monkeypatch):
    path=tmp_path / "learning.sqlite3"
    monkeypatch.setattr(learning_store,"LEARNING_PATH",path)
    monkeypatch.setattr(learning_store,"_initialized_path",None)
    monkeypatch.setattr(activity_store,"ACTIVITY_PATH",path)
    monkeypatch.setattr(activity_store,"_initialized_path",None)
    learning_store.initialize_store(); activity_store.initialize_store()
    return path


def _heartbeat(identifier,start,end,device="device-a",**counters):
    return {"id":identifier,"session_id":f"session-{device}","activity_type":"reading","window_start":start,
        "window_end":end,"local_date":date.today().isoformat(),"timezone":"Asia/Shanghai",**counters}


def test_heartbeat_is_idempotent_and_overlapping_devices_are_deduplicated(tmp_path,monkeypatch):
    _isolated_store(tmp_path,monkeypatch)
    first=activity_store.record_heartbeat("user-a","device-a",_heartbeat("beat-1",100,115))
    duplicate=activity_store.record_heartbeat("user-a","device-a",_heartbeat("beat-1",100,115))
    overlap=activity_store.record_heartbeat("user-a","device-b",_heartbeat("beat-2",110,125,"device-b"))
    other=activity_store.record_heartbeat("user-b","device-b",_heartbeat("beat-3",110,125,"device-b"))
    assert first["credited_seconds"] == 15
    assert duplicate == {"credited_seconds":15,"duplicate":True}
    assert overlap["credited_seconds"] == 10
    assert other["credited_seconds"] == 15
    assert activity_store.summary("user-a",7)["active_seconds"] == 25


def test_heartbeat_caps_wall_time_and_aggregates_metrics(tmp_path,monkeypatch):
    _isolated_store(tmp_path,monkeypatch)
    result=activity_store.record_heartbeat("user-a","device-a",_heartbeat("beat-1",100,1000,cards_reviewed=2,sentences_read=3,lookup_count=4))
    assert result["credited_seconds"] == 15
    row=activity_store.heatmap("user-a","1970-01-01","2999-12-31")[0]
    assert row["cards_reviewed"] == 2
    assert row["sentences_read"] == 3
    assert row["lookup_count"] == 4

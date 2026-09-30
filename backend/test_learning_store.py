import sqlite3

from .modules.learning import repository as learning_store


def _isolated_store(tmp_path, monkeypatch):
    path = tmp_path / "learning.sqlite3"
    monkeypatch.setattr(learning_store, "LEARNING_PATH", path)
    monkeypatch.setattr(learning_store, "_initialized_path", None)
    return path


def _event(event_id: str, event_type: str, occurred_at: float, canonical_key: str = "猫|ねこ") -> dict:
    return {
        "id": event_id,
        "item": {
            "id": f"item-{canonical_key}", "type": "vocabulary",
            "canonical_key": canonical_key, "lemma": "猫", "reading": "ねこ",
        },
        "event_type": event_type,
        "occurred_at": occurred_at,
        "context": {"sentence_id": "sentence-1"},
    }


def test_learning_events_are_idempotent_and_replay_is_deterministic(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    events = [_event("event-1", "lookup", 1), _event("event-2", "mark_mastered", 2)]

    assert learning_store.append_events("user-a", "device-a", events) == {"inserted": 2, "projected": 1}
    assert learning_store.append_events("user-a", "device-a", events) == {"inserted": 0, "projected": 0}
    before = learning_store.list_blindspots("user-a")[0]
    assert learning_store.replay_user("user-a") == 1
    after = learning_store.list_blindspots("user-a")[0]

    assert before["mastery"] == after["mastery"]
    assert before["confidence"] == after["confidence"]
    assert before["lookup_count"] == after["lookup_count"] == 1


def test_users_and_word_senses_are_projected_separately(tmp_path, monkeypatch):
    path = _isolated_store(tmp_path, monkeypatch)
    learning_store.append_events("user-a", "device-a", [
        _event("a-1", "lookup", 1, "引く#拉"),
        _event("a-2", "mark_mastered", 2, "引く#感到反感"),
    ])
    learning_store.append_events("user-b", "device-b", [
        _event("b-1", "mark_unknown", 1, "引く#拉"),
    ])

    with sqlite3.connect(path) as connection:
        a_states = connection.execute(
            "SELECT COUNT(*) FROM user_knowledge_states WHERE user_id='user-a'",
        ).fetchone()[0]
        b_states = connection.execute(
            "SELECT COUNT(*) FROM user_knowledge_states WHERE user_id='user-b'",
        ).fetchone()[0]
    assert a_states == 2
    assert b_states == 1


def test_unknown_state_keeps_low_confidence_and_existing_state_is_returned(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    learning_store.append_events("user-a", "device-a", [_event("event-1", "mark_mastered", 1)])
    rows = learning_store.knowledge_states("user-a", [
        {"id":"unused-1","type":"vocabulary","canonical_key":"猫|ねこ","lemma":"猫","reading":"ねこ"},
        {"id":"unused-2","type":"vocabulary","canonical_key":"犬|いぬ","lemma":"犬","reading":"いぬ"},
    ])
    assert rows[0]["mastery"] == 0.75
    assert rows[0]["confidence"] > 0
    assert rows[1]["mastery"] == 0.5
    assert rows[1]["confidence"] == 0

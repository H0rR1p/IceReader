from . import ai_store


USER_ID = "user-1"


def test_usage_and_exact_response_cache_are_persisted(tmp_path, monkeypatch):
    ai_store.close_store()
    monkeypatch.setattr(ai_store, "AI_DATA_PATH", tmp_path / "ai.sqlite3")
    ai_store.record_usage(
        USER_ID, "sentence_explanation", "model-a",
        {
            "prompt_tokens": 100,
            "prompt_cache_hit_tokens": 60,
            "prompt_cache_miss_tokens": 40,
            "completion_tokens": 25,
        },
        duration_ms=321, item_count=3,
    )
    key = ai_store.make_cache_key(
        USER_ID, "sentence", "model-a", "v1", {"text": "猫だ。", "unknown": []},
    )
    assert key == ai_store.make_cache_key(
        USER_ID, "sentence", "model-a", "v1", {"unknown": [], "text": "猫だ。"},
    )
    ai_store.set_cached_response(USER_ID, key, "sentence", "model-a", "v1", {"meaning": "是猫。"})
    assert ai_store.get_cached_response(USER_ID, key) == {"meaning": "是猫。"}

    summary = ai_store.usage_summary(USER_ID, {"cache_hit": 0.1, "cache_miss": 1.0, "output": 2.0})
    assert summary["requests"] == 1
    assert summary["items"] == 3
    assert summary["cache_hit_tokens"] == 60
    assert summary["cache_miss_tokens"] == 40
    assert summary["completion_tokens"] == 25
    assert summary["response_cache_entries"] == 1
    assert summary["response_cache_hits"] == 1
    assert summary["estimated_cost_usd"] == 0.000096
    ai_store.close_store()


def test_ai_store_reuses_one_initialized_connection(tmp_path, monkeypatch):
    ai_store.close_store()
    monkeypatch.setattr(ai_store, "AI_DATA_PATH", tmp_path / "ai.sqlite3")
    real_connect = ai_store.sqlite3.connect
    connections = []

    def counted_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(ai_store.sqlite3, "connect", counted_connect)

    ai_store.record_usage(USER_ID, "test", "model", None, duration_ms=1)
    key = ai_store.make_cache_key(USER_ID, "sentence", "model", "v1", {"text": "猫。"})
    ai_store.set_cached_response(USER_ID, key, "sentence", "model", "v1", {"meaning": "猫。"})
    assert ai_store.get_cached_response(USER_ID, key) == {"meaning": "猫。"}
    assert ai_store.usage_summary(USER_ID)["requests"] == 1
    assert len(connections) == 1

    ai_store.close_store()


def test_usage_and_cache_are_isolated_by_user(tmp_path, monkeypatch):
    ai_store.close_store()
    monkeypatch.setattr(ai_store, "AI_DATA_PATH", tmp_path / "ai.sqlite3")
    ai_store.record_usage("user-a", "test", "model", None, duration_ms=1)
    key_a = ai_store.make_cache_key("user-a", "sentence", "model", "v1", {"text": "猫。"})
    key_b = ai_store.make_cache_key("user-b", "sentence", "model", "v1", {"text": "猫。"})
    ai_store.set_cached_response("user-a", key_a, "sentence", "model", "v1", {"meaning": "甲"})

    assert key_a != key_b
    assert ai_store.get_cached_response("user-b", key_a) is None
    assert ai_store.usage_summary("user-a")["requests"] == 1
    assert ai_store.usage_summary("user-b")["requests"] == 0
    ai_store.close_store()

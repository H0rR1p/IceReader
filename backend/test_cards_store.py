import sqlite3
import time

from .modules.cards import repository as cards_store
from .modules.cards.scheduler import MODEL_VERSION, retrievability, schedule_review
from .modules.learning import repository as learning_store


def _isolated_store(tmp_path, monkeypatch):
    path = tmp_path / "learning.sqlite3"
    monkeypatch.setattr(learning_store, "LEARNING_PATH", path)
    monkeypatch.setattr(learning_store, "_initialized_path", None)
    monkeypatch.setattr(cards_store, "CARDS_PATH", path)
    monkeypatch.setattr(cards_store, "_initialized_path", None)
    learning_store.initialize_store()
    cards_store.initialize_store()
    return path


def _candidate(user_id="user-a", sentence_id="sentence-1"):
    item_id = learning_store.ensure_knowledge_item({
        "id": f"item-{user_id}-{sentence_id}", "type": "vocabulary",
        "canonical_key": f"猫|ねこ|{sentence_id}", "lemma": "猫", "reading": "ねこ",
    })
    return cards_store.create_candidate(user_id, item_id, {
        "lemma": "猫", "reading": "ねこ", "gloss": "猫",
        "sentence": "吾輩は猫である。", "book_id": "book-1", "book_title": "吾輩は猫である",
        "chapter_id": "chapter-1", "sentence_id": sentence_id,
    })


def test_candidate_dedup_search_and_owner_boundary(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    first = _candidate()
    second = _candidate()
    assert first["id"] == second["id"]
    card = cards_store.accept_candidate("user-a", first["id"])
    assert cards_store.search_cards("user-a", "猫")[0]["id"] == card["id"]
    assert cards_store.search_cards("user-b", "猫") == []
    assert cards_store.due_cards("user-a")[0]["id"] == card["id"]


def test_review_is_idempotent_and_preserves_log_state(tmp_path, monkeypatch):
    path = _isolated_store(tmp_path, monkeypatch)
    card = cards_store.accept_candidate("user-a", _candidate()["id"])
    first = cards_store.review_card("user-a", "device-a", card["id"], "good", 1_000_000, "review-1")
    repeated = cards_store.review_card("user-a", "device-a", card["id"], "easy", 2_000_000, "review-1")
    assert first == repeated
    assert first["stability"] == 2.4
    assert first["model_version"] == MODEL_VERSION
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM review_logs").fetchone()[0] == 1


def test_scheduler_fixed_vectors():
    assert schedule_review("again").interval_days == 1.0
    assert schedule_review("good").stability == 2.4
    assert retrievability(10, 0) == 1.0
    assert round(retrievability(10, 10), 6) == 0.9


def test_bulk_tags_and_ten_thousand_card_fts_search(tmp_path, monkeypatch):
    path = _isolated_store(tmp_path, monkeypatch)
    now = time.time()
    with sqlite3.connect(path) as connection:
        notes=[]; cards=[]; states=[]; search=[]
        for index in range(10_000):
            note_id=f"note-{index}"; card_id=f"card-{index}"; item_id=f"knowledge-{index}"
            notes.append((note_id,"user-a",item_id,f"単語{index}",f"たんご{index}",f"释义{index}",f"原句{index}","book-1","性能测试书","chapter-1",f"sentence-{index}","[]",now,now))
            cards.append((card_id,"user-a",note_id,item_id,"context-recognition","active",now,now))
            states.append((card_id,"user-a",now,MODEL_VERSION,now))
            search.append((card_id,"user-a",f"単語{index}",f"たんご{index}",f"释义{index}",f"原句{index}","性能测试书",""))
        connection.executemany("INSERT INTO notes(id,user_id,knowledge_item_id,lemma,reading,gloss,sentence,book_id,book_title,chapter_id,sentence_id,tags_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",notes)
        connection.executemany("INSERT INTO cards(id,user_id,note_id,knowledge_item_id,card_template,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",cards)
        connection.executemany("INSERT INTO memory_states(card_id,user_id,due_at,model_version,updated_at) VALUES(?,?,?,?,?)",states)
        connection.executemany("INSERT INTO card_search(card_id,user_id,lemma,reading,gloss,sentence,book_title,tags) VALUES(?,?,?,?,?,?,?,?)",search)
        connection.commit()
    started=time.perf_counter()
    result=cards_store.search_cards("user-a","単語9999")
    assert time.perf_counter()-started < 1.0
    assert result[0]["id"] == "card-9999"
    assert cards_store.update_card_tags("user-a",["card-9999"],"主题::测试")["updated"] == 1
    assert cards_store.search_cards("user-a","主题")[0]["id"] == "card-9999"


def test_card_edit_bulk_changes_and_undo(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    card = cards_store.accept_candidate("user-a", _candidate()["id"])

    edited = cards_store.update_card_note("user-a", card["id"], {
        "lemma": "吾輩", "reading": "わがはい", "gloss": "我、本大爷", "sentence": "吾輩は猫である。",
    })
    assert cards_store.search_cards("user-a", "本大爷")[0]["lemma"] == "吾輩"
    assert cards_store.undo_card_action("user-a", edited["undo_id"])["restored"] == 1
    assert cards_store.search_cards("user-a", "猫")[0]["lemma"] == "猫"

    tagged = cards_store.update_card_tags("user-a", [card["id"]], "作品::猫")
    assert cards_store.list_tags("user-a") == [{"name": "作品::猫", "count": 1}]
    assert cards_store.undo_card_action("user-a", tagged["undo_id"])["restored"] == 1
    assert cards_store.list_tags("user-a") == []

    changed = cards_store.update_card_statuses("user-a", [card["id"]], "suspended")
    assert cards_store.search_cards("user-a", status="suspended")[0]["id"] == card["id"]
    cards_store.undo_card_action("user-a", changed["undo_id"])
    assert cards_store.search_cards("user-a", status="active")[0]["id"] == card["id"]


def test_daily_new_and_review_limits_are_enforced(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    assert cards_store.update_card_preferences("user-a", 1, 1) == {
        "daily_new_limit": 1, "daily_review_limit": 1,
    }
    first = cards_store.accept_candidate("user-a", _candidate(sentence_id="sentence-1")["id"])
    second = _candidate(sentence_id="sentence-2")
    try:
        cards_store.accept_candidate("user-a", second["id"])
        assert False, "daily new-card limit should reject the second card"
    except cards_store.DailyNewLimitError:
        pass
    assert [card["id"] for card in cards_store.due_cards("user-a", limit=100)] == [first["id"]]
    summary = cards_store.card_summary("user-a")
    assert summary["new_today"] == 1
    assert summary["daily_new_limit"] == 1
    assert summary["daily_review_limit"] == 1


def test_merge_cards_preserves_review_history_and_tags(tmp_path, monkeypatch):
    path = _isolated_store(tmp_path, monkeypatch)
    first = cards_store.accept_candidate("user-a", _candidate(sentence_id="sentence-1")["id"])
    second = cards_store.accept_candidate("user-a", _candidate(sentence_id="sentence-2")["id"])
    cards_store.update_card_tags("user-a", [first["id"]], "来源::一")
    cards_store.update_card_tags("user-a", [second["id"]], "来源::二")
    cards_store.review_card("user-a", "device-a", second["id"], "good", time.time(), "merge-review")

    result = cards_store.merge_cards("user-a", first["id"], [second["id"]])
    assert result == {"target_card_id": first["id"], "merged": 1}
    assert cards_store.search_cards("user-a", status="active")[0]["tags"] == ["来源::一", "来源::二"]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT card_id FROM review_logs WHERE id='merge-review'").fetchone()[0] == first["id"]
        assert connection.execute("SELECT deleted_at FROM cards WHERE id=?", (second["id"],)).fetchone()[0] is not None

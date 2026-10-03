import json
import sqlite3
import zipfile

import pytest

from .modules.data_portability import service


def _database(path, statements):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        for statement, values in statements:
            connection.execute(statement, values)


def test_backup_restore_is_user_scoped_and_validates_assets(tmp_path, monkeypatch):
    library_path = tmp_path / "library.sqlite3"
    learning_path = tmp_path / "learning.sqlite3"
    ai_path = tmp_path / "ai.sqlite3"
    _database(library_path, [
        ("CREATE TABLE records(owner_user_id TEXT,table_name TEXT,record_key TEXT,payload TEXT,PRIMARY KEY(owner_user_id,table_name,record_key))", ()),
        ("INSERT INTO records VALUES(?,?,?,?)", ("user-a", "books", "book-a", json.dumps({"id": "book-a"}))),
        ("INSERT INTO records VALUES(?,?,?,?)", ("user-b", "books", "book-b", json.dumps({"id": "book-b"}))),
    ])
    _database(learning_path, [
        ("CREATE TABLE card_preferences(user_id TEXT PRIMARY KEY,daily_new_limit INTEGER,daily_review_limit INTEGER,updated_at REAL)", ()),
        ("INSERT INTO card_preferences VALUES(?,?,?,?)", ("user-a", 12, 80, 1.0)),
        ("INSERT INTO card_preferences VALUES(?,?,?,?)", ("user-b", 30, 300, 1.0)),
    ])
    _database(ai_path, [
        ("CREATE TABLE usage(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,operation TEXT)", ()),
        ("INSERT INTO usage(user_id,operation) VALUES(?,?)", ("user-a", "translate")),
    ])
    monkeypatch.setattr(service, "DATA_DIR", tmp_path)
    monkeypatch.setattr(service, "DATABASE_SPECS", {
        "library": {"path": library_path, "tables": {"records": "owner_user_id"}},
        "learning": {"path": learning_path, "tables": {"card_preferences": "user_id"}},
        "ai": {"path": ai_path, "tables": {"usage": "user_id"}},
    })
    user_file = tmp_path / "users" / "user-a" / "profile" / "preferences.json"
    user_file.parent.mkdir(parents=True)
    user_file.write_text('{"theme":"ice"}', encoding="utf-8")
    voice_file = tmp_path / "voice" / "users" / "user-a" / "template.ymmp"
    voice_file.parent.mkdir(parents=True)
    voice_file.write_text("voice-template", encoding="utf-8")

    backup = service.create_backup("user-a", tmp_path / "backup.zip")
    user_file.unlink()
    voice_file.unlink()
    with sqlite3.connect(library_path) as connection:
        connection.execute("DELETE FROM records WHERE owner_user_id='user-a'")
    with sqlite3.connect(learning_path) as connection:
        connection.execute("UPDATE card_preferences SET daily_new_limit=99 WHERE user_id='user-a'")

    result = service.restore_backup("user-a", backup)
    assert result["restored_rows"] == 3
    with sqlite3.connect(library_path) as connection:
        assert connection.execute("SELECT record_key FROM records WHERE owner_user_id='user-a'").fetchone()[0] == "book-a"
        assert connection.execute("SELECT record_key FROM records WHERE owner_user_id='user-b'").fetchone()[0] == "book-b"
    with sqlite3.connect(learning_path) as connection:
        assert connection.execute("SELECT daily_new_limit FROM card_preferences WHERE user_id='user-a'").fetchone()[0] == 12
        assert connection.execute("SELECT daily_new_limit FROM card_preferences WHERE user_id='user-b'").fetchone()[0] == 30
    assert user_file.read_text(encoding="utf-8") == '{"theme":"ice"}'
    assert voice_file.read_text(encoding="utf-8") == "voice-template"


def test_backup_rejects_checksum_tampering(tmp_path, monkeypatch):
    library_path = tmp_path / "library.sqlite3"
    _database(library_path, [
        ("CREATE TABLE records(owner_user_id TEXT,table_name TEXT,record_key TEXT,payload TEXT,PRIMARY KEY(owner_user_id,table_name,record_key))", ()),
    ])
    monkeypatch.setattr(service, "DATA_DIR", tmp_path)
    monkeypatch.setattr(service, "DATABASE_SPECS", {
        "library": {"path": library_path, "tables": {"records": "owner_user_id"}},
    })
    backup = service.create_backup("user-a", tmp_path / "backup.zip")
    with zipfile.ZipFile(backup, "a") as archive:
        archive.writestr("data.json", b"{}")
    with pytest.raises(ValueError, match="校验失败|重复文件"):
        service.restore_backup("user-a", backup)


def test_book_transfer_merges_books_and_assets_without_replacing_target_data(tmp_path, monkeypatch):
    library_path = tmp_path / "library.sqlite3"
    resource_key = "a" * 20
    rows = [
        ("books", "book-a", {"id": "book-a", "title": "猫", "coverUrl": f"/api/assets/{resource_key}/cover.jpg", "customCover": True, "createdAt": 1, "updatedAt": 2}),
        ("chapters", "chapter-a", {"id": "chapter-a", "bookId": "book-a", "title": "一", "order": 0}),
        ("sentences", "sentence-a", {"id": "sentence-a", "chapter_id": "chapter-a", "text": "吾輩は猫である。"}),
        ("tokens", "token-a", {"id": "token-a", "sentence_id": "sentence-a", "lexemeKey": "猫|ねこ"}),
        ("lexemes", "猫|ねこ", {"key": "猫|ねこ", "lemma": "猫", "reading": "ねこ"}),
    ]
    statements = [
        ("CREATE TABLE records(owner_user_id TEXT,table_name TEXT,record_key TEXT,payload TEXT,PRIMARY KEY(owner_user_id,table_name,record_key))", ()),
        ("CREATE TABLE user_library_items(user_id TEXT,book_id TEXT,metadata_json TEXT,created_at REAL,updated_at REAL,PRIMARY KEY(user_id,book_id))", ()),
        ("INSERT INTO user_library_items VALUES(?,?,?,?,?)", ("target", "existing", json.dumps({"id": "existing", "title": "已有"}), 1, 1)),
    ]
    statements.extend(("INSERT INTO records VALUES(?,?,?,?)", ("source", table, key, json.dumps(payload))) for table, key, payload in rows)
    _database(library_path, statements)
    monkeypatch.setattr(service, "DATA_DIR", tmp_path)
    monkeypatch.setattr(service, "DATABASE_SPECS", {"library": {"path": library_path, "tables": {"records": "owner_user_id"}}})
    asset = tmp_path / "books" / resource_key / "cover.jpg"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"cover")
    custom_cover = tmp_path / "books" / "custom-covers" / "source" / "book-a.png"
    custom_cover.parent.mkdir(parents=True)
    custom_cover.write_bytes(b"custom")

    package = service.create_book_transfer("source", tmp_path / "transfer.zip")
    result = service.import_book_transfer("target", package)

    assert result == {"imported_books": 1, "skipped_books": 0, "imported_records": 5, "imported_cards": 0, "imported_learning_records": 0}
    with sqlite3.connect(library_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM user_library_items WHERE user_id='target'").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM records WHERE owner_user_id='target' AND table_name='chapters'").fetchone()[0] == 1
    assert (tmp_path / "books" / resource_key / "cover.jpg").read_bytes() == b"cover"
    assert (tmp_path / "books" / "custom-covers" / "target" / "book-a.png").read_bytes() == b"custom"
    assert service.import_book_transfer("target", package)["skipped_books"] == 1
    with zipfile.ZipFile(package) as original, zipfile.ZipFile(tmp_path / "version-one.zip", "w") as legacy:
        for name in original.namelist():
            content = original.read(name)
            if name == "manifest.json":
                manifest = json.loads(content)
                manifest["schema_version"] = 1
                content = json.dumps(manifest).encode()
            legacy.writestr(name, content)
    assert service.import_book_transfer("legacy-target", tmp_path / "version-one.zip")["imported_books"] == 1


def test_transfer_cards_reviews_learning_and_time_without_credentials(tmp_path, monkeypatch):
    from .test_cards_store import _isolated_store, _candidate
    from .modules.cards import repository as cards
    from .modules.activity import repository as activity
    from .modules.learning import repository as learning
    learning_path = _isolated_store(tmp_path, monkeypatch)
    monkeypatch.setattr(activity, "ACTIVITY_PATH", learning_path)
    monkeypatch.setattr(activity, "_initialized_path", None)
    activity.initialize_store()
    source_card = cards.accept_candidate("source", _candidate("source")["id"])
    cards.review_card("source", "device", source_card["id"], "good", 1_000_000, "review-source")
    target_card = cards.accept_candidate("target", _candidate("target", "other")["id"])
    item = {"id": "learning-item", "type": "vocabulary", "canonical_key": "読む|よむ", "lemma": "読む", "reading": "よむ"}
    learning.append_events("source", "device", [{"id": "event-source", "item": item, "event_type": "lookup", "occurred_at": 10, "context": {}}])
    with sqlite3.connect(learning_path) as connection:
        connection.execute("INSERT INTO daily_learning_stats(user_id,local_date,timezone,active_seconds,reading_seconds,updated_at) VALUES('source','2026-10-03','Asia/Shanghai',120,120,1)")
        connection.execute("INSERT INTO daily_learning_stats(user_id,local_date,timezone,active_seconds,reading_seconds,updated_at) VALUES('target','2026-10-03','Asia/Shanghai',60,60,1)")
    library_path = tmp_path / "library.sqlite3"
    _database(library_path, [
        ("CREATE TABLE records(owner_user_id TEXT,table_name TEXT,record_key TEXT,payload TEXT,PRIMARY KEY(owner_user_id,table_name,record_key))", ()),
        ("CREATE TABLE user_library_items(user_id TEXT,book_id TEXT,metadata_json TEXT,created_at REAL,updated_at REAL,PRIMARY KEY(user_id,book_id))", ()),
    ])
    specs = {name: {**spec, "path": tmp_path / (name + ".sqlite3")} for name, spec in service.DATABASE_SPECS.items()}
    monkeypatch.setattr(service, "DATA_DIR", tmp_path)
    monkeypatch.setattr(service, "DATABASE_SPECS", specs)
    secret = tmp_path / "users" / "source" / "settings.json"
    secret.parent.mkdir(parents=True)
    secret.write_text('{"apiKey":"private-secret"}', encoding="utf-8")
    package = service.create_book_transfer("source", tmp_path / "transfer.zip")
    with zipfile.ZipFile(package) as archive:
        assert b"private-secret" not in archive.read("data.json")
        assert not any("settings" in name for name in archive.namelist())
    result = service.import_book_transfer("target", package)
    assert result["imported_cards"] == 1
    found = cards.search_cards("target", "猫")
    assert len(found) == 2
    imported = next(card for card in found if card["id"] != target_card["id"])
    assert imported["id"] != source_card["id"]
    assert imported["reps"] == 1
    assert imported["stability"] == source_card["stability"] or imported["stability"] == 2.4
    repeated = service.import_book_transfer("target", package)
    assert repeated["imported_cards"] == 0
    assert repeated["imported_learning_records"] == 0
    with sqlite3.connect(learning_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM review_logs WHERE user_id='target'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM learning_events WHERE user_id='target'").fetchone()[0] == 1
        assert connection.execute("SELECT active_seconds FROM daily_learning_stats WHERE user_id='target'").fetchone()[0] == 180


@pytest.mark.parametrize("selection", [["a"], ["a", "b"]])
def test_selected_book_share_keeps_analysis_and_excludes_private_data(tmp_path, monkeypatch, selection):
    library = tmp_path / "library.sqlite3"
    statements = [
        ("CREATE TABLE records(owner_user_id TEXT,table_name TEXT,record_key TEXT,payload TEXT,PRIMARY KEY(owner_user_id,table_name,record_key))", ()),
        ("CREATE TABLE user_library_items(user_id TEXT,book_id TEXT,metadata_json TEXT,created_at REAL,updated_at REAL,PRIMARY KEY(user_id,book_id))", ())]
    for owner, book in [("source", "a"), ("source", "b"), ("source", "c"), ("other", "foreign")]:
        resource = {"a": "a", "b": "b", "c": "c", "foreign": "d"}[book] * 20
        rows = [
            ("books", book, {"id": book, "title": book, "currentSentenceId": "private-position", "collectionName": "private-group", "coverUrl": f"/api/assets/{resource}/cover.png"}),
            ("chapters", f"ch-{book}", {"id": f"ch-{book}", "bookId": book, "status": "local-ready", "text": "猫。未翻译。"}),
            ("sentences", f"s-{book}", {"id": f"s-{book}", "chapter_id": f"ch-{book}", "original": "猫。", "translation_zh": "猫。", "explanation_detail": "full"}),
            ("sentences", f"pending-{book}", {"id": f"pending-{book}", "chapter_id": f"ch-{book}", "original": "未翻译。", "translation_zh": "", "explanation_status": "idle"}),
            ("tokens", f"t-{book}", {"id": f"t-{book}", "sentence_id": f"s-{book}", "surface": "猫", "lexemeKey": f"lex-{book}"}),
            ("contextSenses", f"t-{book}", {"token_id": f"t-{book}", "gloss_zh": "猫"}),
            ("annotations", f"ann-{book}", {"id": f"ann-{book}", "sentence_id": f"s-{book}", "structure": "名词句"}),
            ("lexemes", f"lex-{book}", {"key": f"lex-{book}", "senses_zh": ["猫"], "groups": ["private-group"]})]
        statements.extend(("INSERT INTO records VALUES(?,?,?,?)", (owner, table, key, json.dumps(value))) for table,key,value in rows)
        asset = tmp_path / "books" / resource / "cover.png"
        asset.parent.mkdir(parents=True); asset.write_bytes(book.encode())
    statements.append(("INSERT INTO records VALUES(?,?,?,?)", ("source", "lexemes", "unrelated", json.dumps({"key":"unrelated","senses_zh":["private-word"]}))))
    _database(library, statements)
    monkeypatch.setattr(service, "DATA_DIR", tmp_path)
    monkeypatch.setattr(service, "DATABASE_SPECS", {"library": {"path":library,"tables":{"records":"owner_user_id"}}})
    # Sharing must not read all account learning/preferences tables.
    monkeypatch.setattr(service, "_export_payload", lambda *_: (_ for _ in ()).throw(AssertionError("share reads private account data")))
    package = service.create_book_transfer("source", tmp_path / "share.zip", selection)
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        payload = json.loads(archive.read("data.json"))
        assert manifest["purpose"] == "book-share"
        assert manifest["book_count"] == len(selection)
        assert payload["learning"] == {}
        assert all(not rows for rows in payload["library_data"].values())
        assert "private-" not in archive.read("data.json").decode()
        assert {row["record_key"] for row in payload["records"] if row["table_name"]=="books"} == set(selection)
        assert not any('c'*20 in name or 'd'*20 in name for name in archive.namelist())
    result = service.import_book_transfer("target", package)
    assert result["imported_books"] == len(selection)
    assert result["imported_cards"] == 0
    assert result["imported_learning_records"] == 0
    with sqlite3.connect(library) as c:
        records = {(table,key):json.loads(encoded) for table,key,encoded in c.execute("SELECT table_name,record_key,payload FROM records WHERE owner_user_id='target'")}
    for book in selection:
        assert records[("sentences",f"s-{book}")]["translation_zh"] == "猫。"
        assert records[("sentences",f"pending-{book}")]["explanation_status"] == "idle"
        assert records[("chapters",f"ch-{book}")]["status"] == "local-ready"
        assert records[("contextSenses",f"t-{book}")]["gloss_zh"] == "猫"
        assert records[("annotations",f"ann-{book}")]["structure"] == "名词句"
    assert service.import_book_transfer("target", package)["skipped_books"] == len(selection)
    for invalid in [[], ["foreign"], ["a", "missing"]]:
        with pytest.raises(ValueError):
            service.create_book_transfer("source", tmp_path / "invalid.zip", invalid)

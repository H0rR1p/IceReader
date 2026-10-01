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
    with pytest.raises(ValueError, match="校验失败"):
        service.restore_backup("user-a", backup)

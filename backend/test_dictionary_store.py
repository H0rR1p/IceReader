import io
import json
import zipfile

from . import dictionary_store


def test_import_and_lookup_yomitan_dictionary(tmp_path, monkeypatch):
    monkeypatch.setattr(dictionary_store, "DICTIONARY_PATH", tmp_path / "dictionary.sqlite3")
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("index.json", json.dumps({"title": "测试日中词典"}, ensure_ascii=False))
        archive.writestr("term_bank_1.json", json.dumps([
            ["図書館", "としょかん", "", "", 0, ["图书馆"], 1, ""],
        ], ensure_ascii=False))

    result = dictionary_store.import_yomitan(payload.getvalue(), "dictionary.zip")
    entry = dictionary_store.lookup("図書館", "トショカン")

    assert result == {"source": "测试日中词典", "entries": 1}
    assert entry is not None
    assert entry["senses_zh"] == ["图书馆"]

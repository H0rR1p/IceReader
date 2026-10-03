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
            ["抱き付く", "だきつく", "", "", 0, ["抱住；搂住"], 2, ""],
        ], ensure_ascii=False))

    result = dictionary_store.import_yomitan(payload.getvalue(), "dictionary.zip")
    entry = dictionary_store.lookup("図書館", "トショカン")

    variant = dictionary_store.lookup("抱き着く", "ダキツク", "抱き着き")

    assert result == {"source": "测试日中词典", "entries": 2}
    assert entry is not None
    assert entry["senses_zh"] == ["图书馆"]
    assert variant is not None
    assert variant["senses_zh"] == ["抱住；搂住"]


def test_structured_glossary_excludes_examples_tags_and_attribution():
    glossary = {"type": "structured-content", "content": [
        {"tag": "span", "data": {"content": "part-of-speech-info"}, "content": "conjunction"},
        {"tag": "div", "data": {"content": "sense"}, "content": [
            {"tag": "ul", "data": {"content": "glossary"}, "content": [
                {"tag": "li", "content": ["然", {"tag": "span", "content": "后"}]},
                {"tag": "li", "content": "于是"}]},
            {"tag": "div", "data": {"content": "extra-info"}, "content": "我去京都。"}]},
        {"tag": "div", "data": {"content": "forms"}, "content": "しかして"},
        {"tag": "div", "data": {"content": "attribution"}, "content": "JMdict"}]}
    assert dictionary_store._plain_gloss([glossary]) == ["然后", "于是"]
    assert dictionary_store._plain_gloss({"type": "text", "text": "图书馆"}) == ["图书馆"]


def test_retired_builtin_removal_preserves_custom_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(dictionary_store, "DICTIONARY_PATH", tmp_path / "dictionary.sqlite3")
    monkeypatch.setattr(dictionary_store, "DATA_DIR", tmp_path)
    with dictionary_store._connect() as connection:
        connection.execute("INSERT INTO entries(lemma,reading,senses,source) VALUES('そして','そして','[]','builtin')")
        connection.execute("INSERT INTO entries(lemma,reading,senses,source) VALUES('猫','ねこ','[]','custom')")
        connection.execute("INSERT INTO dictionary_sources VALUES('builtin','greyindex/jitendex-yomitan-zh','v1','','',1,0,2)")
    dictionary_store.remove_retired_builtin()
    dictionary_store.remove_retired_builtin()
    with dictionary_store._connect() as connection:
        assert connection.execute("SELECT source FROM entries").fetchall() == [("custom",)]
        assert connection.execute("SELECT count(*) FROM dictionary_sources").fetchone()[0] == 0

"""The Android row adapter must match SQLite's duplicate-column semantics."""
import importlib.util
from pathlib import Path
import sqlite3

def test_joined_duplicate_columns_keep_first_name():
    spec = importlib.util.spec_from_file_location('mobile_sqlite', Path(__file__).resolve().parent.parent / 'mobile/python/mobile_sqlite.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    with sqlite3.connect(':memory:') as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute("SELECT 'card-id' AS id, 'note-id' AS id, '猫' AS lemma")
        reference = cursor.fetchone()
        row = module.Row([item[0] for item in cursor.description], list(reference))
        assert row['id'] == reference['id'] == 'card-id'
        assert row[1] == reference[1] == 'note-id'
        assert dict(row) == dict(reference)

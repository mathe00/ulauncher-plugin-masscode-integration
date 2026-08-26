"""
Tests for the V3 JSON snippet loader (src.database.loader.load_snippets_json).

Covers:
  - Happy path loading with isDeleted filtering
  - Missing file, invalid JSON, wrong root type
  - SQLite file passed to the V3 loader (type mismatch guard)
  - Path expansion (~)
"""

import json
import os
import sqlite3

import pytest

from src.database.loader import load_snippets_json
from tests.helpers import build_json_db, v3_json_snippet


class TestHappyPath:
    """Valid databases load correctly and filter deleted snippets."""

    def test_loads_active_snippets(self, tmp_path):
        db = build_json_db(
            tmp_path / "db.json",
            [
                v3_json_snippet(1, "alpha", "code a"),
                v3_json_snippet(2, "beta", "code b"),
            ],
        )
        snippets = load_snippets_json(db)

        assert len(snippets) == 2
        assert {s["name"] for s in snippets} == {"alpha", "beta"}

    def test_filters_deleted_snippets(self, tmp_path):
        db = build_json_db(
            tmp_path / "db.json",
            [
                v3_json_snippet(1, "alive", "keep me"),
                v3_json_snippet(2, "dead", "drop me", deleted=True),
            ],
        )
        snippets = load_snippets_json(db)

        assert [s["name"] for s in snippets] == ["alive"]

    def test_preserves_raw_content_and_fields(self, tmp_path):
        raw = v3_json_snippet(7, "full", "content!", folder_id=3)
        db = build_json_db(tmp_path / "db.json", [raw])
        snippets = load_snippets_json(db)

        assert snippets[0] == raw  # V3 loader returns entries verbatim

    def test_missing_snippets_key_yields_empty(self, tmp_path):
        path = tmp_path / "db.json"
        path.write_text(json.dumps({"folders": []}), encoding="utf-8")

        assert load_snippets_json(str(path)) == []

    def test_empty_snippets_list(self, tmp_path):
        db = build_json_db(tmp_path / "db.json", [])
        assert load_snippets_json(db) == []


class TestErrorHandling:
    """All failure modes must return [] instead of raising."""

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_snippets_json(str(tmp_path / "nope.json")) == []

    def test_invalid_json_returns_empty(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not valid json!!", encoding="utf-8")

        assert load_snippets_json(str(path)) == []

    def test_non_dict_root_returns_empty(self, tmp_path):
        # A JSON list has no .get() — the loader must swallow the AttributeError
        path = tmp_path / "list.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")

        assert load_snippets_json(str(path)) == []

    def test_sqlite_file_rejected_with_warning(self, tmp_path, caplog):
        # Build a genuine SQLite database and hand it to the V3 loader
        db_path = str(tmp_path / "massCode.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE t (x)")
        conn.commit()
        conn.close()

        with caplog.at_level("WARNING"):
            result = load_snippets_json(db_path)

        assert result == []
        assert any("SQLite" in m for m in caplog.messages)


class TestPathExpansion:
    """The loader must expand '~' before touching the filesystem."""

    def test_tilde_expanded_before_check(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        build_json_db(tmp_path / "db.json", [])

        # '~/db.json' resolves to <tmp_path>/db.json via HOME override
        result = load_snippets_json("~/db.json")
        assert result == []  # empty db, but no 'file not found' error path

        # Sanity: the expanded file DOES exist, so we hit the happy path
        expanded = os.path.expanduser("~/db.json")
        assert os.path.exists(expanded)

    def test_tilde_missing_file_still_safe(self):
        assert load_snippets_json("~/definitely/not/a/real/db.json") == []


class TestUnicode:
    """Non-ASCII content must survive a load round-trip."""

    def test_unicode_content_loaded_as_is(self, tmp_path):
        raw = v3_json_snippet(1, "café ☕", "print('héllo') 🚀")
        db = build_json_db(tmp_path / "db.json", [raw])

        snippets = load_snippets_json(db)
        assert snippets[0]["name"] == "café ☕"
        assert snippets[0]["content"] == "print('héllo') 🚀"


@pytest.mark.parametrize("snippet_key", ["isDeleted"])
class TestIsDeletedVariants:
    """The isDeleted flag may be absent, false, or true."""

    def test_absent_isdeleted_kept(self, tmp_path, snippet_key):
        entry = {"id": 1, "name": "x", "content": "y"}  # no isDeleted key at all
        db = build_json_db(tmp_path / "db.json", [entry])

        assert len(load_snippets_json(db)) == 1

    def test_false_string_flag_dropped_only_when_truthy(self, tmp_path, snippet_key):
        # Only boolean True drops; anything else (0, "", None) is kept
        for flag_value, expected_count in [
            (0, 1),
            ("", 1),
            (None, 1),
            (False, 1),
            (True, 0),
        ]:
            entry = {"id": 1, "name": "x", "content": "y", snippet_key: flag_value}
            db = build_json_db(tmp_path / "db.json", [entry])
            assert len(load_snippets_json(db)) == expected_count, (
                f"isDeleted={flag_value!r} should yield {expected_count} snippet(s)"
            )

"""
Tests for the V4 SQLite snippet loader (src.database.loader.load_snippets_sqlite).

Covers:
  - Happy path with real temp SQLite databases
  - Single-fragment → string content, multi-fragment → list content
  - Folder name join, description/favorites metadata attachment
  - Deleted snippet exclusion, empty-content handling
  - Type mismatch (JSON file given to V4 loader), corruption, missing file
  - Fragment ordering (SQL ORDER BY label)
"""

import json

from src.database.loader import load_snippets_sqlite
from tests.helpers import SqliteDbBuilder, build_json_db


class TestHappyPath:
    """Standard databases load into V3-compatible dicts."""

    def test_single_fragment_content_is_string(self, tmp_path):
        db = (
            SqliteDbBuilder(tmp_path)
            .add_snippet(1, "greet", contents=[("Fragment 1", "hello()", "python")])
            .build()
        )
        snippets = load_snippets_sqlite(db.path)

        assert len(snippets) == 1
        assert snippets[0]["content"] == "hello()"
        assert isinstance(snippets[0]["content"], str)

    def test_multi_fragment_content_preserved_as_list(self, tmp_path):
        db = (
            SqliteDbBuilder(tmp_path)
            .add_snippet(
                1,
                "multi",
                contents=[
                    ("Fragment 1", "alpha", "python"),
                    ("Fragment 2", "beta", "bash"),
                ],
            )
            .build()
        )
        snippets = load_snippets_sqlite(db.path)
        content = snippets[0]["content"]

        assert isinstance(content, list)
        assert content == [
            {"label": "Fragment 1", "value": "alpha", "language": "python"},
            {"label": "Fragment 2", "value": "beta", "language": "bash"},
        ]

    def test_fragments_ordered_by_label_alphabetically(self, tmp_path):
        # Insert labels out of alphabetical order; SQL orders by sc.label
        db = (
            SqliteDbBuilder(tmp_path)
            .add_snippet(
                1,
                "ordered",
                contents=[
                    ("Zeta", "last?", "txt"),
                    ("Alpha", "first!", "txt"),
                    ("Mid", "middle", "txt"),
                ],
            )
            .build()
        )
        content = load_snippets_sqlite(db.path)[0]["content"]
        labels = [f["label"] for f in content]

        assert labels == ["Alpha", "Mid", "Zeta"]

    def test_deleted_snippets_excluded(self, tmp_path):
        db = (
            SqliteDbBuilder(tmp_path)
            .add_snippet(1, "alive", contents=[("F", "a", "txt")])
            .add_snippet(2, "dead", deleted=True, contents=[("F", "b", "txt")])
            .build()
        )
        names = [s["name"] for s in load_snippets_sqlite(db.path)]

        assert names == ["alive"]

    def test_no_contents_yields_empty_string_content(self, tmp_path):
        db = SqliteDbBuilder(tmp_path).add_snippet(1, "hollow").build()
        snippets = load_snippets_sqlite(db.path)

        assert snippets[0]["content"] == ""

    def test_null_value_row_skipped_but_snippet_kept(self, tmp_path):
        # A content row whose value is NULL must not crash or produce a fragment
        import sqlite3

        db = SqliteDbBuilder(tmp_path).add_snippet(1, "nullish").build()
        conn = sqlite3.connect(db.path)
        conn.execute(
            "INSERT INTO snippet_contents (snippetId, label, value, language)"
            " VALUES (1, 'Fragment 1', NULL, 'txt')"
        )
        conn.commit()
        conn.close()

        snippets = load_snippets_sqlite(db.path)
        assert len(snippets) == 1
        assert snippets[0]["content"] == ""


class TestMetadataAttachment:
    """V4 metadata is attached as underscore-prefixed optional keys."""

    def test_folder_name_joined(self, tmp_path):
        db = (
            SqliteDbBuilder(tmp_path)
            .add_folder(5, "Scripts")
            .add_snippet(1, "tool", folder_id=5, contents=[("F", "code", "py")])
            .build()
        )
        snippets = load_snippets_sqlite(db.path)

        assert snippets[0]["_folder"] == "Scripts"

    def test_description_attached_when_present(self, tmp_path):
        db = (
            SqliteDbBuilder(tmp_path)
            .add_snippet(
                1,
                "doc",
                description="A useful tool",
                contents=[("F", "x", "txt")],
            )
            .build()
        )

        assert load_snippets_sqlite(db.path)[0]["_description"] == "A useful tool"

    def test_no_description_means_no_key(self, tmp_path):
        db = (
            SqliteDbBuilder(tmp_path)
            .add_snippet(1, "bare", contents=[("F", "x", "txt")])
            .build()
        )
        snippets = load_snippets_sqlite(db.path)

        assert "_description" not in snippets[0]

    def test_favorites_flag_attached_only_when_true(self, tmp_path):
        db = (
            SqliteDbBuilder(tmp_path)
            .add_snippet(1, "fav", favorites=True, contents=[("F", "x", "t")])
            .add_snippet(2, "plain", contents=[("F", "y", "t")])
            .build()
        )
        snippets = {s["name"]: s for s in load_snippets_sqlite(db.path)}

        assert snippets["fav"]["_isFavorites"] is True
        assert "_isFavorites" not in snippets["plain"]

    def test_unnamed_snippet_gets_placeholder_name(self, tmp_path):
        import sqlite3

        db = SqliteDbBuilder(tmp_path).add_snippet(1, "placeholder").build()
        conn = sqlite3.connect(db.path)
        conn.execute("UPDATE snippets SET name = NULL WHERE id = 1")
        conn.commit()
        conn.close()

        assert load_snippets_sqlite(db.path)[0]["name"] == "Unnamed"

    def test_base_keys_always_present(self, tmp_path):
        db = (
            SqliteDbBuilder(tmp_path)
            .add_snippet(1, "any", contents=[("F", "x", "t")])
            .build()
        )
        snippet = load_snippets_sqlite(db.path)[0]

        assert set(snippet) >= {"name", "content", "isDeleted"}
        assert snippet["isDeleted"] is False


class TestErrorHandling:
    """Failure modes return [] instead of raising."""

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_snippets_sqlite(str(tmp_path / "ghost.db")) == []

    def test_json_file_rejected_with_warning(self, tmp_path, caplog):
        path = build_json_db(tmp_path / "actually-json.db", [])

        with caplog.at_level("WARNING"):
            result = load_snippets_sqlite(path)

        assert result == []
        assert any("JSON" in m for m in caplog.messages)

    def test_corrupt_sqlite_returns_empty(self, tmp_path):
        path = str(tmp_path / "corrupt.db")
        with open(path, "wb") as f:
            f.write(b"SQLite format 3\x00" + b"\xff garbage" * 100)

        result = load_snippets_sqlite(path)
        assert result == []

    def test_nonexistent_table_returns_empty(self, tmp_path):
        # Valid sqlite file but no MassCode schema
        import sqlite3

        path = str(tmp_path / "empty-schema.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE unrelated (x)")
        conn.commit()
        conn.close()

        assert load_snippets_sqlite(path) == []

    def test_directory_instead_of_file_returns_empty(self, tmp_path):
        dir_path = str(tmp_path / "i-am-a-dir")
        import os

        os.mkdir(dir_path)

        assert load_snippets_sqlite(dir_path) == []


class TestMultipleSnippets:
    """Grouping of several snippets and fragments stays consistent."""

    def test_multiple_snippets_each_grouped_correctly(self, tmp_path):
        db = (
            SqliteDbBuilder(tmp_path)
            .add_snippet(1, "one", contents=[("F", "a", "t")])
            .add_snippet(2, "two", contents=[("F1", "b", "t"), ("F2", "c", "t")])
            .add_snippet(3, "three", contents=[("F", "d", "t")])
            .build()
        )
        by_name = {s["name"]: s for s in load_snippets_sqlite(db.path)}

        assert isinstance(by_name["one"]["content"], str)
        assert isinstance(by_name["two"]["content"], list)
        assert len(by_name["two"]["content"]) == 2
        assert isinstance(by_name["three"]["content"], str)

    def test_unicode_names_and_content(self, tmp_path):
        db = (
            SqliteDbBuilder(tmp_path)
            .add_snippet(1, "café ☕", contents=[("Fr", "print('héllo 🚀')", "py")])
            .build()
        )
        snippet = load_snippets_sqlite(db.path)[0]

        assert snippet["name"] == "café ☕"
        assert snippet["content"] == "print('héllo 🚀')"

    def test_load_preserves_table_contents(self, tmp_path):
        """
        Loading must not mutate the database contents.

        NOTE: we compare ROW DATA rather than file mtime — opening a SQLite
        db in WAL mode checkpoints on last-connection close, which rewrites
        the main file (bumping its mtime) without changing any data.
        """
        import sqlite3

        db = (
            SqliteDbBuilder(tmp_path)
            .add_snippet(1, "x", contents=[("F", "y", "t")])
            .build()
        )

        def snapshot():
            conn = sqlite3.connect(db.path)
            rows = conn.execute("SELECT * FROM snippets ORDER BY id").fetchall()
            contents = conn.execute(
                "SELECT * FROM snippet_contents ORDER BY id"
            ).fetchall()
            conn.close()
            return rows, contents

        before = snapshot()
        load_snippets_sqlite(db.path)

        assert snapshot() == before


class TestJsonRoundTripShape:
    """The produced dicts must be JSON-serializable (Ulauncher IPC contract)."""

    def test_loaded_snippets_serializable(self, tmp_path):
        db = (
            SqliteDbBuilder(tmp_path)
            .add_folder(2, "Folder")
            .add_snippet(
                1,
                "s",
                description="d",
                folder_id=2,
                favorites=True,
                contents=[("F1", "a", "t"), ("F2", "b", "t")],
            )
            .build()
        )
        snippets = load_snippets_sqlite(db.path)

        serialized = json.dumps(snippets)  # must not raise
        assert "s" in serialized

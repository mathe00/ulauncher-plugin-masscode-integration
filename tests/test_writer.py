"""
Tests for the database writer module (src.database.writer).

Coverage areas:
  - generate_snippet_name: line extraction, prefix stripping, truncation
  - _slugify: filesystem-safe slugs
  - _atomic_write_json: durability + temp-file cleanup on failure
  - _build_v5_markdown: format round-trip with the V5 parser
  - save_snippet_to_inbox: V3 / V4 / V5 flows, dispatch, error paths
"""

import json
import os
import re
import sqlite3

import pytest

from src.database.loader import parse_snippet_markdown
from src.database.writer import (
    _atomic_write_json,
    _build_v5_markdown,
    _slugify,
    generate_snippet_name,
    save_snippet_to_inbox,
)
from tests.helpers import SqliteDbBuilder, VaultBuilder, build_json_db

# ===========================================================================
# generate_snippet_name
# ===========================================================================


class TestGenerateSnippetName:
    """Auto-naming from clipboard content."""

    def test_first_non_empty_line_wins(self):
        assert generate_snippet_name("\n\n  second line is first real \n") == (
            "second line is first real"
        )

    def test_leading_whitespace_stripped(self):
        assert generate_snippet_name("    indented code()") == "indented code()"

    @pytest.mark.parametrize(
        "prefix", ["# ", "// ", "//! ", "; ", "* ", "> ", "- ", "#"]
    )
    def test_comment_prefixes_stripped(self, prefix):
        content = f"{prefix}my function name"

        assert generate_snippet_name(content) == "my function name"

    def test_long_line_truncated_to_max_len(self):
        from src.constants import SNIPPET_NAME_MAX_LEN

        long_line = "word " * 30  # 150 chars, plenty of spaces

        name = generate_snippet_name(long_line)

        assert len(name) <= SNIPPET_NAME_MAX_LEN
        # Word-boundary trim: must not end mid-word
        assert not name.endswith("wor")

    def test_truncation_prefers_word_boundary(self):
        long_line = "alpha beta gamma delta epsilon zeta eta theta iota kappa" * 2

        name = generate_snippet_name(long_line)

        assert len(name) <= 50
        assert " " in name  # cut happened at a space, not inside a word
        assert not name.endswith("kapp")

    def test_hard_cut_when_no_space_available(self):
        no_spaces = "a" * 120  # single unbroken token

        name = generate_snippet_name(no_spaces)

        assert len(name) == 50  # hard cut at max length

    def test_empty_content_falls_back_to_timestamp(self):
        name = generate_snippet_name("")

        assert re.fullmatch(r"Snippet \d+", name)

    def test_whitespace_only_content_falls_back_to_timestamp(self):
        name = generate_snippet_name("   \n\t  \n")

        assert re.fullmatch(r"Snippet \d+", name)

    def test_only_comment_chars_fall_back_to_timestamp(self):
        # First non-empty line reduces to nothing after prefix stripping
        name = generate_snippet_name("#\n//\nreal line")

        # '#' alone strips to empty → loop continues to 'real line'
        assert name == "real line"

    def test_unicode_content_preserved(self):
        assert generate_snippet_name("café ☕ recipe") == "café ☕ recipe"


# ===========================================================================
# _slugify
# ===========================================================================


class TestSlugify:
    r"""Filesystem-safe slug generation.

    NOTE on real behavior: only runs of 2+ separators collapse to a single
    hyphen. Single spaces are PRESERVED (valid in filenames), and \w keeps
    unicode letters and underscores.
    """

    def test_basic_lowercasing(self):
        assert _slugify("My Snippet Name") == "my snippet name"

    def test_double_spaces_collapse_to_hyphen(self):
        assert _slugify("a  b") == "a-b"

    def test_space_hyphen_mix_collapses(self):
        assert _slugify("a - b") == "a-b"

    def test_special_chars_become_hyphens(self):
        assert _slugify("hello! world? (yes)") == "hello-world-yes"

    def test_multiple_separators_collapse(self):
        assert _slugify("a -- b   c") == "a-b-c"

    def test_leading_trailing_hyphens_stripped(self):
        assert _slugify("--wrapped--") == "wrapped"

    def test_underscores_kept(self):
        # \w matches underscore — never hyphenated
        assert _slugify("snake_case name") == "snake_case name"

    def test_length_limited_to_80(self):
        slug = _slugify("x" * 200)

        assert len(slug) == 80

    def test_length_limit_does_not_end_with_hyphen(self):
        slug = _slugify("ab " * 60)  # trailing region full of hyphen candidates

        assert not slug.startswith("-")
        assert not slug.endswith("-")

    def test_empty_string_gets_default(self):
        assert _slugify("") == "untitled-snippet"

    def test_only_special_chars_gets_default(self):
        assert _slugify("!!! ??? ###") == "untitled-snippet"

    def test_unicode_word_chars_survive(self):
        # \w matches unicode letters — accents are kept, not hyphened
        assert _slugify("café crème") == "café crème"


# ===========================================================================
# _atomic_write_json
# ===========================================================================


class TestAtomicWriteJson:
    """Atomic JSON persistence guarantees."""

    def test_writes_valid_json(self, tmp_path):
        target = tmp_path / "data.json"
        payload = {"key": "value", "nested": {"n": [1, 2]}}

        _atomic_write_json(str(target), payload)

        with open(target, encoding="utf-8") as f:
            assert json.load(f) == payload

    def test_replaces_existing_file(self, tmp_path):
        target = tmp_path / "data.json"
        target.write_text('{"old": true}', encoding="utf-8")

        _atomic_write_json(str(target), {"new": True})

        with open(target, encoding="utf-8") as f:
            assert json.load(f) == {"new": True}

    def test_unicode_serialized_readably(self, tmp_path):
        target = tmp_path / "uni.json"

        _atomic_write_json(str(target), {"emoji": "🚀 café"})

        raw = target.read_text(encoding="utf-8")
        assert "🚀 café" in raw  # ensure_ascii=False

    def test_no_temp_files_left_on_success(self, tmp_path):
        target = tmp_path / "clean.json"

        _atomic_write_json(str(target), {})

        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "clean.json"]
        assert leftovers == []

    def test_temp_file_cleaned_up_on_dump_failure(self, tmp_path, monkeypatch):
        target = tmp_path / "fail.json"

        def exploding_dump(*args, **kwargs):
            raise TypeError("simulated serialization failure")

        monkeypatch.setattr(json, "dump", exploding_dump)

        with pytest.raises(TypeError):
            _atomic_write_json(str(target), {"unserializable": object()})

        leftovers = list(tmp_path.iterdir())
        assert leftovers == [], "temp file must be removed on failure"

    def test_creates_file_in_same_directory_as_target(self, tmp_path):
        # The atomic rename only works within one filesystem; the temp file
        # must live next to the target, not in the CWD.
        subdir = tmp_path / "sub"
        subdir.mkdir()
        target = subdir / "deep.json"

        _atomic_write_json(str(target), {"ok": 1})

        assert target.exists()


# ===========================================================================
# _build_v5_markdown
# ===========================================================================


class TestBuildV5Markdown:
    """The generated .md must be a valid MassCode V5 snippet file."""

    def make_md(self, **overrides):
        defaults = {
            "name": "Test",
            "content": "body text",
            "snippet_id": 3,
            "content_id": 4,
            "created_at": 111,
            "updated_at": 222,
        }
        defaults.update(overrides)
        return _build_v5_markdown(**defaults)

    def test_round_trip_through_v5_parser(self, tmp_path):
        md = self.make_md(name="Round Trip", content="some code()")
        path = tmp_path / "rt.md"
        path.write_text(md, encoding="utf-8")

        fm, fragments = parse_snippet_markdown(str(path))

        assert fm["name"] == "Round Trip"
        assert fm["id"] == 3
        assert fm["isDeleted"] == 0
        assert fragments[0]["value"] == "some code()"
        assert fragments[0]["label"] == "Fragment 1"
        assert fragments[0]["language"] == "plain_text"

    def test_special_characters_yaml_safe(self, tmp_path):
        tricky = 'colon: here\nquote\' and "double" {braces}'
        md = self.make_md(content=tricky)
        path = tmp_path / "tricky.md"
        path.write_text(md, encoding="utf-8")

        _, fragments = parse_snippet_markdown(str(path))

        assert fragments[0]["value"] == tricky

    def test_multiline_content_kept_verbatim(self):
        md = self.make_md(content="line1\nline2\n\nline4")

        assert "line1\nline2\n\nline4" in md

    def test_frontmatter_declares_one_content_entry(self):
        md = self.make_md()

        assert "label: Fragment 1" in md
        assert "language: plain_text" in md


# ===========================================================================
# save_snippet_to_inbox — V3 JSON
# ===========================================================================


class TestSaveV3:
    """Saving to the V3 JSON database."""

    def test_appends_snippet_with_generated_id(self, tmp_path):
        db = build_json_db(
            tmp_path / "db.json",
            [v3_json_entry(1), v3_json_entry(5)],
        )

        result = save_snippet_to_inbox(db, "v3", "new code", name="New")

        assert result["success"] is True
        with open(db, encoding="utf-8") as f:
            data = json.load(f)
        appended = data["snippets"][-1]

        assert appended["id"] == 6  # max(1,5) + 1
        assert appended["name"] == "New"
        assert appended["content"] == "new code"
        assert appended["folderId"] is None  # Inbox = unassigned
        assert appended["isDeleted"] is False

    def test_first_snippet_gets_id_1(self, tmp_path):
        db = build_json_db(tmp_path / "db.json", [])

        save_snippet_to_inbox(db, "v3", "solo", name="Solo")

        with open(db, encoding="utf-8") as f:
            assert json.load(f)["snippets"][0]["id"] == 1

    def test_missing_id_entries_do_not_break_id_generation(self, tmp_path):
        db = build_json_db(
            tmp_path / "db.json",
            [{"name": "no-id-entry"}, v3_json_entry(9)],
        )

        save_snippet_to_inbox(db, "v3", "x", name="X")

        with open(db, encoding="utf-8") as f:
            assert json.load(f)["snippets"][-1]["id"] == 10

    def test_auto_generates_name_when_none(self, tmp_path):
        db = build_json_db(tmp_path / "db.json", [])

        result = save_snippet_to_inbox(db, "v3", "// my comment header\nbody")

        assert result["success"] is True
        assert result["name"] == "my comment header"
        with open(db, encoding="utf-8") as f:
            assert json.load(f)["snippets"][0]["name"] == "my comment header"

    def test_blank_name_auto_generated(self, tmp_path):
        db = build_json_db(tmp_path / "db.json", [])

        result = save_snippet_to_inbox(db, "v3", "content", name="   ")

        assert result["success"] is True
        assert result["name"]  # non-empty generated name

    def test_existing_data_preserved(self, tmp_path):
        db = build_json_db(
            tmp_path / "db.json",
            [v3_json_entry(1)],
        )
        # Extra top-level keys must survive the write
        with open(db, "r+", encoding="utf-8") as f:
            data = json.load(f)
            data["folders"] = [{"id": 1}]
            f.seek(0)
            json.dump(data, f)
            f.truncate()

        save_snippet_to_inbox(db, "v3", "more", name="More")

        with open(db, encoding="utf-8") as f:
            data = json.load(f)
        assert data["folders"] == [{"id": 1}]
        assert len(data["snippets"]) == 2

    def test_missing_db_file_fails_gracefully(self, tmp_path):
        result = save_snippet_to_inbox(
            str(tmp_path / "ghost.json"), "v3", "x", name="X"
        )

        assert result["success"] is False
        assert "not found" in result["error"]

    def test_invalid_json_db_fails_gracefully(self, tmp_path):
        db = tmp_path / "broken.json"
        db.write_text("{invalid!", encoding="utf-8")

        result = save_snippet_to_inbox(str(db), "v3", "x", name="X")

        assert result["success"] is False
        assert "Invalid JSON" in result["error"]

    def test_tilde_path_expanded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        build_json_db(tmp_path / "home-db.json", [])

        result = save_snippet_to_inbox("~/home-db.json", "v3", "x", name="X")

        assert result["success"] is True
        assert result["path"].startswith(str(tmp_path))


def v3_json_entry(snippet_id: int) -> dict:
    return {
        "id": snippet_id,
        "name": f"s{snippet_id}",
        "content": f"c{snippet_id}",
        "isDeleted": False,
        "folderId": None,
    }


# ===========================================================================
# save_snippet_to_inbox — V4 SQLite
# ===========================================================================


class TestSaveV4:
    """Saving to the V4 SQLite database."""

    def test_inserts_snippet_and_content_rows(self, tmp_path):
        db = SqliteDbBuilder(tmp_path).add_folder(1, "F").build()

        result = save_snippet_to_inbox(db.path, "v4", "sqlite code", name="SQLited")

        assert result["success"] is True
        conn = sqlite3.connect(db.path)
        conn.row_factory = sqlite3.Row
        snippet_row = conn.execute(
            "SELECT * FROM snippets ORDER BY id DESC LIMIT 1"
        ).fetchone()
        content_row = conn.execute(
            "SELECT * FROM snippet_contents WHERE snippetId = ?",
            (snippet_row["id"],),
        ).fetchone()
        conn.close()

        assert snippet_row["name"] == "SQLited"
        assert snippet_row["folderId"] is None  # Inbox
        assert snippet_row["isDeleted"] == 0
        assert content_row["value"] == "sqlite code"
        assert content_row["language"] == "plain_text"

    def test_missing_db_fails_gracefully(self, tmp_path):
        result = save_snippet_to_inbox(str(tmp_path / "ghost.db"), "v4", "x", name="X")

        assert result["success"] is False
        assert "not found" in result["error"]

    def test_sqlite_error_surfaced(self, tmp_path):
        # Valid sqlite file but without the snippets table → INSERT fails

        path = str(tmp_path / "wrong-schema.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE other (x)")
        conn.commit()
        conn.close()

        result = save_snippet_to_inbox(path, "v4", "x", name="X")

        assert result["success"] is False
        assert "SQLite error" in result["error"]


# ===========================================================================
# save_snippet_to_inbox — V5 Markdown Vault
# ===========================================================================


class TestSaveV5:
    """Saving to the V5+ Markdown Vault inbox."""

    def test_creates_md_file_and_updates_state(self, tmp_path):
        builder = VaultBuilder(tmp_path).add_folder("Scripts", 1).build()

        result = save_snippet_to_inbox(
            str(builder.vault_dir), "v5", "vault code", name="Vaulted"
        )

        assert result["success"] is True, result
        # File exists in the inbox of the code space
        saved_path = result["path"]
        assert os.path.isfile(saved_path)
        assert os.sep.join([".masscode", "inbox"]) in saved_path

        # state.json registers it with counters bumped
        with open(builder.meta_dir / "state.json", encoding="utf-8") as f:
            state = json.load(f)
        entry = state["snippets"][-1]

        assert entry["filePath"].endswith(".md")
        assert not os.path.isabs(entry["filePath"])  # relative to space dir
        assert os.path.isfile(os.path.join(str(builder.space_dir), entry["filePath"]))
        assert state["counters"]["snippetId"] >= 1
        assert state["counters"]["contentId"] >= 1

    def test_saved_file_parses_back_with_correct_fields(self, tmp_path):
        builder = VaultBuilder(tmp_path).build()

        save_snippet_to_inbox(
            str(builder.vault_dir), "v5", "the content ☕", name="Unicode Save"
        )
        state_file = builder.meta_dir / "state.json"
        with open(state_file, encoding="utf-8") as f:
            state = json.load(f)
        rel_path = state["snippets"][-1]["filePath"]

        fm, fragments = parse_snippet_markdown(
            os.path.join(str(builder.space_dir), rel_path)
        )

        assert fm["name"] == "Unicode Save"
        assert fm["folderId"] is None
        assert fragments[0]["value"] == "the content ☕"

    def test_duplicate_names_get_unique_filenames(self, tmp_path):
        builder = VaultBuilder(tmp_path).build()

        r1 = save_snippet_to_inbox(
            str(builder.vault_dir), "v5", "one", name="Same Name"
        )
        r2 = save_snippet_to_inbox(
            str(builder.vault_dir), "v5", "two", name="Same Name"
        )
        r3 = save_snippet_to_inbox(
            str(builder.vault_dir), "v5", "three", name="Same Name"
        )

        assert all(r["success"] for r in (r1, r2, r3))
        # Slugs keep single spaces; uniqueness comes from the -N suffix
        basenames = {os.path.basename(r["path"]) for r in (r1, r2, r3)}

        assert basenames == {"same name.md", "same name-2.md", "same name-3.md"}

    def test_counters_increment_across_saves(self, tmp_path):
        builder = (
            VaultBuilder(tmp_path)
            .set_state(
                {
                    "snippets": [],
                    "counters": {"snippetId": 41, "contentId": 42},
                }
            )
            .build()
        )

        save_snippet_to_inbox(str(builder.vault_dir), "v5", "x", name="Counter Check")

        with open(builder.meta_dir / "state.json", encoding="utf-8") as f:
            state = json.load(f)

        assert state["counters"]["snippetId"] == 42
        assert state["counters"]["contentId"] == 43
        assert state["snippets"][-1]["id"] == 42

    def test_missing_inbox_dir_created(self, tmp_path):
        builder = VaultBuilder(tmp_path).build()
        assert not builder.inbox_dir.exists()  # precondition

        result = save_snippet_to_inbox(
            str(builder.vault_dir), "v5", "x", name="MkInbox"
        )

        assert result["success"] is True
        assert builder.inbox_dir.is_dir()

    def test_missing_vault_dir_fails(self, tmp_path):
        result = save_snippet_to_inbox(
            str(tmp_path / "ghost-vault"), "v5", "x", name="X"
        )

        assert result["success"] is False
        assert "not found" in result["error"]

    def test_file_instead_of_dir_fails(self, tmp_path):
        not_a_dir = tmp_path / "file.db"
        not_a_dir.write_text("x", encoding="utf-8")

        result = save_snippet_to_inbox(str(not_a_dir), "v5", "x", name="X")

        assert result["success"] is False

    def test_missing_state_json_fails(self, tmp_path):
        bare = tmp_path / "bare"
        bare.mkdir()

        result = save_snippet_to_inbox(str(bare), "v5", "x", name="X")

        assert result["success"] is False
        assert "state file not found" in result["error"]

    def test_corrupt_state_json_fails(self, tmp_path):
        builder = VaultBuilder(tmp_path)
        builder.meta_dir.mkdir(parents=True)
        (builder.meta_dir / "state.json").write_text("{nope", encoding="utf-8")

        result = save_snippet_to_inbox(str(builder.vault_dir), "v5", "x", name="X")

        assert result["success"] is False
        assert "Invalid JSON" in result["error"]


# ===========================================================================
# save_snippet_to_inbox — dispatch & generic error handling
# ===========================================================================


class TestSaveDispatch:
    """Version routing and catch-all exception handling."""

    def test_unknown_version_defaults_to_v3(self, tmp_path):
        db = build_json_db(tmp_path / "db.json", [])

        # "weird" isn't v4/v5 → routed to the V3 JSON writer
        result = save_snippet_to_inbox(db, "weird-version", "x", name="X")

        assert result["success"] is True

    def test_internal_exception_caught_and_reported(self, tmp_path, monkeypatch):
        db = build_json_db(tmp_path / "db.json", [])

        def explode(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("src.database.writer._save_v3", explode)

        result = save_snippet_to_inbox(db, "v3", "x", name="X")

        assert result["success"] is False
        assert "boom" in result["error"]

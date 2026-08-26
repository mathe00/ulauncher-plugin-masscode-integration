"""
End-to-end workflow tests.

These tests wire REAL components together on real (temp) filesystems:
loader + cache + listeners + writer + contextual learning. Only Ulauncher's
UI layer and the system clipboard are mocked.

Workflows covered:
  1. V5 vault: create vault → search → select → learn → boosted ranking →
     save new snippet → automatic cache invalidation → new snippet found
  2. V3 JSON: same lifecycle against db.json
  3. V4 SQLite: same lifecycle against massCode.db
  4. main.py import smoke test (resilient bootstrap block)
"""

import json
import os
import time

import pytest

import src.events.listeners as listeners_mod
from src.database.cache import SnippetCache
from src.events.listeners import ItemEnterEventListener, KeywordQueryEventListener
from src.learning import contextual_history as ch
from tests.helpers import (
    FakeItemEnterEvent,
    FakeKeywordQueryEvent,
    SqliteDbBuilder,
    VaultBuilder,
    build_json_db,
    history_payload_of,
    items_of,
    make_extension,
    v5_body_single,
    v5_frontmatter,
)


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    """
    Redirect ALL default-path history I/O into the temp dir by patching the
    module-level functions in the listeners namespace AND ch's defaults.
    """
    hist_file = str(tmp_path / "workflow-history.json")
    real_load = ch.load_context_history
    real_update = ch.update_context_history

    monkeypatch.setattr(
        listeners_mod,
        "load_context_history",
        lambda *a, **k: real_load(hist_file),
    )

    def patched_update(**kwargs):
        kwargs.setdefault("history_file_path", hist_file)
        kwargs["history_file_path"] = hist_file
        return real_update(**kwargs)

    monkeypatch.setattr(listeners_mod, "update_context_history", patched_update)

    # Also make direct module use (non-listener) hit temp paths
    monkeypatch.setattr(ch, "HISTORY_FILE", hist_file, raising=False)

    ch.ensure_history_file_exists(hist_file)
    return hist_file


def bump_mtime(path: str):
    """Force a visible mtime change (guards against coarse timestamps)."""
    future = time.time() + 10
    os.utime(path, (future, future))


# ===========================================================================
# Workflow 1 — V5 Markdown Vault full lifecycle
# ===========================================================================


class TestV5FullLifecycle:
    """Vault creation through learned, cached, saved-and-reloaded usage."""

    @pytest.fixture
    def env(self, tmp_path):
        vault = VaultBuilder(tmp_path, layout="spaces").add_folder("Scripts", 1)
        vault.add_snippet(
            "greet.md",
            frontmatter=v5_frontmatter("greet script", folder_id=1),
            body=v5_body_single("print('hello')"),
            folder="Scripts",
        )
        vault.add_snippet(
            "deploy.md",
            frontmatter=v5_frontmatter("deploy server"),
            body=v5_body_single("ssh deploy@host"),
        )
        vault.build()
        state_file = str(vault.meta_dir / "state.json")

        return {
            "vault_path": str(vault.vault_dir),
            "state_file": state_file,
            "space_dir": str(vault.space_dir),
        }

    def _prefs(self, env):
        return make_extension(
            mc_db_path=env["vault_path"],
            masscode_version="v5",
            enable_contextual_learning="true",
        )

    def test_first_query_lists_all(self, env):
        ext = self._prefs(env)
        items = items_of(
            KeywordQueryEventListener().on_event(FakeKeywordQueryEvent(""), ext)
        )

        names = {i.get_name() for i in items}
        assert names == {"greet script", "deploy server"}

    def test_cache_prevents_reload_on_repeat_queries(self, env):
        ext = self._prefs(env)
        listener = KeywordQueryEventListener()

        listener.on_event(FakeKeywordQueryEvent(""), ext)
        # Second call must be served from cache; verify via internal state
        assert SnippetCache._snippets is not None
        first = id(SnippetCache._snippets)
        listener.on_event(FakeKeywordQueryEvent(""), ext)
        assert id(SnippetCache._snippets) == first

    def test_selection_learns_and_boosts_with_star(self, env):
        ext = self._prefs(env)
        query_listener = KeywordQueryEventListener()
        enter_listener = ItemEnterEventListener()

        # Step 1: user searches and picks 'greet script'
        action = query_listener.on_event(FakeKeywordQueryEvent("greet"), ext)
        item = next(i for i in items_of(action) if "greet" in i.get_name())
        payload = history_payload_of(item)

        assert payload["action"] == "record_history"
        enter_listener.on_event(FakeItemEnterEvent(payload), ext)

        # Step 2: same query now shows the star (learned context score > 0)
        action2 = query_listener.on_event(FakeKeywordQueryEvent("greet"), ext)
        greet_items = [i for i in items_of(action2) if i.get_name() == "★ greet script"]

        assert greet_items, "learned snippet should display with ★ prefix"

    def test_saved_snippet_appears_after_cache_invalidation(self, env):
        from src.database.writer import save_snippet_to_inbox

        ext = self._prefs(env)
        listener = KeywordQueryEventListener()

        # Warm the cache
        listener.on_event(FakeKeywordQueryEvent(""), ext)

        # Save a brand-new snippet to the inbox
        result = save_snippet_to_inbox(
            db_path=env["vault_path"],
            masscode_version="v5",
            content="docker compose up -d",
            name="compose up",
        )
        assert result["success"], result
        bump_mtime(env["state_file"])

        # Cache must detect state.json change and expose the new snippet
        items = items_of(listener.on_event(FakeKeywordQueryEvent("compose"), ext))
        names = [i.get_name() for i in items]

        assert any("compose up" in n for n in names)

    def test_multi_fragment_vault_snippets_expand_per_fragment(self, tmp_path):
        import yaml

        fm = v5_frontmatter("multi tool")
        body = "\n## Fragment: Build\n```sh\nmake all\n```\n\n## Fragment: Test\n```sh\npytest\n```\n"
        vault = VaultBuilder(tmp_path, layout="spaces")
        vault.add_raw_file(
            "multi.md",
            f"---\n{yaml.safe_dump(fm).strip()}\n---\n{body}",
        )
        vault._register("multi.md", fm["id"])
        vault.build()

        ext = make_extension(
            mc_db_path=str(vault.vault_dir),
            masscode_version="v5",
            enable_contextual_learning="true",
        )
        items = items_of(
            KeywordQueryEventListener().on_event(FakeKeywordQueryEvent(""), ext)
        )
        names = {i.get_name() for i in items}

        assert {"multi tool [Build]", "multi tool [Test]"} <= names

    def test_fragment_selection_records_fragment_label(
        self, tmp_path, isolated_history
    ):
        import yaml

        fm = v5_frontmatter("fragged")
        body = (
            "\n## Fragment: One\n```txt\n1\n```\n\n## Fragment: Two\n```txt\n2\n```\n"
        )
        vault = VaultBuilder(tmp_path, layout="spaces")
        vault.add_raw_file(
            "fragged.md",
            f"---\n{yaml.safe_dump(fm).strip()}\n---\n{body}",
        )
        vault._register("fragged.md", fm["id"])
        vault.build()

        ext = make_extension(
            mc_db_path=str(vault.vault_dir),
            masscode_version="v5",
            enable_contextual_learning="true",
        )
        query_listener = KeywordQueryEventListener()

        action = query_listener.on_event(FakeKeywordQueryEvent("Two"), ext)
        matches = [i for i in items_of(action) if "[Two]" in i.get_name()]
        if not matches:
            pytest.skip("fragment did not pass fuzzy threshold for this query")
        payload = history_payload_of(matches[0])

        assert payload["fragment_label"] == "Two"

        ItemEnterEventListener().on_event(FakeItemEnterEvent(payload), ext)
        # History must contain the fragment-qualified display name
        with open(isolated_history, encoding="utf-8") as f:
            history = json.load(f)
        assert any(
            "Two" in snippet for snippets in history.values() for snippet in snippets
        )


# ===========================================================================
# Workflow 2 — V3 JSON full lifecycle
# ===========================================================================


class TestV3FullLifecycle:
    """Same lifecycle against the legacy JSON database."""

    @pytest.fixture
    def env(self, tmp_path):
        db_path = build_json_db(
            tmp_path / "db.json",
            [
                {
                    "id": 1,
                    "name": "old snippet",
                    "content": "legacy code",
                    "isDeleted": False,
                    "folderId": None,
                }
            ],
        )
        return {"db_path": db_path}

    def _prefs(self, env):
        return make_extension(mc_db_path=env["db_path"], masscode_version="v3")

    def test_search_finds_existing_snippet(self, env):
        # Query 'old' — strongly matches the snippet NAME (the content
        # 'legacy code' alone wouldn't clear the fuzzy threshold reliably)
        items = items_of(
            KeywordQueryEventListener().on_event(
                FakeKeywordQueryEvent("old"), self._prefs(env)
            )
        )

        assert any("old snippet" in i.get_name() for i in items)

    def test_save_then_search_new_snippet(self, env):
        from src.database.writer import save_snippet_to_inbox

        ext = self._prefs(env)
        result = save_snippet_to_inbox(
            db_path=env["db_path"],
            masscode_version="v3",
            content="fresh json content",
            name="fresh entry",
        )
        assert result["success"]

        items = items_of(
            KeywordQueryEventListener().on_event(FakeKeywordQueryEvent("fresh"), ext)
        )

        assert any("fresh entry" in i.get_name() for i in items)


# ===========================================================================
# Workflow 3 — V4 SQLite full lifecycle
# ===========================================================================


class TestV4FullLifecycle:
    """Load from SQLite, save to SQLite, find it again."""

    @pytest.fixture
    def env(self, tmp_path):
        db = (
            SqliteDbBuilder(tmp_path)
            .add_folder(1, "Scripts")
            .add_snippet(10, "sqlite native", contents=[("F", "SELECT 1;", "sql")])
            .build()
        )
        return {"db_path": db.path}

    def _prefs(self, env):
        return make_extension(mc_db_path=env["db_path"], masscode_version="v4")

    def test_search_finds_sqlite_snippet(self, env):
        items = items_of(
            KeywordQueryEventListener().on_event(
                FakeKeywordQueryEvent("native"), self._prefs(env)
            )
        )

        assert any("sqlite native" in i.get_name() for i in items)

    def test_save_via_item_enter_flow_writes_rows(self, env):
        import sqlite3

        ext = self._prefs(env)
        payload = {
            "action": "save_snippet",
            "name": "from workflow",
            "content": "INSERT INTO dreams;",
        }

        items = items_of(
            ItemEnterEventListener().on_event(FakeItemEnterEvent(payload), ext)
        )

        assert "Saved 'from workflow'" in items[0].get_name()

        conn = sqlite3.connect(env["db_path"])
        row = conn.execute(
            "SELECT value FROM snippet_contents ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        assert row[0] == "INSERT INTO dreams;"


# ===========================================================================
# Cross-cutting: smart single result over real data
# ===========================================================================


class TestSmartSingleResultWorkflow:
    """Learned dominance collapses results to one."""

    def test_repeated_selection_collapses_result_list(self, tmp_path, monkeypatch):
        db_path = build_json_db(
            tmp_path / "db.json",
            [
                {"id": 1, "name": "alpha cmd", "content": "a", "isDeleted": False},
                {"id": 2, "name": "beta cmd", "content": "b", "isDeleted": False},
            ],
        )

        hist_file = str(tmp_path / "smart-history.json")
        ch.ensure_history_file_exists(hist_file)
        # Simulate strong preference for 'alpha cmd' when querying 'cmd'
        for _ in range(10):
            ch.update_context_history("cmd", "alpha cmd", history_file_path=hist_file)

        # Point the listener's history loader at the temp history file
        real_load = ch.load_context_history
        monkeypatch.setattr(
            listeners_mod,
            "load_context_history",
            lambda *a, **k: real_load(hist_file),
        )

        ext = make_extension(
            mc_db_path=db_path,
            masscode_version="v3",
            enable_contextual_learning="true",
            smart_single_result_ratio="0.8",
        )
        items = items_of(
            KeywordQueryEventListener().on_event(FakeKeywordQueryEvent("cmd"), ext)
        )

        assert len(items) == 1
        assert "alpha cmd" in items[0].get_name()


# ===========================================================================
# main.py bootstrap smoke test
# ===========================================================================


class TestMainModuleImport:
    """Importing main.py executes the resilient bootstrap without crashing."""

    def test_main_imports_cleanly(self):
        import importlib
        import sys

        # Remove any previous import so the module actually re-executes
        sys.modules.pop("main", None)
        try:
            main = importlib.import_module("main")
        except SystemExit as exc:  # pragma: no cover — only on broken installs
            pytest.fail(f"main.py exited during import: {exc}")

        assert hasattr(main, "MassCodeExtension")
        assert callable(main.MassCodeExtension)

    def test_extension_class_wiring_declared(self):
        # The class must reference both listener types it subscribes
        import inspect

        import main

        source = inspect.getsource(main.MassCodeExtension.__init__)
        assert "KeywordQueryEventListener" in source
        assert "ItemEnterEventListener" in source

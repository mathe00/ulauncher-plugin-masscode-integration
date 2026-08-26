"""
Shared test helpers for the masscode-snippet extension test suite.

This module provides:
  - Snippet factory functions (V3-compatible dict shapes)
  - Database builders (JSON db, SQLite db, V5 Markdown Vault) on tmp paths
  - Fake Ulauncher event/extension objects for listener workflow tests
  - Introspection utilities to assert on Ulauncher action objects

All builders write to caller-provided directories (usually pytest tmp_path)
so tests never touch the user's real MassCode data.
"""

import json
import pickle
import sqlite3
from typing import Any

# ===========================================================================
# Snippet factories (V3-compatible shape used across the codebase)
# ===========================================================================


def make_snippet(
    name: str = "my snippet",
    content: Any = "print('hello')",
    *,
    description: str | None = None,
    folder: str | None = None,
    favorites: bool = False,
    deleted: bool = False,
) -> dict[str, Any]:
    """
    Build a minimal V3-compatible snippet dict.

    Optional metadata keys (_description, _folder, _isFavorites) mirror what
    the V4/V5 loaders attach.
    """
    snippet: dict[str, Any] = {
        "name": name,
        "content": content,
        "isDeleted": deleted,
    }
    if description:
        snippet["_description"] = description
    if folder:
        snippet["_folder"] = folder
    if favorites:
        snippet["_isFavorites"] = True
    return snippet


def make_fragment(
    label: str = "Fragment 1",
    value: str = "code here",
    language: str = "python",
) -> dict[str, Any]:
    """Build a single fragment entry as produced by the V4/V5 loaders."""
    return {"label": label, "value": value, "language": language}


def make_multi_fragment_snippet(
    name: str = "multi",
    fragments: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build a snippet whose content is a list of fragments."""
    if fragments is None:
        fragments = [
            make_fragment("Fragment 1", "alpha"),
            make_fragment("Fragment 2", "beta"),
        ]
    return make_snippet(name=name, content=fragments, **kwargs)


# ===========================================================================
# V3 JSON database builder
# ===========================================================================


def build_json_db(path: str, snippets: list[dict[str, Any]]) -> str:
    """
    Write a MassCode V3-style JSON database to `path`.

    Args:
        path: Target file path (e.g. tmp_path / "db.json")
        snippets: List of raw snippet dicts (MassCode JSON format)

    Returns:
        The path as a str
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"snippets": snippets}, f)
    return str(path)


def v3_json_snippet(
    snippet_id: int,
    name: str,
    content: str,
    *,
    folder_id: int | None = None,
    deleted: bool = False,
) -> dict[str, Any]:
    """Build a raw snippet entry in the exact MassCode V3 JSON format."""
    return {
        "id": snippet_id,
        "name": name,
        "content": content,
        "isDeleted": deleted,
        "folderId": folder_id,
        "createdAt": 1700000000000,
        "updatedAt": 1700000000000,
    }


# ===========================================================================
# V4 SQLite database builder
# ===========================================================================

SQLITE_SCHEMA = """
CREATE TABLE folders (
    id INTEGER PRIMARY KEY,
    name TEXT,
    icon TEXT
);
CREATE TABLE snippets (
    id INTEGER PRIMARY KEY,
    name TEXT,
    description TEXT,
    folderId INTEGER,
    isDeleted INTEGER DEFAULT 0,
    isFavorites INTEGER DEFAULT 0,
    createdAt INTEGER,
    updatedAt INTEGER
);
CREATE TABLE snippet_contents (
    id INTEGER PRIMARY KEY,
    snippetId INTEGER,
    label TEXT,
    value TEXT,
    language TEXT
);
"""


class SqliteDbBuilder:
    """
    Fluent builder for a MassCode V4 SQLite database in a temp directory.

    Usage:
        db = SqliteDbBuilder(tmp_path).add_folder(1, "Scripts").add_snippet(
            10, "greet", folder_id=1, contents=[("Fragment 1", "hello", "py")]
        ).build()
        path = db.path
    """

    def __init__(self, directory):
        self.path = str(directory / "massCode.db")
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SQLITE_SCHEMA)

    def add_folder(self, folder_id: int, name: str) -> "SqliteDbBuilder":
        self.conn.execute(
            "INSERT INTO folders (id, name) VALUES (?, ?)", (folder_id, name)
        )
        return self

    def add_snippet(
        self,
        snippet_id: int,
        name: str,
        *,
        description: str | None = None,
        folder_id: int | None = None,
        deleted: bool = False,
        favorites: bool = False,
        contents: list[tuple] | None = None,
    ) -> "SqliteDbBuilder":
        """
        Add a snippet row plus its content rows.

        Args:
            contents: List of (label, value, language) tuples. Empty value or
                      None entries are skipped by the loader — pass ("label", "", "lang")
                      only if you specifically want an empty-string fragment.
        """
        self.conn.execute(
            "INSERT INTO snippets (id, name, description, folderId, isDeleted,"
            " isFavorites, createdAt, updatedAt) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snippet_id,
                name,
                description,
                folder_id,
                int(deleted),
                int(favorites),
                1700000000000,
                1700000000000,
            ),
        )
        for label, value, language in contents or []:
            self.conn.execute(
                "INSERT INTO snippet_contents (snippetId, label, value, language)"
                " VALUES (?, ?, ?, ?)",
                (snippet_id, label, value, language),
            )
        return self

    def build(self) -> "SqliteDbBuilder":
        self.conn.commit()
        # Leave connection open-free; loader opens its own connection.
        self.conn.close()
        return self


# ===========================================================================
# V5 Markdown Vault builder
# ===========================================================================


def v5_frontmatter(
    name: str,
    *,
    snippet_id: int = 1,
    contents: list[dict[str, Any]] | None = None,
    folder_id: int | None = None,
    deleted: int = 0,
    favorites: int = 0,
    description: str | None = None,
) -> dict[str, Any]:
    """Build frontmatter metadata matching real MassCode V5 snippet files."""
    if contents is None:
        contents = [{"id": 1, "label": "Fragment 1", "language": "plaintext"}]
    return {
        "contents": contents,
        "createdAt": 1700000000000,
        "description": description,
        "folderId": folder_id,
        "id": snippet_id,
        "isDeleted": deleted,
        "isFavorites": favorites,
        "name": name,
        "tags": [],
        "updatedAt": 1700000000000,
    }


def v5_markdown_file(frontmatter: dict[str, Any], body: str = "") -> str:
    """
    Serialize frontmatter + body into a .md snippet file content.

    Args:
        frontmatter: Metadata dict (see v5_frontmatter)
        body: Markdown body AFTER the closing '---'. Should typically contain
              '## Fragment:' headings and fenced code blocks.
    """
    import yaml

    yaml_str = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
    body_part = body if not body or body.startswith("\n") else f"\n{body}"
    return f"---\n{yaml_str}\n---{body_part}\n"


def v5_body_single(code: str, lang: str = "python", label: str = "Fragment 1") -> str:
    """Body with one fragment heading and one fenced code block."""
    return f"\n## Fragment: {label}\n```{lang}\n{code}\n```\n"


def v5_body_multi(fragments: list[tuple]) -> str:
    """
    Body with several fragments.

    Args:
        fragments: List of (label, language, code) tuples
    """
    parts = []
    for label, lang, code in fragments:
        parts.append(f"\n## Fragment: {label}\n```{lang}\n{code}\n```\n")
    return "".join(parts)


class VaultBuilder:
    """
    Builder for a MassCode V5+ Markdown Vault fixture in a temp directory.

    Supports both layouts:
      - spaces (default): <vault>/code/.masscode/state.json + folders under code/
      - legacy:           <vault>/.masscode/state.json + folders at vault root

    Usage:
        vault = VaultBuilder(tmp_path).add_folder("Scripts", 1).add_snippet(
            "greet.md", frontmatter=v5_frontmatter("greet"),
            body=v5_body_single("print('hi')")
        ).build()
        vault_path, space_dir = str(vault.vault_dir), str(vault.space_dir)
    """

    def __init__(self, directory, layout: str = "spaces"):
        if layout not in ("spaces", "legacy"):
            raise ValueError(f"Unknown layout: {layout}")
        self.layout = layout
        self.vault_dir = directory / "markdown-vault"
        self.space_dir = self.vault_dir / ("code" if layout == "spaces" else "")
        self.meta_dir = self.space_dir / ".masscode"
        self.inbox_dir = self.meta_dir / "inbox"
        # Entries registered in state.json: {"filePath": relative-to-space, "id": n}
        self._entries: list[dict[str, Any]] = []
        self._counters = {"snippetId": 0, "contentId": 0}
        self._built = False

    def add_folder(self, name: str, folder_id: int, *, legacy_meta: bool = False):
        """
        Create a folder directory with .meta.yaml (or legacy .masscode-folder.yml).

        Args:
            name: Directory AND folder display name
            folder_id: Numeric folder id stored in the metadata file
            legacy_meta: Write '.masscode-folder.yml' with 'masscode_id' key instead
        """
        folder_path = self.space_dir / name
        folder_path.mkdir(parents=True, exist_ok=True)
        import yaml

        if legacy_meta:
            meta_file = folder_path / ".masscode-folder.yml"
            meta = {"masscode_id": folder_id, "name": name}
        else:
            meta_file = folder_path / ".meta.yaml"
            meta = {"id": folder_id, "name": name}
        with open(meta_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(meta, f)
        return self

    def add_raw_file(self, rel_to_space: str, content: str):
        """
        Drop a raw file inside the space dir (relative path), e.g. a snippet .md.
        Does NOT register it in state.json (use add_snippet for that).
        """
        target = self.space_dir / rel_to_space
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        return self

    def add_snippet_in_inbox(
        self, filename: str, content_md: str, snippet_id: int | None = None
    ):
        """
        Register an inbox snippet: file written to .masscode/inbox/ and listed
        in state.json. Mirrors what writer.py produces and MassCode indexes.
        """
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        (self.inbox_dir / filename).write_text(content_md, encoding="utf-8")
        sid = snippet_id if snippet_id is not None else self._counters["snippetId"] + 1
        self._register(f".masscode/inbox/{filename}", sid)
        return self

    def add_snippet(
        self,
        filename: str,
        *,
        frontmatter: dict[str, Any],
        body: str = "",
        folder: str | None = None,
        register: bool = True,
    ):
        """
        Create a snippet .md inside `folder` (or vault root of the space when None).

        Args:
            filename: File name, e.g. 'greet.md'
            frontmatter: Metadata from v5_frontmatter()
            body: Markdown body from v5_body_* helpers
            folder: Folder directory name; created without metadata unless
                    previously added via add_folder()
            register: Whether to list this file in state.json
        """
        rel_dir = folder if folder else ""
        target_dir = self.space_dir / rel_dir if rel_dir else self.space_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        md_content = v5_markdown_file(frontmatter, body)
        (target_dir / filename).write_text(md_content, encoding="utf-8")
        if register:
            import os

            rel = os.path.join(rel_dir, filename)
            self._register(rel, frontmatter.get("id"))
        return self

    def _register(self, rel_path: str, snippet_id: Any):
        self._entries.append({"filePath": rel_path, "id": snippet_id})
        if isinstance(snippet_id, int) and snippet_id > self._counters["snippetId"]:
            self._counters["snippetId"] = snippet_id
        return self

    def set_state(self, state: dict[str, Any]):
        """Override the entire state payload that build() will write."""
        self._custom_state = state
        return self

    def build(self) -> "VaultBuilder":
        """Write state.json and mark the vault ready."""
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        state = getattr(
            self,
            "_custom_state",
            {"snippets": self._entries, "counters": self._counters},
        )
        with open(self.meta_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump(state, f)
        self._built = True
        return self


# ===========================================================================
# Fake Ulauncher objects (for listener workflow tests)
# ===========================================================================


class FakeExtension:
    """Minimal stand-in for MassCodeExtension carrying only preferences."""

    def __init__(self, preferences: dict[str, str] | None = None):
        self.preferences = preferences or {}


DEFAULT_PREFS = {
    "mc_db_path": "/fake/db.json",
    "masscode_version": "v3",
    "enable_contextual_learning": "false",
    "smart_single_result_ratio": "0.0",
    "icon": "images/icon.png",
}


def make_extension(**pref_overrides: Any) -> FakeExtension:
    """FakeExtension pre-filled with valid defaults; override any preference."""
    prefs = dict(DEFAULT_PREFS)
    prefs.update(pref_overrides)
    return FakeExtension(prefs)


class FakeKeywordQueryEvent:
    """Stand-in for KeywordQueryEvent exposing get_argument()."""

    def __init__(self, argument: str = ""):
        self._argument = argument

    def get_argument(self):
        return self._argument


class FakeItemEnterEvent:
    """Stand-in for ItemEnterEvent exposing get_data()."""

    def __init__(self, data: Any):
        self._data = data

    def get_data(self):
        return self._data


# ===========================================================================
# Ulauncher action introspection utilities
# ===========================================================================


def items_of(action) -> list[Any]:
    """Extract the result item list from a RenderResultListAction."""
    return list(action.result_list)


def single_item(action):
    """Assert the action renders exactly one item and return it."""
    items = items_of(action)
    assert len(items) == 1, f"Expected 1 item, got {len(items)}: {items}"
    return items[0]


def custom_action_data(on_enter_action) -> Any:
    """
    Recover the pickled payload of an ExtensionCustomAction.

    ExtensionCustomAction stores data pickle-serialized in ._data;
    ItemEnterEvent will deliver exactly this object back to listeners.
    """
    return pickle.loads(on_enter_action._data)


def history_payload_of(item) -> dict[str, Any]:
    """
    Extract the record_history action data attached to a search result item.

    Search result items use ActionList([CopyToClipboardAction, ExtensionCustomAction]);
    this returns the unpickled custom action dict.
    """
    actions = list(item._on_enter)
    customs = [a for a in actions if hasattr(a, "_data")]
    assert customs, f"No ExtensionCustomAction found in {item._on_enter}"
    return custom_action_data(customs[0])


def copy_text_of(item) -> str:
    """Extract the clipboard text attached to a search result item."""
    actions = list(item._on_enter)
    copies = [a for a in actions if hasattr(a, "text")]
    assert copies, f"No CopyToClipboardAction found in {item._on_enter}"
    return copies[0].text


def desc_of(item) -> str:
    """
    Return an item's stored description text.

    NOTE: ResultItem.get_description() requires a live `query` argument in
    current Ulauncher versions; reading the backing attribute directly keeps
    these tests independent of that API detail.
    """
    return item._description


def name_of(item) -> str:
    """Return an item's display name (mirrors get_name())."""
    return item._name

"""
Pytest configuration for masscode-snippet extension tests.

Sets up the Python path so that the extension's src/ package is importable,
and provides shared fixtures for cache tests.
"""

import os
import sys
from collections.abc import Generator

import pytest

# ---------------------------------------------------------------------------
# Path setup: add project root to sys.path so that 'from src.xxx import yyy'
# works. This mirrors how Ulauncher adds the extension root to sys.path.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


@pytest.fixture(autouse=True)
def reset_snippet_cache() -> Generator[None, None, None]:
    """
    Auto-use fixture that resets SnippetCache state before each test.

    SnippetCache uses module-level class variables that persist across
    test cases. This fixture ensures each test starts with a clean cache
    (cold state: _snippets = None).

    Usage:
        Automatically applied to every test — no need to include it explicitly.
    """
    # Import here (not at module top) so the fixture runs even if
    # the import chain has side effects
    from src.database.cache import SnippetCache

    SnippetCache.invalidate()

    yield  # Test runs here

    # Teardown: reset cache state after test (good hygiene)
    SnippetCache.invalidate()


@pytest.fixture
def mock_loader(monkeypatch) -> None:
    """
    Mock the load_snippets function so that tests don't touch the filesystem.

    Usage:
        def test_something(mock_loader):
            # load_snippets() now returns ["fake"] by default

    To control the return value:
        monkeypatch.setattr(
            "src.database.cache.load_snippets",
            lambda **kw: [{"name": "custom"}],
        )
    """
    import src.database.cache as cache_mod

    monkeypatch.setattr(cache_mod, "load_snippets", lambda **kw: [{"name": "fake"}])
    yield


@pytest.fixture
def mock_mtime(monkeypatch) -> callable:
    """
    Mock os.path.getmtime to return a controllable value.

    Returns a helper function to set the mtime value.

    Usage:
        set_mtime = mock_mtime  # fixture is already the helper
        set_mtime(12345.0)      # all getmtime calls return 12345.0
    """
    import src.database.cache as cache_mod

    def _set_mtime(value: float = 1000.0) -> None:
        monkeypatch.setattr(cache_mod.os.path, "getmtime", lambda p: value)
        monkeypatch.setattr(cache_mod.os.path, "exists", lambda p: True)

    _set_mtime()  # Set a default
    return _set_mtime


@pytest.fixture
def mock_vault_path(monkeypatch) -> None:
    """
    Mock resolve_vault_space_dir to return a predictable path.

    This prevents vault layout detection from touching the filesystem.
    """
    import src.database.cache as cache_mod

    monkeypatch.setattr(
        cache_mod, "resolve_vault_space_dir", lambda p: os.path.join(p, "code")
    )
    yield


@pytest.fixture
def mock_all(mock_loader, mock_mtime, mock_vault_path) -> callable:
    """
    Convenience fixture that mocks all external dependencies at once.

    Returns the mtime setter so you can still control mtime per test.

    Usage:
        set_mtime = mock_all
        set_mtime(42.0)  # optional
        # test SnippetCache without any filesystem access
    """
    return mock_mtime


# ---------------------------------------------------------------------------
# Real-history-file protection
# ---------------------------------------------------------------------------
# src.constants.HISTORY_FILE points at <extension root>/context_history.json —
# the user's REAL learning history. Some listener code paths call
# update_context_history() with its default path argument. This autouse
# fixture snapshots the file before each test and restores it afterwards,
# so even an accidentally-unmocked write cannot corrupt real data.
# ---------------------------------------------------------------------------

import os


@pytest.fixture(autouse=True)
def protect_real_history_file():
    """Snapshot and restore the real context_history.json around each test."""
    import json

    from src.constants import HISTORY_FILE

    backup = None
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            backup = f.read()

    yield

    if backup is None:
        # Test deleted or never touched the real file — recreate empty to
        # preserve the pre-suite state (empty history).
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump({}, f)
    else:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            f.write(backup)

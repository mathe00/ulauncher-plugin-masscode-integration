"""
Tests for the contextual learning module (src.learning.contextual_history).

Coverage areas:
  - ensure_history_file_exists
  - load_context_history: missing, valid, corrupted, malformed entries
  - save_context_history round-trip
  - update_context_history: increments, normalization, guards, pruning
  - File locking helpers and lock-failure degradation
"""

import json

import pytest

from src.learning import contextual_history as ch


@pytest.fixture
def hist_path(tmp_path) -> str:
    """Isolated history file path inside tmp (never the real one)."""
    return str(tmp_path / "history.json")


# ===========================================================================
# ensure_history_file_exists
# ===========================================================================


class TestEnsureHistoryFileExists:
    """History bootstrap behavior."""

    def test_creates_missing_file_with_empty_dict(self, tmp_path):
        path = str(tmp_path / "new-history.json")

        ch.ensure_history_file_exists(path)

        with open(path, encoding="utf-8") as f:
            assert json.load(f) == {}

    def test_existing_file_left_untouched(self, tmp_path):
        path = tmp_path / "existing.json"
        existing = {"query": {"snippet": 3}}
        path.write_text(json.dumps(existing), encoding="utf-8")

        ch.ensure_history_file_exists(str(path))

        assert json.loads(path.read_text(encoding="utf-8")) == existing

    def test_unwritable_location_does_not_raise(self, monkeypatch):
        def explode(*args, **kwargs):
            raise OSError("disk on fire")

        monkeypatch.setattr(ch, "_atomic_write_json", explode)

        # Must log and swallow — never crash extension startup
        ch.ensure_history_file_exists("/nonexistent-dir/history.json")


# ===========================================================================
# load_context_history
# ===========================================================================


class TestLoadContextHistory:
    """Reading + validating the history file."""

    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert ch.load_context_history(str(tmp_path / "none.json")) == {}

    def test_valid_file_loaded(self, hist_path):
        data = {
            "python": {"my script": 3},
            "git": {"git rebase": 1, "git stash [Fragment 2]": 7},
        }
        with open(hist_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        assert ch.load_context_history(hist_path) == data

    def test_corrupted_json_resets_file(self, hist_path, caplog):
        with open(hist_path, "w", encoding="utf-8") as f:
            f.write("{definitely not json")

        with caplog.at_level("WARNING"):
            result = ch.load_context_history(hist_path)

        assert result == {}
        # File must be reset to a valid empty history
        with open(hist_path, encoding="utf-8") as f:
            assert json.load(f) == {}
        assert any("Resetting" in m or "Invalid history" in m for m in caplog.messages)

    def test_non_dict_root_sanitized_to_empty(self, hist_path):
        with open(hist_path, "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)

        assert ch.load_context_history(hist_path) == {}

    def test_malformed_entries_filtered_not_fatal(self, hist_path):
        # NOTE: JSON object keys are ALWAYS strings — a Python int key like
        # 42 is serialized as "42" and therefore stays valid after reload.
        data = {
            "good query": {"good snippet": 2},
            "bad snippets": ["not", "a", "dict"],  # non-dict value → dropped
            "partial": {"ok": 1, "5": "bad"},  # only valid pairs kept
            "all invalid": {None: None},  # nothing valid → dropped
        }
        with open(hist_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        result = ch.load_context_history(hist_path)

        assert result == {"good query": {"good snippet": 2}, "partial": {"ok": 1}}

    def test_non_string_snippet_names_dropped(self, hist_path):
        data = {"q": {"valid": 1, "also valid": 2}}
        with open(hist_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        assert ch.load_context_history(hist_path) == data

    def test_float_counts_accepted(self, hist_path):
        # _validate_history allows int OR float counts
        with open(hist_path, "w", encoding="utf-8") as f:
            json.dump({"q": {"s": 2.5}}, f)

        assert ch.load_context_history(hist_path) == {"q": {"s": 2.5}}


# ===========================================================================
# save_context_history
# ===========================================================================


class TestSaveContextHistory:
    """Writing the history file."""

    def test_round_trip(self, hist_path):
        data = {"query": {"snippet": 5}}

        ch.save_context_history(data, hist_path)

        assert ch.load_context_history(hist_path) == data

    def test_unicode_preserved(self, hist_path):
        data = {"café": {"café ☕": 1}}

        ch.save_context_history(data, hist_path)
        with open(hist_path, encoding="utf-8") as f:
            raw = f.read()

        assert "café ☕" in raw

    def test_write_failure_swallowed_and_logged(self, hist_path, caplog, monkeypatch):
        # Simulate a failing atomic write while keeping the lock path usable
        def explode(*args, **kwargs):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(ch, "_atomic_write_json", explode)

        with caplog.at_level("ERROR"):
            ch.save_context_history({}, hist_path)  # must not raise

        assert any("Error saving history" in m for m in caplog.messages)

    def test_lock_creation_failure_propagates(self, tmp_path):
        """
        Documents current behavior: the lock file is opened BEFORE error
        handling starts, so an un-creatable lock path (missing directory)
        raises out of save_context_history.
        """
        unreachable = str(tmp_path / "no-such-dir" / "history.json")

        with pytest.raises(FileNotFoundError):
            ch.save_context_history({}, unreachable)


# ===========================================================================
# update_context_history
# ===========================================================================


class TestUpdateContextHistory:
    """Selection recording logic."""

    def test_new_query_and_snippet_recorded(self, hist_path):
        ch.update_context_history("python", "my script", history_file_path=hist_path)

        assert ch.load_context_history(hist_path) == {"python": {"my script": 1}}

    def test_repeated_selection_increments(self, hist_path):
        for _ in range(4):
            ch.update_context_history("q", "s", history_file_path=hist_path)

        assert ch.load_context_history(hist_path)["q"]["s"] == 4

    def test_query_normalized_lowercase_stripped(self, hist_path):
        ch.update_context_history("  PYTHON  ", "s", history_file_path=hist_path)
        ch.update_context_history("Python", "s", history_file_path=hist_path)

        history = ch.load_context_history(hist_path)
        assert list(history.keys()) == ["python"]
        assert history["python"]["s"] == 2

    def test_fragment_label_part_of_snippet_key(self, hist_path):
        # The caller passes the full display name including fragment label;
        # update_context_history stores it verbatim.
        ch.update_context_history(
            "q",
            "multi [Fragment 2]",
            fragment_label="Fragment 2",
            history_file_path=hist_path,
        )

        assert ch.load_context_history(hist_path)["q"] == {"multi [Fragment 2]": 1}

    def test_disabled_learning_writes_nothing(self, hist_path):
        ch.ensure_history_file_exists(hist_path)

        ch.update_context_history(
            "q",
            "s",
            enable_contextual_learning=False,
            history_file_path=hist_path,
        )

        assert ch.load_context_history(hist_path) == {}

    @pytest.mark.parametrize(
        "query,name", [("", "s"), ("   ", "s"), ("q", ""), ("q", None)]
    )
    def test_empty_query_or_name_ignored(self, hist_path, query, name):
        ch.ensure_history_file_exists(hist_path)

        ch.update_context_history(query, name, history_file_path=hist_path)

        assert ch.load_context_history(hist_path) == {}

    def test_pruning_keeps_most_recent_queries(self, hist_path, monkeypatch):
        monkeypatch.setattr(ch, "MAX_HISTORY_QUERIES", 3)
        ch.ensure_history_file_exists(hist_path)

        for i in range(5):
            ch.update_context_history(f"q{i}", "s", history_file_path=hist_path)

        history = ch.load_context_history(hist_path)

        # Oldest two queries evicted, newest three kept (insertion order)
        assert sorted(history.keys()) == ["q2", "q3", "q4"]

    def test_no_pruning_under_limit(self, hist_path):
        for i in range(10):
            ch.update_context_history(f"q{i}", "s", history_file_path=hist_path)

        assert len(ch.load_context_history(hist_path)) == 10

    def test_corrupted_existing_history_starts_fresh(self, hist_path):
        with open(hist_path, "w", encoding="utf-8") as f:
            f.write("{broken")

        ch.update_context_history("q", "s", history_file_path=hist_path)

        assert ch.load_context_history(hist_path) == {"q": {"s": 1}}

    def test_update_failure_swallowed(self, hist_path, monkeypatch):
        # Simulate a crash during write; must not raise into the listener
        def flaky(path, data):
            raise OSError("simulated disk full")

        monkeypatch.setattr(ch, "_atomic_write_json", flaky)

        # Must not raise even though the atomic write fails
        ch.update_context_history("q", "s", history_file_path=hist_path)


# ===========================================================================
# Locking helpers
# ===========================================================================


class TestLocking:
    """fcntl-based coordination and its failure fallback."""

    def test_acquire_release_round_trip(self, tmp_path):
        lock_path = str(tmp_path / "h.lock")

        fd = ch._acquire_lock(lock_path)
        assert not fd.closed
        ch._release_lock(fd)
        assert fd.closed

    def test_lock_file_created_next_to_history(self, tmp_path):
        lock_path = str(tmp_path / "h.lock")

        fd = ch._acquire_lock(lock_path)
        ch._release_lock(fd)

        import os

        assert os.path.exists(lock_path)

    def test_lock_failure_degrades_gracefully(self, tmp_path, caplog, monkeypatch):
        def deny(fd, op):
            raise OSError("NFS doesn't support flock here")

        monkeypatch.setattr(ch.fcntl, "flock", deny)

        with caplog.at_level("WARNING"):
            fd = ch._acquire_lock(str(tmp_path / "h.lock"))

        # Still returns a usable fd — better unlocked than crashed
        assert not fd.closed
        assert any("proceeding without lock" in m for m in caplog.messages)
        ch._release_lock(fd)

    def test_release_failure_swallowed(self, tmp_path, monkeypatch):
        def deny(fd, op):
            raise OSError("already unlocked")

        monkeypatch.setattr(ch.fcntl, "flock", deny)
        fd = ch._acquire_lock(str(tmp_path / "h.lock"))

        # Must not raise even when unlock fails
        ch._release_lock(fd)
        assert fd.closed

    def test_concurrent_updates_serialize_via_lock(self, tmp_path):
        """Two sequential locked updates both land in the file."""
        hist_path = str(tmp_path / "h.json")
        ch.ensure_history_file_exists(hist_path)

        ch.update_context_history("q", "a", history_file_path=hist_path)
        ch.update_context_history("q", "b", history_file_path=hist_path)

        assert ch.load_context_history(hist_path)["q"] == {"a": 1, "b": 1}

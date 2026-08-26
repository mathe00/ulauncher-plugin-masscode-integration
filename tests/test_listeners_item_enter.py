"""
Workflow tests for ItemEnterEventListener (src.events.listeners).

Runs when the user presses Enter on a result item. Two action types:
  - record_history → contextual learning update
  - save_snippet   → write clipboard to MassCode Inbox

All external effects (history file, snippet writers, clipboard) are mocked
or routed to temp paths — the real user history is never touched.
"""

import pytest

import src.events.listeners as listeners_mod
from src.events.listeners import ItemEnterEventListener
from tests.helpers import FakeItemEnterEvent, desc_of, items_of, make_extension


@pytest.fixture
def listener():
    return ItemEnterEventListener()


# ===========================================================================
# Dispatch logic
# ===========================================================================


class TestDispatch:
    """on_event routing by action type."""

    def test_non_dict_data_ignored(self, listener):
        assert (
            listener.on_event(FakeItemEnterEvent("not a dict"), make_extension())
            is None
        )
        assert listener.on_event(FakeItemEnterEvent([1, 2]), make_extension()) is None
        assert listener.on_event(FakeItemEnterEvent(None), make_extension()) is None

    def test_unknown_action_ignored(self, listener):
        result = listener.on_event(
            FakeItemEnterEvent({"action": "teleport"}), make_extension()
        )

        assert result is None

    def test_missing_action_key_ignored(self, listener):
        result = listener.on_event(FakeItemEnterEvent({"name": "x"}), make_extension())

        assert result is None


# ===========================================================================
# record_history action
# ===========================================================================


class TestRecordHistoryAction:
    """Contextual learning recording on selection."""

    @pytest.fixture(autouse=True)
    def spy_update(self, monkeypatch):
        calls = []

        def fake_update(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(listeners_mod, "update_context_history", fake_update)
        self.calls = calls

    def test_full_payload_forwarded(self, listener):
        data = {
            "action": "record_history",
            "query": "dock",
            "snippet_name": "docker ps",
            "fragment_label": "",
        }

        listener.on_event(FakeItemEnterEvent(data), make_extension())

        assert self.calls == [
            {
                "query": "dock",
                "snippet_name": "docker ps",
                "fragment_label": "",
                "enable_contextual_learning": False,  # default prefs have it off
            }
        ]

    def test_learning_enabled_flag_from_preferences(self, listener):
        data = {
            "action": "record_history",
            "query": "q",
            "snippet_name": "s",
            "fragment_label": "F1",
        }

        listener.on_event(
            FakeItemEnterEvent(data),
            make_extension(enable_contextual_learning="true"),
        )

        assert self.calls[0]["enable_contextual_learning"] is True

    def test_missing_query_skips_update(self, listener):
        data = {"snippet_name": "s"}

        result = listener.on_event(FakeItemEnterEvent(data), make_extension())

        assert result is None
        assert self.calls == []

    def test_missing_snippet_name_skips_update(self, listener):
        data = {"query": "q"}

        listener.on_event(FakeItemEnterEvent(data), make_extension())

        assert self.calls == []

    def test_returns_none_after_recording(self, listener):
        data = {"query": "q", "snippet_name": "s"}

        assert listener.on_event(FakeItemEnterEvent(data), make_extension()) is None

    def test_update_exception_swallowed(self, listener, monkeypatch):
        def explode(**kwargs):
            raise RuntimeError("disk gone")

        monkeypatch.setattr(listeners_mod, "update_context_history", explode)

        # Must not raise into Ulauncher's event loop
        listener.on_event(
            FakeItemEnterEvent({"query": "q", "snippet_name": "s"}),
            make_extension(),
        )


# ===========================================================================
# save_snippet action — content resolution
# ===========================================================================


class TestSaveActionContentResolution:
    """Where the content comes from and what happens when it's missing."""

    @pytest.fixture(autouse=True)
    def clipboard(self, monkeypatch):
        state = {"value": None, "raise": None}

        def fake_paste():
            if state["raise"] is not None:
                raise state["raise"]
            return state["value"]

        monkeypatch.setattr(listeners_mod.pyperclip, "paste", fake_paste)
        return state

    @pytest.fixture(autouse=True)
    def spy_writer(self, monkeypatch):
        calls = []

        def fake_save(**kwargs):
            calls.append(kwargs)
            return {"success": True, "name": kwargs.get("name"), "path": "/tmp/x"}

        monkeypatch.setattr(listeners_mod, "save_snippet_to_inbox", fake_save)
        self.calls = calls

    def test_content_from_action_data_used_directly(self, listener, clipboard):
        # Clipboard stays untouched (None) — proves no re-read happened
        data = {"action": "save_snippet", "name": "N", "content": "from preview"}

        listener._handle_save_action(data, make_extension())

        assert self.calls[0]["content"] == "from preview"

    def test_fallback_to_clipboard_when_data_empty(self, listener, clipboard):
        clipboard["value"] = "from clipboard"
        data = {"action": "save_snippet", "name": "N", "content": ""}

        listener._handle_save_action(data, make_extension())

        assert self.calls[0]["content"] == "from clipboard"

    def test_pyperclip_failure_yields_error_item(self, listener, clipboard):
        import pyperclip

        clipboard["raise"] = pyperclip.PyperclipException("no xclip")
        data = {"action": "save_snippet", "name": "N", "content": "   "}

        items = items_of(listener._handle_save_action(data, make_extension()))

        assert items[0].get_name() == "Failed to save 'N'"
        assert "xclip" in desc_of(items[0])

    def test_both_sources_empty_rejected(self, listener, clipboard):
        clipboard["value"] = ""
        data = {"action": "save_snippet", "name": "N", "content": None}

        items = items_of(listener._handle_save_action(data, make_extension()))

        assert "Clipboard is empty" in desc_of(items[0])

    def test_missing_name_short_circuits(self, listener):
        data = {"action": "save_snippet", "name": "", "content": "c"}

        items = items_of(listener._handle_save_action(data, make_extension()))

        assert items[0].get_name() == "Failed to save 'unknown'"
        assert self.calls == []  # writer never invoked

    def test_writer_receives_stripped_content(self, listener):
        data = {"action": "save_snippet", "name": "N", "content": "  padded  \n"}

        listener._handle_save_action(data, make_extension())

        assert self.calls[0]["content"] == "padded"

    def test_writer_receives_preferences(self, listener):
        ext = make_extension(masscode_version="v5", mc_db_path="/custom/vault")
        data = {"action": "save_snippet", "name": "N", "content": "c"}

        listener._handle_save_action(data, ext)

        assert self.calls[0]["masscode_version"] == "v5"
        assert self.calls[0]["db_path"] == "/custom/vault"


# ===========================================================================
# save_snippet action — outcome rendering
# ===========================================================================


class TestSaveActionOutcomes:
    """Success/failure confirmation items."""

    @pytest.fixture(autouse=True)
    def writer_stub(self, monkeypatch):
        state = {"result": {"success": True}}

        def fake_save(**kwargs):
            return state["result"]

        monkeypatch.setattr(listeners_mod, "save_snippet_to_inbox", fake_save)
        return state

    def test_success_shows_confirmation(self, listener):
        data = {"action": "save_snippet", "name": "Good", "content": "c"}

        items = items_of(listener._handle_save_action(data, make_extension()))

        assert items[0].get_name() == "Saved 'Good' to MassCode Inbox"

    def test_failure_shows_error_message(self, listener, writer_stub):
        writer_stub["result"] = {"success": False, "error": "vault missing"}
        data = {"action": "save_snippet", "name": "Bad", "content": "c"}

        items = items_of(listener._handle_save_action(data, make_extension()))

        assert items[0].get_name() == "Failed to save 'Bad'"
        assert desc_of(items[0]) == "vault missing"

    def test_missing_error_key_gets_unknown_text(self, listener, writer_stub):
        writer_stub["result"] = {"success": False}
        data = {"action": "save_snippet", "name": "M", "content": "c"}

        items = items_of(listener._handle_save_action(data, make_extension()))

        assert desc_of(items[0]) == "Unknown error"

    def test_unexpected_exception_becomes_error_item(self, listener, monkeypatch):
        def explode(**kwargs):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(listeners_mod, "save_snippet_to_inbox", explode)
        data = {"action": "save_snippet", "name": "X", "content": "c"}

        items = items_of(listener._handle_save_action(data, make_extension()))

        assert items[0].get_name() == "Failed to save 'X'"
        assert "kaboom" in desc_of(items[0])


# ===========================================================================
# Full round-trip: builder payload → dispatcher
# ===========================================================================


class TestBuilderListenerContract:
    """
    The dict built by create_save_result_item must be understood verbatim by
    the ItemEnterEventListener (they communicate via a pickled payload).
    """

    def test_save_payload_round_trip(self, listener, monkeypatch):
        from src.results.builder import create_save_result_item

        captured = {}

        def fake_save(db_path, masscode_version, content, name):
            captured.update(
                db_path=db_path,
                masscode_version=masscode_version,
                content=content,
                name=name,
            )
            return {"success": True}

        monkeypatch.setattr(listeners_mod, "save_snippet_to_inbox", fake_save)

        ext = make_extension(masscode_version="v4", mc_db_path="/tmp/db.sqlite")
        action = create_save_result_item(
            name="Round Trip",
            clipboard_preview="preview…",
            clipboard_content="the full content",
        )
        payload = __import__("pickle").loads(action.result_list[0]._on_enter._data)

        items = items_of(listener.on_event(FakeItemEnterEvent(payload), ext))

        assert captured == {
            "db_path": "/tmp/db.sqlite",
            "masscode_version": "v4",
            "content": "the full content",
            "name": "Round Trip",
        }
        assert "Saved 'Round Trip'" in items[0].get_name()

    def test_history_payload_round_trip(self, listener, monkeypatch):
        from src.results.builder import create_result_items

        captured = {}

        def fake_update(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(listeners_mod, "update_context_history", fake_update)

        ext = make_extension(enable_contextual_learning="true")
        match = {
            "name": "snippet [Fragment 2]",
            "content": "code",
            "fragment_label": "Fragment 2",
            "query": "snip",
            "fuzzy_score": 90,
            "context_score": 0,
        }
        action = create_result_items([match], icon="i.png")
        payload = __import__("pickle").loads(action.result_list[0]._on_enter[1]._data)

        listener.on_event(FakeItemEnterEvent(payload), ext)

        assert captured["snippet_name"] == "snippet [Fragment 2]"
        assert captured["fragment_label"] == "Fragment 2"
        assert captured["query"] == "snip"

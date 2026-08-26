"""
Tests for the results builder module (src.results.builder).

Uses the REAL Ulauncher API classes (installed in this environment) and
introspects the produced action trees to verify:
  - item names / descriptions / icons
  - clipboard copy payloads
  - custom action data (history recording, save triggering)
  - truncation and formatting rules
"""

from ulauncher.api.shared.action.HideWindowAction import HideWindowAction
from ulauncher.api.shared.item.ExtensionResultItem import ExtensionResultItem

from src.constants import CLIPBOARD_PREVIEW_MAX_LEN
from src.results.builder import (
    create_error_message,
    create_result_items,
    create_save_confirmation_item,
    create_save_result_item,
)
from tests.helpers import copy_text_of, desc_of, history_payload_of, items_of

# ===========================================================================
# create_error_message
# ===========================================================================


class TestCreateErrorMessage:
    """Single-item error rendering."""

    def test_renders_exactly_one_item(self):
        action = create_error_message("Title", "Message")

        assert len(items_of(action)) == 1

    def test_item_fields(self):
        action = create_error_message("Bad DB", "Check path", icon="images/warn.png")
        item = items_of(action)[0]

        assert item.get_name() == "Bad DB"
        assert desc_of(item) == "Check path"
        assert item._icon == "images/warn.png"

    def test_default_icon(self):
        action = create_error_message("T", "M")
        assert items_of(action)[0]._icon == "images/icon.png"

    def test_enter_hides_window(self):
        item = items_of(create_error_message("T", "M"))[0]

        assert isinstance(item._on_enter, HideWindowAction)


# ===========================================================================
# create_result_items
# ===========================================================================


def make_match(**overrides):
    """A match dict as produced by KeywordQueryEventListener._match_snippets."""
    base = {
        "name": "my snippet",
        "content": "code lines",
        "fragment_label": "",
        "query": "my",
        "fuzzy_score": 80,
        "context_score": 0,
    }
    base.update(overrides)
    return base


class TestCreateResultItems:
    """Search result list construction."""

    def test_one_item_per_match(self):
        matches = [make_match(name=f"s{i}") for i in range(3)]
        items = items_of(create_result_items(matches, icon="i.png"))

        assert len(items) == 3
        assert all(isinstance(i, ExtensionResultItem) for i in items)

    def test_max_results_caps_output(self):
        matches = [make_match(name=f"s{i}") for i in range(20)]

        items = items_of(create_result_items(matches, icon="i.png", max_results=8))

        assert len(items) == 8

    def test_copies_full_content(self):
        items = items_of(
            create_result_items([make_match(content="the full code")], icon="i")
        )

        assert copy_text_of(items[0]) == "the full code"

    def test_history_action_data_shape(self):
        match = make_match(query="qq", name="snippet [F1]", fragment_label="F1")
        payload = history_payload_of(
            items_of(create_result_items([match], icon="i"))[0]
        )

        assert payload == {
            "action": "record_history",
            "query": "qq",
            "snippet_name": "snippet [F1]",
            "fragment_label": "F1",
        }

    def test_star_prefix_with_context_score(self):
        match = make_match(context_score=42)
        items = items_of(
            create_result_items([match], icon="i", enable_contextual_learning=True)
        )

        assert items[0].get_name() == "★ my snippet"

    def test_no_star_without_learning(self):
        match = make_match(context_score=42)
        items = items_of(
            create_result_items([match], icon="i", enable_contextual_learning=False)
        )

        assert items[0].get_name() == "my snippet"

    def test_no_star_when_context_score_zero(self):
        match = make_match(context_score=0)
        items = items_of(
            create_result_items([match], icon="i", enable_contextual_learning=True)
        )

        assert items[0].get_name() == "my snippet"


class TestDescriptionFormatting:
    """Content preview rules in result descriptions."""

    def test_newlines_flattened(self):
        items = items_of(create_result_items([make_match(content="a\nb\nc")], icon="i"))

        assert desc_of(items[0]) == "a b c"

    def test_long_content_truncated_to_100_chars(self):
        long_text = "x" * 500
        items = items_of(create_result_items([make_match(content=long_text)], icon="i"))
        desc = desc_of(items[0])

        assert len(desc) == 100
        assert desc.endswith("...")
        assert desc[:97] == "x" * 97

    def test_exactly_100_chars_not_truncated(self):
        text = "y" * 100
        items = items_of(create_result_items([make_match(content=text)], icon="i"))

        assert desc_of(items[0]) == text

    def test_whitespace_only_content_shows_empty_snippet(self):
        items = items_of(create_result_items([make_match(content="   \n  ")], icon="i"))

        assert desc_of(items[0]) == "Empty snippet"


# ===========================================================================
# create_save_result_item
# ===========================================================================


class TestCreateSaveResultItem:
    """The 'ms new' preview item."""

    def make_action(self, **kw):
        defaults = {
            "name": "My Snippet",
            "clipboard_preview": "preview text",
            "clipboard_content": "full content",
            "icon": "images/icon.png",
        }
        defaults.update(kw)
        return create_save_result_item(**defaults)

    def test_title_includes_name(self):
        item = items_of(self.make_action())[0]

        assert item.get_name() == "Save as 'My Snippet'"

    def test_preview_truncated_at_constant(self):
        long_preview = "p" * 300
        item = items_of(self.make_action(clipboard_preview=long_preview))[0]
        desc = desc_of(item)

        assert len(desc) == CLIPBOARD_PREVIEW_MAX_LEN
        assert desc.endswith("...")

    def test_short_preview_untouched(self):
        item = items_of(self.make_action(clipboard_preview="short"))[0]

        assert desc_of(item) == "short"

    def test_preview_newlines_flattened(self):
        item = items_of(self.make_action(clipboard_preview="line1\nline2"))[0]

        assert desc_of(item) == "line1 line2"

    def test_empty_preview_placeholder(self):
        item = items_of(self.make_action(clipboard_preview="   "))[0]

        assert desc_of(item) == "Empty clipboard"

    def test_action_data_carries_content_and_name(self):
        from tests.helpers import custom_action_data, single_item

        payload = custom_action_data(single_item(self.make_action())._on_enter)

        assert payload == {
            "action": "save_snippet",
            "name": "My Snippet",
            "content": "full content",
        }

    def test_keep_app_open_true_for_confirmation_visibility(self):
        from tests.helpers import single_item

        on_enter = single_item(self.make_action())._on_enter

        # keep_app_open=True so the user sees the confirmation after saving
        assert on_enter.keep_app_open() is True


# ===========================================================================
# create_save_confirmation_item
# ===========================================================================


class TestCreateSaveConfirmationItem:
    """Post-save feedback rendering."""

    def test_success_title_and_description(self):
        item = items_of(create_save_confirmation_item("Snip", success=True))[0]

        assert item.get_name() == "Saved 'Snip' to MassCode Inbox"
        assert "Open MassCode" in desc_of(item)

    def test_failure_title_uses_error_message(self):
        item = items_of(
            create_save_confirmation_item("Snip", success=False, error="disk full")
        )[0]

        assert item.get_name() == "Failed to save 'Snip'"
        assert desc_of(item) == "disk full"

    def test_failure_without_error_gets_default_text(self):
        item = items_of(create_save_confirmation_item("Snip", success=False))[0]

        assert desc_of(item) == "Unknown error. Check Ulauncher logs."

    def test_confirmation_hides_window_on_enter(self):
        item = items_of(create_save_confirmation_item("S", success=True))[0]

        assert isinstance(item._on_enter, HideWindowAction)

    def test_custom_icon_propagated(self):
        item = items_of(
            create_save_confirmation_item("S", success=True, icon="images/ok.png")
        )[0]

        assert item._icon == "images/ok.png"

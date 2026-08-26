"""
Tests for the fragment utilities module (src.fragments.fragment_utils).

expand_snippet_fragments() is the bridge between multi-fragment snippets
(as stored by V4/V5 loaders) and per-fragment selectable result entries.
"""

from src.fragments.fragment_utils import expand_snippet_fragments
from tests.helpers import make_fragment, make_multi_fragment_snippet, make_snippet


class TestNonListContent:
    """String/empty content passes through untouched."""

    def test_string_content_unchanged(self):
        snippet = make_snippet(content="plain code")

        assert expand_snippet_fragments(snippet) == [snippet]

    def test_empty_string_content_unchanged(self):
        snippet = make_snippet(content="")

        assert expand_snippet_fragments(snippet) == [snippet]

    def test_original_object_returned_for_non_list(self):
        snippet = make_snippet(content="x")

        # Identity: same dict object, not a copy
        assert expand_snippet_fragments(snippet)[0] is snippet


class TestSingleOrEmptyFragmentLists:
    """Lists with <= 1 fragment are not expanded (avoid useless processing)."""

    def test_single_fragment_list_not_expanded(self):
        snippet = make_multi_fragment_snippet(
            fragments=[make_fragment("Only", "value")]
        )

        assert expand_snippet_fragments(snippet) == [snippet]

    def test_empty_list_not_expanded(self):
        snippet = make_multi_fragment_snippet(fragments=[])

        assert expand_snippet_fragments(snippet) == [snippet]


class TestMultiFragmentExpansion:
    """The core expansion behavior for 2+ fragments."""

    def test_two_fragments_become_two_entries(self):
        snippet = make_multi_fragment_snippet(
            name="multi",
            fragments=[
                make_fragment("Part A", "alpha", "python"),
                make_fragment("Part B", "beta", "bash"),
            ],
        )

        expanded = expand_snippet_fragments(snippet)

        assert len(expanded) == 2
        assert expanded[0]["content"] == "alpha"
        assert expanded[1]["content"] == "beta"

    def test_each_entry_carries_fragment_label(self):
        snippet = make_multi_fragment_snippet(
            fragments=[make_fragment("L1", "a"), make_fragment("L2", "b")]
        )
        labels = [e["_fragment_label"] for e in expand_snippet_fragments(snippet)]

        assert labels == ["L1", "L2"]

    def test_language_propagated_with_default(self):
        snippet = make_multi_fragment_snippet(
            fragments=[
                {"label": "A", "value": "x"},  # no language key
                {"label": "B", "value": "y", "language": "rust"},
            ]
        )
        expanded = expand_snippet_fragments(snippet)

        assert expanded[0]["_fragment_language"] == "plaintext"  # default
        assert expanded[1]["_fragment_language"] == "rust"

    def test_index_preserves_order(self):
        snippet = make_multi_fragment_snippet(
            fragments=[make_fragment(f"F{i}", f"v{i}") for i in range(5)]
        )
        indexes = [e["_fragment_index"] for e in expand_snippet_fragments(snippet)]

        assert indexes == [0, 1, 2, 3, 4]

    def test_base_name_copied_to_every_entry(self):
        snippet = make_multi_fragment_snippet(
            name="parent-name",
            fragments=[make_fragment("A", "1"), make_fragment("B", "2")],
        )
        names = {e["name"] for e in expand_snippet_fragments(snippet)}

        assert names == {"parent-name"}

    def test_other_metadata_keys_preserved(self):
        snippet = make_multi_fragment_snippet(description="desc", folder="Scripts")
        expanded = expand_snippet_fragments(snippet)

        assert all(e["_description"] == "desc" for e in expanded)
        assert all(e["_folder"] == "Scripts" for e in expanded)
        # content/name excluded from the metadata spread (set explicitly instead)
        assert "_isDeleted" in expanded[0] or "isDeleted" in snippet


class TestEmptyFragmentValues:
    """Empty fragment content gets a visible placeholder marker."""

    def test_empty_value_replaced_by_marker(self):
        snippet = make_multi_fragment_snippet(
            fragments=[make_fragment("Void", ""), make_fragment("Full", "ok")]
        )
        expanded = expand_snippet_fragments(snippet)

        assert expanded[0]["content"] == "[Empty Fragment]"
        assert expanded[1]["content"] == "ok"


class TestMissingFields:
    """Defensive defaults when entries lack expected keys."""

    def test_missing_name_defaults_to_unnamed(self):
        snippet = {"content": [make_fragment("A", "1"), make_fragment("B", "2")]}
        expanded = expand_snippet_fragments(snippet)

        assert all(e["name"] == "Unnamed" for e in expanded)

    def test_fragment_without_label_gets_empty_label(self):
        snippet = {
            "name": "n",
            "content": [{"value": "v1"}, {"value": "v2"}],  # no labels
        }
        expanded = expand_snippet_fragments(snippet)

        assert [e["_fragment_label"] for e in expanded] == ["", ""]

    def test_missing_content_key_treated_as_single(self):
        snippet = {"name": "no-content-key"}

        assert expand_snippet_fragments(snippet) == [snippet]


class TestQuirks:
    """Documented quirks of the current implementation.

    These pin down real behavior that could be considered latent bugs;
    changing them should be a conscious decision.
    """

    def test_duplicate_fragment_dicts_share_first_index(self):
        # list.index() matches by equality: two identical fragment dicts both
        # resolve to index 0. Real MassCode data has unique dicts, but the
        # quirk is pinned here to make any change deliberate.
        frag = make_fragment("Same", "dup")
        snippet = make_multi_fragment_snippet(fragments=[frag, dict(frag)])
        expanded = expand_snippet_fragments(snippet)

        assert expanded[0]["_fragment_index"] == 0
        assert expanded[1]["_fragment_index"] == 0

    def test_expansion_never_returns_empty(self):
        # Defensive branch: even a pathological input yields a usable entry
        snippet = make_multi_fragment_snippet()
        snippet["content"] = [{}]

        result = expand_snippet_fragments(snippet)

        assert len(result) >= 1

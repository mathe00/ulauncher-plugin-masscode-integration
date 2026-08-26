"""
Tests for the fuzzy search module (src.utils.fuzzy_search).

Coverage areas:
  - calculate_fuzzy_score: fuzzywuzzy path, fallback path, edge inputs
  - calculate_relevance: exact/prefix/fuzzy/none tiers
  - find_relevant_contexts: filtering and best-relevance selection
  - get_context_score: max-across-contexts aggregation
"""

from typing import ClassVar

import pytest

from src.utils import fuzzy_search as fs

# ===========================================================================
# calculate_fuzzy_score — fuzzywuzzy available (default in this env)
# ===========================================================================


class TestCalculateFuzzyScoreWithFuzzywuzzy:
    """The primary scoring path using weighted name/content matching."""

    def test_weights_are_70_name_30_content(self, monkeypatch):

        # Freeze fuzz.partial_ratio to known values to verify the weighting
        scores = {}

        def fake_ratio(a, b):
            return scores[b]

        monkeypatch.setattr(fs.fuzz, "partial_ratio", fake_ratio)
        monkeypatch.setattr(fs, "FUZZY_AVAILABLE", True)
        scores["my snippet"] = 80  # title score
        scores["some content"] = 40  # content score

        result = fs.calculate_fuzzy_score("my snippet", "my snippet", "some content")

        assert result == int(0.7 * 80 + 0.3 * 40)

    def test_empty_content_skips_content_component(self, monkeypatch):
        monkeypatch.setattr(fs, "FUZZY_AVAILABLE", True)
        monkeypatch.setattr(
            fs.fuzz, "partial_ratio", lambda a, b: 100 if b == "name" else 0
        )

        result = fs.calculate_fuzzy_score("q", "name", "")

        assert result == 70  # only the title component contributes

    def test_case_insensitive(self):
        assert fs.calculate_fuzzy_score("PYTHON", "python script") > 50

    def test_perfect_match_scores_high(self):
        score = fs.calculate_fuzzy_score("greet", "greet", "print('hello')")

        assert score >= 70

    def test_unrelated_strings_score_low(self):
        score = fs.calculate_fuzzy_score(
            "zzzqqq", "completely different", "nothing here"
        )

        assert score < 30


class TestCalculateFuzzyScoreEdgeInputs:
    """Degenerate query/name combinations."""

    def test_empty_query_returns_perfect_score(self):
        # Empty query = "show everything" → guaranteed pass
        assert fs.calculate_fuzzy_score("", "anything") == 100
        assert fs.calculate_fuzzy_score("", "") == 100

    def test_unicode_inputs(self):
        score = fs.calculate_fuzzy_score("café", "recette café ☕", "du contenu")

        assert score > 50


# ===========================================================================
# calculate_fuzzy_score — substring fallback (fuzzywuzzy unavailable)
# ===========================================================================


class TestCalculateFuzzyScoreFallback:
    """When fuzzywuzzy is missing: binary substring matching."""

    @pytest.fixture(autouse=True)
    def force_fallback(self, monkeypatch):
        monkeypatch.setattr(fs, "FUZZY_AVAILABLE", False)
        monkeypatch.setattr(fs, "_fuzzy_missing_warned", False)  # reset warn latch

    def test_substring_in_name_passes_threshold(self):
        assert fs.calculate_fuzzy_score("git", "my git command", "") == 51

    def test_substring_in_content_passes(self):
        assert (
            fs.calculate_fuzzy_score("secret", "unrelated name", "the secret sauce")
            == 51
        )

    def test_case_insensitive_substring(self):
        assert fs.calculate_fuzzy_score("GIT", "my git command", "") == 51

    def test_no_match_scores_zero(self):
        assert fs.calculate_fuzzy_score("zzz", "abc", "def") == 0

    def test_warning_logged_once_only(self, caplog):
        with caplog.at_level("WARNING"):
            fs.calculate_fuzzy_score("a", "a", "")
            fs.calculate_fuzzy_score("b", "b", "")

        warnings = [m for m in caplog.messages if "substring" in m]
        assert len(warnings) == 1, "warning must be emitted only once per session"


# ===========================================================================
# calculate_relevance
# ===========================================================================


class TestCalculateRelevance:
    """Tiered relevance between a normalized query and a history query."""

    def test_exact_match_is_one(self):
        assert fs.calculate_relevance("docker", "docker") == 1.0

    def test_query_prefix_of_history_tier(self):
        # 'dock' is a prefix of 'docker' → 0.9 * len-ratio
        expected = (len("dock") / len("docker")) * 0.9
        assert fs.calculate_relevance("dock", "docker") == pytest.approx(expected)

    def test_history_prefix_of_query_tier(self):
        # 'dock' (history) is a prefix of 'docker' (query) → 0.8 * ratio
        expected = (len("dock") / len("docker")) * 0.8
        assert fs.calculate_relevance("docker", "dock") == pytest.approx(expected)

    @pytest.mark.parametrize("query,hist", [("ab", "abc"), ("ab", "abx")])
    def test_too_short_queries_skip_prefix_tiers(self, query, hist):
        # len <= 2 disables prefix tiers; falls through to fuzzy tier which
        # also requires len > 3 → relevance 0 unless exact match.
        assert fs.calculate_relevance(query, hist) == 0.0

    def test_short_history_skips_history_prefix_tier(self):
        # hist length <= 2 can't be a meaningful prefix anchor
        assert fs.calculate_relevance("abcdef", "ab") == 0.0

    def test_fuzzy_similar_queries_get_partial_credit(self):
        # ratio('pyton script', 'python script') is high (>85)
        relevance = fs.calculate_relevance("pyton script", "python script")

        assert 0 < relevance <= 0.7

    def test_fuzzy_below_85_gets_zero(self):
        relevance = fs.calculate_relevance("totally different", "other stuff here!!")

        assert relevance == 0.0

    def test_fuzzy_tier_requires_fuzzywuzzy(self, monkeypatch):
        monkeypatch.setattr(fs, "FUZZY_AVAILABLE", False)

        assert fs.calculate_relevance("pyton script", "python script") == 0.0

    def test_completely_unrelated_is_zero(self):
        assert fs.calculate_relevance("banana", "quantum physics") == 0.0


# ===========================================================================
# find_relevant_contexts
# ===========================================================================


class TestFindRelevantContexts:
    """History scanning for contexts relevant to a query."""

    HISTORY: ClassVar[dict] = {
        "docker": {"docker ps": 3},
        "docker compose": {"compose up": 2},
        "python": {"py script": 1},
    }

    def test_empty_query_returns_empty(self):
        assert fs.find_relevant_contexts("", self.HISTORY) == {}
        assert fs.find_relevant_contexts("   ", self.HISTORY) == {}

    def test_exact_match_included_with_full_relevance(self):
        contexts = fs.find_relevant_contexts("docker", self.HISTORY)

        assert contexts["docker"]["relevance"] == 1.0
        assert contexts["docker"]["snippets"] == {"docker ps": 3}

    def test_prefix_match_included(self):
        contexts = fs.find_relevant_contexts("docker c", self.HISTORY)

        assert "docker compose" in contexts
        assert contexts["docker compose"]["relevance"] > 0

    def test_irrelevant_histories_excluded(self):
        contexts = fs.find_relevant_contexts("docker", self.HISTORY)

        assert "python" not in contexts

    def test_snippets_data_passed_through(self):
        contexts = fs.find_relevant_contexts("python", self.HISTORY)

        assert contexts["python"]["snippets"] == {"py script": 1}

    def test_empty_history_returns_empty(self):
        assert fs.find_relevant_contexts("anything", {}) == {}


# ===========================================================================
# get_context_score
# ===========================================================================


class TestGetContextScore:
    """Contextual score aggregation for a snippet display name."""

    CONTEXTS: ClassVar[dict] = {
        "q1": {"snippets": {"alpha": 2}, "relevance": 1.0},
        "q2": {"snippets": {"alpha": 5}, "relevance": 0.8},
        "q3": {"snippets": {"beta": 9}, "relevance": 0.5},
    }

    def test_max_across_contexts(self):
        # q1: 2*1.0*100=200 ; q2: 5*0.8*100=400 → max 400
        assert fs.get_context_score("alpha", self.CONTEXTS) == 400

    def test_unknown_snippet_scores_zero(self):
        assert fs.get_context_score("ghost", self.CONTEXTS) == 0

    def test_empty_contexts_scores_zero(self):
        assert fs.get_context_score("alpha", {}) == 0

    def test_score_cast_to_int(self):
        # 3 * 0.333 * 100 = 99.9 → int() floors it
        contexts = {"q": {"snippets": {"s": 3}, "relevance": 0.333}}

        assert fs.get_context_score("s", contexts) == int(99.9)


# ===========================================================================
# Module-level behavior
# ===========================================================================


class TestFuzzyAvailabilityDetection:
    """The module must tolerate both import outcomes at load time."""

    def test_module_exports_flag(self):
        # FUZZY_AVAILABLE is set at import time based on fuzzywuzzy presence;
        # in this environment it should be truthy (bundled or system install).
        assert isinstance(fs.FUZZY_AVAILABLE, bool)

"""
Tests for load_snippets() version dispatch in src.database.loader.

The dispatcher routes to the correct backend based on masscode_version.
Unknown/legacy values must fall back to the V3 JSON loader.
"""

import pytest

from src.database.loader import load_snippets


@pytest.fixture
def spy_backends(monkeypatch):
    """Patch all three backend loaders and record which one was called."""
    import src.database.loader as loader_mod

    calls = {"json": 0, "sqlite": 0, "markdown": 0}

    def make(backend):
        def fake(db_path):
            calls[backend] += 1
            return [{"backend": backend}]

        return fake

    monkeypatch.setattr(loader_mod, "load_snippets_json", make("json"))
    monkeypatch.setattr(loader_mod, "load_snippets_sqlite", make("sqlite"))
    monkeypatch.setattr(loader_mod, "load_snippets_markdown", make("markdown"))

    return calls


class TestDispatch:
    """Version strings route to exactly one backend each."""

    def test_v5_routes_to_markdown(self, spy_backends):
        result = load_snippets("/any", "v5")

        assert result == [{"backend": "markdown"}]
        assert spy_backends == {"json": 0, "sqlite": 0, "markdown": 1}

    def test_v4_routes_to_sqlite(self, spy_backends):
        result = load_snippets("/any", "v4")

        assert result == [{"backend": "sqlite"}]
        assert spy_backends["sqlite"] == 1

    @pytest.mark.parametrize("version", ["v3", "", "unknown", "V4", "v30"])
    def test_unknown_versions_fall_back_to_json(self, spy_backends, version):
        # Anything that isn't v5/v4 — including case variants and garbage —
        # is treated as "V3 or earlier"
        result = load_snippets("/any", version)

        assert result == [{"backend": "json"}]

    def test_default_version_is_json(self, spy_backends):
        result = load_snippets("/any")

        assert result == [{"backend": "json"}]


class TestDispatchLogging:
    """The dispatcher logs how many snippets were loaded."""

    def test_logs_loaded_count(self, spy_backends, caplog):
        import logging

        with caplog.at_level(logging.INFO):
            load_snippets("/any", "v3")

        assert any("Loaded 1 snippets" in m for m in caplog.messages)

"""
Tests for the error handler module (src.utils.error_handler).

Simple logging facade — verified through caplog capture.
"""

import logging

from src.utils.error_handler import log_debug, log_error, log_info, log_warning


class TestLogError:
    """log_error formatting combinations."""

    def test_error_alone(self, caplog):
        with caplog.at_level(logging.ERROR):
            log_error(ValueError("boom"), exc_info=False)

        assert "boom" in caplog.text

    def test_with_context_prefix(self, caplog):
        with caplog.at_level(logging.ERROR):
            log_error(ValueError("boom"), context="saving snippet", exc_info=False)

        assert "saving snippet: boom" in caplog.text

    def test_with_message_and_context(self, caplog):
        with caplog.at_level(logging.ERROR):
            log_error(
                ValueError("boom"),
                message="unexpected failure",
                context="writer",
                exc_info=False,
            )

        assert "unexpected failure - writer: boom" in caplog.text

    def test_exc_info_includes_traceback(self, caplog):
        try:
            raise RuntimeError("traceback-marker-12345")
        except RuntimeError as err:
            with caplog.at_level(logging.ERROR):
                log_error(err)

        assert "traceback-marker-12345" in caplog.text
        assert "RuntimeError" in caplog.text  # traceback present


class TestLogLevelHelpers:
    """The remaining facade functions map to the right levels."""

    def test_log_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            log_warning("careful!")

        assert any(
            r.levelno == logging.WARNING and "careful!" in r.message
            for r in caplog.records
        )

    def test_log_info(self, caplog):
        with caplog.at_level(logging.INFO):
            log_info("fyi")

        assert any(
            r.levelno == logging.INFO and "fyi" in r.message for r in caplog.records
        )

    def test_log_debug(self, caplog):
        with caplog.at_level(logging.DEBUG):
            log_debug("whisper")

        assert any(
            r.levelno == logging.DEBUG and "whisper" in r.message
            for r in caplog.records
        )

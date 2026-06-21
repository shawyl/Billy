import io
import logging

from src.logging_config import setup_logging


def test_httpx_polling_logs_are_suppressed_by_default():
    setup_logging("INFO", "WARNING")

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
    assert logging.getLogger("telegram").level == logging.WARNING
    assert logging.getLogger("telegram.ext").level == logging.WARNING


def test_bot_token_like_strings_are_redacted():
    stream = io.StringIO()
    logger = setup_logging("INFO", "INFO")
    handler = logging.StreamHandler(stream)
    for existing in logging.getLogger().handlers:
        for flt in existing.filters:
            handler.addFilter(flt)
    logger.addHandler(handler)
    logger.info("POST https://api.telegram.org/bot123456:ABCdefSecret/getUpdates")
    logger.removeHandler(handler)

    output = stream.getvalue()
    assert "123456:ABCdefSecret" not in output
    assert "https://api.telegram.org/bot<REDACTED>/getUpdates" in output


def test_billy_logs_still_show_app_events():
    logger = setup_logging("INFO", "WARNING")

    assert logger.name == "billy"
    assert logger.isEnabledFor(logging.INFO)

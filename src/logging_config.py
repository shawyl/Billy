"""Central logging setup with quiet third-party logs and token redaction."""

from __future__ import annotations

import logging
import re


PROJECT_LOGGER_NAME = "billy"
THIRD_PARTY_LOGGERS = ("httpx", "httpcore", "telegram", "telegram.ext")


class SecretRedactionFilter(logging.Filter):
    """Redact Telegram bot tokens from log messages before handlers emit them."""

    _patterns = (
        re.compile(r"https://api\.telegram\.org/bot[^/\s]+/", re.I),
        re.compile(r"\bbot[^/\s]+/(?=(?:getUpdates|sendMessage|sendPhoto|sendDocument|answerCallbackQuery)\b)", re.I),
    )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern in self._patterns:
            if pattern.pattern.startswith("https"):
                message = pattern.sub("https://api.telegram.org/bot<REDACTED>/", message)
            else:
                message = pattern.sub("bot<REDACTED>/", message)
        record.msg = message
        record.args = ()
        return True


def setup_logging(log_level: str = "INFO", third_party_log_level: str = "WARNING") -> logging.Logger:
    logging.basicConfig(level=_level(log_level), format="%(levelname)s:%(name)s:%(message)s", force=True)
    redactor = SecretRedactionFilter()
    root = logging.getLogger()
    root.addFilter(redactor)
    for handler in root.handlers:
        handler.addFilter(redactor)
    for name in THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(_level(third_party_log_level))
    logger = logging.getLogger(PROJECT_LOGGER_NAME)
    logger.setLevel(_level(log_level))
    return logger


def _level(value: str) -> int:
    return getattr(logging, str(value or "INFO").upper(), logging.INFO)

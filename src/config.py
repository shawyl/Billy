"""Environment-based configuration for local Telegram and Ollama runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - keeps pure calculation tests dependency-light
    def load_dotenv() -> bool:
        return False


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    ollama_base_url: str
    ollama_text_model: str
    ollama_vision_model: str
    ollama_vision_fallback_model: str | None
    allowed_chat_id: int | None
    consensus_runs: int
    log_level: str
    third_party_log_level: str
    image_debug: bool
    temp_image_dir: str
    default_currency: str
    self_name: str
    result_detail_level: str


def _optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(value)


def _bool_env(value: str | None, *, default: bool = False) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().casefold() in {"1", "true", "yes", "y", "on"}


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").strip(),
        ollama_text_model=os.environ.get("OLLAMA_TEXT_MODEL", "").strip(),
        ollama_vision_model=os.environ.get("OLLAMA_VISION_MODEL", "").strip(),
        ollama_vision_fallback_model=os.environ.get("OLLAMA_VISION_FALLBACK_MODEL", "").strip() or None,
        allowed_chat_id=_optional_int(os.environ.get("ALLOWED_CHAT_ID")),
        consensus_runs=int(os.environ.get("CONSENSUS_RUNS", "5")),
        log_level=os.environ.get("LOG_LEVEL", "INFO").strip().upper(),
        third_party_log_level=os.environ.get("THIRD_PARTY_LOG_LEVEL", "WARNING").strip().upper(),
        image_debug=_bool_env(os.environ.get("IMAGE_DEBUG"), default=False),
        temp_image_dir=os.environ.get("TEMP_IMAGE_DIR", "temp_images").strip(),
        default_currency=os.environ.get("DEFAULT_CURRENCY", "SGD").strip().upper(),
        self_name=os.environ.get("DEFAULT_USER_NAME", "").strip(),
        result_detail_level=os.environ.get("RESULT_DETAIL_LEVEL", "normal").strip().casefold() or "normal",
    )


def validate_runtime_settings(settings: Settings) -> None:
    missing = []
    if not settings.telegram_bot_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not settings.ollama_base_url:
        missing.append("OLLAMA_BASE_URL")
    if not settings.ollama_text_model:
        missing.append("OLLAMA_TEXT_MODEL")
    if not settings.ollama_vision_model:
        missing.append("OLLAMA_VISION_MODEL")
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

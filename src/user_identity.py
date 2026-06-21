"""Resolve the display name used for first-person split instructions."""

from __future__ import annotations


def resolve_current_user_display_name(configured_name: str | None = None, telegram_first_name: str | None = None) -> str:
    configured = (configured_name or "").strip()
    if configured:
        return configured
    first_name = (telegram_first_name or "").strip()
    if first_name:
        return first_name
    return "You"

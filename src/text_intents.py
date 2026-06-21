"""Small dependency-light text intent helpers for Telegram routing."""

from __future__ import annotations

import re

from .conversation_state import Stage


def is_confirmation_text(text: str) -> bool:
    normalized = text.strip().casefold()
    return normalized in {
        "confirm",
        "yes",
        "y",
        "ok",
        "okay",
        "looks good",
        "looks right",
        "looks correct",
        "correct",
        "proceed",
        "calculate",
        "go ahead",
    }


def is_reset_text(text: str) -> bool:
    return text.strip().casefold() in {"new", "reset", "start over", "cancel"}


def confirmation_and_remainder(text: str) -> tuple[bool, str | None]:
    lines = [line.strip() for line in text.splitlines()]
    meaningful = [(index, line) for index, line in enumerate(lines) if line]
    if not meaningful:
        return False, None
    first_index, first_line = meaningful[0]
    if is_confirmation_text(first_line):
        rest = "\n".join(line for line in lines[first_index + 1 :] if line).strip()
        return True, rest or None
    for phrase in ["confirm", "yes", "ok", "okay", "proceed", "calculate", "go ahead", "looks good"]:
        match = re.match(rf"^{re.escape(phrase)}[\s:,-]+(.+)$", first_line, re.I)
        if match:
            rest_parts = [match.group(1).strip(), *[line for line in lines[first_index + 1 :] if line]]
            return True, "\n".join(part for part in rest_parts if part).strip() or None
    return False, None


def status_text(state) -> str:
    if state.stage == Stage.IDLE:
        return "Status: waiting for a bill."
    if state.stage == Stage.COMPLETED:
        if state.last_result_summary:
            return "Status: completed.\n\n" + state.last_result_summary
        return "Status: completed. Send a new bill or type reset."
    return f"Status: {state.stage.value.replace('_', ' ')}."

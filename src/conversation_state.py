"""Simple in-memory per-chat conversation state for Telegram sessions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .models import ExtractedBill


class Stage(str, Enum):
    IDLE = "waiting_for_bill"
    WAITING_BILL_CONFIRMATION = "waiting_for_confirmation"
    WAITING_CHARGE_CLARIFICATION = "waiting_for_charge_clarification"
    WAITING_SPLIT_INSTRUCTIONS = "waiting_for_split_clarification"
    CALCULATING = "calculating"
    COMPLETED = "completed"


@dataclass(slots=True)
class ChatState:
    stage: Stage = Stage.IDLE
    bill: ExtractedBill | None = None
    people: list[str] | None = None
    pending_split_instructions: str | None = None
    split_rules_raw: str | None = None
    pending_ambiguity_message: str | None = None
    receipt_confirmed: bool = False
    canonical_split: dict | None = None
    validation_status: str | None = None
    current_user_display_name: str | None = None
    last_result_summary: str | None = None


class StateStore:
    def __init__(self) -> None:
        self._states: dict[int, ChatState] = {}

    def get(self, chat_id: int) -> ChatState:
        if chat_id not in self._states:
            self._states[chat_id] = ChatState()
        return self._states[chat_id]

    def reset(self, chat_id: int) -> None:
        self._states[chat_id] = ChatState()

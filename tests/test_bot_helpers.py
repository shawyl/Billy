from src.conversation_state import ChatState, Stage
from src.text_intents import is_confirmation_text, is_reset_text, status_text


def test_smart_confirmation_phrases():
    for text in ["Confirm", "yes", "y", "ok", "okay", "looks good", "correct", "proceed", "calculate", "go ahead"]:
        assert is_confirmation_text(text)


def test_reset_phrases():
    for text in ["new", "reset", "start over", "cancel"]:
        assert is_reset_text(text)


def test_status_text_reports_current_state():
    assert "waiting for a bill" in status_text(ChatState()).casefold()
    completed = ChatState(stage=Stage.COMPLETED, last_result_summary="Total: $1.00")
    assert "completed" in status_text(completed).casefold()
    assert "Total: $1.00" in status_text(completed)

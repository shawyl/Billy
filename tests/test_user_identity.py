from src.user_identity import resolve_current_user_display_name


def test_default_user_name_takes_priority():
    assert resolve_current_user_display_name("Alex", "TelegramName") == "Alex"


def test_telegram_first_name_fallback_when_default_blank():
    assert resolve_current_user_display_name("", "Taylor") == "Taylor"


def test_you_fallback_when_no_name_available():
    assert resolve_current_user_display_name("", None) == "You"

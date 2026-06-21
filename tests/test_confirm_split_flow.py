from decimal import Decimal

from src.calculator import calculate_split
from src.conversation_state import ChatState, Stage
from src.formatter import format_concise_result
from src.models import BillItem, ExtractedBill
from src.split_resolver import resolve_manual_split
from src.text_intents import confirmation_and_remainder


def receipt_bill():
    return ExtractedBill(
        currency="SGD",
        items=[
            BillItem(name="Guinness Extra FULL PINT", quantity=Decimal("9"), unit_price=Decimal("15.00"), total=Decimal("135.00")),
            BillItem(name="Suntory Gin", quantity=Decimal("5"), unit_price=Decimal("13.00"), total=Decimal("65.00")),
        ],
        subtotal=Decimal("200.00"),
        tax=Decimal("0.00"),
        service_charge=Decimal("20.00"),
        service_charge_rate=Decimal("10"),
        total=Decimal("220.00"),
        notes=["Not subject to GST"],
    )


def test_confirmation_only_moves_to_waiting_for_split_rules_shape():
    state = ChatState(stage=Stage.WAITING_BILL_CONFIRMATION, bill=receipt_bill())
    confirmed, split_rules = confirmation_and_remainder("Confirm")

    if confirmed and not split_rules:
        state.receipt_confirmed = True
        state.stage = Stage.WAITING_SPLIT_INSTRUCTIONS

    assert state.receipt_confirmed
    assert state.stage == Stage.WAITING_SPLIT_INSTRUCTIONS
    assert split_rules is None


def test_confirmation_plus_split_rules_preserves_remaining_text():
    confirmed, split_rules = confirmation_and_remainder(
        "Confirm\n\n3 Guiness on me, 6 on C, 5 Suntory Gin on Y"
    )

    assert confirmed
    assert split_rules == "3 Guiness on me, 6 on C, 5 Suntory Gin on Y"


def test_quantity_split_typo_inheritance_and_current_user_name():
    resolved = resolve_manual_split(
        receipt_bill(),
        [],
        "3 Guiness on me, 6 on C, 5 Suntory Gin on Y",
        self_name="Alex",
    )

    assert resolved.plan["people"] == ["Alex", "C", "Y"]
    guinness = resolved.plan["items"][0]
    suntory = resolved.plan["items"][1]
    assert guinness["split"] == {"type": "quantity", "allocations": {"Alex": "3", "C": "6"}}
    assert suntory["split"] == {"type": "quantity", "allocations": {"Y": "5"}}
    assert "* Guinness Extra FULL PINT x9: Alex 3, C 6" in resolved.summary
    assert "* Suntory Gin x5: Y 5" in resolved.summary


def test_quantity_split_falls_back_to_you_without_configured_name():
    resolved = resolve_manual_split(
        receipt_bill(),
        [],
        "3 Guiness on me, 6 on C, 5 Suntory Gin on Y",
        self_name="You",
    )

    assert resolved.plan["people"] == ["You", "C", "Y"]
    assert resolved.plan["items"][0]["split"]["allocations"]["You"] == "3"


def test_receipt_quantity_split_calculates_expected_totals_and_validates_five_times():
    resolved = resolve_manual_split(
        receipt_bill(),
        [],
        "3 Guiness on me, 6 on C, 5 Suntory Gin on Y",
        self_name="You",
    )

    results = [calculate_split(resolved.plan) for _ in range(5)]
    assert all(result.canonical() == results[0].canonical() for result in results)
    result = results[0]
    result.validation_runs_requested = 5
    result.validation_runs_matched = 5

    assert result.validation["subtotal"] == Decimal("200.00")
    assert result.validation["service_charge"] == Decimal("20.00")
    assert result.validation["tax"] == Decimal("0.00")
    assert result.total_bill == Decimal("220.00")
    assert result.people["You"]["final"] == Decimal("49.50")
    assert result.people["C"]["final"] == Decimal("99.00")
    assert result.people["Y"]["final"] == Decimal("71.50")
    assert ("Guinness Extra FULL PINT x3", Decimal("45.00")) in result.breakdown["You"]
    assert ("Guinness Extra FULL PINT x6", Decimal("90.00")) in result.breakdown["C"]
    assert ("Suntory Gin x5", Decimal("65.00")) in result.breakdown["Y"]

    concise = format_concise_result(result)
    assert concise.startswith("Total:")
    assert "Concise split" not in concise
    assert "You: $49.50" in concise

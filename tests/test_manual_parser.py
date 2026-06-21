from decimal import Decimal

import pytest

from src.bill_confirmation import apply_charge_instruction, format_bill_confirmation, requires_charge_clarification
from src.calculator import calculate_split
from src.manual_parser import detect_ambiguous_quantity_family_split, parse_manual_bill_text
from src.parser import parse_extracted_bill
from src.models import ValidationError
from src.split_resolver import resolve_manual_split


FAILED_INPUT = """1X Battered Fish Bites —> 14.00
1X Pizza —> 26.00
6X HH Guinness —> 11.00
2X Gin Tonic —> 12.00
1X Guinness —> 14.00

Me, C & Y

Battered fish bites and Pizza split equally among 3
All Gin Tonic on Y
2.5 Guinness on Me, rest on C
"""


def test_failed_input_extracts_all_manual_items():
    parsed = parse_manual_bill_text(FAILED_INPUT, default_currency="SGD", self_name="Alex")

    assert [item.name for item in parsed.bill.items] == [
        "Battered Fish Bites",
        "Pizza",
        "HH Guinness",
        "Gin Tonic",
        "Guinness",
    ]
    assert [item.total for item in parsed.bill.items] == [
        Decimal("14.00"),
        Decimal("26.00"),
        Decimal("66.00"),
        Decimal("24.00"),
        Decimal("14.00"),
    ]
    assert parsed.bill.subtotal == Decimal("144.00")


def test_quantity_prefix_treats_amount_as_unit_price():
    parsed = parse_manual_bill_text("6X HH Guinness —> 11.00", default_currency="SGD")
    item = parsed.bill.items[0]

    assert item.quantity == Decimal("6")
    assert item.unit_price == Decimal("11.00")
    assert item.total == Decimal("66.00")
    assert item.source_line == "6X HH Guinness —> 11.00"


def test_text_confirmation_is_manual_not_receipt():
    parsed = parse_manual_bill_text(FAILED_INPUT, default_currency="SGD", self_name="Alex")
    text = format_bill_confirmation(
        parsed.bill,
        heading="Bill text detected",
        people=parsed.people,
        split_instructions=parsed.split_instructions,
        show_single_quantity=True,
    )

    assert text.startswith("Bill text detected")
    assert "Receipt detected" not in text
    assert "HH Guinness x6 - $66.00" in text


def test_placeholder_item_is_rejected():
    with pytest.raises(ValidationError, match="placeholder"):
        parse_extracted_bill('{"currency":"SGD","items":[{"name":"Item name","quantity":"1","total":"0.00"}]}')


def test_gst_service_clarification_required_when_missing():
    parsed = parse_manual_bill_text(FAILED_INPUT, default_currency="SGD", self_name="Alex")

    assert requires_charge_clarification(parsed.bill)


def test_detects_people_and_split_rules_from_same_message():
    parsed = parse_manual_bill_text(FAILED_INPUT, default_currency="SGD", self_name="Alex")

    assert parsed.people == ["Alex", "C", "Y"]
    assert parsed.split_instructions == [
        "Battered fish bites and Pizza split equally among 3",
        "All Gin Tonic on Y",
        "2.5 Guinness on Alex, rest on C",
    ]


def test_asks_clarification_for_combined_guinness_different_prices():
    parsed = parse_manual_bill_text(FAILED_INPUT, default_currency="SGD", self_name="Alex")

    assert parsed.ambiguity_message is not None
    assert "I found 7 Guinness total, but they are priced differently" in parsed.ambiguity_message
    assert "HH Guinness x6 at $11.00 each" in parsed.ambiguity_message
    assert "Guinness x1 at $14.00 each" in parsed.ambiguity_message


def test_final_calculation_is_blocked_until_guinness_ambiguity_resolved():
    parsed = parse_manual_bill_text(FAILED_INPUT, default_currency="SGD", self_name="Alex")

    assert detect_ambiguous_quantity_family_split(parsed.bill, parsed.split_text, "Alex") is not None


def test_clarification_resolves_remaining_guinness_allocations():
    text = """1X Battered Fish Bites -> 14.00
1X Pizza -> 26.00
6X HH Guinness -> 11.00
2X Gin Tonic -> 12.00
1X Guinness -> 14.00

Me, C & Y

Battered fish bites and Pizza split equally among 3
All Gin Tonic on Y
Alex: 2.5 HH Guinness, C: remaining HH Guinness + Guinness

Add 10% GST and 9% service charge
"""
    parsed = parse_manual_bill_text(text, default_currency="SGD", self_name="Alex")
    bill = apply_charge_instruction(parsed.bill, text)

    assert detect_ambiguous_quantity_family_split(bill, parsed.split_text, "Alex") is None
    resolved = resolve_manual_split(bill, parsed.people, parsed.split_text, "Alex")

    hh_guinness = next(item for item in resolved.plan["items"] if item["name"] == "HH Guinness")
    regular_guinness = next(item for item in resolved.plan["items"] if item["name"] == "Guinness")
    assert hh_guinness["split"] == {"type": "quantity", "allocations": {"Alex": "2.5", "C": "3.5"}}
    assert regular_guinness["split"] == {"type": "fixed", "person": "C"}


def test_resolved_clarification_calculates_without_llm_instability():
    text = """1X Battered Fish Bites -> 14.00
1X Pizza -> 26.00
6X HH Guinness -> 11.00
2X Gin Tonic -> 12.00
1X Guinness -> 14.00

Me, C & Y

Battered fish bites and Pizza split equally among 3
All Gin Tonic on Y
Alex: 2.5 HH Guinness, C: remaining HH Guinness + Guinness

Add 10% GST and 9% service charge
"""
    parsed = parse_manual_bill_text(text, default_currency="SGD", self_name="Alex")
    bill = apply_charge_instruction(parsed.bill, text)
    resolved = resolve_manual_split(bill, parsed.people, parsed.split_text, "Alex")

    results = [calculate_split(resolved.plan) for _ in range(5)]

    assert all(result.canonical() == results[0].canonical() for result in results)
    assert results[0].validation["subtotal"] == Decimal("144.00")
    assert results[0].validation["tax"] == Decimal("14.40")
    assert results[0].validation["service_charge"] == Decimal("12.96")
    assert results[0].total_bill == Decimal("171.36")
    assert sum(row["final"] for row in results[0].people.values()) == Decimal("171.36")


def test_exact_9_gst_10_service_scenario_totals_and_rounding():
    text = """1X Battered Fish Bites -> 14.00
1X Pizza -> 26.00
6X HH Guinness -> 11.00
2X Gin Tonic -> 12.00
1X Guinness -> 14.00

Me, C & Y

Battered fish bites and Pizza split equally among 3
All Gin Tonic on Y
Alex: 2.5 HH Guinness, C: remaining HH Guinness + Guinness

Add 9% GST and 10% service charge
"""
    parsed = parse_manual_bill_text(text, default_currency="SGD", self_name="Alex")
    bill = apply_charge_instruction(parsed.bill, text)
    resolved = resolve_manual_split(bill, parsed.people, parsed.split_text, "Alex")
    result = calculate_split(resolved.plan)

    assert bill.tax_rate == Decimal("9")
    assert bill.service_charge_rate == Decimal("10")
    assert result.validation["subtotal"] == Decimal("144.00")
    assert result.validation["tax"] == Decimal("12.96")
    assert result.validation["service_charge"] == Decimal("14.40")
    assert result.total_bill == Decimal("171.36")
    assert result.people["Alex"]["final"] == Decimal("48.59")
    assert result.people["C"]["final"] == Decimal("78.34")
    assert result.people["Y"]["final"] == Decimal("44.43")
    assert sum(row["final"] for row in result.people.values()) == Decimal("171.36")


def test_exact_scenario_with_three_hh_guinness_totals_and_preview():
    text = """1X Battered Fish Bites -> 14.00
1X Pizza -> 26.00
6X HH Guinness -> 11.00
2X Gin Tonic -> 12.00
1X Guinness -> 14.00

Me, C & Y

Battered fish bites and Pizza split equally among 3
All Gin Tonic on Y
Alex: 3 HH Guinness, C: remaining HH Guinness + Guinness

Add 9% GST and 10% service charge
"""
    parsed = parse_manual_bill_text(text, default_currency="SGD", self_name="Alex")
    bill = apply_charge_instruction(parsed.bill, text)
    resolved = resolve_manual_split(bill, parsed.people, parsed.split_text, "Alex")
    result = calculate_split(resolved.plan)

    assert "* Fish bites + Pizza: Alex, C, Y equally" in resolved.summary
    assert "* HH Guinness x6: Alex 3, C 3" in resolved.summary
    assert "Validation: running 5 checks..." in resolved.summary
    assert result.validation["subtotal"] == Decimal("144.00")
    assert result.validation["tax"] == Decimal("12.96")
    assert result.validation["service_charge"] == Decimal("14.40")
    assert result.total_bill == Decimal("171.36")
    assert sum(row["final"] for row in result.people.values()) == Decimal("171.36")

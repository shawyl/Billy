from decimal import Decimal

from src.calculator import calculate_split
from src.formatter import format_concise_result, format_final_result


def test_final_result_is_human_readable_without_json():
    result = calculate_split(
        {
            "currency": "SGD",
            "people": ["Alex", "C"],
            "items": [{"name": "Pizza", "quantity": "1", "total": "24.00", "split": {"type": "equal", "people": ["Alex", "C"]}}],
            "service_charge": "0",
            "tax": "0",
            "discount": "0",
            "charge_allocation": "proportional_by_subtotal",
        }
    )

    text = format_final_result(result)

    assert "Bill split result" in text
    assert "Validation: 0/0 matched" in text
    assert "Alex: $12.00" in text
    assert "{" not in text
    assert "}" not in text


def test_final_result_includes_validation_status():
    result = calculate_split(
        {
            "currency": "SGD",
            "people": ["Alex", "C"],
            "items": [{"name": "Pizza", "quantity": "1", "total": "24.00", "split": {"type": "equal", "people": ["Alex", "C"]}}],
            "service_charge": "0",
            "tax": "0",
            "discount": "0",
            "charge_allocation": "proportional_by_subtotal",
        }
    )
    result.validation_runs_requested = 5
    result.validation_runs_matched = 5

    assert "Validation: 5/5 matched" in format_final_result(result)


def test_concise_result_has_no_breakdown_or_notes():
    result = calculate_split(
        {
            "currency": "SGD",
            "people": ["Alex", "C"],
            "items": [{"name": "Pizza", "quantity": "1", "total": "24.00", "split": {"type": "equal", "people": ["Alex", "C"]}}],
            "service_charge": "0",
            "tax": "0",
            "discount": "0",
            "charge_allocation": "proportional_by_subtotal",
        }
    )

    text = format_concise_result(result)

    assert text.startswith("Total:")
    assert "Concise split" not in text
    assert text.splitlines() == ["Total: $24.00", "", "Alex: $12.00", "C: $12.00"]
    assert "Breakdown" not in text
    assert "Notes" not in text
    assert "Validation" not in text
    assert "matched" not in text
    assert "*" not in text


def test_final_result_groups_shared_food_when_safe():
    result = calculate_split(
        {
            "currency": "SGD",
            "people": ["Alex", "C", "Y"],
            "items": [
                {"name": "Battered Fish Bites", "quantity": "1", "total": "14.00", "split": {"type": "equal", "people": ["Alex", "C", "Y"]}},
                {"name": "Pizza", "quantity": "1", "total": "26.00", "split": {"type": "equal", "people": ["Alex", "C", "Y"]}},
            ],
            "service_charge": "0",
            "tax": "0",
            "discount": "0",
            "charge_allocation": "proportional_by_subtotal",
        }
    )

    text = format_final_result(result)

    assert "Shared food: $13.33" in text
    assert "Battered Fish Bites: $4.67" not in text

from decimal import Decimal

import pytest

from src.calculator import calculate_split
from src.models import ValidationError


def base_plan():
    return {
        "currency": "SGD",
        "people": ["Alex", "C", "Y"],
        "items": [
            {
                "name": "Pizza",
                "quantity": "1",
                "total": "24.00",
                "split": {"type": "equal", "people": ["Alex", "C", "Y"]},
            },
            {
                "name": "Guinness",
                "quantity": "5",
                "total": "50.00",
                "split": {"type": "quantity", "allocations": {"Alex": "2", "C": "3"}},
            },
            {
                "name": "Fries",
                "quantity": "1",
                "total": "12.00",
                "split": {"type": "equal", "people": ["Alex", "Y"]},
            },
        ],
        "service_charge": "8.60",
        "tax": "7.80",
        "discount": "0.00",
        "charge_allocation": "proportional_by_subtotal",
        "rounding": "half_up_2dp",
    }


def test_equal_selected_quantity_and_proportional_charges():
    result = calculate_split(base_plan())

    assert result.people["Alex"]["subtotal"] == Decimal("34.00")
    assert result.people["C"]["subtotal"] == Decimal("38.00")
    assert result.people["Y"]["subtotal"] == Decimal("14.00")
    assert result.people["Alex"]["final"] == Decimal("40.48")
    assert result.people["C"]["final"] == Decimal("45.25")
    assert result.people["Y"]["final"] == Decimal("16.67")
    assert result.total_bill == Decimal("102.40")


def test_discount_allocation_reduces_final_total():
    plan = base_plan()
    plan["discount"] = "8.60"
    result = calculate_split(plan)

    assert result.people["Alex"]["discount"] == Decimal("3.40")
    assert result.people["C"]["discount"] == Decimal("3.80")
    assert result.people["Y"]["discount"] == Decimal("1.40")
    assert result.total_bill == Decimal("93.80")


def test_rounding_preserves_bill_total():
    plan = {
        "currency": "SGD",
        "people": ["A", "B", "C"],
        "items": [{"name": "Snack", "quantity": "1", "total": "10.00", "split": {"type": "equal", "people": ["A", "B", "C"]}}],
        "service_charge": "0",
        "tax": "0",
        "discount": "0",
        "charge_allocation": "proportional_by_subtotal",
    }
    result = calculate_split(plan)

    assert sum(row["final"] for row in result.people.values()) == Decimal("10.00")
    assert sorted(row["final"] for row in result.people.values()) == [Decimal("3.33"), Decimal("3.33"), Decimal("3.34")]


def test_missing_person_in_allocation_fails():
    plan = base_plan()
    plan["items"][0]["split"]["people"] = ["Alex", "NotOnBill"]

    with pytest.raises(ValidationError, match="unknown people"):
        calculate_split(plan)


def test_total_mismatch_detection():
    plan = base_plan()
    plan["total"] = "101.00"

    with pytest.raises(ValidationError, match="total mismatch"):
        calculate_split(plan)


def test_quantity_mismatch_detection():
    plan = base_plan()
    plan["items"][1]["split"]["allocations"] = {"Alex": "1", "C": "3"}

    with pytest.raises(ValidationError, match="quantity mismatch"):
        calculate_split(plan)


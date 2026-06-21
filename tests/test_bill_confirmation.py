from decimal import Decimal

from src.bill_confirmation import apply_charge_instruction, format_bill_confirmation, requires_charge_clarification
from src.models import BillItem, ExtractedBill


def test_mandatory_gst_service_prompt_when_missing():
    bill = ExtractedBill(currency="SGD", items=[BillItem(name="Pizza", quantity=Decimal("1"), total=Decimal("24.00"))])

    assert requires_charge_clarification(bill)
    text = format_bill_confirmation(bill)
    assert "GST: Not found" in text
    assert "Should I include GST and service charge?" in text


def test_no_gst_service_prompt_when_present():
    bill = ExtractedBill(
        currency="SGD",
        items=[BillItem(name="Pizza", quantity=Decimal("1"), total=Decimal("24.00"))],
        tax=Decimal("2.16"),
        service_charge=Decimal("2.40"),
        total=Decimal("28.56"),
    )

    assert not requires_charge_clarification(bill)
    text = format_bill_confirmation(bill)
    assert "Also, should I include GST and service charge?" not in text
    assert "Reply \"Confirm\" if the bill looks right" in text


def test_charge_instruction_percentages_are_deterministic():
    bill = ExtractedBill(currency="SGD", items=[BillItem(name="Pizza", quantity=Decimal("1"), total=Decimal("100.00"))])

    updated = apply_charge_instruction(bill, "Add 10% service charge + 9% GST")

    assert updated.service_charge == Decimal("10.00")
    assert updated.tax == Decimal("9.00")
    assert updated.total == Decimal("119.00")


def test_charge_instruction_labels_are_literal():
    bill = ExtractedBill(currency="SGD", items=[BillItem(name="Pizza", quantity=Decimal("1"), total=Decimal("144.00"))])

    updated = apply_charge_instruction(bill, "Add 10% GST and 9% service charge")

    assert updated.tax == Decimal("14.40")
    assert updated.service_charge == Decimal("12.96")
    assert updated.total == Decimal("171.36")


def test_charge_instruction_from_original_message_uses_literal_labels():
    bill = ExtractedBill(currency="SGD", items=[BillItem(name="Bill", quantity=Decimal("1"), total=Decimal("144.00"))])

    updated = apply_charge_instruction(bill, "Add 9% GST and 10% service charge")

    assert updated.tax_rate == Decimal("9")
    assert updated.service_charge_rate == Decimal("10")
    assert updated.tax == Decimal("12.96")
    assert updated.service_charge == Decimal("14.40")
    assert updated.total == Decimal("171.36")


def test_confirmation_shows_found_charge_percentages_without_reasking():
    bill = ExtractedBill(
        currency="SGD",
        items=[BillItem(name="Bill", quantity=Decimal("1"), total=Decimal("144.00"))],
        subtotal=Decimal("144.00"),
    )
    charged = apply_charge_instruction(bill, "Add 9% GST and 10% service charge")

    text = format_bill_confirmation(
        charged,
        heading="Bill text detected",
        people=["Alex", "C", "Y"],
        split_instructions=["Bill split equally among Alex, C, and Y"],
        show_single_quantity=True,
    )

    assert "GST 9%: $12.96" in text
    assert "Service charge 10%: $14.40" in text
    assert "Estimated total: $171.36" in text
    assert "Should I include GST and service charge?" not in text
    assert "After that, send me how you want to split the bill." not in text
    assert "I also found" not in text
    assert "Items:" in text
    assert "People:" in text
    assert "Split rules:" in text


def test_receipt_confirmation_shows_charge_amounts_rates_discount_and_total():
    bill = ExtractedBill(
        currency="SGD",
        items=[
            BillItem(name="Guinness", quantity=Decimal("1"), total=Decimal("14.00")),
            BillItem(name="Tanqueray Gin Glass", quantity=Decimal("1"), total=Decimal("12.00")),
        ],
        subtotal=Decimal("26.00"),
        tax=Decimal("2.32"),
        tax_rate=Decimal("9"),
        service_charge=Decimal("2.34"),
        discount=Decimal("2.60"),
        total=Decimal("28.06"),
    )

    text = format_bill_confirmation(bill)

    assert "Service charge: $2.34" in text
    assert "GST 9%: $2.32" in text
    assert "Discount: -$2.60" in text
    assert "Total: $28.06" in text
    assert "Should I include GST and service charge?" not in text


def test_receipt_confirmation_shows_not_subject_to_gst():
    bill = ExtractedBill(
        currency="SGD",
        items=[BillItem(name="Guinness Extra FULL PINT", quantity=Decimal("9"), total=Decimal("135.00"))],
        subtotal=Decimal("135.00"),
        tax=Decimal("0.00"),
        service_charge=Decimal("13.50"),
        service_charge_rate=Decimal("10"),
        total=Decimal("148.50"),
        notes=["Not subject to GST"],
    )

    text = format_bill_confirmation(bill)

    assert "Service charge 10%: $13.50" in text
    assert "GST: $0.00, not subject to GST" in text

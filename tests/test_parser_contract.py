import pytest

from src.parser import parse_extracted_bill, parse_json_object, validate_allocation_plan
from src.models import ValidationError


def test_parse_extracted_bill_contract():
    bill = parse_extracted_bill(
        """
        {
          "currency": "SGD",
          "items": [{"name": "Pizza", "quantity": "1", "unit_price": null, "total": "24.00"}],
          "subtotal": "24.00",
          "tax": null,
          "service_charge": null,
          "discount": null,
          "total": "24.00"
        }
        """
    )

    assert bill.currency == "SGD"
    assert bill.items[0].name == "Pizza"


def test_invalid_json_rejected():
    with pytest.raises(ValidationError, match="invalid JSON"):
        parse_json_object("Here is not JSON")


def test_json_inside_model_text_is_accepted():
    assert parse_json_object('Here is the JSON: {"ok": true}') == {"ok": True}


def test_allocation_plan_requires_split():
    with pytest.raises(ValidationError, match="split is required"):
        validate_allocation_plan({"people": ["A"], "items": [{"name": "Pizza"}]})


def test_receipt_payload_prefers_your_bill_over_grand_total():
    bill = parse_extracted_bill(
        """
        {
          "currency": "SGD",
          "items": [
            {"name": "Guinness", "quantity": "1", "line_total": "14.00"},
            {"name": "Tanqueray Gin Glass", "quantity": "1", "line_total": "12.00"}
          ],
          "service_charge": {"amount": "2.34", "percentage": null},
          "gst": {"amount": "2.32", "percentage": "9"},
          "discount": "2.60",
          "grand_total": "26.00",
          "your_bill": "28.06",
          "notes": []
        }
        """
    )

    assert bill.subtotal == bill.item_subtotal()
    assert bill.total == bill.total.__class__("28.06")
    assert bill.tax_rate == bill.tax_rate.__class__("9")


def test_receipt_payload_cleans_ocr_quantity_and_currency_amounts():
    bill = parse_extracted_bill(
        """
        {
          "currency": "SGD",
          "items": [
            {"name": "Guinness", "quantity": "x1", "unit_price": "S$14.00 each", "line_total": "S$14.00"},
            {"name": "Tanqueray Gin Glass", "quantity": "1 pc", "unit_price": "$12.00", "line_total": "$12.00"}
          ],
          "service_charge": {"amount": "S$2.34", "percentage": null},
          "gst": {"amount": "S$2.32", "percentage": "9%"},
          "discount": "-S$2.60",
          "grand_total": "S$26.00",
          "your_bill": "S$28.06"
        }
        """
    )

    assert bill.items[0].quantity == bill.items[0].quantity.__class__("1")
    assert bill.items[0].unit_price == bill.items[0].unit_price.__class__("14.00")
    assert bill.items[0].total == bill.items[0].total.__class__("14.00")
    assert bill.items[1].unit_price == bill.items[1].unit_price.__class__("12.00")
    assert bill.tax_rate == bill.tax_rate.__class__("9")
    assert bill.discount == bill.discount.__class__("2.60")
    assert bill.total == bill.total.__class__("28.06")


def test_receipt_payload_prefers_payable_for_physical_receipt():
    bill = parse_extracted_bill(
        """
        {
          "currency": "SGD",
          "items": [
            {"name": "Guinness Extra FULL PINT", "quantity": "9", "line_total": "135.00"},
            {"name": "Suntory Gin", "quantity": "5", "line_total": "65.00"}
          ],
          "subtotal": "200.00",
          "service_charge": {"amount": "20.00", "percentage": "10"},
          "gst": {"amount": "0.00", "percentage": null},
          "payable": "220.00",
          "notes": ["Not subject to GST"]
        }
        """
    )

    assert bill.total == bill.total.__class__("220.00")
    assert bill.tax == bill.tax.__class__("0.00")
    assert bill.service_charge_rate == bill.service_charge_rate.__class__("10")

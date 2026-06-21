from pathlib import Path

from PIL import Image

from src.bill_confirmation import format_incomplete_receipt_warning
from src.parser import parse_extracted_bill
from src.receipt_extraction import extract_receipt_from_image
from src.receipt_validation import validate_receipt_consistency


PARTIAL_QLUB = """
{
  "currency": "SGD",
  "items": [
    {"name": "Guinness", "quantity": "1", "line_total": "14.00"}
  ],
  "service_charge": {"amount": "2.34", "percentage": null},
  "gst": {"amount": "2.32", "percentage": "9"},
  "discount": "2.60",
  "grand_total": "26.00",
  "your_bill": "28.06"
}
"""


REPAIRED_QLUB = """
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
  "your_bill": "28.06"
}
"""


class FakeVisionClient:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def generate_vision(self, prompt, image_path, *, model=None):
        self.calls.append({"prompt": prompt, "image_path": Path(image_path), "model": model})
        if self.outputs:
            return self.outputs.pop(0)
        return PARTIAL_QLUB


def _image(path):
    Image.new("RGB", (240, 420), color="white").save(path)


def test_partial_qlub_extraction_is_incomplete_and_triggers_repair(tmp_path):
    source = tmp_path / "receipt.jpg"
    _image(source)
    client = FakeVisionClient([PARTIAL_QLUB, PARTIAL_QLUB])

    result = extract_receipt_from_image(
        client,
        source,
        default_currency="SGD",
        vision_model="vision-a",
        allow_repair=True,
    )

    assert result.validation.status == "incomplete_extraction"
    assert result.validation.reason == "item_sum_mismatch"
    assert result.validation.missing_amount == result.validation.missing_amount.__class__("12.00")
    assert len(client.calls) >= 2
    assert "Previous extraction" in client.calls[1]["prompt"]


def test_repair_output_adds_missing_tanqueray_and_validates(tmp_path):
    source = tmp_path / "receipt.jpg"
    _image(source)
    client = FakeVisionClient([PARTIAL_QLUB, REPAIRED_QLUB])

    result = extract_receipt_from_image(
        client,
        source,
        default_currency="SGD",
        vision_model="vision-a",
        allow_repair=True,
    )

    assert result.validation.status == "valid"
    assert result.repair_used
    assert [item.name for item in result.bill.items] == ["Guinness", "Tanqueray Gin Glass"]
    assert result.bill.item_subtotal() == result.bill.item_subtotal().__class__("26.00")
    assert result.bill.subtotal == result.bill.subtotal.__class__("26.00")
    assert result.bill.total == result.bill.total.__class__("28.06")


def test_incomplete_receipt_warning_is_not_normal_confirmation():
    bill = parse_extracted_bill(PARTIAL_QLUB)
    validation = validate_receipt_consistency(bill)
    bill.validation_status = validation.status
    bill.validation_reason = validation.reason
    bill.missing_amount = validation.missing_amount

    text = format_incomplete_receipt_warning(bill)

    assert text.startswith("Receipt detected, but I may have missed some items.")
    assert "Guinness x1 - $14.00" in text
    assert "$12.00 is unaccounted for" in text
    assert "Please confirm the missing item" in text


def test_qlub_your_bill_grand_total_and_discount_validate():
    bill = parse_extracted_bill(REPAIRED_QLUB)
    validation = validate_receipt_consistency(bill)

    assert bill.subtotal == bill.subtotal.__class__("26.00")
    assert bill.total == bill.total.__class__("28.06")
    assert bill.discount == bill.discount.__class__("2.60")
    assert validation.status == "valid"
    assert validation.charges == validation.charges.__class__("4.66")

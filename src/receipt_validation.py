"""Receipt consistency checks for detecting partial image extraction."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from .calculator import money
from .models import ExtractedBill


@dataclass(frozen=True, slots=True)
class ReceiptValidation:
    status: str
    reason: str | None
    item_sum: Decimal
    explicit_subtotal: Decimal | None
    final_total: Decimal | None
    charges: Decimal
    discount: Decimal
    missing_amount: Decimal | None = None

    @property
    def is_valid(self) -> bool:
        return self.status == "valid"


def validate_receipt_consistency(bill: ExtractedBill) -> ReceiptValidation:
    item_sum = money(bill.item_subtotal())
    explicit_subtotal = money(bill.subtotal) if bill.subtotal is not None else None
    final_total = money(bill.total) if bill.total is not None else None
    charges = money((bill.tax or Decimal("0")) + (bill.service_charge or Decimal("0")))
    discount = money(abs(bill.discount or Decimal("0")))

    if explicit_subtotal is not None and abs(item_sum - explicit_subtotal) > Decimal("0.02"):
        missing = money(explicit_subtotal - item_sum)
        status = "incomplete_extraction" if missing > 0 else "total_mismatch"
        return ReceiptValidation(
            status=status,
            reason="item_sum_mismatch",
            item_sum=item_sum,
            explicit_subtotal=explicit_subtotal,
            final_total=final_total,
            charges=charges,
            discount=discount,
            missing_amount=missing,
        )

    if final_total is not None and explicit_subtotal is not None:
        expected = money(explicit_subtotal + charges - discount)
        if abs(expected - final_total) > Decimal("0.02"):
            return ReceiptValidation(
                status="total_mismatch",
                reason="final_total_mismatch",
                item_sum=item_sum,
                explicit_subtotal=explicit_subtotal,
                final_total=final_total,
                charges=charges,
                discount=discount,
                missing_amount=money(final_total - expected),
            )

    return ReceiptValidation(
        status="valid",
        reason=None,
        item_sum=item_sum,
        explicit_subtotal=explicit_subtotal,
        final_total=final_total,
        charges=charges,
        discount=discount,
    )


def attach_receipt_validation(bill: ExtractedBill, validation: ReceiptValidation) -> ExtractedBill:
    notes = list(bill.notes)
    if not validation.is_valid and validation.reason:
        note = f"Receipt validation: {validation.reason}"
        if note not in notes:
            notes.append(note)
    return replace(
        bill,
        validation_status=validation.status,
        validation_reason=validation.reason,
        missing_amount=validation.missing_amount,
        extraction_confidence="high" if validation.is_valid else "low",
        notes=notes,
    )


def validation_log_text(validation: ReceiptValidation) -> str:
    return (
        "Receipt validation: "
        f"items={validation.item_sum} "
        f"explicit_subtotal={validation.explicit_subtotal} "
        f"final_total={validation.final_total} "
        f"charges={validation.charges} "
        f"discount={validation.discount} "
        f"status={validation.status} "
        f"reason={validation.reason} "
        f"missing_amount={validation.missing_amount}"
    )

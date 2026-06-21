"""Shared data models and strict validation helpers for bills and splits."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any


class ValidationError(ValueError):
    """Raised when LLM output or normalized calculation input is invalid."""


MoneyInput = str | int | float | Decimal | None


def decimal_from(value: MoneyInput, field_name: str, *, allow_none: bool = False) -> Decimal | None:
    if value is None:
        if allow_none:
            return None
        raise ValidationError(f"{field_name} is required")
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValidationError(f"{field_name} must be a decimal value") from exc


def optional_decimal_from(value: MoneyInput, field_name: str) -> Decimal | None:
    return decimal_from(value, field_name, allow_none=True)


@dataclass(slots=True)
class BillItem:
    name: str
    total: Decimal
    quantity: Decimal = Decimal("1")
    unit_price: Decimal | None = None
    source_line: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BillItem":
        if not isinstance(data, dict):
            raise ValidationError("each item must be an object")
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValidationError("item.name is required")
        quantity = decimal_from(data.get("quantity", 1), f"item.quantity for {name}")
        total = decimal_from(data.get("total"), f"item.total for {name}")
        unit_price = optional_decimal_from(data.get("unit_price"), f"item.unit_price for {name}")
        if quantity is None or quantity <= 0:
            raise ValidationError(f"item.quantity for {name} must be greater than zero")
        if total is None or total < 0:
            raise ValidationError(f"item.total for {name} must be zero or greater")
        if unit_price is not None and unit_price < 0:
            raise ValidationError(f"item.unit_price for {name} must be zero or greater")
        source_line = data.get("source_line")
        return cls(
            name=name,
            quantity=quantity,
            unit_price=unit_price,
            total=total,
            source_line=str(source_line) if source_line else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "quantity": str(self.quantity),
            "unit_price": str(self.unit_price) if self.unit_price is not None else None,
            "total": str(self.total),
            "source_line": self.source_line,
        }


@dataclass(slots=True)
class ExtractedBill:
    currency: str
    items: list[BillItem]
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    service_charge: Decimal | None = None
    tax_rate: Decimal | None = None
    service_charge_rate: Decimal | None = None
    discount: Decimal | None = None
    total: Decimal | None = None
    total_is_estimated: bool = False
    extraction_confidence: str | None = None
    validation_status: str | None = None
    validation_reason: str | None = None
    missing_amount: Decimal | None = None
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any], default_currency: str = "SGD") -> "ExtractedBill":
        if not isinstance(data, dict):
            raise ValidationError("bill JSON must be an object")
        raw_items = data.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise ValidationError("bill.items must be a non-empty list")
        items = [BillItem.from_dict(item) for item in raw_items]
        currency = str(data.get("currency") or default_currency).strip().upper()
        if not currency:
            currency = default_currency
        notes = data.get("notes") or []
        if not isinstance(notes, list):
            notes = [str(notes)]
        return cls(
            currency=currency,
            items=items,
            subtotal=optional_decimal_from(data.get("subtotal"), "subtotal"),
            tax=optional_decimal_from(data.get("tax", data.get("gst")), "tax"),
            service_charge=optional_decimal_from(data.get("service_charge"), "service_charge"),
            tax_rate=optional_decimal_from(data.get("tax_rate", data.get("gst_rate")), "tax_rate"),
            service_charge_rate=optional_decimal_from(data.get("service_charge_rate"), "service_charge_rate"),
            discount=optional_decimal_from(data.get("discount"), "discount"),
            total=optional_decimal_from(data.get("total", data.get("final_total")), "total"),
            total_is_estimated=bool(data.get("total_is_estimated", False)),
            extraction_confidence=str(data.get("extraction_confidence")).strip() if data.get("extraction_confidence") is not None else None,
            validation_status=str(data.get("validation_status")).strip() if data.get("validation_status") is not None else None,
            validation_reason=str(data.get("validation_reason")).strip() if data.get("validation_reason") is not None else None,
            missing_amount=optional_decimal_from(data.get("missing_amount"), "missing_amount"),
            notes=[str(note) for note in notes if str(note).strip()],
        )

    def item_subtotal(self) -> Decimal:
        return sum((item.total for item in self.items), Decimal("0"))

    def effective_subtotal(self) -> Decimal:
        return self.subtotal if self.subtotal is not None else self.item_subtotal()

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "items": [item.to_dict() for item in self.items],
            "subtotal": str(self.subtotal) if self.subtotal is not None else None,
            "tax": str(self.tax) if self.tax is not None else None,
            "service_charge": str(self.service_charge) if self.service_charge is not None else None,
            "tax_rate": str(self.tax_rate) if self.tax_rate is not None else None,
            "service_charge_rate": str(self.service_charge_rate) if self.service_charge_rate is not None else None,
            "discount": str(self.discount) if self.discount is not None else None,
            "total": str(self.total) if self.total is not None else None,
            "total_is_estimated": self.total_is_estimated,
            "extraction_confidence": self.extraction_confidence,
            "validation_status": self.validation_status,
            "validation_reason": self.validation_reason,
            "missing_amount": str(self.missing_amount) if self.missing_amount is not None else None,
            "notes": self.notes,
        }


@dataclass(slots=True)
class CalculationResult:
    currency: str
    total_bill: Decimal
    people: dict[str, dict[str, Decimal]]
    breakdown: dict[str, list[tuple[str, Decimal]]]
    notes: list[str]
    validation: dict[str, Decimal]
    validation_runs_requested: int = 0
    validation_runs_matched: int = 0

    def canonical(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "total_bill": f"{self.total_bill:.2f}",
            "people": {
                name: {key: f"{value:.2f}" for key, value in totals.items()}
                for name, totals in sorted(self.people.items())
            },
            "validation": {key: f"{value:.2f}" for key, value in sorted(self.validation.items())},
        }


@dataclass(slots=True)
class ConsensusSuccess:
    result: CalculationResult
    runs: list[dict[str, Any]]


@dataclass(slots=True)
class ConsensusFailure:
    reason: str
    details: list[str]
    runs: list[dict[str, Any]]
    validation_runs_requested: int = 0
    validation_runs_matched: int = 0

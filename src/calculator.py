"""Deterministic Decimal-based bill splitting engine."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .models import CalculationResult, ValidationError, decimal_from

CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _format_decimal(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _cents(value: Decimal) -> int:
    return int((money(value) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _from_cents(cents: int) -> Decimal:
    return Decimal(cents) / Decimal("100")


def _allocate_cents(amount: Decimal, weights: dict[str, Decimal]) -> dict[str, Decimal]:
    if not weights:
        raise ValidationError("cannot allocate amount without people")
    if any(weight < 0 for weight in weights.values()):
        raise ValidationError("allocation weights cannot be negative")
    total_weight = sum(weights.values(), Decimal("0"))
    if total_weight <= 0:
        raise ValidationError("allocation weights must be greater than zero")

    target_cents = _cents(amount)
    exact = {
        person: (Decimal(target_cents) * weight / total_weight)
        for person, weight in weights.items()
    }
    floors = {person: int(value.to_integral_value(rounding="ROUND_FLOOR")) for person, value in exact.items()}
    remaining = target_cents - sum(floors.values())
    order = sorted(exact, key=lambda person: (exact[person] - floors[person], person), reverse=True)
    for person in order[:remaining]:
        floors[person] += 1
    return {person: _from_cents(cents) for person, cents in floors.items()}


def _allocate_exact(amount: Decimal, weights: dict[str, Decimal]) -> dict[str, Decimal]:
    if not weights:
        raise ValidationError("cannot allocate amount without people")
    total_weight = sum(weights.values(), Decimal("0"))
    if total_weight <= 0:
        raise ValidationError("allocation weights must be greater than zero")
    return {person: amount * weight / total_weight for person, weight in weights.items()}


def _require_people(known_people: set[str], referenced: list[str], item_name: str) -> None:
    missing = [person for person in referenced if person not in known_people]
    if missing:
        raise ValidationError(f"{item_name} references unknown people: {', '.join(missing)}")


def _find_plan_item(items: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [item for item in items if str(item.get("name", "")).strip().casefold() == name.casefold()]
    if len(matches) != 1:
        raise ValidationError(f"allocation for item '{name}' was not found exactly once")
    return matches[0]


def _validate_total(plan: dict[str, Any], computed_total: Decimal) -> None:
    raw_total = plan.get("total")
    if raw_total is None:
        return
    expected = decimal_from(raw_total, "total")
    if expected is not None and money(expected) != money(computed_total):
        raise ValidationError(f"total mismatch: expected {money(expected):.2f}, computed {money(computed_total):.2f}")


def calculate_split(plan: dict[str, Any]) -> CalculationResult:
    """Calculate final payable amounts from normalized input.

    The plan may come from an LLM interpretation, but this function treats it
    as untrusted input and validates references before doing deterministic math.
    """

    currency = str(plan.get("currency") or "SGD").upper()
    people = [str(person).strip() for person in plan.get("people", [])]
    if not people or len(set(people)) != len(people):
        raise ValidationError("people must be unique and non-empty")
    known_people = set(people)

    totals: dict[str, dict[str, Decimal]] = {
        person: {
            "subtotal": Decimal("0.00"),
            "service_charge": Decimal("0.00"),
            "tax": Decimal("0.00"),
            "discount": Decimal("0.00"),
            "final": Decimal("0.00"),
        }
        for person in people
    }
    breakdown: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
    notes: list[str] = []

    plan_items = plan.get("items")
    if not isinstance(plan_items, list) or not plan_items:
        raise ValidationError("items must be a non-empty list")

    subtotal_weights: dict[str, Decimal] = {person: Decimal("0") for person in people}

    for item in plan_items:
        if not isinstance(item, dict):
            raise ValidationError("each item must be an object")
        name = str(item.get("name", "")).strip()
        if not name:
            raise ValidationError("item name is required")
        quantity = decimal_from(item.get("quantity", "1"), f"{name}.quantity")
        total = decimal_from(item.get("total"), f"{name}.total")
        if quantity is None or quantity <= 0 or total is None:
            raise ValidationError(f"{name} has invalid quantity or total")
        split = item.get("split")
        if not isinstance(split, dict):
            raise ValidationError(f"{name} is missing split details")
        split_type = split.get("type")
        breakdown_labels: dict[str, str] = {}

        if split_type == "equal":
            split_people = [str(person).strip() for person in split.get("people", [])]
            if not split_people:
                split_people = people
            _require_people(known_people, split_people, name)
            allocation = _allocate_exact(total, {person: Decimal("1") for person in split_people})
            notes.append(f"{name} split equally between {', '.join(split_people)}.")
        elif split_type == "quantity":
            raw_allocations = split.get("allocations")
            if not isinstance(raw_allocations, dict) or not raw_allocations:
                raise ValidationError(f"{name} quantity split requires allocations")
            allocations = {
                str(person).strip(): decimal_from(qty, f"{name}.allocations.{person}")
                for person, qty in raw_allocations.items()
            }
            _require_people(known_people, list(allocations), name)
            allocated_quantity = sum((qty for qty in allocations.values() if qty is not None), Decimal("0"))
            if allocated_quantity != quantity:
                raise ValidationError(f"{name} quantity mismatch: allocated {allocated_quantity}, bill has {quantity}")
            allocation = _allocate_exact(total, {person: qty for person, qty in allocations.items() if qty is not None})
            breakdown_labels = {person: f"{name} x{_format_decimal(qty)}" for person, qty in allocations.items() if qty is not None}
            notes.append(f"{name} allocated by quantity.")
        elif split_type == "fixed":
            owner = str(split.get("person") or split.get("owner") or "").strip()
            if not owner:
                raw_people = split.get("people")
                if isinstance(raw_people, list) and len(raw_people) == 1:
                    owner = str(raw_people[0]).strip()
            _require_people(known_people, [owner], name)
            allocation = {owner: money(total)}
            breakdown_labels = {owner: f"{name} x{_format_decimal(quantity)}"}
            notes.append(f"{name} assigned to {owner}.")
        else:
            raise ValidationError(f"{name} has unsupported split type: {split_type}")

        for person, amount in allocation.items():
            totals[person]["subtotal"] += amount
            subtotal_weights[person] += amount
            breakdown[person].append((breakdown_labels.get(person, name), amount))

    subtotal = sum(subtotal_weights.values(), Decimal("0"))
    service_charge = decimal_from(plan.get("service_charge") or "0.00", "service_charge") or Decimal("0")
    tax = decimal_from(plan.get("tax") or "0.00", "tax") or Decimal("0")
    service_charge_rate = decimal_from(plan.get("service_charge_rate"), "service_charge_rate", allow_none=True)
    tax_rate = decimal_from(plan.get("tax_rate"), "tax_rate", allow_none=True)
    discount = decimal_from(plan.get("discount") or "0.00", "discount") or Decimal("0")
    charge_allocation = plan.get("charge_allocation", "proportional_by_subtotal")
    if charge_allocation != "proportional_by_subtotal":
        raise ValidationError(f"unsupported charge allocation: {charge_allocation}")

    weights = subtotal_weights if subtotal > 0 else {person: Decimal("1") for person in people}
    service_alloc = _allocate_cents(service_charge, weights)
    tax_alloc = _allocate_cents(tax, weights)
    discount_alloc = _allocate_cents(discount, weights)

    for person in people:
        totals[person]["service_charge"] = money(service_alloc.get(person, Decimal("0")))
        totals[person]["tax"] = money(tax_alloc.get(person, Decimal("0")))
        totals[person]["discount"] = money(discount_alloc.get(person, Decimal("0")))
        if totals[person]["service_charge"] or totals[person]["tax"]:
            breakdown[person].append(("GST/service share", totals[person]["service_charge"] + totals[person]["tax"]))
        if totals[person]["discount"]:
            breakdown[person].append(("Discount share", -totals[person]["discount"]))

    computed_total = money(subtotal + service_charge + tax - discount)
    _validate_total(plan, computed_total)
    # Final cents are allocated from exact proportional totals, then any cent
    # difference is assigned by largest remainder in _allocate_cents.
    charge_multiplier = (subtotal + service_charge + tax - discount) / subtotal if subtotal else Decimal("1")
    final_weights = {person: subtotal_weights[person] * charge_multiplier for person in people}
    final_alloc = _allocate_cents(computed_total, final_weights)
    for person in people:
        totals[person]["final"] = final_alloc[person]
    final_sum = money(sum((row["final"] for row in totals.values()), Decimal("0")))
    if final_sum != computed_total:
        raise ValidationError(f"split total mismatch: people sum {final_sum:.2f}, bill total {computed_total:.2f}")

    return CalculationResult(
        currency=currency,
        total_bill=computed_total,
        people={person: {key: money(value) for key, value in row.items()} for person, row in totals.items()},
        breakdown={person: entries for person, entries in breakdown.items()},
        notes=notes + ["GST/service charge split proportionally by subtotal."],
        validation={
            "subtotal": money(subtotal),
            "service_charge": money(service_charge),
            "tax": money(tax),
            "service_charge_rate": service_charge_rate if service_charge_rate is not None else Decimal("-1"),
            "tax_rate": tax_rate if tax_rate is not None else Decimal("-1"),
            "discount": money(discount),
            "people_total": final_sum,
            "bill_total": computed_total,
        },
    )

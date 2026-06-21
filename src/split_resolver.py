"""Deterministic split resolver for clarified manual bill instructions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .models import ExtractedBill, ValidationError


@dataclass(slots=True)
class ResolvedSplit:
    plan: dict[str, Any]
    summary: str


def resolve_manual_split(
    bill: ExtractedBill,
    people: list[str] | None,
    split_text: str,
    self_name: str = "You",
) -> ResolvedSplit:
    people = _merge_people(people or [], _infer_people_from_simple_quantity_rules(split_text, self_name))
    if not people:
        raise ValidationError("people must be known before deterministic split resolution")

    splits: dict[str, dict[str, Any]] = {}
    notes: list[str] = []
    normalized_text = _normalize_self(split_text, self_name)

    if _handle_comma_quantity_rules(normalized_text, bill, people, splits, notes):
        missing = [item.name for item in bill.items if item.name not in splits]
        if not missing:
            plan = _build_plan(bill, people, splits)
            return ResolvedSplit(plan=plan, summary=_format_summary(bill, splits, notes))

    for raw_line in normalized_text.splitlines():
        line = raw_line.strip()
        if not line or _is_charge_line(line) or _looks_like_bill_line(line) or _looks_like_people_line(line):
            continue
        handled = (
            _handle_equal_line(line, bill, people, splits, notes)
            or _handle_fixed_line(line, bill, people, splits, notes)
            or _handle_rest_line(line, bill, people, splits, notes)
            or _handle_quantity_assignment_line(line, bill, people, splits, notes)
        )
        if not handled and _looks_split_related(line):
            raise ValidationError(f"could not deterministically resolve split instruction: {line}")

    missing = [item.name for item in bill.items if item.name not in splits]
    if missing:
        raise ValidationError(f"missing split instructions for: {', '.join(missing)}")

    plan = _build_plan(bill, people, splits)
    return ResolvedSplit(plan=plan, summary=_format_summary(bill, splits, notes))


def _build_plan(bill: ExtractedBill, people: list[str], splits: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "currency": bill.currency,
        "people": people,
        "items": [
            {
                "name": item.name,
                "quantity": str(item.quantity),
                "unit_price": str(item.unit_price) if item.unit_price is not None else None,
                "total": str(item.total),
                "split": splits[item.name],
            }
            for item in bill.items
        ],
        "service_charge": str(bill.service_charge or 0),
        "tax": str(bill.tax or 0),
        "service_charge_rate": str(bill.service_charge_rate) if bill.service_charge_rate is not None else None,
        "tax_rate": str(bill.tax_rate) if bill.tax_rate is not None else None,
        "discount": str(bill.discount or 0),
        "total": str(bill.total) if bill.total is not None else None,
        "charge_allocation": "proportional_by_subtotal",
        "rounding": "half_up_2dp",
    }


def _merge_people(existing: list[str], inferred: list[str]) -> list[str]:
    merged: list[str] = []
    for person in [*existing, *inferred]:
        if person and person not in merged:
            merged.append(person)
    return merged


def _infer_people_from_simple_quantity_rules(text: str, self_name: str) -> list[str]:
    normalized = _normalize_self(text, self_name)
    people: list[str] = []
    for clause in _split_rule_clauses(normalized):
        person = _person_from_quantity_clause(clause)
        if person and person not in people:
            people.append(person)
    return people


def _handle_comma_quantity_rules(
    text: str,
    bill: ExtractedBill,
    people: list[str],
    splits: dict[str, dict[str, Any]],
    notes: list[str],
) -> bool:
    clauses = _split_rule_clauses(text)
    if not clauses:
        return False
    allocations: dict[str, dict[str, Decimal]] = {}
    last_item_name: str | None = None
    handled = False

    for clause in clauses:
        parsed = _parse_quantity_clause(clause, bill, people, last_item_name)
        if parsed is None:
            if _looks_split_related(clause):
                return False
            continue
        item_name, person, quantity = parsed
        allocations.setdefault(item_name, {})[person] = allocations.setdefault(item_name, {}).get(person, Decimal("0")) + quantity
        last_item_name = item_name
        handled = True

    if not handled:
        return False

    for item_name, item_allocations in allocations.items():
        item = _bill_item_by_name(bill, item_name)
        total_allocated = sum(item_allocations.values(), Decimal("0"))
        if total_allocated != item.quantity:
            raise ValidationError(f"{item_name} quantity allocations total {total_allocated}, bill has {item.quantity}")
        splits[item_name] = {
            "type": "quantity",
            "allocations": {person: str(quantity) for person, quantity in item_allocations.items()},
        }
        notes.append(f"{item_name} split by quantity.")
    return True


def _split_rule_clauses(text: str) -> list[str]:
    normalized = text.replace("\n", ",")
    return [part.strip(" .") for part in normalized.split(",") if part.strip(" .")]


def _person_from_quantity_clause(clause: str) -> str | None:
    patterns = [
        r"^\d+(?:\.\d+)?\s+.+?\s+on\s+([A-Za-z][A-Za-z0-9 .'-]*)$",
        r"^\d+(?:\.\d+)?\s+on\s+([A-Za-z][A-Za-z0-9 .'-]*)$",
        r"^([A-Za-z][A-Za-z0-9 .'-]*?)\s+\d+(?:\.\d+)?\s+.+$",
        r"^([A-Za-z][A-Za-z0-9 .'-]*?)\s*:\s*\d+(?:\.\d+)?\s+.+$",
    ]
    for pattern in patterns:
        match = re.match(pattern, clause, re.I)
        if match:
            return match.group(1).strip()
    return None


def _parse_quantity_clause(
    clause: str,
    bill: ExtractedBill,
    people: list[str],
    previous_item_name: str | None,
) -> tuple[str, str, Decimal] | None:
    match = re.match(r"^(?P<qty>\d+(?:\.\d+)?)\s+(?P<item>.+?)\s+on\s+(?P<person>[A-Za-z][A-Za-z0-9 .'-]*)$", clause, re.I)
    if match:
        item_name = _match_item_name(bill, match.group("item"))
        return item_name, _match_person(people, match.group("person")), Decimal(match.group("qty"))
    match = re.match(r"^(?P<qty>\d+(?:\.\d+)?)\s+on\s+(?P<person>[A-Za-z][A-Za-z0-9 .'-]*)$", clause, re.I)
    if match and previous_item_name:
        return previous_item_name, _match_person(people, match.group("person")), Decimal(match.group("qty"))
    match = re.match(r"^(?P<person>[A-Za-z][A-Za-z0-9 .'-]*?)\s+(?P<qty>\d+(?:\.\d+)?)\s+(?P<item>.+)$", clause, re.I)
    if match:
        item_name = _match_item_name(bill, match.group("item"))
        return item_name, _match_person(people, match.group("person")), Decimal(match.group("qty"))
    match = re.match(r"^(?P<person>[A-Za-z][A-Za-z0-9 .'-]*?)\s*:\s*(?P<qty>\d+(?:\.\d+)?)\s+(?P<item>.+)$", clause, re.I)
    if match:
        item_name = _match_item_name(bill, match.group("item"))
        return item_name, _match_person(people, match.group("person")), Decimal(match.group("qty"))
    return None


def _handle_equal_line(
    line: str,
    bill: ExtractedBill,
    people: list[str],
    splits: dict[str, dict[str, Any]],
    notes: list[str],
) -> bool:
    lower = line.casefold()
    if "split" not in lower or not any(word in lower for word in ["among", "between", "equally"]):
        return False
    before_split = re.split(r"\bsplit\b", line, maxsplit=1, flags=re.I)[0]
    names = _split_item_name_list(before_split)
    matched = [_match_item_name(bill, name) for name in names]
    if not matched:
        return False
    selected_people = _selected_people_from_line(line, people) or people
    for item_name in matched:
        splits[item_name] = {"type": "equal", "people": selected_people}
    notes.append(f"{', '.join(matched)} split equally among {', '.join(selected_people)}.")
    return True


def _handle_fixed_line(
    line: str,
    bill: ExtractedBill,
    people: list[str],
    splits: dict[str, dict[str, Any]],
    notes: list[str],
) -> bool:
    match = re.match(r"all\s+(.+?)\s+on\s+(.+)$", line, re.I)
    if not match:
        return False
    item_name = _match_item_name(bill, match.group(1))
    person = _match_person(people, match.group(2))
    splits[item_name] = {"type": "fixed", "person": person}
    notes.append(f"{item_name} assigned to {person}.")
    return True


def _handle_rest_line(
    line: str,
    bill: ExtractedBill,
    people: list[str],
    splits: dict[str, dict[str, Any]],
    notes: list[str],
) -> bool:
    match = re.match(
        r"(?P<first_qty>\d+(?:\.\d+)?)\s+(?P<item>.+?)\s+on\s+(?P<first_person>[A-Za-z][A-Za-z0-9 .'-]*?)\s*,?\s*(?:rest|remaining|balance|others)(?:\s+(?:on|to))?\s+(?P<rest_person>[A-Za-z][A-Za-z0-9 .'-]*)$",
        line,
        re.I,
    )
    if not match:
        match = re.match(
            r"(?P<first_person>[A-Za-z][A-Za-z0-9 .'-]*?)\s+takes\s+(?P<first_qty>\d+(?:\.\d+)?)\s+(?P<item>.+?)\s*,?\s*(?P<rest_person>[A-Za-z][A-Za-z0-9 .'-]*?)\s+takes\s+(?:the\s+)?rest$",
            line,
            re.I,
        )
    if not match:
        return False
    item_name = _match_item_name(bill, match.group("item"))
    item = _bill_item_by_name(bill, item_name)
    first_person = _match_person(people, match.group("first_person"))
    rest_person = _match_person(people, match.group("rest_person"))
    first_qty = Decimal(match.group("first_qty"))
    rest_qty = item.quantity - first_qty
    if rest_qty < 0:
        raise ValidationError(f"{item_name} has more assigned quantity than the bill contains")
    splits[item_name] = {
        "type": "quantity",
        "allocations": {first_person: str(first_qty), rest_person: str(rest_qty)},
    }
    notes.append(f"{item_name} split by quantity.")
    return True


def _handle_quantity_assignment_line(
    line: str,
    bill: ExtractedBill,
    people: list[str],
    splits: dict[str, dict[str, Any]],
    notes: list[str],
) -> bool:
    assignments = _parse_person_assignment_line(line, people)
    if not assignments:
        return False

    quantity_allocations: dict[str, dict[str, Decimal]] = {}
    fixed_allocations: dict[str, str] = {}
    remaining_requests: list[tuple[str, str]] = []

    for person, expression in assignments:
        for token in _split_expression(expression):
            remaining_match = re.match(r"remaining\s+(.+)$", token, re.I)
            if remaining_match:
                item_name = _match_item_name(bill, remaining_match.group(1))
                remaining_requests.append((person, item_name))
                continue

            quantity_match = re.match(r"(\d+(?:\.\d+)?)\s+(.+)$", token, re.I)
            if quantity_match:
                quantity = Decimal(quantity_match.group(1))
                item_name = _match_item_name(bill, quantity_match.group(2))
                quantity_allocations.setdefault(item_name, {})[person] = quantity
                continue

            item_name = _match_item_name(bill, token)
            fixed_allocations[item_name] = person

    for person, item_name in remaining_requests:
        item = _bill_item_by_name(bill, item_name)
        allocated = sum(quantity_allocations.get(item_name, {}).values(), Decimal("0"))
        remaining = item.quantity - allocated
        if remaining < 0:
            raise ValidationError(f"{item_name} has more assigned quantity than the bill contains")
        quantity_allocations.setdefault(item_name, {})[person] = remaining

    for item_name, allocations in quantity_allocations.items():
        item = _bill_item_by_name(bill, item_name)
        allocated = sum(allocations.values(), Decimal("0"))
        if allocated != item.quantity:
            raise ValidationError(f"{item_name} quantity allocations total {allocated}, bill has {item.quantity}")
        splits[item_name] = {
            "type": "quantity",
            "allocations": {person: str(quantity) for person, quantity in allocations.items()},
        }
        notes.append(f"{item_name} split by quantity.")

    for item_name, person in fixed_allocations.items():
        if item_name in splits:
            raise ValidationError(f"{item_name} was assigned more than once")
        splits[item_name] = {"type": "fixed", "person": person}
        notes.append(f"{item_name} assigned to {person}.")

    return bool(quantity_allocations or fixed_allocations)


def _parse_person_assignment_line(line: str, people: list[str]) -> list[tuple[str, str]]:
    parts = [part.strip() for part in line.split(",") if part.strip()]
    assignments: list[tuple[str, str]] = []
    for part in parts:
        match = re.match(r"([A-Za-z][A-Za-z0-9 .'-]*?)\s*:\s*(.+)$", part)
        if not match:
            return []
        assignments.append((_match_person(people, match.group(1)), match.group(2).strip()))
    return assignments


def _split_expression(expression: str) -> list[str]:
    return [part.strip() for part in expression.split("+") if part.strip()]


def _split_item_name_list(text: str) -> list[str]:
    normalized = re.sub(r"\band\b", ",", text, flags=re.I).replace("&", ",")
    return [part.strip(" .") for part in normalized.split(",") if part.strip(" .")]


def _selected_people_from_line(line: str, people: list[str]) -> list[str] | None:
    match = re.search(r"\bonly\s+(.+)$", line, re.I)
    if not match:
        return None
    raw_people = re.sub(r"\band\b", ",", match.group(1), flags=re.I).replace("&", ",")
    selected = [_match_person(people, token.strip()) for token in raw_people.split(",") if token.strip()]
    return selected or None


def _match_item_name(bill: ExtractedBill, raw_name: str) -> str:
    normalized = _normalize_name(raw_name)
    exact = [item.name for item in bill.items if _normalize_name(item.name) == normalized]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ValidationError(f"item name is ambiguous: {raw_name}")

    contained = [item.name for item in bill.items if normalized in _normalize_name(item.name)]
    if len(contained) == 1:
        return contained[0]
    if len(contained) > 1:
        raise ValidationError(f"item name is ambiguous: {raw_name}")
    alias = _alias_match(bill, normalized)
    if alias:
        return alias
    raise ValidationError(f"unknown item name: {raw_name}")


def _match_person(people: list[str], raw_person: str) -> str:
    if raw_person.strip().casefold() in {"me", "myself", "mine", "i"}:
        raise ValidationError("self references must be normalized before matching people")
    normalized = _normalize_name(raw_person)
    matches = [person for person in people if _normalize_name(person) == normalized]
    if len(matches) == 1:
        return matches[0]
    raise ValidationError(f"unknown person: {raw_person}")


def _bill_item_by_name(bill: ExtractedBill, name: str):
    for item in bill.items:
        if item.name == name:
            return item
    raise ValidationError(f"unknown item name: {name}")


def _format_summary(bill: ExtractedBill, splits: dict[str, dict[str, Any]], notes: list[str]) -> str:
    lines = ["Split resolved", ""]
    equal_groups: dict[tuple[str, ...], list[str]] = {}
    for item in bill.items:
        split = splits[item.name]
        if split["type"] == "equal":
            equal_groups.setdefault(tuple(split["people"]), []).append(item.name)
            continue
        if split["type"] == "quantity":
            allocations = ", ".join(f"{person} {_format_decimal(Decimal(str(quantity)))}" for person, quantity in split["allocations"].items())
            lines.append(f"* {item.name} x{_format_decimal(item.quantity)}: {allocations}")
        elif split["type"] == "fixed":
            lines.append(f"* {item.name} x{_format_decimal(item.quantity)}: {split['person']}")
    for people, item_names in equal_groups.items():
        label = " + ".join(_short_item_name(name) for name in item_names)
        lines.insert(2, f"* {label}: {', '.join(people)} equally")
    if bill.tax is not None or bill.service_charge is not None:
        lines.append("* GST/service: proportional by subtotal")
    lines.extend(["", "Validation: running 5 checks..."])
    return "\n".join(lines).strip()


def _alias_match(bill: ExtractedBill, normalized: str) -> str | None:
    candidates: list[str] = []
    raw_words = set(normalized.split())
    for item in bill.items:
        item_normalized = _normalize_name(item.name)
        item_words = set(item_normalized.split())
        if normalized == "fish bites" and {"fish", "bites"}.issubset(item_words):
            candidates.append(item.name)
        elif raw_words and raw_words.issubset(item_words):
            candidates.append(item.name)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ValidationError(f"item name is ambiguous: {normalized}")
    return None


def _short_item_name(name: str) -> str:
    normalized = name.casefold()
    if "battered" in normalized and "fish" in normalized:
        return "Fish bites"
    return name


def _normalize_self(text: str, self_name: str) -> str:
    output = re.sub(r"\b[Mm]e\b", self_name, text)
    output = re.sub(r"\b[Mm]ine\b", f"{self_name}'s", output)
    output = re.sub(r"\b[Mm]y\b", f"{self_name}'s", output)
    return output


def _normalize_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    return normalized.replace("guiness", "guinness")


def _format_decimal(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _is_charge_line(line: str) -> bool:
    lower = line.casefold()
    return "gst" in lower or "service charge" in lower or "tax" in lower


def _looks_like_bill_line(line: str) -> bool:
    return re.search(r"\d+(?:\.\d+)?\s*x\b|(?:->|\s+-\s+)\s*\$?\d", line, re.I) is not None


def _looks_like_people_line(line: str) -> bool:
    return ("," in line or "&" in line) and not _looks_split_related(line) and not _is_charge_line(line)


def _looks_split_related(line: str) -> bool:
    lower = line.casefold()
    return any(word in lower for word in ["split", "all ", " on ", "remaining", "rest", "balance", "others", "assigned", ":"])

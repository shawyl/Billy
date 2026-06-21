"""Deterministic parser for common manual bill text formats."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from .calculator import money
from .models import BillItem, ExtractedBill, ValidationError

PRICE_RE = re.compile(r"\$?\d+(?:\.\d{1,2})")
ITEM_SEPARATOR_RE = r"(?:—>|–>|->|→|=>|:|\s+-\s+)"
SPLIT_WORDS = (
    "split",
    "shared",
    "share",
    "assigned",
    "all ",
    " on ",
    " rest ",
    "remaining",
    " among ",
    " between ",
)


@dataclass(slots=True)
class ManualBillParseResult:
    bill: ExtractedBill
    people: list[str]
    split_instructions: list[str]
    split_text: str
    ambiguity_message: str | None = None


class ManualBillParseError(ValidationError):
    """Raised when text looks like a bill but cannot be converted safely."""


def manual_parse_failure_message() -> str:
    return (
        "I could not safely read the bill items.\n\n"
        "I saw prices in your message, but could not convert them into valid bill lines.\n"
        "Please send the items in this format:\n\n"
        "1X Pizza -> 26.00\n"
        "2X Guinness -> 11.00"
    )


def contains_bill_like_lines(text: str) -> bool:
    return any(_parse_item_line(line) is not None for line in text.splitlines())


def contains_prices(text: str) -> bool:
    return PRICE_RE.search(text) is not None


def parse_manual_bill_text(text: str, default_currency: str = "SGD", self_name: str = "You") -> ManualBillParseResult:
    items: list[BillItem] = []
    trailing_lines: list[str] = []
    saw_item = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if saw_item:
                trailing_lines.append("")
            continue
        item = _parse_item_line(line)
        if item is not None:
            items.append(item)
            saw_item = True
            continue
        if saw_item:
            trailing_lines.append(line)

    if not items:
        if contains_prices(text):
            raise ManualBillParseError(manual_parse_failure_message())
        raise ManualBillParseError("No manual bill item lines were found.")

    subtotal = money(sum((item.total for item in items), Decimal("0")))
    if subtotal <= 0:
        raise ManualBillParseError(manual_parse_failure_message())

    people, split_lines = _extract_people_and_split_lines(trailing_lines, self_name)
    normalized_split_lines = [_normalize_self_references(line, self_name) for line in split_lines]
    bill = ExtractedBill(
        currency=default_currency,
        items=items,
        subtotal=subtotal,
        tax=None,
        service_charge=None,
        discount=None,
        total=None,
    )
    ambiguity = detect_ambiguous_quantity_family_split(bill, "\n".join(normalized_split_lines), self_name)
    return ManualBillParseResult(
        bill=bill,
        people=people,
        split_instructions=normalized_split_lines,
        split_text="\n".join(normalized_split_lines).strip(),
        ambiguity_message=ambiguity,
    )


def detect_ambiguous_quantity_family_split(bill: ExtractedBill, split_text: str, self_name: str = "You") -> str | None:
    normalized = _normalize_self_references(split_text, self_name)
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s+([A-Za-z][A-Za-z ]*?)\s+on\s+([A-Za-z][A-Za-z0-9 ]*)", normalized, re.I):
        family = _clean_name(match.group(2))
        if not family:
            continue
        matched_items = [item for item in bill.items if family.casefold() in item.name.casefold()]
        unit_prices = {
            money(item.unit_price if item.unit_price is not None else item.total / item.quantity)
            for item in matched_items
        }
        if len(matched_items) > 1 and len(unit_prices) > 1:
            total_quantity = sum((item.quantity for item in matched_items), Decimal("0"))
            item_lines = [
                f"* {item.name} x{_format_decimal(item.quantity)} at ${money(item.unit_price if item.unit_price is not None else item.total / item.quantity):.2f} each"
                for item in matched_items
            ]
            payer = match.group(3).strip()
            return "\n".join(
                [
                    f"I found {_format_decimal(total_quantity)} {family} total, but they are priced differently:",
                    "",
                    *item_lines,
                    "",
                    "You said:",
                    match.group(0),
                    "",
                    f"Please clarify which {family} should count under {payer}'s {match.group(1)}.",
                    "",
                    "For example:",
                    "",
                    f"* {payer}: {match.group(1)} {matched_items[0].name}, C: remaining {matched_items[0].name} + {matched_items[-1].name}",
                    f"* {payer}: 2 {matched_items[0].name} + 0.5 {matched_items[-1].name}",
                    f"* Split {family} cost by total proportion instead",
                ]
            )
    return None


def _parse_item_line(line: str) -> BillItem | None:
    if _looks_like_split_rule(line):
        return None
    normalized = line.strip().replace("—>", "->").replace("–>", "->").replace("→", "->")
    patterns = [
        re.compile(
            rf"^(?P<qty>\d+(?:\.\d+)?)\s*[xX]\s+(?P<name>.+?)\s*{ITEM_SEPARATOR_RE}\s*\$?(?P<amount>\d+(?:\.\d{{1,2}})?)\s*(?P<label>total|each|ea|unit)?$",
            re.I,
        ),
        re.compile(
            rf"^(?P<qty>\d+(?:\.\d+)?)\s+[xX]\s+(?P<name>.+?)\s*{ITEM_SEPARATOR_RE}\s*\$?(?P<amount>\d+(?:\.\d{{1,2}})?)\s*(?P<label>total|each|ea|unit)?$",
            re.I,
        ),
        re.compile(
            r"^(?P<name>.+?)\s+[xX]\s*(?P<qty>\d+(?:\.\d+)?)\s+\$?(?P<amount>\d+(?:\.\d{1,2})?)\s*(?P<label>total|each|ea|unit)?$",
            re.I,
        ),
    ]
    for pattern in patterns:
        match = pattern.match(normalized)
        if match:
            return _item_from_match(match, line)

    simple = re.match(r"^(?P<name>[A-Za-z][A-Za-z0-9 &'/.()_-]*?)\s+\$?(?P<amount>\d+(?:\.\d{1,2})?)$", normalized)
    if simple and not _looks_like_split_rule(simple.group("name")):
        amount = Decimal(simple.group("amount"))
        name = _clean_name(simple.group("name"))
        if not name:
            return None
        return BillItem(name=name, quantity=Decimal("1"), unit_price=money(amount), total=money(amount), source_line=line)
    return None


def _item_from_match(match: re.Match[str], source_line: str) -> BillItem | None:
    quantity = Decimal(match.group("qty"))
    name = _clean_name(match.group("name"))
    amount = Decimal(match.group("amount"))
    label = (match.groupdict().get("label") or "").casefold()
    if not name or quantity <= 0:
        return None
    if label == "total":
        total = money(amount)
        unit_price = money(total / quantity)
    else:
        unit_price = money(amount)
        total = money(unit_price * quantity)
    return BillItem(name=name, quantity=quantity, unit_price=unit_price, total=total, source_line=source_line)


def _extract_people_and_split_lines(lines: list[str], self_name: str) -> tuple[list[str], list[str]]:
    people: list[str] = []
    split_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if not people and _looks_like_people_line(stripped):
            people = _parse_people(stripped, self_name)
            continue
        if _looks_like_split_rule(stripped):
            split_lines.append(stripped)
    return people, split_lines


def _looks_like_people_line(line: str) -> bool:
    lower = line.casefold()
    if contains_prices(line) or any(word in lower for word in SPLIT_WORDS):
        return False
    if "," not in line and "&" not in line and " and " not in lower:
        return False
    names = _split_people_tokens(line)
    return len(names) >= 2 and all(re.fullmatch(r"[A-Za-z][A-Za-z0-9 .'-]*", name) for name in names)


def _parse_people(line: str, self_name: str) -> list[str]:
    people: list[str] = []
    for token in _split_people_tokens(line):
        person = _normalize_person(token, self_name)
        if person and person not in people:
            people.append(person)
    return people


def _split_people_tokens(line: str) -> list[str]:
    normalized = re.sub(r"\band\b", ",", line, flags=re.I).replace("&", ",")
    return [part.strip() for part in normalized.split(",") if part.strip()]


def _looks_like_split_rule(line: str) -> bool:
    lower = f" {line.casefold()} "
    return any(word in lower for word in SPLIT_WORDS) or re.match(r"\s*[A-Za-z][A-Za-z0-9 .'-]*\s*:", line) is not None


def _normalize_person(value: str, self_name: str) -> str:
    cleaned = value.strip().strip(".")
    if cleaned.casefold() in {"me", "myself", "mine", "i"}:
        return self_name
    return cleaned


def _normalize_self_references(text: str, self_name: str) -> str:
    output = re.sub(r"\b[Mm]e\b", self_name, text)
    output = re.sub(r"\b[Mm]ine\b", f"{self_name}'s", output)
    output = re.sub(r"\b[Mm]y\b", f"{self_name}'s", output)
    return output


def _clean_name(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value.strip(" -:\t"))
    cleaned = re.sub(r"^\d+\.\s*", "", cleaned)
    return cleaned


def _format_decimal(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text

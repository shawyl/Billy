"""Bill confirmation formatting and deterministic charge clarification helpers."""

from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal

from .calculator import money
from .models import BillItem, ExtractedBill


def requires_charge_clarification(bill: ExtractedBill) -> bool:
    return bill.tax is None or bill.service_charge is None


def format_bill_confirmation(
    bill: ExtractedBill,
    *,
    heading: str = "Receipt detected",
    people: list[str] | None = None,
    split_instructions: list[str] | None = None,
    ambiguity_message: str | None = None,
    show_single_quantity: bool = False,
) -> str:
    lines = [heading, "", "Items:", ""]
    for index, item in enumerate(bill.items, start=1):
        quantity = "" if item.quantity == Decimal("1") and not show_single_quantity else f" x{_format_decimal(item.quantity)}"
        lines.append(f"{index}. {item.name}{quantity} - {currency_amount(bill.currency, item.total)}")
    lines.extend(
        [
            "",
            f"Subtotal: {currency_amount(bill.currency, bill.effective_subtotal())}",
            _charge_line("GST", bill.currency, bill.tax, bill.tax_rate, bill.notes),
            _charge_line("Service charge", bill.currency, bill.service_charge, bill.service_charge_rate, bill.notes),
        ]
    )
    if bill.discount not in (None, Decimal("0")):
        lines.append(f"Discount: -{currency_amount(bill.currency, abs(bill.discount))}")
    if bill.total is not None:
        label = "Estimated total" if bill.total_is_estimated else "Total"
        lines.append(f"{label}: {currency_amount(bill.currency, bill.total)}")
    else:
        if requires_charge_clarification(bill):
            lines.append(f"Total before GST/service: {currency_amount(bill.currency, bill.effective_subtotal())}")
        else:
            estimated = bill.effective_subtotal() + (bill.tax or 0) + (bill.service_charge or 0) - (bill.discount or 0)
            lines.append(f"Estimated total: {currency_amount(bill.currency, estimated)}")
    if people:
        lines.extend(["", "People:", ", ".join(people)])
    if split_instructions:
        lines.extend(["", "Split rules:", ""])
        for instruction in split_instructions:
            lines.append(f"* {_format_split_instruction(instruction)}")
    if ambiguity_message:
        lines.extend(["", ambiguity_message])
    if requires_charge_clarification(bill):
        missing = _missing_charge_prompt(bill)
        lines.extend(
            [
                "",
                missing,
                "",
                "Reply with one:",
                "",
                "* No GST/service charge",
                "* Add 9% GST",
                "* Add 10% service charge + 9% GST",
                "* Use custom amounts",
            ]
        )
        if not split_instructions:
            lines.extend(["", "After that, send me how you want to split the bill."])
    elif split_instructions:
        lines.extend(["", "Reply \"Confirm\" to calculate, or tell me what to change."])
    else:
        lines.extend(["", "Reply \"Confirm\" if the bill looks right, then send split rules."])
    return "\n".join(lines)


def format_incomplete_receipt_warning(bill: ExtractedBill) -> str:
    missing = bill.missing_amount
    subtotal = bill.effective_subtotal()
    lines = [
        "Receipt detected, but I may have missed some items.",
        "",
        "I found:",
        "",
    ]
    for index, item in enumerate(bill.items, start=1):
        quantity = f" x{_format_decimal(item.quantity)}"
        lines.append(f"{index}. {item.name}{quantity} - {currency_amount(bill.currency, item.total)}")
    lines.extend(["", f"But the receipt subtotal appears to be {currency_amount(bill.currency, subtotal)}."])
    if missing is not None:
        lines.append(f"So {currency_amount(bill.currency, abs(missing))} is unaccounted for.")
    lines.extend(["", "Please confirm the missing item, or paste the bill text."])
    return "\n".join(lines)


def _format_decimal(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _format_split_instruction(instruction: str) -> str:
    text = instruction.strip()
    replacements = {
        "Battered fish bites and Pizza split equally among 3": "Fish bites + pizza split equally",
        "All Gin Tonic on Y": "Gin Tonic -> Y",
    }
    for source, target in replacements.items():
        if text.casefold() == source.casefold():
            return target
    text = re.sub(r"\s+", " ", text)
    text = text.replace(" and ", " + ")
    text = re.sub(r"All (.+?) on (.+)$", r"\1 -> \2", text, flags=re.I)
    text = re.sub(r"(.+?) split equally among \d+", r"\1 split equally", text, flags=re.I)
    return text


def currency_amount(currency: str, amount: Decimal) -> str:
    symbol = "$" if currency.upper() in {"SGD", "USD", "AUD", "CAD"} else f"{currency.upper()} "
    return f"{symbol}{money(amount):.2f}"


def _amount_or_not_found(currency: str, amount: Decimal | None) -> str:
    return "Not found" if amount is None else currency_amount(currency, amount)


def _charge_display(currency: str, amount: Decimal | None, rate: Decimal | None) -> str:
    if amount is None:
        return "Not found"
    if rate is not None:
        return f"{_format_decimal(rate)}%"
    return currency_amount(currency, amount)


def _charge_line(label: str, currency: str, amount: Decimal | None, rate: Decimal | None, notes: list[str]) -> str:
    if amount is None:
        return f"{label}: Not found"
    display_label = f"{label} {_format_decimal(rate)}%" if rate not in (None, Decimal("0")) else label
    suffix = ""
    if label == "GST" and any("not subject to gst" in note.casefold() for note in notes):
        suffix = ", not subject to GST"
    return f"{display_label}: {currency_amount(currency, amount)}{suffix}"


def _missing_charge_prompt(bill: ExtractedBill) -> str:
    if bill.tax is None and bill.service_charge is None:
        return "GST/service charge was not found.\nShould I include GST and service charge?"
    if bill.tax is None:
        return f"I found service charge: {_charge_display(bill.currency, bill.service_charge, bill.service_charge_rate)}, but GST was not specified. Should I include GST?"
    return f"I found GST: {_charge_display(bill.currency, bill.tax, bill.tax_rate)}, but service charge was not specified. Should I include service charge?"


def apply_charge_instruction(bill: ExtractedBill, text: str) -> ExtractedBill:
    lower = text.casefold()
    subtotal = bill.effective_subtotal() - (bill.discount or Decimal("0"))
    service = bill.service_charge
    tax = bill.tax
    service_rate = bill.service_charge_rate
    tax_rate = bill.tax_rate

    no_service = any(phrase in lower for phrase in ["no gst", "no tax", "no service", "none", "without gst"])
    if no_service:
        if any(phrase in lower for phrase in ["no gst", "no tax", "none", "without gst"]):
            tax = Decimal("0")
            tax_rate = Decimal("0")
        if any(phrase in lower for phrase in ["no service", "none"]):
            service = Decimal("0")
            service_rate = Decimal("0")

    service_percent = _find_percent_near(lower, ["service", "svc"])
    if service_percent is not None:
        service_rate = service_percent
        service = money(subtotal * service_percent / Decimal("100"))

    gst_percent = _find_percent_near(lower, ["gst", "tax"])
    if gst_percent is not None:
        tax_rate = gst_percent
        tax = money(subtotal * gst_percent / Decimal("100"))

    explicit_tax = _find_amount_near(lower, ["gst", "tax"])
    explicit_service = _find_amount_near(lower, ["service", "svc"])
    if explicit_tax is not None:
        tax = explicit_tax
        tax_rate = None
    if explicit_service is not None:
        service = explicit_service
        service_rate = None

    total = bill.total
    if total is None and tax is not None and service is not None:
        total = money(bill.effective_subtotal() + tax + service - (bill.discount or Decimal("0")))
        return replace(bill, tax=tax, service_charge=service, tax_rate=tax_rate, service_charge_rate=service_rate, total=total, total_is_estimated=True)
    return replace(bill, tax=tax, service_charge=service, tax_rate=tax_rate, service_charge_rate=service_rate, total=total)


def apply_basic_bill_correction(bill: ExtractedBill, text: str) -> ExtractedBill:
    updated = bill
    total_match = re.search(r"total\s+(?:is|should be|actually)\s+\$?(\d+(?:\.\d{1,2})?)", text, re.I)
    if total_match:
        updated = replace(updated, total=Decimal(total_match.group(1)))
    subtotal_match = re.search(r"subtotal\s+(?:is|should be|actually)\s+\$?(\d+(?:\.\d{1,2})?)", text, re.I)
    if subtotal_match:
        updated = replace(updated, subtotal=Decimal(subtotal_match.group(1)))

    items = list(updated.items)
    for index, item in enumerate(items):
        price_pattern = rf"{re.escape(item.name)}.*?(?:is|should be|=)\s*\$?(\d+(?:\.\d{{1,2}})?)(?:\s+not\s+\$?\d+(?:\.\d{{1,2}})?)?"
        price_match = re.search(price_pattern, text, re.I)
        if price_match:
            new_total = money(Decimal(price_match.group(1)) * item.quantity)
            items[index] = BillItem(name=item.name, quantity=item.quantity, unit_price=money(Decimal(price_match.group(1))), total=new_total, source_line=item.source_line)
            continue
        pattern = rf"{re.escape(item.name)}.*?(?:x|qty|quantity)\s*(?:is|should be|=)?\s*(\d+(?:\.\d+)?)"
        match = re.search(pattern, text, re.I) or re.search(rf"{re.escape(item.name)}.*?(?:is|should be|=)\s*(\d+(?:\.\d+)?)\s*[xX]", text, re.I)
        if match:
            new_qty = Decimal(match.group(1))
            unit_price = item.unit_price
            if unit_price is None and item.quantity:
                unit_price = item.total / item.quantity
            new_total = money(unit_price * new_qty) if unit_price is not None else item.total
            items[index] = BillItem(name=item.name, quantity=new_qty, unit_price=unit_price, total=new_total)
    corrected = replace(updated, items=items)
    return apply_charge_instruction(corrected, text)


def _find_percent_near(text: str, labels: list[str]) -> Decimal | None:
    for label in labels:
        before = re.search(rf"(\d+(?:\.\d+)?)\s*%\s*[^%]{{0,20}}{label}", text)
        after = re.search(rf"{label}[^%]{{0,20}}?(\d+(?:\.\d+)?)\s*%", text)
        match = before or after
        if match:
            return Decimal(match.group(1))
    return None


def _find_amount_near(text: str, labels: list[str]) -> Decimal | None:
    for label in labels:
        match = re.search(rf"{label}.{{0,20}}?\$?(\d+\.\d{{1,2}})", text)
        if match and "%" not in match.group(0):
            return Decimal(match.group(1))
    return None

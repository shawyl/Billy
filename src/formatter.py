"""Telegram-friendly response formatting for split results and failures."""

from __future__ import annotations

from decimal import Decimal

from .bill_confirmation import currency_amount
from .models import CalculationResult, ConsensusFailure


def format_final_result(result: CalculationResult, detail_level: str = "normal") -> str:
    lines = [
        "Bill split result",
        "",
        f"Subtotal: {currency_amount(result.currency, result.validation.get('subtotal', Decimal('0')))}",
        f"{_charge_label('GST', result.validation.get('tax_rate'))}: {currency_amount(result.currency, result.validation.get('tax', Decimal('0')))}",
        f"{_charge_label('Service', result.validation.get('service_charge_rate'))}: {currency_amount(result.currency, result.validation.get('service_charge', Decimal('0')))}",
        f"Total: {currency_amount(result.currency, result.total_bill)}",
        "",
        f"Validation: {result.validation_runs_matched}/{result.validation_runs_requested} matched",
        "",
    ]
    for person, totals in result.people.items():
        lines.append(f"{person}: {currency_amount(result.currency, totals['final'])}")
    if detail_level == "concise_only":
        return "\n".join(lines)
    lines.extend(["", "Breakdown:"])
    for person, entries in result.breakdown.items():
        lines.extend(["", f"{person} - {currency_amount(result.currency, result.people[person]['final'])}", ""])
        for label, amount in _group_breakdown(entries):
            if amount < Decimal("0"):
                lines.append(f"* {label}: -{currency_amount(result.currency, abs(amount))}")
            else:
                lines.append(f"* {label}: {currency_amount(result.currency, amount)}")
    if detail_level == "detailed" and result.notes:
        lines.extend(["", "Notes:", ""])
        for note in _dedupe_notes(result.notes):
            lines.append(f"* {note}")
    return "\n".join(lines)


def format_concise_result(result: CalculationResult) -> str:
    lines = [
        f"Total: {currency_amount(result.currency, result.total_bill)}",
        "",
    ]
    for person, totals in result.people.items():
        lines.append(f"{person}: {currency_amount(result.currency, totals['final'])}")
    return "\n".join(lines)


def should_send_detailed_result(detail_level: str) -> bool:
    return detail_level != "concise_only"


def should_send_concise_result(detail_level: str) -> bool:
    return detail_level in {"concise_only", "normal", "detailed"}


def format_consensus_failure(failure: ConsensusFailure) -> str:
    issue = _summarize_issue(failure)
    return "\n".join(
        [
            "I could not calculate this safely because the 5 validation runs did not agree.",
            "",
            f"Validation: failed, {failure.validation_runs_matched}/{failure.validation_runs_requested} matched",
            "",
            "Issue:",
            issue,
            "",
            "Please clarify:",
            _clarification_for(issue),
        ]
    )


def _dedupe_notes(notes: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for note in notes:
        if note not in seen:
            seen.add(note)
            output.append(note)
    return output


def _charge_label(label: str, rate: Decimal | None) -> str:
    if rate is None or rate < 0:
        return label
    text = format(rate.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{label} {text}%"


def _group_breakdown(entries: list[tuple[str, Decimal]]) -> list[tuple[str, Decimal]]:
    grouped: list[tuple[str, Decimal]] = []
    shared_total = Decimal("0")
    gst_service_total = Decimal("0")
    for label, amount in entries:
        lower = label.casefold()
        if lower in {"battered fish bites", "pizza"}:
            shared_total += amount
        elif lower == "gst/service share":
            gst_service_total += amount
        else:
            grouped.append((label, amount))
    if shared_total:
        grouped.insert(0, ("Shared food", shared_total))
    if gst_service_total:
        grouped.append(("GST/service", gst_service_total))
    return grouped


def _summarize_issue(failure: ConsensusFailure) -> str:
    text = " ".join(failure.details or [failure.reason])
    if "quantity" in text.casefold():
        return "The Guinness split was inconsistent across validation runs."
    if "unknown people" in text.casefold() or "person" in text.casefold():
        return "One or more people in the split instructions did not match the allocation plan."
    if "total mismatch" in text.casefold():
        return "The bill total did not match the item, GST, service, and discount amounts."
    if "invalid JSON" in text:
        return "One or more validation runs returned invalid structured data."
    if "different rounded calculation" in text:
        return "The same instructions led to different rounded split results across runs."
    return failure.reason


def _clarification_for(issue: str) -> str:
    lower = issue.casefold()
    if "quantity" in lower:
        return "Please confirm whether \"Guinness\" refers to HH Guinness only, regular Guinness only, or both Guinness items."
    if "people" in lower:
        return "Please list the people exactly once and say which items each person shares or owns."
    if "total" in lower:
        return "Please confirm the subtotal, GST, service charge, discount, and final total."
    return "Please restate the split rules with each item and the people responsible for it."

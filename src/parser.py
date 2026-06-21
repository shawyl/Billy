"""Strict JSON parsing plus a small CLI for local non-Telegram testing."""

from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from .bill_confirmation import apply_charge_instruction, format_bill_confirmation, requires_charge_clarification
from .config import load_settings
from .models import ExtractedBill, ValidationError
from .ollama_client import OllamaClient
from .prompts import bill_text_extraction_prompt

logger = logging.getLogger(__name__)


def parse_json_object(raw: str) -> dict[str, Any]:
    raw = _extract_json_text(raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError("invalid JSON from LLM") from exc
    if not isinstance(parsed, dict):
        raise ValidationError("LLM JSON must be an object")
    return parsed


def parse_extracted_bill(raw: str, default_currency: str = "SGD") -> ExtractedBill:
    bill = ExtractedBill.from_dict(_normalize_bill_payload(parse_json_object(raw)), default_currency=default_currency)
    validate_extracted_bill_sane(bill)
    return bill


def _extract_json_text(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.casefold().startswith("json"):
            text = text[4:].strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return raw
    return text[start : end + 1]


def _normalize_bill_payload(data: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(data)
    normalized["items"] = [_normalize_item(item) for item in normalized.get("items") or []]

    service = normalized.get("service_charge")
    if isinstance(service, dict):
        normalized["service_charge"] = _clean_decimal_text(service.get("amount"), money=True)
        normalized["service_charge_rate"] = _clean_decimal_text(service.get("percentage") or service.get("rate"))
    else:
        normalized["service_charge"] = _clean_decimal_text(normalized.get("service_charge"), money=True)

    gst = normalized.get("gst", normalized.get("tax"))
    if isinstance(gst, dict):
        normalized["tax"] = _clean_decimal_text(gst.get("amount"), money=True)
        normalized["tax_rate"] = _clean_decimal_text(gst.get("percentage") or gst.get("rate"))
    elif "gst" in normalized:
        normalized["tax"] = _clean_decimal_text(normalized.get("gst"), money=True)
    else:
        normalized["tax"] = _clean_decimal_text(normalized.get("tax"), money=True)

    for key in ("subtotal", "discount", "grand_total", "your_bill", "payable", "amount_paid", "final_payable", "final_total", "total"):
        if key in normalized:
            normalized[key] = _clean_decimal_text(normalized.get(key), money=True)
    if normalized.get("discount") is not None:
        normalized["discount"] = str(abs(Decimal(str(normalized["discount"]))))

    notes = normalized.get("notes") or []
    if not isinstance(notes, list):
        notes = [str(notes)]
    note_text = " ".join(str(note) for note in notes).casefold()
    if "not subject to gst" in note_text and normalized.get("tax") is None:
        normalized["tax"] = "0.00"
        normalized["tax_rate"] = "0"

    item_subtotal = _sum_item_totals(normalized.get("items") or [])
    grand_total = normalized.get("grand_total")
    if normalized.get("subtotal") is None and grand_total is not None:
        normalized["subtotal"] = grand_total

    for key in ("your_bill", "payable", "amount_paid", "final_payable", "final_total", "total"):
        value = normalized.get(key)
        if value is not None:
            normalized["total"] = value
            break

    if normalized.get("subtotal") is None and item_subtotal is not None:
        normalized["subtotal"] = str(item_subtotal)
    return normalized


def _normalize_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    normalized = dict(item)
    if "total" not in normalized and "line_total" in normalized:
        normalized["total"] = normalized.get("line_total")
    normalized["quantity"] = _clean_decimal_text(normalized.get("quantity") or "1") or "1"
    normalized["total"] = _clean_decimal_text(normalized.get("total"), money=True)
    normalized["unit_price"] = _clean_decimal_text(normalized.get("unit_price"), money=True)
    quantity = normalized.get("quantity")
    total = normalized.get("total")
    if normalized.get("unit_price") is None and quantity and total:
        try:
            qty = Decimal(str(quantity))
            if qty:
                normalized["unit_price"] = str(Decimal(str(total)) / qty)
        except Exception:
            pass
    return normalized


def _clean_decimal_text(value: Any, *, money: bool = False) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    text = str(value).strip()
    if not text or text.casefold() in {"null", "none", "n/a", "na", "-"}:
        return None
    text = text.replace(",", "")
    if money:
        text = text.replace("S$", "").replace("$", "").replace("SGD", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    return match.group(0)


def _sum_item_totals(items: list[dict[str, Any]]) -> Decimal | None:
    total = Decimal("0")
    found = False
    for item in items:
        try:
            total += Decimal(str(item.get("total")))
            found = True
        except Exception:
            continue
    return total if found else None


def _decimal_equal(left: Any, right: Decimal) -> bool:
    try:
        return Decimal(str(left)).quantize(Decimal("0.01")) == right.quantize(Decimal("0.01"))
    except Exception:
        return False


def validate_extracted_bill_sane(bill: ExtractedBill) -> None:
    placeholder_names = {"item name", "name", "item"}
    if any(item.name.strip().casefold() in placeholder_names for item in bill.items):
        raise ValidationError("placeholder item names are not valid bill items")
    if any(not item.name.strip() for item in bill.items):
        raise ValidationError("empty item names are not valid bill items")
    if bill.item_subtotal() <= 0:
        raise ValidationError("bill subtotal must be greater than zero")
    if all(item.total == 0 for item in bill.items):
        raise ValidationError("zero-value placeholder bill items are not valid")


def validate_allocation_plan(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw.get("people"), list) or not raw["people"]:
        raise ValidationError("people must be a non-empty list")
    people = [str(person).strip() for person in raw["people"]]
    if any(not person for person in people) or len(set(people)) != len(people):
        raise ValidationError("people must contain unique non-empty names")
    if not isinstance(raw.get("items"), list) or not raw["items"]:
        raise ValidationError("items must be a non-empty list")
    for item in raw["items"]:
        if not isinstance(item, dict):
            raise ValidationError("each allocation item must be an object")
        if not str(item.get("name", "")).strip():
            raise ValidationError("allocation item.name is required")
        split = item.get("split")
        if not isinstance(split, dict):
            raise ValidationError(f"split is required for {item.get('name')}")
        split_type = split.get("type")
        if split_type not in {"equal", "quantity", "fixed"}:
            raise ValidationError(f"unsupported split type: {split_type}")
    return raw


def read_manual_bill(path: Path, client: OllamaClient, default_currency: str) -> ExtractedBill:
    text = path.read_text(encoding="utf-8")
    raw = client.generate_text(bill_text_extraction_prompt(text, default_currency))
    return parse_extracted_bill(raw, default_currency=default_currency)


def _interactive_charge_resolution(bill: ExtractedBill) -> ExtractedBill:
    current = bill
    if not requires_charge_clarification(current):
        return current
    print()
    print("GST/service charge details are missing or unclear.")
    print("Examples: no GST/service charge | add 9% GST | add 10% service charge + 9% GST")
    answer = input("How should charges be handled? ").strip()
    current = apply_charge_instruction(current, answer)
    if current.tax is None:
        current = replace(current, tax=0)
    if current.service_charge is None:
        current = replace(current, service_charge=0)
    if current.discount is None:
        current = replace(current, discount=0)
    return current


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bill parser and split consensus flow without Telegram.")
    parser.add_argument("manual_bill_file", type=Path)
    parser.add_argument("split_instruction_file", type=Path)
    args = parser.parse_args()

    settings = load_settings()
    logging.basicConfig(level=settings.log_level)
    client = OllamaClient(settings.ollama_base_url, settings.ollama_text_model, settings.ollama_vision_model)

    bill = read_manual_bill(args.manual_bill_file, client, settings.default_currency)
    print(format_bill_confirmation(bill))
    bill = _interactive_charge_resolution(bill)
    print()
    print("Using bill:")
    print(format_bill_confirmation(bill))

    split_text = args.split_instruction_file.read_text(encoding="utf-8")
    from .consensus import ConsensusEngine
    consensus = ConsensusEngine(client=client, runs=settings.consensus_runs, log_dir=Path("logs"))
    outcome = consensus.run(bill, split_text)
    from .formatter import format_consensus_failure, format_final_result

    if hasattr(outcome, "result"):
        print(format_final_result(outcome.result))
        return 0
    print(format_consensus_failure(outcome))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Five-pass LLM interpretation consensus with deterministic calculation."""

from __future__ import annotations

import json
import logging
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from .calculator import calculate_split
from .models import ConsensusFailure, ConsensusSuccess, ExtractedBill, ValidationError
from .parser import parse_json_object, validate_allocation_plan
from .prompts import split_interpretation_prompt

logger = logging.getLogger(__name__)


class ConsensusEngine:
    def __init__(self, client: Any, runs: int = 5, log_dir: Path | str = "logs") -> None:
        self.client = client
        self.runs = runs
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def run(self, bill: ExtractedBill, split_instructions: str) -> ConsensusSuccess | ConsensusFailure:
        parsed_runs: list[dict[str, Any]] = []
        canonical_results: list[dict[str, Any]] = []
        result_hashes: list[str] = []
        allocation_hashes: list[str] = []
        errors: list[str] = []
        logger.info("Consensus validation started: runs=%s", self.runs)

        for index in range(self.runs):
            try:
                raw = self.client.generate_text(split_interpretation_prompt(bill, split_instructions))
                parsed = validate_allocation_plan(parse_json_object(raw))
                normalized = self._normalize_with_confirmed_bill(bill, parsed)
                result = calculate_split(normalized)
                canonical = result.canonical()
                allocation_hash = _hash_payload(normalized)
                result_hash = _hash_payload(canonical)
                logger.info("Run %s allocation_hash=%s result_hash=%s status=matched", index + 1, allocation_hash, result_hash)
                parsed_runs.append({"run": index + 1, "parsed": parsed, "normalized": normalized, "allocation_hash": allocation_hash, "result_hash": result_hash, "status": "matched"})
                canonical_results.append(canonical)
                allocation_hashes.append(allocation_hash)
                result_hashes.append(result_hash)
            except Exception as exc:
                message = str(exc) or exc.__class__.__name__
                errors.append(f"run {index + 1}: {message}")
                parsed_runs.append({"run": index + 1, "error": message})

        self._write_debug_log(parsed_runs, errors, allocation_hashes, result_hashes)

        if errors:
            logger.warning("Consensus failed with errors: %s", errors)
            logger.warning("Consensus validation failed: %s/%s matched", _matched_count(result_hashes), self.runs)
            return ConsensusFailure(
                reason="One or more validation runs failed.",
                details=errors,
                runs=parsed_runs,
                validation_runs_requested=self.runs,
                validation_runs_matched=_matched_count(result_hashes),
            )
        if len(canonical_results) != self.runs:
            return ConsensusFailure(
                reason=f"Expected {self.runs} valid runs, got {len(canonical_results)}.",
                details=["Not enough valid runs were produced."],
                runs=parsed_runs,
                validation_runs_requested=self.runs,
                validation_runs_matched=_matched_count(result_hashes),
            )
        first = canonical_results[0]
        disagreements = [
            f"run {index + 1} produced a different rounded calculation"
            for index, result in enumerate(canonical_results[1:], start=1)
            if result != first
        ]
        if disagreements:
            logger.warning("Consensus failed because outputs differed: %s", disagreements)
            logger.warning("Consensus validation failed: %s/%s matched", _matched_count(result_hashes), self.runs)
            return ConsensusFailure(
                reason="The validation runs did not agree after rounding.",
                details=disagreements,
                runs=parsed_runs,
                validation_runs_requested=self.runs,
                validation_runs_matched=_matched_count(result_hashes),
            )
        logger.info("Consensus validation passed: %s/%s matched", self.runs, self.runs)
        final_result = calculate_split(parsed_runs[0]["normalized"])
        final_result.validation_runs_requested = self.runs
        final_result.validation_runs_matched = self.runs
        return ConsensusSuccess(result=final_result, runs=parsed_runs)

    def _normalize_with_confirmed_bill(self, bill: ExtractedBill, parsed: dict[str, Any]) -> dict[str, Any]:
        plan_items = parsed.get("items", [])
        normalized_items: list[dict[str, Any]] = []
        used_names: set[str] = set()
        for bill_item in bill.items:
            plan_item = _find_unique_item(plan_items, bill_item.name)
            used_names.add(str(plan_item.get("name", "")).strip().casefold())
            parsed_total = plan_item.get("total")
            if parsed_total is not None and str(parsed_total).strip() and str(parsed_total) != str(bill_item.total):
                raise ValidationError(f"{bill_item.name} total mismatch between bill and split plan")
            normalized_items.append(
                {
                    "name": bill_item.name,
                    "quantity": str(bill_item.quantity),
                    "total": str(bill_item.total),
                    "split": plan_item["split"],
                }
            )
        extra_items = [
            item.get("name")
            for item in plan_items
            if str(item.get("name", "")).strip().casefold() not in used_names
        ]
        if extra_items:
            raise ValidationError(f"split plan referenced unknown bill items: {', '.join(map(str, extra_items))}")

        return {
            "currency": bill.currency,
            "people": parsed["people"],
            "items": normalized_items,
            "service_charge": str(bill.service_charge or 0),
            "tax": str(bill.tax or 0),
            "discount": str(bill.discount or 0),
            "total": str(bill.total) if bill.total is not None else None,
            "charge_allocation": parsed.get("charge_allocation", "proportional_by_subtotal"),
            "rounding": parsed.get("rounding", "half_up_2dp"),
        }

    def _write_debug_log(self, runs: list[dict[str, Any]], errors: list[str], allocation_hashes: list[str], result_hashes: list[str]) -> None:
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        path = self.log_dir / f"consensus_{timestamp}.json"
        payload = {
            "requested_runs": self.runs,
            "valid_runs": len(result_hashes),
            "allocation_hashes": allocation_hashes,
            "result_hashes": result_hashes,
            "all_final_payable_amounts_matched": len(result_hashes) == self.runs and len(set(result_hashes)) == 1,
            "all_canonical_allocation_structures_matched": len(allocation_hashes) == self.runs and len(set(allocation_hashes)) == 1,
            "failure_reason": "; ".join(errors) if errors else None,
            "errors": errors,
            "runs": runs,
        }
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _find_unique_item(items: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [item for item in items if str(item.get("name", "")).strip().casefold() == name.casefold()]
    if len(matches) != 1:
        raise ValidationError(f"split plan must include exactly one allocation for {name}")
    return matches[0]


def _hash_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def _matched_count(result_hashes: list[str]) -> int:
    if not result_hashes:
        return 0
    return max(result_hashes.count(value) for value in set(result_hashes))

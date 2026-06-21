"""Image receipt extraction with consistency validation and repair retries."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .image_handler import prepare_receipt_image_variants
from .models import ExtractedBill
from .ollama_client import OllamaClient
from .parser import parse_extracted_bill
from .prompts import bill_image_extraction_prompt, bill_image_repair_prompt
from .receipt_validation import ReceiptValidation, attach_receipt_validation, validate_receipt_consistency, validation_log_text

logger = logging.getLogger("billy.receipt")


@dataclass(frozen=True, slots=True)
class ReceiptExtractionResult:
    bill: ExtractedBill
    validation: ReceiptValidation
    raw_extractions: list[str]
    repair_used: bool = False
    retry_count: int = 0
    model: str | None = None


def extract_receipt_from_image(
    client: OllamaClient,
    image_path: Path,
    *,
    default_currency: str,
    vision_model: str,
    fallback_model: str | None = None,
    image_debug: bool = False,
    allow_repair: bool = True,
) -> ReceiptExtractionResult:
    variants = prepare_receipt_image_variants(image_path, image_debug=image_debug)
    models = [vision_model] + ([fallback_model] if fallback_model else [])
    raw_extractions: list[str] = []
    best: ReceiptExtractionResult | None = None
    attempts = 0

    for model in models:
        for variant in variants:
            attempts += 1
            result = _run_extraction_attempt(
                client,
                bill_image_extraction_prompt(default_currency),
                variant,
                default_currency,
                model,
                raw_extractions,
                retry_count=max(0, attempts - 1),
            )
            if result is None:
                continue
            best = _choose_better(best, result)
            logger.info(validation_log_text(result.validation))
            if result.validation.is_valid:
                return result
            if allow_repair and result.validation.status == "incomplete_extraction":
                logger.info("Retrying receipt extraction with focused missing-item prompt.")
                repair_prompt = bill_image_repair_prompt(
                    default_currency,
                    json.dumps(result.bill.to_dict(), indent=2),
                    result.validation.reason or "unknown",
                    str(result.validation.missing_amount or ""),
                )
                repair = _run_extraction_attempt(
                    client,
                    repair_prompt,
                    variant,
                    default_currency,
                    model,
                    raw_extractions,
                    retry_count=max(0, attempts),
                    repair_used=True,
                )
                if repair is None:
                    continue
                best = _choose_better(best, repair)
                logger.info(validation_log_text(repair.validation))
                if repair.validation.is_valid:
                    return repair

    if best is not None:
        return best
    raise RuntimeError("receipt extraction failed")


def _run_extraction_attempt(
    client: OllamaClient,
    prompt: str,
    image_path: Path,
    default_currency: str,
    model: str,
    raw_extractions: list[str],
    *,
    retry_count: int,
    repair_used: bool = False,
) -> ReceiptExtractionResult | None:
    try:
        raw = client.generate_vision(prompt, image_path, model=model)
        raw_extractions.append(raw)
        bill = parse_extracted_bill(raw, default_currency)
        validation = validate_receipt_consistency(bill)
        bill = attach_receipt_validation(bill, validation)
        return ReceiptExtractionResult(
            bill=bill,
            validation=validation,
            raw_extractions=list(raw_extractions),
            repair_used=repair_used,
            retry_count=retry_count,
            model=model,
        )
    except Exception as exc:
        logger.warning("Receipt extraction attempt failed for %s with %s: %s", image_path, model, exc)
        return None


def _choose_better(current: ReceiptExtractionResult | None, candidate: ReceiptExtractionResult) -> ReceiptExtractionResult:
    if current is None:
        return candidate
    if candidate.validation.is_valid and not current.validation.is_valid:
        return candidate
    if len(candidate.bill.items) > len(current.bill.items):
        return candidate
    current_missing = abs(current.validation.missing_amount or 0)
    candidate_missing = abs(candidate.validation.missing_amount or 0)
    if candidate_missing and (not current_missing or candidate_missing < current_missing):
        return candidate
    return current

"""Local CLI for testing receipt image extraction without Telegram."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .bill_confirmation import format_bill_confirmation
from .config import load_settings
from .image_handler import inspect_image
from .logging_config import setup_logging
from .ollama_client import OllamaClient
from .receipt_extraction import extract_receipt_from_image

logger = logging.getLogger("billy.receipt_debug")


def extract_receipt(path: Path, *, model: str | None = None, repair: bool = True) -> tuple[dict, str, dict]:
    settings = load_settings()
    setup_logging(settings.log_level, settings.third_party_log_level)
    client = OllamaClient(
        settings.ollama_base_url,
        settings.ollama_text_model,
        settings.ollama_vision_model,
        image_debug=settings.image_debug,
    )
    info = inspect_image(path)
    selected_model = model or settings.ollama_vision_model
    logger.info("Receipt extraction started: model=%s path=%s dimensions=%sx%s", selected_model, path, info.width, info.height)
    result = extract_receipt_from_image(
        client,
        path,
        default_currency=settings.default_currency,
        vision_model=selected_model,
        fallback_model=settings.ollama_vision_fallback_model if model is None else None,
        image_debug=settings.image_debug,
        allow_repair=repair,
    )
    debug = {
        "validation_status": result.validation.status,
        "validation_reason": result.validation.reason,
        "item_sum": str(result.validation.item_sum),
        "explicit_subtotal": str(result.validation.explicit_subtotal) if result.validation.explicit_subtotal is not None else None,
        "final_total": str(result.validation.final_total) if result.validation.final_total is not None else None,
        "charges": str(result.validation.charges),
        "discount": str(result.validation.discount),
        "missing_amount": str(result.validation.missing_amount) if result.validation.missing_amount is not None else None,
        "repair_used": result.repair_used,
        "retry_count": result.retry_count,
        "model": result.model,
        "raw_extractions": result.raw_extractions,
    }
    return result.bill.to_dict(), format_bill_confirmation(result.bill, show_single_quantity=True), debug


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Billy receipt image extraction without Telegram.")
    parser.add_argument("image_path", type=Path)
    parser.add_argument("--repair", action="store_true", help="Enable focused repair pass for incomplete extractions.")
    parser.add_argument("--model", help="Override the configured primary vision model for this run.")
    args = parser.parse_args()
    payload, confirmation, debug = extract_receipt(args.image_path, model=args.model, repair=args.repair)
    print("Raw extraction and validation:")
    print(json.dumps(debug, indent=2))
    print()
    print("Final normalized receipt:")
    print(json.dumps(payload, indent=2))
    print()
    print("Telegram confirmation preview:")
    print(confirmation)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

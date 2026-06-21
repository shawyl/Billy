"""Telegram bot entry point for local-first bill splitting with Ollama."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .bill_confirmation import (
    apply_basic_bill_correction,
    apply_charge_instruction,
    format_bill_confirmation,
    format_incomplete_receipt_warning,
    requires_charge_clarification,
)
from .calculator import calculate_split
from .config import load_settings, validate_runtime_settings
from .consensus import ConsensusEngine
from .conversation_state import Stage, StateStore
from .formatter import (
    format_concise_result,
    format_consensus_failure,
    format_final_result,
    should_send_concise_result,
    should_send_detailed_result,
)
from .image_handler import download_message_image, inspect_image
from .logging_config import setup_logging
from .manual_parser import (
    ManualBillParseError,
    contains_bill_like_lines,
    contains_prices,
    detect_ambiguous_quantity_family_split,
    manual_parse_failure_message,
    parse_manual_bill_text,
)
from .ollama_client import OllamaClient
from .parser import parse_extracted_bill
from .prompts import bill_image_extraction_prompt, bill_text_extraction_prompt
from .receipt_extraction import extract_receipt_from_image
from .split_resolver import resolve_manual_split
from .text_intents import confirmation_and_remainder, is_reset_text, status_text
from .user_identity import resolve_current_user_display_name

logger = logging.getLogger("billy.bot")


settings = load_settings()
states = StateStore()
ollama = OllamaClient(settings.ollama_base_url, settings.ollama_text_model, settings.ollama_vision_model, image_debug=settings.image_debug)
consensus = ConsensusEngine(ollama, runs=settings.consensus_runs, log_dir=Path("logs"))


def _is_allowed(update: Update) -> bool:
    if settings.allowed_chat_id is None:
        return True
    chat = update.effective_chat
    return chat is not None and chat.id == settings.allowed_chat_id


async def _reject_if_needed(update: Update) -> bool:
    if _is_allowed(update):
        return False
    if update.message:
        await update.message.reply_text("This bot is not configured for this chat.")
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_needed(update):
        return
    states.reset(update.effective_chat.id)
    await update.message.reply_text(
        _help_text()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_needed(update):
        return
    await update.message.reply_text(_help_text())


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_needed(update):
        return
    states.reset(update.effective_chat.id)
    await update.message.reply_text("Reset. Send a new bill when you're ready.")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_needed(update):
        return
    state = states.get(update.effective_chat.id)
    await update.message.reply_text(status_text(state))


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_needed(update):
        return
    assert update.message is not None
    chat_id = update.effective_chat.id
    try:
        logger.info("Receipt image message received: chat_id=%s", chat_id)
        image_path = await download_message_image(update.message, settings.temp_image_dir)
        info = inspect_image(image_path)
        logger.info("Receipt image downloaded: path=%s dimensions=%sx%s", info.path, info.width, info.height)
        result = _extract_receipt_from_image(image_path)
        bill = result.bill
        state = states.get(chat_id)
        state.bill = bill
        state.current_user_display_name = _current_user_display_name(update)
        state.stage = Stage.WAITING_BILL_CONFIRMATION
        if bill.validation_status and bill.validation_status != "valid":
            await update.message.reply_text(format_incomplete_receipt_warning(bill))
        else:
            await update.message.reply_text(format_bill_confirmation(bill, show_single_quantity=True))
    except Exception as exc:
        logger.exception("Failed to handle receipt photo")
        await update.message.reply_text(_image_failure_message())


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await _reject_if_needed(update):
        return
    assert update.message is not None
    chat_id = update.effective_chat.id
    text = update.message.text or ""
    state = states.get(chat_id)
    state.current_user_display_name = _current_user_display_name(update)

    try:
        if is_reset_text(text):
            states.reset(chat_id)
            await update.message.reply_text("Reset. Send a new bill when you're ready.")
            return
        if state.stage == Stage.COMPLETED and contains_bill_like_lines(text):
            states.reset(chat_id)
            await _extract_manual_bill(update, text)
            return
        if state.stage == Stage.IDLE:
            await _extract_manual_bill(update, text)
        elif state.stage == Stage.WAITING_BILL_CONFIRMATION:
            await _handle_bill_confirmation(update, text)
        elif state.stage == Stage.WAITING_CHARGE_CLARIFICATION:
            await _handle_charge_clarification(update, text)
        elif state.stage == Stage.WAITING_SPLIT_INSTRUCTIONS:
            await _handle_split_instructions(update, text)
        elif state.stage == Stage.CALCULATING:
            await update.message.reply_text("I am still validating the split. Please wait a moment.")
        elif state.stage == Stage.COMPLETED:
            await update.message.reply_text("That split is complete. Send a new bill, or type reset to start over.")
    except Exception:
        logger.exception("Failed to handle text message")
        await update.message.reply_text("I could not process that safely. Please restate the bill or split instructions more clearly.")


async def _extract_manual_bill(update: Update, text: str) -> None:
    if contains_bill_like_lines(text):
        await _extract_deterministic_manual_bill(update, text)
        return
    if contains_prices(text):
        await update.message.reply_text(manual_parse_failure_message())
        return

    chat_id = update.effective_chat.id
    raw = ollama.generate_text(bill_text_extraction_prompt(text, settings.default_currency))
    bill = parse_extracted_bill(raw, settings.default_currency)
    state = states.get(chat_id)
    state.bill = bill
    state.stage = Stage.WAITING_BILL_CONFIRMATION
    await update.message.reply_text(format_bill_confirmation(bill, heading="Bill text detected"))


async def _extract_deterministic_manual_bill(update: Update, text: str) -> None:
    chat_id = update.effective_chat.id
    current_user = _current_user_display_name(update)
    try:
        parsed = parse_manual_bill_text(text, settings.default_currency, current_user)
    except ManualBillParseError as exc:
        await update.message.reply_text(str(exc))
        return
    state = states.get(chat_id)
    charged_bill = apply_charge_instruction(parsed.bill, text)
    state.bill = charged_bill
    state.people = parsed.people
    state.current_user_display_name = current_user
    state.pending_split_instructions = parsed.split_text or None
    state.split_rules_raw = parsed.split_text or None
    state.pending_ambiguity_message = detect_ambiguous_quantity_family_split(charged_bill, parsed.split_text, current_user)
    state.stage = Stage.WAITING_BILL_CONFIRMATION
    await update.message.reply_text(
        format_bill_confirmation(
            charged_bill,
            heading="Bill text detected",
            people=parsed.people,
            split_instructions=parsed.split_instructions,
            ambiguity_message=parsed.ambiguity_message,
            show_single_quantity=True,
        )
    )


async def _handle_bill_confirmation(update: Update, text: str) -> None:
    chat_id = update.effective_chat.id
    state = states.get(chat_id)
    if state.bill is None:
        state.stage = Stage.IDLE
        await update.message.reply_text("Please send the bill again.")
        return

    lower = text.casefold()
    confirmed, split_rules = confirmation_and_remainder(text)
    if state.pending_ambiguity_message and (contains_bill_like_lines(text) or ":" in text):
        await _handle_split_instructions(update, text)
        return

    if confirmed:
        state.receipt_confirmed = True
        if split_rules:
            state.pending_split_instructions = split_rules
            state.split_rules_raw = split_rules
        if requires_charge_clarification(state.bill):
            state.stage = Stage.WAITING_CHARGE_CLARIFICATION
            await update.message.reply_text(
                "Should I include GST and service charge?\n\nExamples:\n* No GST/service charge\n* Add 9% GST\n* Add 10% service charge + 9% GST\n* GST $7.80, service charge $8.60"
            )
            return
        if split_rules:
            state.stage = Stage.WAITING_SPLIT_INSTRUCTIONS
            await _continue_after_bill_ready(update, state)
            return
        state.stage = Stage.WAITING_SPLIT_INSTRUCTIONS
        await update.message.reply_text(
            "Bill confirmed.\n\n"
            "Send me the split rules.\n"
            "Example:\n"
            "3 Guinness on me, 6 on C, 5 Suntory Gin on Y"
        )
        return

    if any(word in lower for word in ["correct", "actually", "should be", "add ", "no gst", "service", "gst", "tax", "total"]):
        state.people = _apply_people_correction(state.people, text)
        state.bill = apply_basic_bill_correction(state.bill, text)
        state.pending_split_instructions = _apply_split_correction(state.pending_split_instructions, text)
        if requires_charge_clarification(state.bill):
            state.stage = Stage.WAITING_CHARGE_CLARIFICATION
            await update.message.reply_text(
                format_bill_confirmation(
                    state.bill,
                    heading="Bill text detected",
                    people=state.people,
                    show_single_quantity=True,
                )
            )
            return
        state.stage = Stage.WAITING_SPLIT_INSTRUCTIONS
        await _continue_after_bill_ready(update, state)
        return

    state.bill = apply_basic_bill_correction(state.bill, text)
    state.people = _apply_people_correction(state.people, text)
    state.pending_split_instructions = _apply_split_correction(state.pending_split_instructions, text)
    state.split_rules_raw = state.pending_split_instructions
    await update.message.reply_text(format_bill_confirmation(state.bill))


async def _handle_charge_clarification(update: Update, text: str) -> None:
    chat_id = update.effective_chat.id
    state = states.get(chat_id)
    if state.bill is None:
        state.stage = Stage.IDLE
        await update.message.reply_text("Please send the bill again.")
        return
    state.bill = apply_charge_instruction(state.bill, text)
    if requires_charge_clarification(state.bill):
        await update.message.reply_text("I still need the GST and service charge handling. For example: no GST/service charge, or add 10% service charge + 9% GST.")
        return
    state.stage = Stage.WAITING_SPLIT_INSTRUCTIONS
    await _continue_after_bill_ready(update, state)


async def _continue_after_bill_ready(update: Update, state) -> None:
    if state.bill is None:
        await update.message.reply_text("Please send the bill again.")
        return
    if state.pending_ambiguity_message:
        state.stage = Stage.WAITING_SPLIT_INSTRUCTIONS
        await update.message.reply_text(state.pending_ambiguity_message)
        return
    if state.pending_split_instructions:
        split_text = state.pending_split_instructions
        state.pending_split_instructions = None
        await _handle_split_instructions(update, split_text)
        return
    state.stage = Stage.WAITING_SPLIT_INSTRUCTIONS
    await update.message.reply_text(
        "Bill confirmed.\n\n"
        "Send me the split rules.\n"
        "Example:\n"
        "3 Guinness on me, 6 on C, 5 Suntory Gin on Y"
    )


async def _handle_split_instructions(update: Update, text: str) -> None:
    chat_id = update.effective_chat.id
    state = states.get(chat_id)
    if state.bill is None:
        state.stage = Stage.IDLE
        await update.message.reply_text("Please send the bill again.")
        return

    split_text = text
    state.split_rules_raw = text
    current_user = state.current_user_display_name or _current_user_display_name(update)
    if contains_bill_like_lines(text):
        try:
            parsed = parse_manual_bill_text(text, settings.default_currency, current_user)
            state.bill = parsed.bill
            state.people = parsed.people or state.people
            split_text = parsed.split_text or text
        except ManualBillParseError:
            pass

    state.bill = apply_charge_instruction(state.bill, text)
    ambiguity = detect_ambiguous_quantity_family_split(state.bill, split_text, current_user)
    if ambiguity:
        state.pending_ambiguity_message = ambiguity
        state.pending_split_instructions = None
        state.stage = Stage.WAITING_SPLIT_INSTRUCTIONS
        await update.message.reply_text(ambiguity)
        return
    state.pending_ambiguity_message = None
    if requires_charge_clarification(state.bill):
        state.stage = Stage.WAITING_CHARGE_CLARIFICATION
        await update.message.reply_text("Before splitting, please clarify GST and service charge handling.")
        return

    try:
        resolved = resolve_manual_split(state.bill, state.people or [], split_text, current_user)
        state.people = resolved.plan["people"]
        state.canonical_split = resolved.plan
        result = _calculate_locked_plan(resolved.plan)
        state.validation_status = "5/5 matched"
        state.stage = Stage.CALCULATING
        await update.message.reply_text(resolved.summary)
        await _send_result(update, result)
        state.stage = Stage.COMPLETED
        state.last_result_summary = format_concise_result(result)
        return
    except Exception as exc:
        logger.info("Deterministic split resolution did not handle instructions: %s", exc)

    state.stage = Stage.CALCULATING
    await update.message.reply_text("Thanks. I am validating the split 5 times before calculating.")
    outcome = consensus.run(state.bill, split_text)
    if hasattr(outcome, "result"):
        await _send_result(update, outcome.result)
        state.stage = Stage.COMPLETED
        state.last_result_summary = format_concise_result(outcome.result)
    else:
        await update.message.reply_text(format_consensus_failure(outcome))
        state.stage = Stage.WAITING_SPLIT_INSTRUCTIONS


def _calculate_locked_plan(plan):
    logger.info("Consensus validation started: runs=%s", settings.consensus_runs)
    results = []
    hashes = []
    allocation_hash = _hash_payload(plan)
    for index in range(settings.consensus_runs):
        result = calculate_split(plan)
        result_hash = _hash_payload(result.canonical())
        logger.info("Run %s allocation_hash=%s result_hash=%s status=matched", index + 1, allocation_hash, result_hash)
        results.append(result)
        hashes.append(result_hash)
    first = results[0].canonical()
    if any(result.canonical() != first for result in results[1:]):
        logger.warning("Consensus validation failed: %s/%s matched", max(hashes.count(value) for value in set(hashes)), settings.consensus_runs)
        _write_locked_validation_log([allocation_hash] * settings.consensus_runs, hashes, False, "locked deterministic results differed")
        raise RuntimeError("locked deterministic calculation did not stay stable")
    logger.info("Consensus validation passed: %s/%s matched", settings.consensus_runs, settings.consensus_runs)
    _write_locked_validation_log([allocation_hash] * settings.consensus_runs, hashes, True, None)
    results[0].validation_runs_requested = settings.consensus_runs
    results[0].validation_runs_matched = settings.consensus_runs
    return results[0]


def _current_user_display_name(update: Update) -> str:
    user = update.effective_user
    first_name = user.first_name if user else None
    return resolve_current_user_display_name(settings.self_name, first_name)


def _extract_receipt_from_image(image_path: Path):
    logger.info("Receipt extraction started: model=%s", settings.ollama_vision_model)
    result = extract_receipt_from_image(
        ollama,
        image_path,
        default_currency=settings.default_currency,
        vision_model=settings.ollama_vision_model,
        fallback_model=settings.ollama_vision_fallback_model,
        image_debug=settings.image_debug,
    )
    if result.validation.is_valid:
        logger.info(
            "Receipt extraction success: items=%s total=%s model=%s repair_used=%s retry_count=%s",
            len(result.bill.items),
            result.bill.total,
            result.model,
            result.repair_used,
            result.retry_count,
        )
    else:
        logger.warning(
            "Receipt extraction incomplete: items=%s subtotal=%s total=%s status=%s reason=%s missing_amount=%s",
            len(result.bill.items),
            result.bill.subtotal,
            result.bill.total,
            result.validation.status,
            result.validation.reason,
            result.validation.missing_amount,
        )
    return result


async def _send_result(update: Update, result) -> None:
    detail_level = settings.result_detail_level
    if detail_level not in {"concise_only", "normal", "detailed"}:
        detail_level = "normal"
    if should_send_detailed_result(detail_level):
        await update.message.reply_text(format_final_result(result, detail_level=detail_level))
    if should_send_concise_result(detail_level):
        await update.message.reply_text(format_concise_result(result))


def _apply_split_correction(existing: str | None, text: str) -> str | None:
    lower = text.casefold()
    if "split" not in lower and " on " not in lower and "pay" not in lower:
        return existing
    return "\n".join(part for part in [existing, text.strip()] if part)


def _apply_people_correction(people: list[str] | None, text: str) -> list[str] | None:
    if not people:
        return people
    updated = list(people)
    rename_match = re.search(r"\b([A-Za-z][A-Za-z0-9 .'-]*)\s+is\s+([A-Za-z][A-Za-z0-9 .'-]*)", text, re.I)
    if rename_match and "actually" in text.casefold():
        old_name = rename_match.group(1).strip()
        new_name = rename_match.group(2).strip()
        for index, person in enumerate(updated):
            if person.casefold() == old_name.casefold():
                updated[index] = new_name
                return updated
    add_match = re.search(r"add\s+([A-Za-z][A-Za-z0-9 .'-]*)\s+as\s+another\s+person", text, re.I)
    if add_match:
        person = add_match.group(1).strip()
        if person not in updated:
            updated.append(person)
    return updated


def _help_text() -> str:
    return (
        "Send a bill like:\n\n"
        "1X Pizza -> 26.00\n"
        "2X Guinness -> 11.00\n\n"
        "Then add people and split rules:\n\n"
        "Me, C, Y\n"
        "Pizza split equally\n"
        "Guinness on C\n"
        "Add 9% GST and 10% service charge"
    )


def _hash_payload(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def _write_locked_validation_log(allocation_hashes, result_hashes, passed, failure_reason):
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "mode": "locked_deterministic",
        "requested_runs": settings.consensus_runs,
        "valid_runs": len(result_hashes),
        "allocation_hashes": allocation_hashes,
        "result_hashes": result_hashes,
        "all_final_payable_amounts_matched": passed,
        "all_canonical_allocation_structures_matched": passed,
        "failure_reason": failure_reason,
    }
    (log_dir / f"locked_consensus_{timestamp}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _image_failure_message() -> str:
    return (
        "I could not read this receipt safely.\n\n"
        "What I need:\n\n"
        "* items\n"
        "* quantities\n"
        "* prices\n"
        "* total\n\n"
        "You can either:\n\n"
        "1. Send a clearer/cropped image of the receipt section, or\n"
        "2. Paste the bill like this:\n\n"
        "1X Guinness -> 14.00\n"
        "1X Gin Tonic -> 12.00\n"
        "Add 9% GST and 10% service charge"
    )


def main() -> None:
    validate_runtime_settings(settings)
    app_logger = setup_logging(settings.log_level, settings.third_party_log_level)
    app_logger.info("Billy started.")
    app_logger.info("Telegram polling active.")
    app_logger.info("Ollama: %s", settings.ollama_base_url)
    app_logger.info("Text model: %s", settings.ollama_text_model)
    app_logger.info("Vision model: %s", settings.ollama_vision_model)
    if settings.ollama_vision_fallback_model:
        app_logger.info("Vision fallback model: %s", settings.ollama_vision_fallback_model)
    application = Application.builder().token(settings.telegram_bot_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.run_polling()


if __name__ == "__main__":
    main()

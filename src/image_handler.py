"""Telegram image download and local preprocessing helpers for receipts."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from telegram import Message
except ImportError:  # pragma: no cover - keeps local image tests dependency-light
    Message = Any

logger = logging.getLogger("billy.image")


@dataclass(frozen=True, slots=True)
class ImageInfo:
    path: Path
    width: int
    height: int


async def download_message_image(message: Message, temp_dir: str | Path) -> Path:
    directory = Path(temp_dir)
    directory.mkdir(parents=True, exist_ok=True)
    suffix = ".jpg"

    if message.photo:
        logger.info("Image received: telegram photo variants=%s", len(message.photo))
        photo = message.photo[-1]
        telegram_file = await photo.get_file()
        suffix = ".jpg"
    elif message.document and (message.document.mime_type or "").casefold() in {"image/jpeg", "image/png"}:
        logger.info("Image received: document mime_type=%s", message.document.mime_type)
        telegram_file = await message.document.get_file()
        suffix = ".png" if message.document.mime_type == "image/png" else ".jpg"
    else:
        raise ValueError("message does not contain a supported receipt image")

    path = directory / f"receipt_{message.chat_id}_{uuid4().hex}{suffix}"
    await telegram_file.download_to_drive(custom_path=path)
    logger.info("Image downloaded: %s", path)
    return path


async def download_largest_photo(message: Message, temp_dir: str | Path) -> Path:
    return await download_message_image(message, temp_dir)


def inspect_image(path: str | Path) -> ImageInfo:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Pillow is required for image receipt handling") from exc
    with Image.open(path) as image:
        width, height = image.size
    return ImageInfo(path=Path(path), width=width, height=height)


def prepare_receipt_image_variants(path: str | Path, *, image_debug: bool = False) -> list[Path]:
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Pillow is required for image receipt handling") from exc

    source = Path(path)
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
    except Exception as exc:
        raise ValueError("invalid image file") from exc

    width, height = image.size
    logger.info("Image dimensions: %sx%s", width, height)
    if max(width, height) < 1400:
        scale = 1400 / max(width, height)
        image = image.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)

    enhanced = ImageEnhance.Contrast(image).enhance(1.6)
    enhanced = ImageEnhance.Sharpness(enhanced).enhance(1.8)
    enhanced = enhanced.filter(ImageFilter.SHARPEN)
    enhanced_path = source.with_name(f"{source.stem}_enhanced.jpg")
    enhanced.save(enhanced_path, quality=95)

    crop_top = int(image.height * 0.25)
    cropped = image.crop((0, crop_top, image.width, image.height))
    cropped = ImageEnhance.Contrast(cropped).enhance(1.5)
    crop_path = source.with_name(f"{source.stem}_bottom_panel.jpg")
    cropped.save(crop_path, quality=95)

    variants = [source, enhanced_path, crop_path]
    if image_debug:
        gray = ImageOps.grayscale(enhanced)
        gray = ImageEnhance.Contrast(gray).enhance(1.8)
        debug_path = source.with_name(f"{source.stem}_debug_high_contrast.jpg")
        gray.save(debug_path, quality=95)
        variants.append(debug_path)
        logger.info("Image debug variants: %s", ", ".join(str(item) for item in variants))
    return variants

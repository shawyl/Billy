import asyncio

import pytest

from src.image_handler import download_message_image, prepare_receipt_image_variants


class FakeFile:
    def __init__(self, marker):
        self.marker = marker

    async def download_to_drive(self, custom_path):
        custom_path.write_text(self.marker, encoding="utf-8")


class FakePhoto:
    def __init__(self, marker):
        self.marker = marker

    async def get_file(self):
        return FakeFile(self.marker)


class FakeDocument:
    def __init__(self, mime_type, marker):
        self.mime_type = mime_type
        self.marker = marker

    async def get_file(self):
        return FakeFile(self.marker)


class FakeMessage:
    chat_id = 123

    def __init__(self, *, photo=None, document=None):
        self.photo = photo or []
        self.document = document


def test_photo_handler_selects_highest_resolution_photo(tmp_path):
    message = FakeMessage(photo=[FakePhoto("small"), FakePhoto("large")])

    path = asyncio.run(download_message_image(message, tmp_path))

    assert path.read_text(encoding="utf-8") == "large"


def test_image_document_handler_accepts_jpeg_and_png(tmp_path):
    jpeg = asyncio.run(download_message_image(FakeMessage(document=FakeDocument("image/jpeg", "jpeg")), tmp_path))
    png = asyncio.run(download_message_image(FakeMessage(document=FakeDocument("image/png", "png")), tmp_path))

    assert jpeg.suffix == ".jpg"
    assert png.suffix == ".png"


def test_preprocessing_outputs_valid_enhanced_file(tmp_path):
    from PIL import Image

    source = tmp_path / "receipt.jpg"
    Image.new("RGB", (200, 100), color="white").save(source)

    variants = prepare_receipt_image_variants(source)

    assert variants[0] == source
    assert variants[1].exists()
    assert variants[1].suffix == ".jpg"


def test_invalid_image_fails_gracefully(tmp_path):
    source = tmp_path / "not_an_image.jpg"
    source.write_text("nope", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid image"):
        prepare_receipt_image_variants(source)

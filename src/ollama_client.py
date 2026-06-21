"""Isolated Ollama HTTP client for text and vision JSON generation."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("billy.ollama")


class OllamaClient:
    def __init__(self, base_url: str, text_model: str, vision_model: str, timeout: int = 120, image_debug: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.text_model = text_model
        self.vision_model = vision_model
        self.timeout = timeout
        self.image_debug = image_debug

    def generate_text(self, prompt: str) -> str:
        return self._generate(model=self.text_model, prompt=prompt)

    def generate_vision(self, prompt: str, image_path: str | Path, *, model: str | None = None) -> str:
        image_bytes = Path(image_path).read_bytes()
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return self._generate(model=model or self.vision_model, prompt=prompt, images=[encoded])

    def _generate(self, model: str, prompt: str, images: list[str] | None = None) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }
        if images:
            payload["images"] = images
        import requests

        try:
            response = requests.post(f"{self.base_url}/api/generate", json=payload, timeout=self.timeout)
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(f"Ollama model request failed for {model}. Check that the model is installed and supports the requested input.") from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"Ollama is not reachable at {self.base_url}") from exc
        data = response.json()
        raw = data.get("response")
        if not isinstance(raw, str):
            raise RuntimeError("Ollama response did not include a text response")
        if self.image_debug:
            logger.info("Ollama raw response preview: %s", raw[:500].replace("\n", " "))
        extracted = _extract_json_text(raw)
        json.loads(extracted)
        return extracted


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

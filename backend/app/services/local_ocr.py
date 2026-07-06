from __future__ import annotations

from io import BytesIO

from PIL import Image
import pytesseract

from app.config import get_settings

settings = get_settings()


def ocr_image_to_text(image_bytes: bytes) -> str:
    image = Image.open(BytesIO(image_bytes))
    # Tesseract expects RGB or L images reliably.
    if image.mode not in {"RGB", "L"}:
        image = image.convert("RGB")
    try:
        return pytesseract.image_to_string(
            image,
            lang=settings.pdf_ocr_language,
            config=settings.tesseract_config,
        ).strip()
    except Exception:
        # fallback if German language pack is unavailable locally
        return pytesseract.image_to_string(image, lang="eng", config=settings.tesseract_config).strip()

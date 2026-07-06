from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pytesseract

from app.config import get_settings

settings = get_settings()


def _prepare_for_ocr(image: Image.Image) -> Image.Image:
    """Improve mobile-camera frames before Tesseract OCR."""
    image = ImageOps.exif_transpose(image)
    if image.mode != "RGB":
        image = image.convert("RGB")

    # Upscale small frames. iPhone video canvas captures are often smaller than
    # the native camera stills, and OCR benefits from larger text.
    max_side = max(image.size)
    if max_side < 1800:
        scale = 1800 / max_side
        new_size = (int(image.width * scale), int(image.height * scale))
        image = image.resize(new_size, Image.Resampling.LANCZOS)

    gray = ImageOps.grayscale(image)
    gray = ImageEnhance.Contrast(gray).enhance(1.8)
    gray = ImageEnhance.Sharpness(gray).enhance(1.4)
    gray = gray.filter(ImageFilter.SHARPEN)
    return gray


def ocr_image_to_text(image_bytes: bytes) -> str:
    image = Image.open(BytesIO(image_bytes))
    image = _prepare_for_ocr(image)

    # PSM 6 works well for a photographed question block. If it returns very
    # little text, fall back to the global config / PSM 3.
    configs = ["--psm 6", settings.tesseract_config or "--psm 3", "--psm 3"]
    best = ""
    for config in configs:
        try:
            text = pytesseract.image_to_string(
                image,
                lang=settings.pdf_ocr_language,
                config=config,
            ).strip()
        except Exception:
            text = pytesseract.image_to_string(image, lang="eng", config=config).strip()
        if len(text) > len(best):
            best = text
        if len(best) > 60:
            break
    return best

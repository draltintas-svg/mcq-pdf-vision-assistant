from __future__ import annotations

import io
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader


VisionOcrCallback = Callable[[bytes, int], str]


@dataclass(frozen=True)
class PageText:
    page: int
    text: str
    method: str = "digital"


@dataclass
class PdfExtractionResult:
    pages: list[PageText]
    page_count: int
    digital_pages_with_text: int = 0
    ocr_pages_attempted: int = 0
    ocr_pages_with_text: int = 0
    ocr_provider: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def used_ocr(self) -> bool:
        return self.ocr_pages_attempted > 0

    @property
    def text_char_count(self) -> int:
        return sum(len(page.text) for page in self.pages)


class OCRUnavailableError(RuntimeError):
    """Raised when OCR is requested but the required binary/package is unavailable."""


def extract_pdf_text(
    path: Path,
    *,
    enable_ocr: bool = False,
    ocr_provider: str = "tesseract",
    min_text_chars_per_page: int = 50,
    max_ocr_pages: int = 250,
    ocr_language: str = "deu+eng",
    render_dpi: int = 220,
    tesseract_config: str = "--psm 3",
    vision_ocr: VisionOcrCallback | None = None,
) -> PdfExtractionResult:
    """Extract PDF text page-by-page and optionally OCR scanned/low-text pages.

    Digital PDFs are handled first with pypdf. Pages that have little or no text are
    rendered to images and OCRed with either local Tesseract or an OpenAI vision callback.
    """
    digital_pages = _extract_digital_pages(path)
    pages_by_number: dict[int, PageText] = {}
    pages_needing_ocr: list[int] = []
    digital_pages_with_text = 0

    for page in digital_pages:
        cleaned = _clean_text(page.text)
        if len(cleaned) >= min_text_chars_per_page:
            digital_pages_with_text += 1
            pages_by_number[page.page] = PageText(page=page.page, text=cleaned, method="digital")
        else:
            if cleaned:
                pages_by_number[page.page] = PageText(page=page.page, text=cleaned, method="digital-short")
            pages_needing_ocr.append(page.page)

    result = PdfExtractionResult(
        pages=[],
        page_count=len(digital_pages),
        digital_pages_with_text=digital_pages_with_text,
        ocr_provider=None,
    )

    provider = (ocr_provider or "off").strip().lower()
    if enable_ocr and provider not in {"", "off", "none", "false"} and pages_needing_ocr:
        target_pages = pages_needing_ocr[: max(max_ocr_pages, 0)]
        skipped = len(pages_needing_ocr) - len(target_pages)
        if skipped > 0:
            result.warnings.append(
                f"OCR wurde auf {len(target_pages)} Seiten begrenzt; {skipped} Seiten wurden übersprungen."
            )

        result.ocr_provider = provider
        result.ocr_pages_attempted = len(target_pages)
        try:
            if provider == "tesseract":
                ocr_pages = _tesseract_ocr_pages(
                    path,
                    target_pages,
                    language=ocr_language,
                    render_dpi=render_dpi,
                    tesseract_config=tesseract_config,
                )
            elif provider == "openai":
                ocr_pages = _vision_ocr_pages(
                    path,
                    target_pages,
                    render_dpi=render_dpi,
                    vision_ocr=vision_ocr,
                )
            else:
                raise OCRUnavailableError(
                    f"Unbekannter PDF_OCR_PROVIDER '{ocr_provider}'. Nutze 'tesseract', 'openai' oder 'off'."
                )
        except OCRUnavailableError as exc:
            ocr_pages = []
            result.warnings.append(str(exc))

        for page in ocr_pages:
            text = _clean_text(page.text)
            if not text:
                continue
            result.ocr_pages_with_text += 1
            current = pages_by_number.get(page.page)
            # Keep the more useful text. This avoids replacing selectable digital
            # text with a worse OCR result on mixed PDFs.
            if current is None or len(text) > len(current.text):
                pages_by_number[page.page] = PageText(page=page.page, text=text, method=page.method)

    result.pages = [
        page
        for _, page in sorted(pages_by_number.items(), key=lambda item: item[0])
        if page.text.strip()
    ]
    return result


def _extract_digital_pages(path: Path) -> list[PageText]:
    reader = PdfReader(str(path))
    pages: list[PageText] = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append(PageText(page=index, text=_clean_text(text), method="digital"))
    return pages


def _tesseract_ocr_pages(
    path: Path,
    page_numbers: Iterable[int],
    *,
    language: str,
    render_dpi: int,
    tesseract_config: str,
) -> list[PageText]:
    if shutil.which("tesseract") is None:
        raise OCRUnavailableError(
            "Tesseract ist nicht installiert. Im Docker-Setup ist es enthalten; lokal: tesseract-ocr + Sprachpakete installieren."
        )

    try:
        import pytesseract
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - depends on optional OCR packages
        raise OCRUnavailableError(
            "Python-OCR-Pakete fehlen. Installiere backend/requirements.txt erneut."
        ) from exc

    results: list[PageText] = []
    for page_number in page_numbers:
        image_bytes = _render_pdf_page_png(path, page_number, render_dpi)
        image = Image.open(io.BytesIO(image_bytes))
        image = ImageOps.grayscale(image)
        image = ImageOps.autocontrast(image)
        text = pytesseract.image_to_string(image, lang=language, config=tesseract_config)
        cleaned = _clean_text(text)
        if cleaned:
            results.append(PageText(page=page_number, text=cleaned, method="ocr-tesseract"))
    return results


def _vision_ocr_pages(
    path: Path,
    page_numbers: Iterable[int],
    *,
    render_dpi: int,
    vision_ocr: VisionOcrCallback | None,
) -> list[PageText]:
    if vision_ocr is None:
        raise OCRUnavailableError("OpenAI-OCR ist ausgewählt, aber kein Vision-OCR-Callback wurde übergeben.")

    results: list[PageText] = []
    for page_number in page_numbers:
        image_bytes = _render_pdf_page_png(path, page_number, render_dpi)
        text = vision_ocr(image_bytes, page_number)
        cleaned = _clean_text(text)
        if cleaned:
            results.append(PageText(page=page_number, text=cleaned, method="ocr-openai"))
    return results


def _render_pdf_page_png(path: Path, page_number: int, render_dpi: int) -> bytes:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - depends on optional OCR packages
        raise OCRUnavailableError("PyMuPDF fehlt. Installiere backend/requirements.txt erneut.") from exc

    zoom = max(render_dpi, 72) / 72
    with fitz.open(str(path)) as document:
        if page_number < 1 or page_number > document.page_count:
            raise OCRUnavailableError(f"PDF-Seite {page_number} existiert nicht.")
        page = document.load_page(page_number - 1)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pixmap.tobytes("png")


def _clean_text(text: str) -> str:
    lines = [line.rstrip() for line in (text or "").splitlines()]
    # Collapse too many blank lines, but keep line breaks because MCQ parsing benefits
    # from visible option boundaries.
    cleaned_lines: list[str] = []
    blank_count = 0
    for line in lines:
        if line.strip():
            cleaned_lines.append(line)
            blank_count = 0
        else:
            blank_count += 1
            if blank_count <= 1:
                cleaned_lines.append("")
    return "\n".join(cleaned_lines).strip()

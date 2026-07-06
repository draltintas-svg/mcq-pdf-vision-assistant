import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Document, Question
from app.services.openai_service import OpenAIService
from app.services.pdf_parser import PdfExtractionResult, extract_pdf_text

settings = get_settings()


def ingest_pdf(db: Session, document: Document, pdf_path: Path) -> Document:
    """Extract questions from a PDF and store them with embeddings."""
    service = OpenAIService()
    extraction = extract_pdf_text(
        pdf_path,
        enable_ocr=settings.pdf_ocr_enabled,
        ocr_provider=settings.pdf_ocr_provider,
        min_text_chars_per_page=settings.pdf_ocr_min_chars_per_page,
        max_ocr_pages=settings.pdf_ocr_max_pages,
        ocr_language=settings.pdf_ocr_language,
        render_dpi=settings.pdf_render_dpi,
        tesseract_config=settings.tesseract_config,
        vision_ocr=service.ocr_pdf_page_image_to_text if settings.pdf_ocr_provider.lower() == "openai" else None,
    )
    pages = extraction.pages

    if not pages:
        document.status = "failed"
        document.message = _extraction_failure_message(extraction)
        db.commit()
        db.refresh(document)
        return document

    answer_key = service.extract_answer_keys_from_pages(pages)
    extracted = service.extract_mcqs_from_pages(pages)

    search_texts: list[str] = []
    normalized_items: list[dict] = []
    for item in extracted:
        correct_answer = item["correct_answer"]
        number = item.get("question_number")
        if correct_answer == "UNKNOWN" and number and number in answer_key:
            correct_answer = answer_key[number]
        normalized = {
            **item,
            "correct_answer": correct_answer,
        }
        normalized_items.append(normalized)
        search_texts.append(service.build_search_text(normalized["question"], normalized["options"]))

    embeddings = service.embed_texts(search_texts)

    for item, embedding in zip(normalized_items, embeddings):
        question = Question(
            document_id=document.id,
            question_number=item.get("question_number"),
            source_page=item.get("page"),
            question_text=item["question"],
            options_json=json.dumps(item.get("options", {}), ensure_ascii=False),
            correct_answer=item.get("correct_answer", "UNKNOWN"),
            explanation=item.get("explanation"),
            raw_text=item.get("raw"),
            embedding_json=json.dumps(embedding),
        )
        db.add(question)

    document.status = "ready"
    document.question_count = len(normalized_items)
    document.message = _build_import_message(extraction, normalized_items)
    db.commit()
    db.refresh(document)
    return document


def _extraction_failure_message(extraction: PdfExtractionResult) -> str:
    warning_text = " ".join(extraction.warnings).strip()
    if extraction.ocr_pages_attempted > 0:
        return (
            "Aus der PDF konnte auch mit OCR kein verwertbarer Text extrahiert werden. "
            f"Seiten: {extraction.page_count}, OCR-Versuche: {extraction.ocr_pages_attempted}. "
            f"{warning_text}"
        ).strip()
    if not settings.pdf_ocr_enabled or settings.pdf_ocr_provider.lower() in {"off", "none", "false", ""}:
        return (
            "Aus der PDF konnte kein Text extrahiert werden. Vermutlich ist es ein Scan. "
            "Aktiviere PDF_OCR_ENABLED=true und PDF_OCR_PROVIDER=tesseract oder openai."
        )
    return (
        "Aus der PDF konnte kein Text extrahiert werden. Prüfe, ob die Datei lesbar ist. "
        f"{warning_text}"
    ).strip()


def _build_import_message(extraction: PdfExtractionResult, normalized_items: list[dict]) -> str:
    base_parts = []
    if len(normalized_items) == 0:
        base_parts.append(
            "PDF-Text wurde gelesen, aber es wurden keine Multiple-Choice-Fragen erkannt. "
            "Prüfe das Layout oder importiere Fragen manuell."
        )
    else:
        unknown = sum(1 for item in normalized_items if item.get("correct_answer") == "UNKNOWN")
        base_parts.append(
            f"{len(normalized_items)} Fragen importiert. "
            f"{unknown} Antworten sind UNKNOWN und sollten manuell kontrolliert werden."
        )

    if extraction.used_ocr:
        provider = extraction.ocr_provider or settings.pdf_ocr_provider
        base_parts.append(
            f"OCR aktiv: {extraction.ocr_pages_with_text}/{extraction.ocr_pages_attempted} Seiten mit {provider} erkannt."
        )
    else:
        base_parts.append(f"Digitaler PDF-Text: {extraction.digital_pages_with_text}/{extraction.page_count} Seiten erkannt.")

    if extraction.warnings:
        base_parts.append("Hinweise: " + " ".join(extraction.warnings))

    return " ".join(part.strip() for part in base_parts if part).strip()

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db, init_db
from app.models import Document, Question
from app.schemas import AnswerOut, CandidateOut, QuestionOut
from app.services.ingestion import ingest_pdf
from app.services.json_import import ingest_json_question_bank
from app.services.local_ocr import ocr_image_to_text
from app.services.local_search import find_best_matches_text
from app.services.openai_service import OpenAIService
from app.services.seed_data import seed_bundled_question_bank
from app.services.search import find_best_matches

settings = get_settings()

app = FastAPI(
    title="MCQ PDF Vision Assistant",
    version="0.2.0",
    description="PDF-/JSON-Fragenbank mit lokaler OCR-Suche und optionalem OpenAI-Web-Fallback.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.app_origin, "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    seed_bundled_question_bank()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/upload-pdf")
def upload_pdf(
    file: Annotated[UploadFile, File(description="PDF with MCQ questions")],
    db: Session = Depends(get_db),
) -> dict:
    """Original PDF import. This still uses OpenAI for semantic extraction."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Bitte eine PDF-Datei hochladen.")

    safe_name = f"{uuid.uuid4().hex}_{Path(file.filename).name}"
    destination = settings.upload_dir / safe_name
    with destination.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    document = Document(filename=file.filename, storage_path=str(destination), status="processing")
    db.add(document)
    db.commit()
    db.refresh(document)

    try:
        document = ingest_pdf(db, document, destination)
    except RuntimeError as exc:
        document.status = "failed"
        document.message = str(exc)
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # keep document status inspectable in the UI
        document.status = "failed"
        document.message = f"Import fehlgeschlagen: {exc}"
        db.commit()
        raise HTTPException(status_code=500, detail=document.message) from exc

    return _document_response(document)


@app.post("/api/upload-json")
async def upload_json(
    file: Annotated[UploadFile, File(description="Structured JSON / JSONL question bank")],
    db: Session = Depends(get_db),
) -> dict:
    """Import a structured question bank without OpenAI/API costs."""
    if not file.filename or not file.filename.lower().endswith((".json", ".jsonl")):
        raise HTTPException(status_code=400, detail="Bitte eine JSON- oder JSONL-Datei hochladen.")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Die Datei ist leer.")
    safe_name = f"{uuid.uuid4().hex}_{Path(file.filename).name}"
    destination = settings.upload_dir / safe_name
    destination.write_bytes(raw)

    document = Document(filename=file.filename, storage_path=str(destination), status="processing")
    db.add(document)
    db.commit()
    db.refresh(document)
    try:
        document = ingest_json_question_bank(db, document, raw, file.filename)
    except Exception as exc:
        document.status = "failed"
        document.message = f"JSON-Import fehlgeschlagen: {exc}"
        db.commit()
        raise HTTPException(status_code=400, detail=document.message) from exc
    return _document_response(document)


@app.get("/api/documents")
def list_documents(db: Session = Depends(get_db)) -> list[dict]:
    docs = db.query(Document).order_by(Document.created_at.desc()).all()
    return [
        {
            "id": doc.id,
            "filename": doc.filename,
            "status": doc.status,
            "message": doc.message,
            "question_count": doc.question_count,
            "created_at": doc.created_at.isoformat(),
        }
        for doc in docs
    ]


@app.get("/api/questions", response_model=list[QuestionOut])
def list_questions(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[QuestionOut]:
    rows = (
        db.query(Question)
        .order_by(Question.created_at.desc())
        .offset(max(offset, 0))
        .limit(min(max(limit, 1), 200))
        .all()
    )
    return [_question_out(row) for row in rows]


class QuestionUpdate(BaseModel):
    correct_answer: str | None = None
    explanation: str | None = None


@app.put("/api/questions/{question_id}", response_model=QuestionOut)
def update_question(
    question_id: int,
    payload: QuestionUpdate,
    db: Session = Depends(get_db),
) -> QuestionOut:
    row = db.get(Question, question_id)
    if not row:
        raise HTTPException(status_code=404, detail="Frage nicht gefunden.")
    if payload.correct_answer is not None:
        row.correct_answer = payload.correct_answer.strip().upper() or "UNKNOWN"
    if payload.explanation is not None:
        row.explanation = payload.explanation.strip() or None
    db.commit()
    db.refresh(row)
    return _question_out(row)


@app.post("/api/answer", response_model=AnswerOut)
async def answer_question(
    text: Annotated[str | None, Form()] = None,
    allow_web_fallback: Annotated[bool, Form()] = False,
    image: Annotated[UploadFile | None, File()] = None,
    db: Session = Depends(get_db),
) -> AnswerOut:
    """Answer from the local DB first. Does not require OpenAI for local text/image matching."""
    extracted_question = (text or "").strip()
    extracted_options: dict[str, str] = {}

    if image is not None:
        if image.content_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise HTTPException(status_code=400, detail="Bitte PNG, JPG/JPEG oder WEBP hochladen.")
        image_bytes = await image.read()
        if not image_bytes:
            raise HTTPException(status_code=400, detail="Das Bild ist leer.")
        local_text = ""
        try:
            local_text = ocr_image_to_text(image_bytes)
        except Exception:
            local_text = ""
        if local_text:
            extracted_question = f"{local_text}\n\nZusatz/Stichworte: {extracted_question}".strip() if extracted_question else local_text
        else:
            # Optional fallback to OpenAI vision only if configured and available.
            service = _try_openai_service()
            if service:
                try:
                    image_payload = service.extract_question_from_image(image_bytes, image.content_type or "image/jpeg")
                    image_question = str(image_payload.get("question") or "").strip()
                    image_options = image_payload.get("options") if isinstance(image_payload.get("options"), dict) else {}
                    extracted_options = {str(k).upper(): str(v) for k, v in image_options.items()}
                    extracted_question = f"{image_question}\n\nZusatz/Stichworte: {extracted_question}".strip() if extracted_question else image_question
                except Exception:
                    pass

    if not extracted_question:
        raise HTTPException(status_code=400, detail="Bitte Fragetext eingeben oder ein Bild hochladen.")

    # Local, free search. Works for JSON-imported data and PDF-imported rows.
    query_text = _build_search_text(extracted_question, extracted_options)
    matches = find_best_matches_text(db, query_text, limit=5)
    candidates = [_candidate_out(match.question, match.score) for match in matches]
    best = matches[0] if matches else None
    local_threshold = 0.30

    if best and best.score >= local_threshold:
        candidate = _candidate_out(best.question, best.score)
        if best.question.correct_answer != "UNKNOWN":
            return AnswerOut(
                source="database_local",
                confidence=round(best.score, 4),
                extracted_question=extracted_question,
                answer=_format_answer(best.question),
                explanation=best.question.explanation,
                matched_question=candidate,
                candidates=candidates,
            )
        return AnswerOut(
            source="database_incomplete",
            confidence=round(best.score, 4),
            extracted_question=extracted_question,
            answer="Die Frage wurde lokal gefunden, aber die korrekte Antwort ist dort UNKNOWN.",
            explanation=best.question.explanation,
            matched_question=candidate,
            candidates=candidates,
            warning="Ergänze die Antwort in der Fragenliste oder importiere eine vollständig strukturierte JSON-Datei.",
        )

    # Optional old embedding search if existing PDF rows contain embeddings and OpenAI is available.
    service = _try_openai_service()
    if service:
        try:
            query_embedding = service.embed_texts([query_text])[0]
            embedding_matches = find_best_matches(db, query_embedding, limit=5)
            if embedding_matches:
                emb_candidates = [_candidate_out(match.question, match.score) for match in embedding_matches]
                emb_best = embedding_matches[0]
                if emb_best.score >= settings.match_threshold and emb_best.question.correct_answer != "UNKNOWN":
                    return AnswerOut(
                        source="database_embedding",
                        confidence=round(emb_best.score, 4),
                        extracted_question=extracted_question,
                        answer=_format_answer(emb_best.question),
                        explanation=emb_best.question.explanation,
                        matched_question=_candidate_out(emb_best.question, emb_best.score),
                        candidates=emb_candidates,
                    )
        except Exception:
            pass

    if allow_web_fallback and service:
        try:
            web_answer = service.answer_with_web(extracted_question, extracted_options)
            return AnswerOut(
                source="web",
                confidence=round(best.score, 4) if best else None,
                extracted_question=extracted_question,
                answer="Externe Recherche genutzt.",
                web_answer=web_answer,
                matched_question=_candidate_out(best.question, best.score) if best else None,
                candidates=candidates,
                warning="Kein sicherer Treffer in der lokalen Datenbank. Antwort stammt aus externer Recherche.",
            )
        except Exception as exc:
            return AnswerOut(
                source="no_match",
                confidence=round(best.score, 4) if best else None,
                extracted_question=extracted_question,
                answer="Keine sichere lokale Lösung gefunden.",
                matched_question=_candidate_out(best.question, best.score) if best else None,
                candidates=candidates,
                warning=f"Web-Fallback nicht verfügbar: {exc}",
            )

    return AnswerOut(
        source="no_match",
        confidence=round(best.score, 4) if best else None,
        extracted_question=extracted_question,
        answer="Keine sichere lokale Lösung gefunden.",
        matched_question=_candidate_out(best.question, best.score) if best else None,
        candidates=candidates,
        warning="Lokale Suche hat keinen sicheren Treffer gefunden. Prüfe die Kandidaten oder gib mehr Fragetext ein.",
    )


def _try_openai_service() -> OpenAIService | None:
    try:
        return OpenAIService()
    except Exception:
        return None


def _document_response(document: Document) -> dict:
    return {
        "document_id": document.id,
        "filename": document.filename,
        "status": document.status,
        "message": document.message,
        "question_count": document.question_count,
    }


def _build_search_text(question_text: str, options: dict[str, str] | None = None) -> str:
    option_text = "\n".join(f"{letter}: {value}" for letter, value in sorted((options or {}).items()))
    return f"{question_text}\n{option_text}".strip()


def _options(row: Question) -> dict[str, str]:
    try:
        parsed = json.loads(row.options_json or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _question_out(row: Question) -> QuestionOut:
    return QuestionOut(
        id=row.id,
        document_id=row.document_id,
        question_number=row.question_number,
        source_page=row.source_page,
        question_text=row.question_text,
        options=_options(row),
        correct_answer=row.correct_answer,
        explanation=row.explanation,
    )


def _candidate_out(row: Question, score: float) -> CandidateOut:
    return CandidateOut(
        id=row.id,
        score=round(score, 4),
        source_page=row.source_page,
        question_text=row.question_text,
        options=_options(row),
        correct_answer=row.correct_answer,
        explanation=row.explanation,
    )


def _format_answer(row: Question) -> str:
    options = _options(row)
    keys = [key.strip().upper() for key in row.correct_answer.split(",") if key.strip()]
    details = []
    for key in keys:
        option = options.get(key) or options.get(key.lower())
        if option:
            details.append(f"{key}: {option}")
        else:
            details.append(key)
    if details:
        return "Richtige Antwort: " + "; ".join(details)
    return f"Richtige Antwort: {row.correct_answer}"


def _mount_frontend_if_available() -> None:
    if not settings.serve_frontend:
        return
    index_file = settings.static_dir / "index.html"
    assets_dir = settings.static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")
    manifest_file = settings.static_dir / "manifest.webmanifest"
    if manifest_file.exists():

        @app.get("/manifest.webmanifest", include_in_schema=False)
        def serve_manifest() -> FileResponse:
            return FileResponse(manifest_file, media_type="application/manifest+json")

    if index_file.exists():

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_spa(full_path: str) -> FileResponse:  # noqa: ARG001 - SPA fallback uses one index file
            return FileResponse(index_file)


_mount_frontend_if_available()

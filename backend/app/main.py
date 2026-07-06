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
from app.services.openai_service import OpenAIService
from app.services.search import find_best_matches

settings = get_settings()

app = FastAPI(
    title="MCQ PDF Vision Assistant",
    version="0.1.0",
    description="PDF-Fragenbank mit Foto-/Textsuche und optionalem Web-Fallback.",
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/upload-pdf")
def upload_pdf(
    file: Annotated[UploadFile, File(description="PDF with MCQ questions")],
    db: Session = Depends(get_db),
) -> dict:
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

    return {
        "document_id": document.id,
        "filename": document.filename,
        "status": document.status,
        "message": document.message,
        "question_count": document.question_count,
    }


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
    allow_web_fallback: Annotated[bool, Form()] = True,
    image: Annotated[UploadFile | None, File()] = None,
    db: Session = Depends(get_db),
) -> AnswerOut:
    service = _service_or_400()

    extracted_question = (text or "").strip()
    extracted_options: dict[str, str] = {}

    if image is not None:
        if image.content_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise HTTPException(status_code=400, detail="Bitte PNG, JPG/JPEG oder WEBP hochladen.")
        image_bytes = await image.read()
        if not image_bytes:
            raise HTTPException(status_code=400, detail="Das Bild ist leer.")
        image_payload = service.extract_question_from_image(image_bytes, image.content_type or "image/jpeg")
        image_question = str(image_payload.get("question") or "").strip()
        image_options = image_payload.get("options") if isinstance(image_payload.get("options"), dict) else {}
        extracted_options = {str(k).upper(): str(v) for k, v in image_options.items()}
        if extracted_question:
            extracted_question = f"{image_question}\n\nZusatz/Stichworte: {extracted_question}".strip()
        else:
            extracted_question = image_question

    if not extracted_question:
        raise HTTPException(status_code=400, detail="Bitte Fragetext eingeben oder ein Bild hochladen.")

    query_text = service.build_search_text(extracted_question, extracted_options)
    query_embedding = service.embed_texts([query_text])[0]
    matches = find_best_matches(db, query_embedding, limit=5)
    candidates = [_candidate_out(match.question, match.score) for match in matches]
    best = matches[0] if matches else None

    if best and best.score >= settings.match_threshold:
        candidate = _candidate_out(best.question, best.score)
        if best.question.correct_answer != "UNKNOWN":
            return AnswerOut(
                source="database",
                confidence=round(best.score, 4),
                extracted_question=extracted_question,
                answer=_format_answer(best.question),
                explanation=best.question.explanation,
                matched_question=candidate,
                candidates=candidates,
            )

        if not allow_web_fallback:
            return AnswerOut(
                source="database_incomplete",
                confidence=round(best.score, 4),
                extracted_question=extracted_question,
                answer="Die Frage wurde in der lokalen Datenbank gefunden, aber die korrekte Antwort ist dort UNKNOWN.",
                explanation=best.question.explanation,
                matched_question=candidate,
                candidates=candidates,
                warning="Ergänze die Antwort in der Fragenliste oder aktiviere den Web-Fallback.",
            )

    if allow_web_fallback:
        web_answer = service.answer_with_web(extracted_question, extracted_options)
        warning = None
        if best and best.score >= settings.near_match_threshold:
            warning = (
                "Es gab einen ähnlichen lokalen Treffer, aber keine sichere lokale Antwort. "
                "Die untenstehende Antwort stammt aus externer Recherche."
            )
        else:
            warning = "Kein sicherer Treffer in der lokalen PDF-Datenbank. Antwort stammt aus externer Recherche."
        return AnswerOut(
            source="web",
            confidence=round(best.score, 4) if best else None,
            extracted_question=extracted_question,
            answer="Externe Recherche genutzt.",
            web_answer=web_answer,
            matched_question=_candidate_out(best.question, best.score) if best else None,
            candidates=candidates,
            warning=warning,
        )

    return AnswerOut(
        source="no_match",
        confidence=round(best.score, 4) if best else None,
        extracted_question=extracted_question,
        answer="Keine sichere Lösung gefunden.",
        matched_question=_candidate_out(best.question, best.score) if best else None,
        candidates=candidates,
        warning="Aktiviere den Web-Fallback oder ergänze die Frage in deiner Datenbank.",
    )


def _service_or_400() -> OpenAIService:
    try:
        return OpenAIService()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    letters = [letter.strip().upper() for letter in row.correct_answer.split(",") if letter.strip()]
    details = []
    for letter in letters:
        option = options.get(letter)
        if option:
            details.append(f"{letter}: {option}")
        else:
            details.append(letter)
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
    if index_file.exists():

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_spa(full_path: str) -> FileResponse:  # noqa: ARG001 - SPA fallback uses one index file
            return FileResponse(index_file)


_mount_frontend_if_available()

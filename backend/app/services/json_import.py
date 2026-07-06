from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import Document, Question


def _load_payload(raw: bytes, filename: str) -> dict[str, Any]:
    text = raw.decode("utf-8-sig")
    if filename.lower().endswith(".jsonl"):
        items = [json.loads(line) for line in text.splitlines() if line.strip()]
        return {"items": items, "source": {"filename": filename}}
    payload = json.loads(text)
    if isinstance(payload, list):
        return {"items": payload, "source": {"filename": filename}}
    if not isinstance(payload, dict):
        raise ValueError("JSON muss ein Objekt mit items oder eine Liste sein.")
    return payload


def ingest_json_question_bank(db: Session, document: Document, raw: bytes, filename: str) -> Document:
    payload = _load_payload(raw, filename)
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Keine items in der JSON-Fragenbank gefunden.")

    count = 0
    skipped = 0
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        question_text = str(item.get("question") or item.get("question_text") or "").strip()
        if not question_text:
            skipped += 1
            continue
        options = item.get("options") if isinstance(item.get("options"), dict) else {}
        correct_answer = str(item.get("correct_answer") or "").strip()
        if not correct_answer:
            answers = item.get("correct_answers") if isinstance(item.get("correct_answers"), list) else []
            correct_answer = ",".join(str(x).strip() for x in answers if str(x).strip())
        correct_answer = correct_answer or "UNKNOWN"
        source_page = item.get("source_page")
        try:
            source_page = int(source_page) if source_page is not None else None
        except Exception:
            source_page = None
        explanation = item.get("explanation") or item.get("answer_text") or None
        raw_text = json.dumps(item, ensure_ascii=False)
        row = Question(
            document_id=document.id,
            question_number=str(item.get("question_number") or item.get("id") or "")[:64] or None,
            source_page=source_page,
            question_text=question_text,
            options_json=json.dumps(options, ensure_ascii=False),
            correct_answer=correct_answer,
            explanation=str(explanation).strip() if explanation else None,
            raw_text=raw_text,
            embedding_json=None,
        )
        db.add(row)
        count += 1

    document.status = "ready"
    document.question_count = count
    document.message = f"{count} strukturierte Fragen importiert. {skipped} Einträge übersprungen. Lokale Suche aktiv, OpenAI nicht nötig."
    db.commit()
    db.refresh(document)
    return document

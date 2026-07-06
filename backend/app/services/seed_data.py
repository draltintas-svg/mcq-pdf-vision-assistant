from __future__ import annotations

from pathlib import Path

from app.database import SessionLocal
from app.models import Document, Question
from app.services.json_import import ingest_json_question_bank

SEED_FILENAME = "biology_question_bank_import.json"
SEED_PATH = Path(__file__).resolve().parents[1] / "seed" / SEED_FILENAME


def seed_bundled_question_bank() -> None:
    """Auto-import the bundled Biology question bank once.

    This makes the iPhone fast-scan mode usable directly after deployment without
    OpenAI billing and without manually uploading the JSON file again.
    """
    if not SEED_PATH.exists():
        return

    with SessionLocal() as db:
        if db.query(Question).count() > 0:
            return
        raw = SEED_PATH.read_bytes()
        document = Document(
            filename="Biology Entrance Exam Question Bank (bundled)",
            storage_path=str(SEED_PATH),
            status="processing",
            message="Bundled seed import started.",
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        ingest_json_question_bank(db, document, raw, SEED_FILENAME)

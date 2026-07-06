from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import Question
from app.services.search import question_search_text

STOPWORDS = {
    "the", "and", "or", "of", "in", "a", "an", "to", "is", "are", "for", "with", "by",
    "which", "what", "this", "that", "from", "into", "as", "on", "be", "has", "have",
    "der", "die", "das", "und", "oder", "ist", "sind", "ein", "eine", "von", "mit", "zu",
}

@dataclass
class TextSearchResult:
    question: Question
    score: float


def normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("→", " ").replace("–", "-")
    text = re.sub(r"[^a-z0-9äöüßα-ωа-я]+", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str) -> list[str]:
    normalized = normalize_text(text)
    return [tok for tok in normalized.split() if len(tok) > 1 and tok not in STOPWORDS]


def token_f1(query: str, candidate: str) -> float:
    q_tokens = set(tokenize(query))
    c_tokens = set(tokenize(candidate))
    if not q_tokens or not c_tokens:
        return 0.0
    overlap = len(q_tokens & c_tokens)
    if overlap == 0:
        return 0.0
    precision = overlap / len(c_tokens)
    recall = overlap / len(q_tokens)
    return 2 * precision * recall / (precision + recall)


def sequence_score(query: str, candidate: str) -> float:
    q = normalize_text(query)
    c = normalize_text(candidate)
    if not q or not c:
        return 0.0
    # limit runtime for long item text
    return SequenceMatcher(None, q[:4000], c[:4000]).ratio()


def local_score(query: str, candidate: str) -> float:
    q = normalize_text(query)
    c = normalize_text(candidate)
    if not q or not c:
        return 0.0
    tf1 = token_f1(q, c)
    seq = sequence_score(q, c)
    # Exact phrase containment should be rewarded strongly for photographed or copied questions.
    contains = 1.0 if len(q) >= 20 and (q in c or c in q) else 0.0
    return max(contains, 0.65 * tf1 + 0.35 * seq)


def find_best_matches_text(db: Session, query_text: str, limit: int = 5) -> list[TextSearchResult]:
    rows = db.query(Question).all()
    results: list[TextSearchResult] = []
    for row in rows:
        candidate_text = question_search_text(row)
        score = local_score(query_text, candidate_text)
        if score > 0:
            results.append(TextSearchResult(question=row, score=score))
    results.sort(key=lambda item: item.score, reverse=True)
    return results[:limit]

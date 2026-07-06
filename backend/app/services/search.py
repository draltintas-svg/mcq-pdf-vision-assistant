import json
import math
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Question


@dataclass
class SearchResult:
    question: Question
    score: float


def question_search_text(question: Question) -> str:
    try:
        options = json.loads(question.options_json or "{}")
    except json.JSONDecodeError:
        options = {}
    option_text = "\n".join(f"{letter}: {value}" for letter, value in sorted(options.items()))
    return f"{question.question_text}\n{option_text}".strip()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def find_best_matches(db: Session, query_embedding: list[float], limit: int = 5) -> list[SearchResult]:
    questions = db.query(Question).filter(Question.embedding_json.isnot(None)).all()
    results: list[SearchResult] = []
    for question in questions:
        try:
            embedding = json.loads(question.embedding_json or "[]")
        except json.JSONDecodeError:
            continue
        score = cosine_similarity(query_embedding, embedding)
        results.append(SearchResult(question=question, score=score))
    results.sort(key=lambda item: item.score, reverse=True)
    return results[:limit]

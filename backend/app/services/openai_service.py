from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from typing import Any

from openai import OpenAI

from app.config import get_settings
from app.services.json_utils import loads_json_lenient
from app.services.pdf_parser import PageText

settings = get_settings()


class OpenAIService:
    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is missing. Copy .env.example to .env and set your key.")
        self.client = OpenAI(api_key=settings.openai_api_key)

    @staticmethod
    def _output_text(response: Any) -> str:
        """Return model output text across OpenAI SDK versions."""
        output_text = getattr(response, "output_text", None)
        if output_text:
            return output_text

        chunks: list[str] = []
        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", None)
                if text:
                    chunks.append(text)
        return "\n".join(chunks).strip()

    @staticmethod
    def _page_header(page: PageText) -> str:
        method = getattr(page, "method", "digital")
        return f"--- PAGE {page.page} [{method}] ---"

    @staticmethod
    def _chunk_pages(pages: list[PageText], max_chars: int = 18_000) -> Iterable[list[PageText]]:
        batch: list[PageText] = []
        current = 0
        for page in pages:
            page_len = len(page.text)
            if batch and current + page_len > max_chars:
                yield batch
                batch = []
                current = 0
            batch.append(page)
            current += page_len
        if batch:
            yield batch

    def extract_mcqs_from_pages(self, pages: list[PageText]) -> list[dict[str, Any]]:
        """Use the model to extract MCQs from PDF text.

        The extraction is conservative: if the correct answer is not clearly visible
        in the page text or solution key, it is stored as UNKNOWN and can be edited
        in the UI later.
        """
        all_questions: list[dict[str, Any]] = []
        for batch in self._chunk_pages(pages):
            source = "\n\n".join(
                self._page_header(page) + "\n" + page.text for page in batch
            )
            prompt = f"""
Extrahiere aus dem folgenden PDF-Text alle Multiple-Choice-Fragen.

Regeln:
- Gib ausschließlich valides JSON zurück.
- Jede Frage soll genau ein Objekt in questions sein.
- Erhalte medizinische Fachbegriffe exakt.
- Optionen sollen als A, B, C, D, E usw. gespeichert werden.
- correct_answer ist nur A/B/C/D/E/UNKNOWN. Nutze UNKNOWN, wenn die Lösung nicht sicher im Text steht.
- Falls mehrere richtige Antworten möglich sind, nutze z. B. "A,C".
- page ist die wahrscheinlichste Quellseite.
- explanation nur aus dem PDF ableiten; sonst leer lassen.

JSON-Format:
{{
  "questions": [
    {{
      "question_number": "1",
      "page": 12,
      "question": "Fragetext...",
      "options": {{"A": "...", "B": "..."}},
      "correct_answer": "UNKNOWN",
      "explanation": ""
    }}
  ]
}}

PDF-TEXT:
{source}
""".strip()
            response = self.client.responses.create(
                model=settings.openai_text_model,
                input=prompt,
            )
            raw = self._output_text(response)
            parsed = loads_json_lenient(raw)
            questions = parsed.get("questions", []) if isinstance(parsed, dict) else []
            for item in questions:
                normalized = self._normalize_mcq(item)
                if normalized:
                    all_questions.append(normalized)
        return all_questions

    def extract_answer_keys_from_pages(self, pages: list[PageText]) -> dict[str, str]:
        """Try to extract solution keys like '1C, 2A...' from the whole PDF."""
        answer_key: dict[str, str] = {}
        for batch in self._chunk_pages(pages, max_chars=25_000):
            source = "\n\n".join(
                self._page_header(page) + "\n" + page.text for page in batch
            )
            prompt = f"""
Suche im folgenden PDF-Text nach Lösungsschlüsseln für Multiple-Choice-Fragen.

Regeln:
- Gib ausschließlich valides JSON zurück.
- Wenn kein Lösungsschlüssel vorkommt, gib {{"answers": []}} zurück.
- question_number muss zur Frage passen, z. B. "1", "12", "3.4".
- correct_answer ist A/B/C/D/E oder bei Mehrfachlösung z. B. "A,C".

JSON-Format:
{{
  "answers": [
    {{"question_number": "1", "correct_answer": "C"}}
  ]
}}

PDF-TEXT:
{source}
""".strip()
            response = self.client.responses.create(
                model=settings.openai_text_model,
                input=prompt,
            )
            raw = self._output_text(response)
            try:
                parsed = loads_json_lenient(raw)
            except Exception:
                continue
            answers = parsed.get("answers", []) if isinstance(parsed, dict) else []
            for item in answers:
                number = str(item.get("question_number", "")).strip()
                answer = self._normalize_answer(str(item.get("correct_answer", "UNKNOWN")))
                if number and answer != "UNKNOWN":
                    answer_key[number] = answer
        return answer_key

    def ocr_pdf_page_image_to_text(self, image_bytes: bytes, page_number: int) -> str:
        """OCR a rendered PDF page image with the configured vision model."""
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:image/png;base64,{image_b64}"
        prompt = f"""
Führe OCR für diese PDF-Seite durch.

Regeln:
- Transkribiere den sichtbaren Text so vollständig und genau wie möglich.
- Keine Zusammenfassung, keine fachliche Interpretation.
- Behalte Frage-/Antwortoptionen, Nummerierung und Zeilenumbrüche möglichst bei.
- Gib ausschließlich valides JSON zurück.

JSON-Format:
{{
  "page": {page_number},
  "text": "erkannter Text"
}}
""".strip()
        response = self.client.responses.create(
            model=settings.openai_vision_model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": data_url},
                    ],
                }
            ],
        )
        raw = self._output_text(response)
        try:
            parsed = loads_json_lenient(raw)
        except Exception:
            return raw.strip()
        if isinstance(parsed, dict):
            return str(parsed.get("text") or parsed.get("raw_text") or "").strip()
        return raw.strip()

    def extract_question_from_image(self, image_bytes: bytes, mime_type: str) -> dict[str, Any]:
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{mime_type};base64,{image_b64}"
        prompt = """
Lies das Bild aus und extrahiere die sichtbare Multiple-Choice-Frage.

Gib ausschließlich valides JSON zurück:
{
  "question": "bereinigter Fragetext",
  "options": {"A": "...", "B": "..."},
  "raw_text": "vollständig erkannter Text"
}

Wenn keine Antwortoptionen sichtbar sind, lasse options leer, aber gib den erkannten Text in question/raw_text zurück.
""".strip()
        response = self.client.responses.create(
            model=settings.openai_vision_model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": data_url},
                    ],
                }
            ],
        )
        raw = self._output_text(response)
        parsed = loads_json_lenient(raw)
        if not isinstance(parsed, dict):
            return {"question": raw, "options": {}, "raw_text": raw}
        question = str(parsed.get("question") or parsed.get("raw_text") or "").strip()
        options = parsed.get("options") if isinstance(parsed.get("options"), dict) else {}
        raw_text = str(parsed.get("raw_text") or question).strip()
        return {"question": question, "options": options, "raw_text": raw_text}

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        batch_size = 64
        for start in range(0, len(texts), batch_size):
            batch = [text if text.strip() else "leer" for text in texts[start : start + batch_size]]
            response = self.client.embeddings.create(
                model=settings.openai_embedding_model,
                input=batch,
            )
            vectors.extend([item.embedding for item in response.data])
        return vectors

    def answer_with_web(self, question_text: str, options: dict[str, str] | None = None) -> str:
        options_text = "\n".join(
            f"{letter}: {value}" for letter, value in sorted((options or {}).items())
        )
        prompt = f"""
Du bist ein sorgfältiger medizinischer Lernassistent.
Die Frage wurde nicht sicher in der lokalen PDF-Fragenbank gefunden.
Recherchiere nur zuverlässige Quellen und beantworte die Multiple-Choice-Frage.

Ausgabe auf Deutsch:
- Wahrscheinlich richtige Option
- Kurze Begründung
- Hinweis, dass diese Antwort aus externer Recherche stammt und nicht aus der lokalen PDF-Datenbank
- Bei Unsicherheit klar sagen, dass keine sichere Lösung gefunden wurde

Frage:
{question_text}

Optionen:
{options_text}
""".strip()
        response = self.client.responses.create(
            model=settings.openai_web_model,
            tools=[{"type": "web_search"}],
            input=prompt,
        )
        return self._output_text(response)

    @staticmethod
    def build_search_text(question_text: str, options: dict[str, str] | None = None) -> str:
        option_text = "\n".join(
            f"{letter}: {value}" for letter, value in sorted((options or {}).items())
        )
        return f"{question_text}\n{option_text}".strip()

    @classmethod
    def _normalize_mcq(cls, item: dict[str, Any]) -> dict[str, Any] | None:
        question = str(item.get("question") or item.get("question_text") or "").strip()
        if len(question) < 12:
            return None
        raw_options = item.get("options") if isinstance(item.get("options"), dict) else {}
        options: dict[str, str] = {}
        for letter, text in raw_options.items():
            key = str(letter).strip().upper()[:1]
            value = str(text).strip()
            if key and value:
                options[key] = value
        page_raw = item.get("page") or item.get("source_page")
        try:
            page = int(page_raw) if page_raw is not None else None
        except (TypeError, ValueError):
            page = None
        return {
            "question_number": str(item.get("question_number") or "").strip() or None,
            "page": page,
            "question": question,
            "options": options,
            "correct_answer": cls._normalize_answer(str(item.get("correct_answer", "UNKNOWN"))),
            "explanation": str(item.get("explanation") or "").strip() or None,
            "raw": json.dumps(item, ensure_ascii=False),
        }

    @staticmethod
    def _normalize_answer(answer: str) -> str:
        cleaned = answer.strip().upper().replace(" ", "")
        if not cleaned or cleaned in {"?", "N/A", "NONE", "UNBEKANNT"}:
            return "UNKNOWN"
        allowed = set("ABCDE")
        parts = [part for part in cleaned.replace(";", ",").replace("/", ",").split(",") if part]
        if parts and all(len(part) == 1 and part in allowed for part in parts):
            return ",".join(parts)
        if len(cleaned) == 1 and cleaned in allowed:
            return cleaned
        return "UNKNOWN"

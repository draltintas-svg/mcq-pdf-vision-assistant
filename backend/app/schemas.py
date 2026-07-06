from pydantic import BaseModel, Field


class QuestionOut(BaseModel):
    id: int
    document_id: int
    question_number: str | None = None
    source_page: int | None = None
    question_text: str
    options: dict[str, str] = Field(default_factory=dict)
    correct_answer: str
    explanation: str | None = None


class CandidateOut(BaseModel):
    id: int
    score: float
    source_page: int | None = None
    question_text: str
    options: dict[str, str] = Field(default_factory=dict)
    correct_answer: str
    explanation: str | None = None


class AnswerOut(BaseModel):
    source: str
    confidence: float | None = None
    extracted_question: str | None = None
    answer: str
    explanation: str | None = None
    matched_question: CandidateOut | None = None
    candidates: list[CandidateOut] = Field(default_factory=list)
    web_answer: str | None = None
    warning: str | None = None

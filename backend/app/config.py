from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Runtime configuration loaded from .env or environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_text_model: str = Field(default="gpt-5.4-mini", alias="OPENAI_TEXT_MODEL")
    openai_vision_model: str = Field(default="gpt-5.4-mini", alias="OPENAI_VISION_MODEL")
    openai_web_model: str = Field(default="gpt-5.4-mini", alias="OPENAI_WEB_MODEL")
    openai_embedding_model: str = Field(default="text-embedding-3-small", alias="OPENAI_EMBEDDING_MODEL")

    match_threshold: float = Field(default=0.78, alias="MATCH_THRESHOLD")
    near_match_threshold: float = Field(default=0.68, alias="NEAR_MATCH_THRESHOLD")

    # OCR for scanned PDFs. Default: local Tesseract OCR inside Docker.
    pdf_ocr_enabled: bool = Field(default=True, alias="PDF_OCR_ENABLED")
    pdf_ocr_provider: str = Field(default="tesseract", alias="PDF_OCR_PROVIDER")  # tesseract | openai | off
    pdf_ocr_language: str = Field(default="deu+eng", alias="PDF_OCR_LANGUAGE")
    pdf_ocr_min_chars_per_page: int = Field(default=50, alias="PDF_OCR_MIN_CHARS_PER_PAGE")
    pdf_ocr_max_pages: int = Field(default=250, alias="PDF_OCR_MAX_PAGES")
    pdf_render_dpi: int = Field(default=220, alias="PDF_RENDER_DPI")
    tesseract_config: str = Field(default="--psm 3", alias="TESSERACT_CONFIG")

    app_origin: str = Field(default="http://localhost:5173", alias="APP_ORIGIN")
    sqlite_url: str = Field(default="sqlite:///./data/app.db", alias="SQLITE_URL")
    serve_frontend: bool = Field(default=False, alias="SERVE_FRONTEND")

    data_dir: Path = Field(default=BASE_DIR / "data", alias="DATA_DIR")
    upload_dir: Path = Field(default=BASE_DIR / "storage" / "uploads", alias="UPLOAD_DIR")
    static_dir: Path = Field(default=BASE_DIR / "static", alias="STATIC_DIR")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    return settings

"""Application configuration loaded from environment variables / .env file."""

from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]

# Default Tesseract locations on Windows (UB-Mannheim installer).
_WINDOWS_TESSERACT_CANDIDATES = [
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe")),
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "Electricity Bill OCR"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # Any SQLAlchemy URL: sqlite, postgresql, mysql+pymysql ...
    DATABASE_URL: str = f"sqlite:///{(BASE_DIR / 'electricity_bills.db').as_posix()}"

    # Uploads
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_SIZE_MB: float = 10

    # OCR
    TESSERACT_CMD: str | None = None
    OCR_LANGUAGE: str = "eng"
    OCR_PSM: int = 3
    OCR_OEM: int = 1
    # auto = try light + binarized preprocessing, keep the better result
    OCR_PREPROCESS_MODE: str = Field(default="auto", pattern="^(auto|light|binary|none)$")
    OCR_TIMEOUT_SECONDS: int = 120
    OCR_LOW_CONFIDENCE_THRESHOLD: float = 60.0

    # PDF
    PDF_RENDER_DPI: int = 300
    PDF_TEXT_MIN_CHARS: int = 50
    PDF_MAX_PAGES: int = 20

    # Validation
    METER_READING_TOLERANCE: float = 1.0

    # Optional JSON file with extra label aliases: {"due_date": ["Pay Till"], ...}
    LABEL_ALIASES_FILE: str | None = None

    @field_validator("DATABASE_URL")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        # Heroku-style URLs
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://"):]
        # Relative SQLite paths are resolved against the project folder, not the CWD
        prefix = "sqlite:///"
        if v.startswith(prefix) and v != prefix + ":memory:":
            path = Path(v[len(prefix):])
            if not path.is_absolute() and not v[len(prefix):].startswith("/"):
                v = prefix + (BASE_DIR / path).resolve().as_posix()
        return v

    @model_validator(mode="after")
    def _require_real_database_in_production(self) -> "Settings":
        # A missing DATABASE_URL on the host would silently fall back to SQLite on an
        # ephemeral disk and lose every bill on restart; refuse to start instead.
        if self.is_production and self.DATABASE_URL.startswith("sqlite"):
            raise ValueError(
                "ENVIRONMENT=production requires DATABASE_URL to point to PostgreSQL/MySQL, not SQLite."
            )
        return self

    @property
    def upload_path(self) -> Path:
        p = Path(self.UPLOAD_DIR)
        return p if p.is_absolute() else BASE_DIR / p

    @property
    def max_upload_bytes(self) -> int:
        return int(self.MAX_UPLOAD_SIZE_MB * 1024 * 1024)

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

    def resolve_tesseract_cmd(self) -> str | None:
        """Return the tesseract executable path, or None if it can't be found."""
        if self.TESSERACT_CMD:
            return self.TESSERACT_CMD
        found = shutil.which("tesseract")
        if found:
            return found
        for candidate in _WINDOWS_TESSERACT_CANDIDATES:
            if candidate.exists():
                return str(candidate)
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""Test setup: an isolated SQLite database and upload folder per test session.

Environment variables are set before the app is imported so the cached
settings and engine point at temporary locations, never the dev database.
"""

import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="bill_ocr_tests_"))
# TEST_DATABASE_URL runs the suite against a real server (e.g. an empty PostgreSQL
# test database); its tables are dropped afterwards, so never point it at real data.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["ENVIRONMENT"] = "test"
os.environ["UPLOAD_DIR"] = str(_TMP / "uploads")
os.environ["LABEL_ALIASES_FILE"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.exceptions import OCRUnavailableError  # noqa: E402
from app.db import models  # noqa: E402,F401
from app.db.database import Base, engine  # noqa: E402

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "sample_bills"


def tesseract_available() -> bool:
    from app.services.ocr_service import get_ocr_service

    try:
        get_ocr_service().check_available()
        return True
    except OCRUnavailableError:
        return False


requires_tesseract = pytest.mark.skipif(not tesseract_available(), reason="Tesseract OCR not installed")


@pytest.fixture(scope="session", autouse=True)
def _database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    engine.dispose()
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def settings():
    return get_settings()


@pytest.fixture()
def sample_dir() -> Path:
    return SAMPLE_DIR / "synthetic"

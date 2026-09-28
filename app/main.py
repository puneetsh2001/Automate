"""FastAPI application: API routes, error handling, health check and the static UI."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.bills import router as bills_router
from app.core.config import BASE_DIR, get_settings
from app.core.exceptions import BillOCRError, OCRUnavailableError
from app.core.logging_config import setup_logging
from app.db.database import get_db
from app.schemas.bill import HealthResponse
from app.services.ocr_service import get_ocr_service

__version__ = "1.0.0"

settings = get_settings()
setup_logging(settings.LOG_LEVEL)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.upload_path.mkdir(parents=True, exist_ok=True)
    try:
        get_ocr_service().check_available()
    except OCRUnavailableError as exc:
        # Start anyway so the UI/health endpoint can explain the problem
        logger.error("OCR unavailable: %s", exc.message)
    logger.info("%s started (env=%s)", settings.APP_NAME, settings.ENVIRONMENT)
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=__version__,
    description=(
        "Extracts structured data (consumer, account, period, due date, meter readings, "
        "units, amount) from Indian electricity bills in PDF/PNG/JPEG form using "
        "PDF text extraction and Tesseract OCR, validates it and stores it."
    ),
    lifespan=lifespan,
)


# ------------------------------------------------------------------ errors
@app.exception_handler(BillOCRError)
async def bill_ocr_error_handler(request: Request, exc: BillOCRError) -> JSONResponse:
    log = logger.error if exc.status_code >= 500 else logger.warning
    log("%s %s -> %d %s: %s", request.method, request.url.path, exc.status_code, exc.error_code, exc.message)
    return JSONResponse(status_code=exc.status_code, content={"error": exc.error_code, "message": exc.message})


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code,
                        content={"error": "http_error", "message": str(exc.detail)})


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path"))
        parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return JSONResponse(status_code=422, content={"error": "request_validation_error", "message": "; ".join(parts)})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Full traceback in the server log only; never sent to the client
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500,
                        content={"error": "internal_error", "message": "An unexpected error occurred."})


# ------------------------------------------------------------------ routes
app.include_router(bills_router)


@app.get("/health", response_model=HealthResponse, tags=["system"], summary="Service health")
def health(db: Session = Depends(get_db)) -> HealthResponse:
    try:
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        logger.warning("Health check: database unavailable", exc_info=True)
        db_status = "unavailable"
    ocr_detail = None
    try:
        get_ocr_service().check_available()
        ocr_status = "ok"
    except OCRUnavailableError as exc:
        ocr_status, ocr_detail = "unavailable", exc.message
    overall = "ok" if db_status == "ok" and ocr_status == "ok" else "degraded"
    return HealthResponse(status=overall, database=db_status, ocr=ocr_status,
                          ocr_detail=ocr_detail, version=__version__)


# Static UI last so it never shadows /api, /docs or /health
app.mount("/", StaticFiles(directory=BASE_DIR / "frontend", html=True), name="frontend")

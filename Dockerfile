# Production image: FastAPI + Tesseract OCR. Used by Render (render.yaml), works on any Docker host.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Tesseract OCR engine + English language data (add e.g. tesseract-ocr-hin for Hindi)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY frontend ./frontend

# Run as an unprivileged user; uploads/ is the only writable folder
RUN useradd --create-home --uid 1000 appuser && mkdir -p uploads && chown appuser:appuser uploads
USER appuser

# Free-tier hosts give a fraction of one CPU: multi-threaded Tesseract then spends its CPU
# quota on thread contention, so run it single-threaded and allow slow scanned pages time.
ENV ENVIRONMENT=production \
    PORT=8000 \
    OMP_THREAD_LIMIT=1 \
    OCR_TIMEOUT_SECONDS=300
EXPOSE 8000

# Apply database migrations, then serve. Render injects $PORT.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]

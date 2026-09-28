"""Upload validation and safe storage.

Never trust the client: extension, declared MIME type and actual file
signature (magic bytes) must all agree, and the stored filename is a
server-generated UUID.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.exceptions import (
    EmptyFileError,
    FileTooLargeError,
    InvalidUploadError,
    UnsupportedFileTypeError,
)

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS: dict[str, str] = {
    ".pdf": "pdf",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
}

ALLOWED_MIME_TYPES: dict[str, set[str]] = {
    "pdf": {"application/pdf", "application/x-pdf"},
    "png": {"image/png"},
    "jpeg": {"image/jpeg", "image/jpg", "image/pjpeg"},
}

# Browsers/tools sometimes send a generic type; we then rely on magic bytes.
GENERIC_MIME_TYPES = {"", "application/octet-stream", "binary/octet-stream"}


@dataclass(frozen=True)
class ValidatedUpload:
    file_type: str  # pdf | png | jpeg
    extension: str
    original_filename: str


def sniff_file_type(data: bytes) -> str | None:
    if data.startswith(b"%PDF-") or data[:1024].find(b"%PDF-") != -1:
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    return None


def sanitize_display_name(filename: str | None) -> str:
    """Keep only the basename for display/audit; it is never used as a path."""
    name = (filename or "upload").replace("\\", "/").split("/")[-1].strip()
    name = "".join(ch for ch in name if ch.isprintable())
    return name[:255] or "upload"


def validate_upload(
    filename: str | None, content_type: str | None, data: bytes, max_bytes: int
) -> ValidatedUpload:
    display_name = sanitize_display_name(filename)
    extension = Path(display_name).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"Unsupported file extension '{extension or '(none)'}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    if not data:
        raise EmptyFileError("The uploaded file is empty.")
    if len(data) > max_bytes:
        raise FileTooLargeError(
            f"File exceeds the maximum upload size of {max_bytes / (1024 * 1024):.0f} MB."
        )

    expected_type = ALLOWED_EXTENSIONS[extension]
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime not in GENERIC_MIME_TYPES and mime not in ALLOWED_MIME_TYPES[expected_type]:
        raise UnsupportedFileTypeError(
            f"MIME type '{mime}' does not match file extension '{extension}'."
        )

    actual_type = sniff_file_type(data)
    if actual_type is None:
        raise UnsupportedFileTypeError("File content is not a valid PDF, PNG or JPEG.")
    if actual_type != expected_type:
        raise InvalidUploadError(
            f"File content ({actual_type.upper()}) does not match extension '{extension}'."
        )

    return ValidatedUpload(file_type=expected_type, extension=extension, original_filename=display_name)


def generate_stored_filename(extension: str) -> str:
    return f"{uuid.uuid4().hex}{extension}"


def safe_upload_path(upload_dir: Path, stored_filename: str) -> Path:
    """Resolve a stored filename inside upload_dir, refusing path traversal."""
    base = upload_dir.resolve()
    target = (base / stored_filename).resolve()
    if target.parent != base:
        raise InvalidUploadError("Invalid stored filename.")
    return target


def save_upload(upload_dir: Path, stored_filename: str, data: bytes) -> Path:
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = safe_upload_path(upload_dir, stored_filename)
    path.write_bytes(data)
    return path


def delete_upload(upload_dir: Path, stored_filename: str) -> None:
    try:
        safe_upload_path(upload_dir, stored_filename).unlink(missing_ok=True)
    except Exception:  # pragma: no cover - best effort cleanup
        logger.warning("Could not delete stored upload %s", stored_filename, exc_info=True)

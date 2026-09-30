"""Page images of stored bills for the UI's document preview.

PDF pages are rendered with PyMuPDF (the same library that reads them), so
the preview works in every browser without a PDF plug-in. Photos are
rotated upright and downscaled, since phone pictures are often 12+ MP.
"""

from __future__ import annotations

import io
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.exceptions import DocumentProcessingError, PageNotFoundError

# Longest side of a preview image: sharp on a HiDPI screen at panel width, still light to send
PREVIEW_MAX_SIDE = 1600
_MAX_PDF_ZOOM = 2.0  # never render tiny pages bigger than 144 DPI

Image.MAX_IMAGE_PIXELS = 100_000_000  # decompression-bomb guard, as for OCR


def render_preview(path: Path, file_type: str, page: int) -> tuple[bytes, str]:
    """Return (image bytes, media type) for one 1-based page of a stored bill."""
    if file_type == "pdf":
        return _render_pdf_page(path, page)
    if page != 1:
        raise PageNotFoundError(f"Page {page} does not exist; an image bill has 1 page.")
    return _render_image(path)


def _render_pdf_page(path: Path, page: int) -> tuple[bytes, str]:
    try:
        pdf = fitz.open(path)
    except Exception as exc:
        raise DocumentProcessingError("The stored PDF cannot be opened for preview.") from exc
    with pdf:
        if page > pdf.page_count:
            raise PageNotFoundError(f"Page {page} does not exist; this bill has {pdf.page_count} page(s).")
        pdf_page = pdf[page - 1]
        zoom = min(PREVIEW_MAX_SIDE / max(pdf_page.rect.width, pdf_page.rect.height), _MAX_PDF_ZOOM)
        pixmap = pdf_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pixmap.tobytes("png"), "image/png"


def _render_image(path: Path) -> tuple[bytes, str]:
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE))
            if image.mode in ("RGBA", "LA", "P"):
                # Flatten transparency onto white, as the OCR pipeline does
                rgba = image.convert("RGBA")
                image = Image.alpha_composite(Image.new("RGBA", rgba.size, (255, 255, 255, 255)), rgba)
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, "JPEG", quality=85, optimize=True)
            return buffer.getvalue(), "image/jpeg"
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise DocumentProcessingError("The stored image cannot be opened for preview.") from exc

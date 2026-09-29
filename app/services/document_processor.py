"""Turn an uploaded PDF/image into text.

PDFs are handled page by page: pages that contain a usable text layer are
read directly (fast, exact); scanned pages are rendered to images and OCR'd.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass, field

import fitz  # PyMuPDF
from PIL import Image, UnidentifiedImageError

from app.core.config import Settings, get_settings
from app.core.exceptions import BillOCRError, DocumentProcessingError
from app.services.ocr_service import OCRService, get_ocr_service
from app.utils.text_layout import Word, words_to_text

logger = logging.getLogger(__name__)

Image.MAX_IMAGE_PIXELS = 100_000_000  # decompression-bomb guard (~10k x 10k)


@dataclass
class PageText:
    page_number: int
    text: str
    method: str  # "text" | "ocr"
    confidence: float | None = None


@dataclass
class DocumentText:
    pages: list[PageText] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def text(self) -> str:
        if len(self.pages) == 1:
            return self.pages[0].text
        return "\n\n".join(f"----- Page {p.page_number} -----\n{p.text}" for p in self.pages)

    @property
    def method(self) -> str:
        methods = {p.method for p in self.pages}
        return methods.pop() if len(methods) == 1 else "mixed"

    @property
    def ocr_confidence(self) -> float | None:
        confs = [p.confidence for p in self.pages if p.confidence is not None]
        return round(sum(confs) / len(confs), 2) if confs else None


# Same flags page.get_text("words") uses, so words and line directions come from one text page
_WORD_FLAGS = fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP


def _horizontal_words(page: fitz.Page) -> list[tuple]:
    """Words of the page's text layer, without diagonal/vertical text.

    Watermarks ("DUPLICATE", "SPECIMEN") and stamps are drawn at an angle
    across the bill; their words would otherwise land inside table rows
    ("DATA S3567281"). Pages whose text is mostly not horizontal are
    returned unfiltered.
    """
    textpage = page.get_textpage(flags=_WORD_FLAGS)
    words = textpage.extractWORDS()
    angled = set()
    for b, block in enumerate(textpage.extractDICT()["blocks"]):
        for ln, line in enumerate(block.get("lines", [])):
            dx, dy = line["dir"]
            if dx <= 0 or abs(dy) > 0.1:  # more than ~6 degrees off horizontal
                angled.add((b, ln))
    kept = [w for w in words if (w[5], w[6]) not in angled]
    return kept if len(kept) * 2 >= len(words) else words


def _is_useful_text(text: str, min_chars: int) -> bool:
    visible = [c for c in text if not c.isspace()]
    if len(visible) < min_chars:
        return False
    # Broken font encodings produce mostly symbols / replacement characters
    alnum_ratio = sum(c.isalnum() for c in visible) / len(visible)
    return alnum_ratio >= 0.5


class DocumentProcessor:
    def __init__(self, ocr: OCRService | None = None, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._ocr = ocr

    @property
    def ocr(self) -> OCRService:
        if self._ocr is None:
            self._ocr = get_ocr_service()
        return self._ocr

    def process(self, data: bytes, file_type: str) -> DocumentText:
        start = time.perf_counter()
        if file_type == "pdf":
            doc = self._process_pdf(data)
        else:
            doc = DocumentText(pages=[self._process_image(data)])
        doc.duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "Document processed: type=%s pages=%d method=%s duration=%dms",
            file_type, len(doc.pages), doc.method, doc.duration_ms,
        )
        return doc

    # ------------------------------------------------------------------ PDF
    def _process_pdf(self, data: bytes) -> DocumentText:
        try:
            pdf = fitz.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise DocumentProcessingError("The PDF file is corrupt or cannot be opened.") from exc
        with pdf:
            if pdf.needs_pass:
                raise DocumentProcessingError("Password-protected PDFs are not supported.")
            if pdf.page_count == 0:
                raise DocumentProcessingError("The PDF has no pages.")
            if pdf.page_count > self.settings.PDF_MAX_PAGES:
                raise DocumentProcessingError(
                    f"PDF has {pdf.page_count} pages; the maximum is {self.settings.PDF_MAX_PAGES}."
                )
            pages: list[PageText] = []
            for index, page in enumerate(pdf, start=1):
                try:
                    pages.append(self._process_pdf_page(page, index))
                except BillOCRError:
                    # OCR timeouts / Tesseract unavailable keep their own message and status
                    raise
                except Exception as exc:
                    logger.exception("Unexpected error reading PDF page %d", index)
                    raise DocumentProcessingError(f"Failed to read PDF page {index}.") from exc
            return DocumentText(pages=pages)

    def _process_pdf_page(self, page: fitz.Page, index: int) -> PageText:
        raw_words = _horizontal_words(page)  # (x0, y0, x1, y1, word, block, line, word_no)
        text_layer = " ".join(w[4] for w in raw_words)
        if _is_useful_text(text_layer, self.settings.PDF_TEXT_MIN_CHARS):
            words = [Word(w[4], w[0], w[1], w[2], w[3]) for w in raw_words]
            logger.info("PDF page %d: using embedded text layer (%d words)", index, len(words))
            return PageText(index, words_to_text(words), "text")

        logger.info("PDF page %d: no usable text layer, rendering at %d DPI for OCR",
                    index, self.settings.PDF_RENDER_DPI)
        pix = page.get_pixmap(dpi=self.settings.PDF_RENDER_DPI, colorspace=fitz.csGRAY)
        image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        result = self.ocr.extract(image)
        return PageText(index, result.text, "ocr", result.mean_confidence)

    # ---------------------------------------------------------------- Image
    def _process_image(self, data: bytes) -> PageText:
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise DocumentProcessingError("The image file is corrupt or cannot be read.") from exc
        result = self.ocr.extract(image)
        return PageText(1, result.text, "ocr", result.mean_confidence)

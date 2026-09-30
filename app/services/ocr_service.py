"""OCR abstraction.

`OCRProvider` is the extension point: add e.g. an AWS Textract provider by
implementing `recognize(image) -> OCRResult` and selecting it in
`get_ocr_service()`. The rest of the pipeline only depends on OCRResult.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np
import pytesseract
from PIL import Image

from app.core.config import Settings, get_settings
from app.core.exceptions import OCRProcessingError, OCRUnavailableError
from app.services.image_preprocessing import PIPELINES, binarize
from app.utils.text_layout import Word, words_to_text

logger = logging.getLogger(__name__)

TESSERACT_INSTALL_HELP = (
    "Tesseract OCR is not installed or not found. On Windows install it with "
    "'winget install UB-Mannheim.TesseractOCR' (or download from "
    "https://github.com/UB-Mannheim/tesseract/wiki), then either add "
    "'C:\\Program Files\\Tesseract-OCR' to PATH or set TESSERACT_CMD in .env."
)


@dataclass
class OCRResult:
    text: str
    words: list[Word] = field(default_factory=list)
    mean_confidence: float | None = None
    preprocessing: str = ""

    @property
    def score(self) -> float:
        """Quality score used to pick between preprocessing variants:
        total confidence of plausible words (rewards both coverage and certainty)."""
        return sum(w.conf for w in self.words if w.conf >= 30 and any(c.isalnum() for c in w.text))


class OCRProvider(ABC):
    name: str = "base"

    @abstractmethod
    def recognize(self, image: np.ndarray | Image.Image) -> OCRResult: ...

    def check_available(self) -> None:
        """Raise OCRUnavailableError if the engine can't be used."""


class TesseractProvider(OCRProvider):
    name = "tesseract"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._checked = False

    def check_available(self) -> None:
        if self._checked:
            return
        cmd = self.settings.resolve_tesseract_cmd()
        if not cmd:
            raise OCRUnavailableError(TESSERACT_INSTALL_HELP)
        pytesseract.pytesseract.tesseract_cmd = cmd
        try:
            version = pytesseract.get_tesseract_version()
        except Exception as exc:  # TesseractNotFoundError, OSError...
            raise OCRUnavailableError(f"{TESSERACT_INSTALL_HELP} (tried: {cmd})") from exc
        try:
            langs = set(pytesseract.get_languages(config=""))
        except Exception:
            langs = set()
        missing = [lang for lang in self.settings.OCR_LANGUAGE.split("+") if langs and lang not in langs]
        if missing:
            raise OCRUnavailableError(
                f"Tesseract language data missing for: {', '.join(missing)}. "
                f"Installed: {', '.join(sorted(langs))}. Re-run the Tesseract installer and "
                "select the additional languages, or change OCR_LANGUAGE."
            )
        logger.info("Tesseract %s available at %s", version, cmd)
        self._checked = True

    def recognize(self, image: np.ndarray | Image.Image) -> OCRResult:
        self.check_available()
        config = (
            f"--oem {self.settings.OCR_OEM} --psm {self.settings.OCR_PSM} "
            "-c preserve_interword_spaces=1"
        )
        try:
            data = pytesseract.image_to_data(
                image,
                lang=self.settings.OCR_LANGUAGE,
                config=config,
                output_type=pytesseract.Output.DICT,
                timeout=self.settings.OCR_TIMEOUT_SECONDS,
            )
        except RuntimeError as exc:  # pytesseract raises RuntimeError on timeout
            raise OCRProcessingError(f"OCR timed out after {self.settings.OCR_TIMEOUT_SECONDS}s") from exc
        except pytesseract.TesseractError as exc:
            raise OCRProcessingError(f"Tesseract failed: {exc.message}") from exc

        words: list[Word] = []
        for i, txt in enumerate(data["text"]):
            txt = (txt or "").strip()
            if not txt:
                continue
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            words.append(Word(txt, x, y, x + w, y + h, float(data["conf"][i])))
        confs = [w.conf for w in words if w.conf >= 0]
        return OCRResult(
            text=words_to_text(words),
            words=words,
            mean_confidence=round(sum(confs) / len(confs), 2) if confs else None,
        )


class OCRService:
    """Runs preprocessing + OCR on a page image and picks the best variant."""

    def __init__(self, provider: OCRProvider, settings: Settings):
        self.provider = provider
        self.settings = settings

    def check_available(self) -> None:
        self.provider.check_available()

    def extract(self, image: Image.Image) -> OCRResult:
        mode = self.settings.OCR_PREPROCESS_MODE
        variants = ["light", "binary"] if mode == "auto" else [mode]
        start = time.perf_counter()
        best: OCRResult | None = None
        light = None
        for variant in variants:
            elapsed = time.perf_counter() - start
            if best is not None and best.words and (best.mean_confidence or 0) >= \
                    self.settings.OCR_SECOND_PASS_BELOW_CONFIDENCE:
                logger.info("Skipping OCR variant=%s: first pass is confident (mean_conf=%s)",
                            variant, best.mean_confidence)
                break
            if best is not None and best.words and elapsed > self.settings.OCR_TIMEOUT_SECONDS / 2:
                # Slow host (e.g. a throttled free-tier CPU): keep the usable first result
                logger.info("Skipping OCR variant=%s: first pass already took %.1fs", variant, elapsed)
                break
            pass_start = time.perf_counter()
            if variant == "binary" and light is not None:
                processed = binarize(light)  # reuse the light pipeline output instead of redoing it
            else:
                processed = PIPELINES[variant](image)
                if variant == "light":
                    light = processed
            result = self.provider.recognize(processed)
            result.preprocessing = variant
            logger.info("OCR pass variant=%s took %.1fs (words=%d, mean_conf=%s, score=%.0f)",
                        variant, time.perf_counter() - pass_start, len(result.words),
                        result.mean_confidence, result.score)
            if best is None or result.score > best.score:
                best = result
        assert best is not None
        logger.info(
            "OCR page done in %.2fs (variant=%s, words=%d, mean_conf=%s)",
            time.perf_counter() - start, best.preprocessing, len(best.words), best.mean_confidence,
        )
        return best

    def extract_text(self, image: Image.Image) -> str:
        return self.extract(image).text


_service: OCRService | None = None


def get_ocr_service() -> OCRService:
    global _service
    if _service is None:
        settings = get_settings()
        _service = OCRService(TesseractProvider(settings), settings)
    return _service

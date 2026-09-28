"""Modular OCR preprocessing steps (OpenCV).

Each step is a small pure function so the pipeline can be tuned later.
We deliberately avoid aggressive operations (morphology, sharpening) that
can destroy thin glyphs; binarization is one of two candidates evaluated
by the OCR service, not forced.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageOps

# Tesseract works best when capital letters are ~30px tall; for typical
# A4 bills that corresponds to a long side of ~2500-3500 px.
TARGET_LONG_SIDE = 3000
MAX_UPSCALE = 3.0
MAX_LONG_SIDE = 5000


def pil_to_gray(image: Image.Image) -> np.ndarray:
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA", "P"):
        # Flatten transparency onto white so transparent areas don't become black
        image = image.convert("RGBA")
        bg = Image.new("RGBA", image.size, (255, 255, 255, 255))
        image = Image.alpha_composite(bg, image)
    return np.array(image.convert("L"))


def rescale(gray: np.ndarray) -> np.ndarray:
    h, w = gray.shape[:2]
    long_side = max(h, w)
    if long_side < TARGET_LONG_SIDE:
        factor = min(TARGET_LONG_SIDE / long_side, MAX_UPSCALE)
        if factor > 1.05:
            return cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
    elif long_side > MAX_LONG_SIDE:
        factor = MAX_LONG_SIDE / long_side
        return cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    return gray


def denoise(gray: np.ndarray) -> np.ndarray:
    # Median 3x3 removes salt-and-pepper/JPEG speckle without eroding strokes
    return cv2.medianBlur(gray, 3)


def estimate_text_height(inv: np.ndarray) -> float:
    """Median height (px) of glyph-sized connected components in an inverted binary image."""
    n, _, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)
    heights = [
        stats[i, cv2.CC_STAT_HEIGHT] for i in range(1, n)
        if 8 <= stats[i, cv2.CC_STAT_HEIGHT] <= 300 and stats[i, cv2.CC_STAT_AREA] >= 20
        and stats[i, cv2.CC_STAT_WIDTH] <= 3 * stats[i, cv2.CC_STAT_HEIGHT]
    ]
    return float(np.median(heights)) if heights else 0.0


def remove_table_lines(gray: np.ndarray) -> np.ndarray:
    """Erase long horizontal/vertical ruling lines (table borders).

    Tesseract often drops whole table cells when glyphs touch cell borders.
    Only strokes far longer than any character are removed: the minimum line
    length scales with the measured text height, so the stems of large glyphs
    (l, 1, N, P...) are never mistaken for table borders.
    """
    h, w = gray.shape[:2]
    _, inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    min_len = int(4 * estimate_text_height(inv))
    horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, w // 25, min_len), 1))
    vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(40, h // 40, min_len)))
    lines = cv2.morphologyEx(inv, cv2.MORPH_OPEN, horiz_kernel) | cv2.morphologyEx(
        inv, cv2.MORPH_OPEN, vert_kernel
    )
    if not lines.any():
        return gray
    lines = cv2.dilate(lines, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    out = gray.copy()
    out[lines > 0] = 255
    return out


def binarize(gray: np.ndarray) -> np.ndarray:
    """Otsu for evenly lit scans; adaptive threshold when illumination varies (photos)."""
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    # Illumination spread: difference between bright and dark background regions
    bg = cv2.medianBlur(gray, 31) if min(gray.shape[:2]) > 31 else gray
    if np.percentile(bg, 95) - np.percentile(bg, 5) > 60:
        return cv2.adaptiveThreshold(
            blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 15
        )
    _, th = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return th


MAX_SKEW_DEGREES = 8.0


def estimate_skew(gray: np.ndarray) -> float:
    """Skew angle (degrees) that makes text rows horizontal.

    Projection-profile search: text rows produce the sharpest horizontal
    projection (highest variance) when the page is level. Runs on a
    downscaled binary copy so it stays fast.
    """
    h, w = gray.shape[:2]
    scale = min(1.0, 1200 / max(h, w))
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    _, inv = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    sh, sw = inv.shape[:2]
    center = (sw / 2, sh / 2)

    def score(angle: float) -> float:
        m = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(inv, m, (sw, sh), flags=cv2.INTER_NEAREST, borderValue=0)
        return float(np.var(rotated.sum(axis=1, dtype=np.float64)))

    coarse = max(np.arange(-MAX_SKEW_DEGREES, MAX_SKEW_DEGREES + 0.01, 0.5), key=score)
    fine = max(np.arange(coarse - 0.5, coarse + 0.51, 0.1), key=score)
    return round(float(fine), 2)


def deskew(gray: np.ndarray) -> np.ndarray:
    """Rotate so text rows are horizontal; skipped for (near-)level pages."""
    angle = estimate_skew(gray)
    if abs(angle) < 0.3 or abs(angle) >= MAX_SKEW_DEGREES:
        return gray
    h, w = gray.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def light_pipeline(image: Image.Image) -> np.ndarray:
    """grayscale -> rescale -> denoise -> deskew -> remove table lines"""
    return remove_table_lines(deskew(denoise(rescale(pil_to_gray(image)))))


def binary_pipeline(image: Image.Image) -> np.ndarray:
    """grayscale -> rescale -> denoise -> binarize"""
    return binarize(light_pipeline(image))


def none_pipeline(image: Image.Image) -> np.ndarray:
    return pil_to_gray(image)


PIPELINES = {
    "light": light_pipeline,
    "binary": binary_pipeline,
    "none": none_pipeline,
}

"""Rebuild layout-preserving text from positioned words.

Both PDF text extraction (PyMuPDF) and Tesseract return words with
bounding boxes. Re-assembling them into visual rows - with horizontal
spacing proportional to the real gaps - keeps table columns aligned so
the parser can match a header ("Previous Reading") with the value
printed underneath it. Column gaps are always rendered as >= 2 spaces,
gaps between words of the same phrase as exactly 1 space.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float = -1.0  # OCR confidence 0-100, -1 when not applicable

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return max(self.y1 - self.y0, 1.0)


# Gap (in average character widths) above which two words belong to different columns
COLUMN_GAP_CHARS = 1.8


def group_rows(words: list[Word]) -> list[list[Word]]:
    words = [w for w in words if w.text.strip()]
    if not words:
        return []
    med_h = median(w.height for w in words)
    rows: list[list[Word]] = []
    row_yc: list[float] = []
    for w in sorted(words, key=lambda w: (w.yc, w.x0)):
        if rows and abs(w.yc - row_yc[-1]) <= 0.5 * med_h:
            rows[-1].append(w)
            n = len(rows[-1])
            row_yc[-1] = row_yc[-1] + (w.yc - row_yc[-1]) / n
        else:
            rows.append([w])
            row_yc.append(w.yc)
    for r in rows:
        r.sort(key=lambda w: w.x0)
    return rows


def words_to_text(words: list[Word]) -> str:
    rows = group_rows(words)
    if not rows:
        return ""
    widths = [(w.x1 - w.x0) / len(w.text) for r in rows for w in r if len(w.text) > 0 and w.x1 > w.x0]
    char_w = median(widths) if widths else 1.0
    char_w = max(char_w, 1e-3)
    min_x = min(w.x0 for r in rows for w in r)

    lines: list[str] = []
    for row in rows:
        line = ""
        prev: Word | None = None
        for w in row:
            if prev is None:
                col = round((w.x0 - min_x) / char_w)
                line = " " * col
            else:
                gap = (w.x0 - prev.x1) / char_w
                if gap < COLUMN_GAP_CHARS:
                    line += " "
                else:
                    target = round((w.x0 - min_x) / char_w)
                    line += " " * max(2, target - len(line))
            line += w.text
            prev = w
        lines.append(line.rstrip())
    return "\n".join(lines)

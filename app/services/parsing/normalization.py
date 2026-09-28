"""Text clean-up and value normalisation for OCR'd bill text.

`normalize_text` produces the working copy the parser reads; the original
OCR text is stored untouched. The `parse_*` helpers turn a raw value string
into a typed value, or return None when the string is not confidently a
value of that type (the parser then keeps looking or reports it missing).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

# --------------------------------------------------------------------- text

_CHAR_MAP = {
    "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    " ": " ", " ": " ", " ": " ", " ": " ", "​": "",
    "﻿": "", "|": " ",  # table borders OCR'd as pipes
}
_CHAR_TABLE = str.maketrans(_CHAR_MAP)


def normalize_text(raw: str) -> str:
    """Normalise line endings, unicode punctuation and blank lines.

    Horizontal runs of spaces are deliberately kept: 2+ spaces mark a column
    gap in the layout-preserving text and the table parser depends on them.
    """
    text = unicodedata.normalize("NFKC", raw or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    text = text.translate(_CHAR_TABLE)
    lines = [line.rstrip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if not line.strip() and out and not out[-1].strip():
            continue  # collapse consecutive blank lines
        out.append(line)
    return "\n".join(out).strip("\n")


def split_cells(line: str) -> list[tuple[int, int, str]]:
    """Split a layout line into cells separated by 2+ spaces -> (start, end, text)."""
    return [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+(?: \S+)*", line)]


# ------------------------------------------------------------------ numbers

# Characters OCR commonly confuses with digits, applied only inside tokens
# that are already mostly numeric (so words are never altered).
_DIGIT_FIXES = str.maketrans({"O": "0", "o": "0", "D": "0", "Q": "0", "I": "1", "l": "1",
                              "|": "1", "S": "5", "B": "8", "Z": "2"})

NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?|[-+]?\.\d+")
# A numeric-looking token that may contain a few OCR letter confusions
_NUMERIC_TOKEN_RE = re.compile(r"^[-+]?[\dOoDQIl|SBZ][\dOoDQIl|SBZ,]*(?:\.[\dOoDQIl|SBZ]+)?$")


def fix_ocr_digits(token: str) -> str:
    """Repair letter/digit confusions in a token that is at least half digits."""
    digits = sum(c.isdigit() for c in token)
    if digits == 0 or digits * 2 < len(token.replace(",", "").replace(".", "")):
        return token
    if not _NUMERIC_TOKEN_RE.match(token):
        return token
    return token.translate(_DIGIT_FIXES)


def _to_decimal(num: str) -> Decimal | None:
    num = num.replace(",", "")
    try:
        value = Decimal(num)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def parse_number(value: str | None) -> Decimal | None:
    """Parse the leading number of a value string: '8,245', '2110.5 kWh', '1O250'."""
    if not value:
        return None
    first = value.strip().split()[0] if value.strip() else ""
    token = fix_ocr_digits(first.rstrip(".,:;"))
    m = NUMBER_RE.fullmatch(token)
    if not m:
        return None
    # Reject malformed digit grouping like '1,2,3' that is probably not a number
    if "," in token and not re.fullmatch(r"[-+]?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?", token):
        return None
    return _to_decimal(token)


# Currency markers, separators, and symbols OCR produces for '₹' / '=' (e.g. '&', '%').
# Letters/digits are never stripped: a wrong amount is worse than a missing one.
_CURRENCY_PREFIX_RE = re.compile(r"^(?:rs\.?|inr|₹|rupees|[=:&%$*#~?�]|-(?!\s*\d))\s*", re.IGNORECASE)


def parse_amount(value: str | None) -> Decimal | None:
    """Parse a monetary value: 'Rs. 2,450.50', '₹1,045.75', '= 5,126.00'. Returns 2dp Decimal."""
    if not value:
        return None
    v = value.strip()
    for _ in range(3):  # strip stacked prefixes like '= Rs.'
        new = _CURRENCY_PREFIX_RE.sub("", v)
        if new == v:
            break
        v = new
    amount = parse_number(v)
    if amount is None:
        return None
    # Amounts on bills have at most 2 decimals; more means we grabbed something else
    if amount.as_tuple().exponent < -2:  # type: ignore[operator]
        return None
    return amount.quantize(Decimal("0.01"))


# -------------------------------------------------------------------- dates

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_NAMES = "|".join(sorted(MONTHS, key=len, reverse=True))

_DATE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 2025-05-31
    (re.compile(r"(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})"), "ymd"),
    # 31-05-2025, 31/05/25, 31.05.2025 (Indian bills are day-first)
    (re.compile(r"(?P<d>\d{1,2})\s?[-/.]\s?(?P<m>\d{1,2})\s?[-/.]\s?(?P<y>\d{4}|\d{2})(?!\d)"), "dmy"),
    # 31 May 2025, 31-May-25, 31 May, 2025
    (re.compile(rf"(?P<d>\d{{1,2}})(?:st|nd|rd|th)?[\s\-/.]*(?P<mon>{_MONTH_NAMES})\.?[\s\-/.,]*(?P<y>\d{{4}}|\d{{2}})(?!\d)",
                re.IGNORECASE), "dMy"),
    # May 31, 2025
    (re.compile(rf"(?P<mon>{_MONTH_NAMES})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y>\d{{4}})", re.IGNORECASE), "Mdy"),
]


def _year(y: str) -> int:
    n = int(y)
    return 2000 + n if n < 100 else n


def _fix_date_digits(value: str) -> str:
    # Fix O/I/l inside digit groups only: '3O-O4-2025' -> '30-04-2025'
    return re.sub(r"[\dOoIl|]{1,4}", lambda m: fix_ocr_digits(m.group()), value)


def find_date(value: str | None) -> tuple[date, int, int] | None:
    """Find the first valid date in a string. Returns (date, start, end)."""
    if not value:
        return None
    text = _fix_date_digits(value)
    best: tuple[date, int, int] | None = None
    for pattern, kind in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            g = m.groupdict()
            try:
                month = MONTHS[g["mon"].lower()] if "mon" in g and g.get("mon") else int(g["m"])
                parsed = date(_year(g["y"]), month, int(g["d"]))
            except (ValueError, KeyError):
                continue
            if not 1990 <= parsed.year <= 2100:
                continue
            if best is None or m.start() < best[1]:
                best = (parsed, m.start(), m.end())
            break
    return best


def parse_date(value: str | None) -> date | None:
    found = find_date(value)
    return found[0] if found else None


# ------------------------------------------------------------ billing period

_RANGE_SEP_RE = re.compile(r"^\s*(?:to|till|until|upto|up to|-|~|—)\s*", re.IGNORECASE)
_MONTH_YEAR_RE = re.compile(
    rf"^\s*(?P<mon>{_MONTH_NAMES})\.?[\s\-/.,']*(?P<y>\d{{4}}|\d{{2}})(?!\d)", re.IGNORECASE
)
_NUM_MONTH_YEAR_RE = re.compile(r"^\s*(?P<m>\d{1,2})\s?[-/.]\s?(?P<y>\d{4})(?!\d)")
# Compact year+month codes used by some billing systems: "202508" (Rajasthan discoms)
_YYYYMM_RE = re.compile(r"^\s*(?P<y>20\d{2})(?P<m>0[1-9]|1[0-2])\s*$")


def parse_billing_period(value: str | None) -> tuple[str, date | None, date | None] | None:
    """Parse '01-05-2025 to 31-05-2025', '12 Apr 2025 - 11 May 2025', 'MAY-2025', '05/2025'.

    Returns (display string, start, end). Date ranges are rendered ISO
    ('2025-05-01 to 2025-05-31'); a bare month as 'May 2025'.
    """
    if not value:
        return None
    value = _fix_date_digits(value)
    first = find_date(value)
    if first and first[1] <= 2:
        rest = value[first[2]:]
        sep = _RANGE_SEP_RE.match(rest)
        if sep:
            second = find_date(rest[sep.end():])
            if second and second[1] <= 2 and second[0] >= first[0]:
                start, end = first[0], second[0]
                return f"{start.isoformat()} to {end.isoformat()}", start, end
    m = _MONTH_YEAR_RE.match(value)
    if m:
        month = MONTHS[m.group("mon").lower()]
        year = _year(m.group("y"))
        return f"{date(year, month, 1):%B %Y}", None, None
    m = _NUM_MONTH_YEAR_RE.match(value) or _YYYYMM_RE.match(value)
    if m and 1 <= int(m.group("m")) <= 12:
        return f"{date(int(m.group('y')), int(m.group('m')), 1):%B %Y}", None, None
    return None


# --------------------------------------------------------------- identifiers

_ACCOUNT_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-/]*[A-Za-z0-9]$")


def parse_account_number(value: str | None) -> str | None:
    """Account/consumer numbers: keep as string (leading zeros matter)."""
    if not value:
        return None
    token = value.strip().split()[0].strip(".,:;")
    # Allow numbers OCR'd with a single space inside, e.g. '1700 1234 5678'
    parts = value.strip().split()
    if len(parts) > 1 and all(re.fullmatch(r"\d{2,6}", p) for p in parts[:4]):
        joined = "".join(p for p in parts[:4] if re.fullmatch(r"\d{2,6}", p))
        if len(joined) >= 6:
            token = joined
    if not _ACCOUNT_TOKEN_RE.match(token):
        return None
    fixed = "".join(fix_ocr_digits(seg) if seg.isalnum() else seg
                    for seg in re.split(r"([\-/])", token))
    digits = sum(c.isdigit() for c in fixed)
    if digits < 4 or digits * 2 < len(fixed.replace("-", "").replace("/", "")):
        return None
    return fixed.upper()


# Letters plus the punctuation company names use: "Acme Steels North-East Limited", "A & B (India) Pvt. Ltd."
_NAME_RE = re.compile(
    r"^(?:(?:mr|mrs|ms|smt|shri|sri|dr|m/s)\.?\s*)?[A-Za-z][A-Za-z.'&()\- ]*[A-Za-z.)]", re.IGNORECASE
)
# A "name" starting with / containing these words is really another label ("Mob No", "Name of ...")
_NAME_STOPWORDS = {"no", "number", "id", "code", "type", "category", "address", "details", "of", "name"}
_NAME_LABEL_WORDS = {"no", "number", "date", "amount", "mob", "mobile", "phone", "address", "email", "e-mail"}


def parse_name(value: str | None) -> str | None:
    if not value:
        return None
    m = _NAME_RE.match(value.strip())
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group()).strip(" .")
    words = name.lower().replace(".", " ").split()
    if not words or words[0] in _NAME_STOPWORDS or len(name) < 3:
        return None
    if any(w.strip("()") in _NAME_LABEL_WORDS for w in words):
        return None
    if len(re.sub(r"[^A-Za-z]", "", name)) < 3:
        return None
    return name.upper() if name.isupper() or name.islower() else name

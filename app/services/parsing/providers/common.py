"""Small helpers shared by provider parsers."""

from __future__ import annotations

import re
from decimal import Decimal

from app.services.parsing.base import ParsedBill
from app.services.parsing.normalization import parse_number

NUM = r"\d[\d,]*(?:\.\d+)?"
METER_FIELDS = ("previous_reading", "current_reading", "units_consumed", "multiplying_factor", "open_access_units")


def num(value: str | None) -> Decimal | None:
    return parse_number(value) if value else None


def clear_meter_fields(bill: ParsedBill) -> None:
    """Drop generic-parser meter values before a provider supplies its own.

    On multi-register bills the generic table logic can latch onto a single
    zone's value; a missing value is better than a wrong one.
    """
    for fld in METER_FIELDS:
        setattr(bill, fld, None)
        bill.sources.pop(fld, None)


def set_field(bill: ParsedBill, fld: str, value, source: str) -> None:
    if value is None:
        return
    setattr(bill, fld, value)
    bill.sources[fld] = source


def find_line(lines: list[str], pattern: str, start: int = 0, flags: int = re.IGNORECASE) -> int | None:
    rx = re.compile(pattern, flags)
    for i in range(start, len(lines)):
        if rx.search(lines[i]):
            return i
    return None

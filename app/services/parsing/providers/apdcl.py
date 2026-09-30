"""Assam Power Distribution Company Ltd (APDCL) - HT bills with TOD metering.

Layout (text PDF):

    Reading Type  Meter Number  MF     Previous Reading  Prev Export  Current Reading  Curr Export  Difference ...
    KWH(Solar)    Q0000001      1.000  1000.000          0            1600.000         0            600.000
    KWH(Peak)     ...
    KWH(Normal)   ...
    Open Access Units Solar  500.000   Open Access Units Peak  200.000   Open Access Units Normal  700.000
    Unit Consumed ...
    Solar | 100.000  ...
    Normal |           ...        (value wrapped onto the next line)
    200.000

The time-of-day registers are separate counters of one meter, so the bill's
previous/current reading is the sum over the registers, and

    units consumed = sum(current - previous) x MF - open-access units
                   = sum of the "Unit Consumed" column.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.services.parsing.base import ParsedBill
from app.services.parsing.generic import GenericBillParser
from app.services.parsing.normalization import normalize_text, parse_name, split_cells
from app.services.parsing.providers.common import NUM, clear_meter_fields, find_line, num, set_field

_DETECT_RE = re.compile(r"Assam\s+Power\s+Distribution|www\.apdcl\.org", re.IGNORECASE)
# The export columns aren't needed for the calculation and are thin cells holding "0",
# which OCR reads as "tt)" / "LY)": accept any short token there.
_METER_ROW_RE = re.compile(
    rf"^\s*KWH\s*(?:\((?P<zone>[A-Za-z\- ]+)\))?\s+(?P<meter>\S+)\s+(?P<mf>{NUM})\s+(?P<prev>{NUM})\s+"
    rf"(?P<prev_exp>\S{{1,12}})\s+(?P<curr>{NUM})\s+(?P<curr_exp>\S{{1,12}})\s+(?P<diff>{NUM})",
    re.IGNORECASE,
)
# On lines mentioning "Open Access": each "Units [Solar|Peak|Normal]" label and its value. The
# label may wrap ("Open Access" / "Units ... Normal"), and OCR can read "1464884.000" as "1464884 000".
_OPEN_ACCESS_LINE_RE = re.compile(r"Open\s+Access", re.IGNORECASE)
_OPEN_ACCESS_VALUE_RE = re.compile(
    rf"\bUnits(?:\s+[A-Za-z\-]+)?\s+(?P<v>{NUM}(?:\s\d{{3}}(?=\s{{2}}|$))?)", re.IGNORECASE
)
_LONE_NUMBER_RE = re.compile(rf"^\s*(?P<v>{NUM})\s*$")


# A register row, readable or not: "KWH(Solar) ..." or "KWH  <meter no>  <MF>" - not the
# glossary line "KWh: Kilo Watt Hour" at the foot of the bill
_KWH_LINE_RE = re.compile(r"^\s*KWH\s*(?:\(|\s\S+\s+\d)", re.IGNORECASE)
_PRINTED_DECIMALS = 3
_RESOLUTION = Decimal("0.001")


# In register, open-access and unit rows a comma between digits can only be the decimal point
# (these columns have no thousands separators). Tesseract builds differ: "41767399,420",
# "41767399,.420", "18868299, 780" (the last splits the number into two tokens).
_DECIMAL_MARK_RE = re.compile(r"(\d)\s?[,.]*,[,.]*\s?(\d{3})(?!\d)")


def _clean(line: str) -> str:
    """Undo OCR decimal-mark slips in a register / open-access / unit row (never amount rows)."""
    return _DECIMAL_MARK_RE.sub(r"\1.\2", line)


def _value(token: str | None) -> Decimal | None:
    """A reading, MF or unit value. APDCL always prints exactly 3 decimals ("40268001.280",
    "1.000"), so OCR slips can be undone: a comma for the point ("41767399,420") or a lost
    point ("1499398140"). Callers cross-check the result (current - previous = difference)."""
    if token is None:
        return None
    token = token.strip()
    if re.fullmatch(rf"\d+,\d{{{_PRINTED_DECIMALS}}}", token):
        token = token.replace(",", ".")
    elif re.fullmatch(rf"\d{{{_PRINTED_DECIMALS + 1},}}", token):
        token = f"{token[:-_PRINTED_DECIMALS]}.{token[-_PRINTED_DECIMALS:]}"
    return num(token)


class APDCLParser(GenericBillParser):
    name = "apdcl"

    @classmethod
    def detect(cls, text: str) -> bool:
        return bool(_DETECT_RE.search(text or ""))

    def parse(self, raw_text: str) -> ParsedBill:
        bill = super().parse(raw_text)
        bill.parser_name = self.name
        lines = normalize_text(raw_text).split("\n")
        self._consumer_name(lines, bill)

        rows = [m for m in (_METER_ROW_RE.match(_clean(line)) for line in lines) if m]
        if not rows:
            return bill  # not the TOD layout: keep the generic result
        clear_meter_fields(bill)

        # A register row OCR couldn't read would make the sums partial
        if len(rows) != sum(bool(_KWH_LINE_RE.match(line)) for line in lines):
            bill.notes.append("A meter register row could not be read; readings not combined")
            return bill
        # Each register must satisfy current - previous = difference; a digit misread by OCR
        # breaks that, and a missing reading is better than a wrong one
        for r in rows:
            prev_r, curr_r, diff_r = _value(r["prev"]), _value(r["curr"]), _value(r["diff"])
            if None in (prev_r, curr_r, diff_r) or abs((curr_r - prev_r) - diff_r) > _RESOLUTION:
                bill.notes.append(f"Readings of register {r['zone'] or 'KWH'} don't add up "
                                  "(current - previous ≠ difference); readings not taken")
                return bill

        mfs = {_value(r["mf"]) for r in rows}
        if len(mfs) != 1:
            bill.notes.append("Meter registers use different multiplying factors; readings not combined")
            return bill
        prev = sum((_value(r["prev"]) for r in rows), Decimal(0))
        curr = sum((_value(r["curr"]) for r in rows), Decimal(0))
        zones = [r["zone"] or "KWH" for r in rows]
        set_field(bill, "previous_reading", prev, "apdcl:sum of KWH registers")
        set_field(bill, "current_reading", curr, "apdcl:sum of KWH registers")
        set_field(bill, "multiplying_factor", mfs.pop(), "apdcl:MF column")
        if len(rows) > 1:
            bill.notes.append(f"Readings are the sum of the {len(rows)} time-of-day registers ({', '.join(zones)})")
        if any(num(r["prev_exp"]) or num(r["curr_exp"]) for r in rows):
            bill.notes.append("Export readings are present (net metering); units shown are import consumption")

        open_access = [_value(m["v"].replace(" ", "."))
                       for line in lines if _OPEN_ACCESS_LINE_RE.search(line)
                       for m in _OPEN_ACCESS_VALUE_RE.finditer(_clean(line))]
        open_access = [v for v in open_access if v is not None]
        if open_access:
            set_field(bill, "open_access_units", sum(open_access, Decimal(0)), "apdcl:Open Access Units")

        units = self._unit_consumed(lines, zones)
        set_field(bill, "units_consumed", units, "apdcl:sum of Unit Consumed")
        return bill

    @staticmethod
    def _consumer_name(lines: list[str], bill: ParsedBill) -> None:
        """Names wrap inside the header cell ('ACME STEELS NORTH-EAST PRIVATE' / 'Ltd')."""
        idx = find_line(lines, r"Consumer\s+Name\s*:")
        if idx is None or idx + 1 >= len(lines):
            return
        cells = split_cells(lines[idx])
        m = re.match(r"Consumer\s+Name\s*:\s*(.+)", cells[0][2], re.IGNORECASE) if cells else None
        if not m:
            return
        name = m.group(1)
        cont = split_cells(lines[idx + 1])
        # A continuation is a short fragment in the same column, not another "Label:"
        if cont and cont[0][0] <= cells[0][0] + 2 and ":" not in cont[0][2] and len(cont[0][2]) <= 25 \
                and cont[0][1] < (cells[1][0] if len(cells) > 1 else 10_000):
            name = f"{name} {cont[0][2]}"
        parsed = parse_name(name)
        if parsed:
            set_field(bill, "consumer_name", parsed, "apdcl:Consumer Name")

    @staticmethod
    def _unit_consumed(lines: list[str], zones: list[str]) -> Decimal | None:
        """Sum the 'Unit Consumed' cell of every TOD zone ('Solar | 34514.140').

        The '|' is part of the cell text but table pipes are blanked during
        normalisation, so rows are matched by the zone names of the meter table.
        """
        start = find_line(lines, r"^\s*Unit\s+Consumed")
        if start is None:
            return None
        values: list[Decimal] = []
        for zone in zones:
            row_re = re.compile(rf"^\s*{re.escape(zone)}\s*\|?\s*(?P<v>{NUM})?(?:\s|$)", re.IGNORECASE)
            for i in range(start + 1, min(start + 12, len(lines))):
                m = row_re.match(_clean(lines[i]))
                if not m:
                    continue
                value = _value(m["v"])
                if value is None and i + 1 < len(lines):  # value wrapped onto the next line
                    nxt = _LONE_NUMBER_RE.match(_clean(lines[i + 1]))
                    value = _value(nxt["v"]) if nxt else None
                if value is not None:
                    values.append(value)
                break
        if len(values) != len(zones):
            return None  # a zone we can't read: never return a partial total
        return sum(values, Decimal(0))

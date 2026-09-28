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
_METER_ROW_RE = re.compile(
    rf"^\s*KWH\s*(?:\((?P<zone>[A-Za-z\- ]+)\))?\s+(?P<meter>\S+)\s+(?P<mf>{NUM})\s+(?P<prev>{NUM})\s+"
    rf"(?P<prev_exp>{NUM})\s+(?P<curr>{NUM})\s+(?P<curr_exp>{NUM})\s+(?P<diff>{NUM})",
    re.IGNORECASE,
)
_OPEN_ACCESS_RE = re.compile(rf"Open\s+Access\s+Units(?:\s+[A-Za-z]+)?\s+(?P<v>{NUM})", re.IGNORECASE)
_LONE_NUMBER_RE = re.compile(rf"^\s*(?P<v>{NUM})\s*$")


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

        rows = [m for m in (_METER_ROW_RE.match(line) for line in lines) if m]
        if not rows:
            return bill  # not the TOD layout: keep the generic result
        clear_meter_fields(bill)

        mfs = {num(r["mf"]) for r in rows}
        if len(mfs) != 1:
            bill.notes.append("Meter registers use different multiplying factors; readings not combined")
            return bill
        prev = sum((num(r["prev"]) for r in rows), Decimal(0))
        curr = sum((num(r["curr"]) for r in rows), Decimal(0))
        zones = [r["zone"] or "KWH" for r in rows]
        set_field(bill, "previous_reading", prev, "apdcl:sum of KWH registers")
        set_field(bill, "current_reading", curr, "apdcl:sum of KWH registers")
        set_field(bill, "multiplying_factor", mfs.pop(), "apdcl:MF column")
        if len(rows) > 1:
            bill.notes.append(f"Readings are the sum of the {len(rows)} time-of-day registers ({', '.join(zones)})")
        if any(num(r["prev_exp"]) or num(r["curr_exp"]) for r in rows):
            bill.notes.append("Export readings are present (net metering); units shown are import consumption")

        open_access = [num(m["v"]) for m in _OPEN_ACCESS_RE.finditer("\n".join(lines))]
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
                m = row_re.match(lines[i])
                if not m:
                    continue
                value = num(m["v"])
                if value is None and i + 1 < len(lines):  # value wrapped onto the next line
                    nxt = _LONE_NUMBER_RE.match(lines[i + 1])
                    value = num(nxt["v"]) if nxt else None
                if value is not None:
                    values.append(value)
                break
        if len(values) != len(zones):
            return None  # a zone we can't read: never return a partial total
        return sum(values, Decimal(0))

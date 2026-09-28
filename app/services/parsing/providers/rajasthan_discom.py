"""Rajasthan discoms (JVVNL / AVVNL / JdVVNL) - HT bills.

Grid-style form where most values sit under column headers:

    K No:   210000000001   Acc No:   90000001 ...
    Billing Month  Tariff Code ... Due Date Of ...   Consumer Name & Address.
                                     Payment
                                                       M/S Example Forgings Ltd. null
    202508         8000        ... 14-08-2025
    ...
    Meter No.  Nature Of Meter  Present Reading  Last Reading  Difference  MF       Consumption
      1              2                3               4          (3-4)=5    6        (5 x 6)=7
    123456 1       KWH          1500.5000        1000.5000     500.0000    10.0000  5000.0000
    123456 2       KVAH         ...

The energy (KWH) register row gives the readings, MF and consumption.
"""

from __future__ import annotations

import re

from app.services.parsing.base import ParsedBill
from app.services.parsing.generic import GenericBillParser
from app.services.parsing.normalization import (
    normalize_text,
    parse_account_number,
    parse_billing_period,
    parse_date,
    parse_name,
    split_cells,
)
from app.services.parsing.providers.common import NUM, clear_meter_fields, num, set_field

_DETECT_RE = re.compile(r"VIDYUT\s+VITRAN\s+NIGAM", re.IGNORECASE)
_K_NO_RE = re.compile(r"(?:^|\s)K\s*No\.?\s*:?\s*(?P<v>\d{6,15})", re.IGNORECASE)
_KWH_ROW_RE = re.compile(
    rf"^\s*(?P<meter>\S+(?:\s\d{{1,2}})?)\s+KWH\s+(?P<present>{NUM})\s+(?P<last>{NUM})\s+(?P<diff>{NUM})\s+"
    rf"(?P<mf>{NUM})\s+(?P<cons>{NUM})",
    re.IGNORECASE,
)


class RajasthanDiscomParser(GenericBillParser):
    name = "rajasthan_discom"

    @classmethod
    def detect(cls, text: str) -> bool:
        return bool(_DETECT_RE.search(text or ""))

    def parse(self, raw_text: str) -> ParsedBill:
        bill = super().parse(raw_text)
        bill.parser_name = self.name
        lines = normalize_text(raw_text).split("\n")

        # K No is the consumer's primary identifier on these bills
        for line in lines:
            m = _K_NO_RE.search(line)
            if m and (acct := parse_account_number(m["v"])):
                set_field(bill, "account_number", acct, "rajasthan:K No")
                break

        name = self._under_header(lines, r"^Consumer\s+Name", self._clean_name)
        set_field(bill, "consumer_name", name, "rajasthan:Consumer Name & Address")
        period = self._under_header(lines, r"^Billing\s+Month$", parse_billing_period)
        if period:
            bill.billing_period, bill.billing_period_start, bill.billing_period_end = period
            bill.sources["billing_period"] = "rajasthan:Billing Month"
        due = self._under_header(lines, r"^Due\s+Date(?:\s+Of)?(?:\s+Payment)?$", parse_date)
        set_field(bill, "due_date", due, "rajasthan:Due Date Of Payment")

        row = next((m for m in (_KWH_ROW_RE.match(line) for line in lines) if m), None)
        if row:
            clear_meter_fields(bill)
            set_field(bill, "current_reading", num(row["present"]), "rajasthan:KWH Present Reading")
            set_field(bill, "previous_reading", num(row["last"]), "rajasthan:KWH Last Reading")
            set_field(bill, "multiplying_factor", num(row["mf"]), "rajasthan:KWH MF")
            set_field(bill, "units_consumed", num(row["cons"]), "rajasthan:KWH Consumption")
            open_access = self._under_header(lines, r"^Test/?\s*Open\s+access", num)
            if open_access:
                set_field(bill, "open_access_units", open_access, "rajasthan:Test/Open access Units")
        return bill

    @staticmethod
    def _clean_name(value: str):
        # The billing system prints empty name parts as "null"
        value = re.sub(r"\bnull\b", " ", value, flags=re.IGNORECASE)
        return parse_name(value.strip(" ,"))

    def _under_header(self, lines: list[str], header_pattern: str, parser):
        """Value in the column below the first header cell matching header_pattern.

        Headers here often wrap onto a second line ("Due Date Of" / "Payment"),
        so non-matching rows are skipped for a few lines instead of ending the search.
        """
        rx = re.compile(header_pattern, re.IGNORECASE)
        for i, line in enumerate(lines):
            headers = split_cells(line)
            if len(headers) < 2:
                continue
            for col, (_, _, text) in enumerate(headers):
                if not rx.search(text.strip()):
                    continue
                for j in range(i + 1, min(i + 5, len(lines))):
                    cell = self._cell_under(split_cells(lines[j]), headers, col)
                    if cell is not None and (value := parser(cell)) is not None:
                        return value
        return None

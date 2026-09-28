"""Karnataka ESCOMs (GESCOM / BESCOM / HESCOM / MESCOM / CESC) - EHT/HT bills.

Usually a scanned letter-style bill:

    Bill for the supply of Electrical Energy for the Month of        July   2025
    Name of the Firm:M/S Example Cements Private LTD @ Some Road ...
    R.R. No:   EHT 9                 Last Date of Payment:   16th of   July
    Particulars   Final Reading  Initial Reading  Difference  Meter Constant  Consumption in Units
    Main M R      100.500        100.000          0.500       175000          87500
    Zone-4 ...                                                                (TOD split of the main meter)
    15. Grand Total: ...                                                  Rs. 950000.40
                                                                   Say    Rs. 950000

units = (final - initial) x meter constant. The due date is printed without
a year; it is taken from the bill month (next year if the due month is
earlier than the bill month, e.g. a December bill due in January).
"""

from __future__ import annotations

import re
from datetime import date

from app.services.parsing.base import ParsedBill
from app.services.parsing.generic import GenericBillParser
from app.services.parsing.normalization import MONTHS, normalize_text, parse_amount, parse_billing_period, parse_name
from app.services.parsing.providers.common import NUM, clear_meter_fields, find_line, num, set_field

_DETECT_RE = re.compile(r"\b(?:GESCOM|BESCOM|HESCOM|MESCOM|CESC(?:OM)?)\b|Electricity\s+Supply\s+Company", re.IGNORECASE)
_RR_RE = re.compile(r"R\.?\s*R\.?\s*No\.?\s*[:\-]?\s*(?P<v>[A-Z]{1,6}\s?\d{1,10}|\d{3,15})", re.IGNORECASE)
_MONTH_OF_RE = re.compile(r"for\s+the\s+Month\s+of\s*[:\-]?\s*(?P<v>.+)$", re.IGNORECASE)
_FIRM_RE = re.compile(r"Name\s+of\s+the\s+(?:Firm|Consumer)\s*[:\-]?\s*(?P<v>.+)$", re.IGNORECASE)
_DUE_RE = re.compile(
    r"Last\s+Date\s+of\s+Payment\s*[:\-]?\s*(?P<d>\d{1,2})\s*(?:st|nd|rd|th)?\s*(?:of)?\s*"
    r"(?P<mon>[A-Za-z]{3,9})\.?\s*(?P<y>\d{4})?",
    re.IGNORECASE,
)
_MAIN_METER_RE = re.compile(
    rf"^\s*Main\s*M\.?\s*R\.?\s+(?P<a>{NUM})\s+(?P<b>{NUM})\s+(?P<diff>{NUM})\s+(?P<mc>{NUM})\s+(?P<cons>{NUM})",
    re.IGNORECASE,
)
_SAY_RE = re.compile(rf"\bSay\b\s*(?:Rs\.?)?\s*(?P<v>{NUM})", re.IGNORECASE)
_GRAND_TOTAL_RE = re.compile(rf"Grand\s*Tota\w*.*?(?P<v>{NUM})\s*$", re.IGNORECASE)


class KarnatakaEscomParser(GenericBillParser):
    name = "karnataka_escom"

    @classmethod
    def detect(cls, text: str) -> bool:
        text = text or ""
        return bool(_DETECT_RE.search(text) and _RR_RE.search(text))

    def parse(self, raw_text: str) -> ParsedBill:
        bill = super().parse(raw_text)
        bill.parser_name = self.name
        lines = normalize_text(raw_text).split("\n")
        text = "\n".join(lines)

        for line in lines:
            if m := _FIRM_RE.search(line):
                set_field(bill, "consumer_name", parse_name(m["v"]), "karnataka:Name of the Firm")
                break
        if m := _RR_RE.search(text):
            set_field(bill, "account_number", re.sub(r"\s+", " ", m["v"]).upper(), "karnataka:R.R. No")
        for line in lines:
            if (m := _MONTH_OF_RE.search(line)) and (period := parse_billing_period(re.sub(r"\s+", " ", m["v"]))):
                bill.billing_period, bill.billing_period_start, bill.billing_period_end = period
                bill.sources["billing_period"] = "karnataka:for the Month of"
                break
        self._due_date(text, bill)
        self._main_meter(lines, bill)
        self._amount(lines, bill)
        return bill

    @staticmethod
    def _due_date(text: str, bill: ParsedBill) -> None:
        m = _DUE_RE.search(text)
        if not m or m["mon"].lower() not in MONTHS:
            return
        month = MONTHS[m["mon"].lower()]
        if m["y"]:
            year = int(m["y"])
        else:
            period_month = _period_month(bill.billing_period)
            if period_month is None:
                return  # no year anywhere: leave it missing rather than guess
            bill_year, bill_month = period_month
            year = bill_year + 1 if month < bill_month else bill_year
            bill.notes.append(f"Due date year ({year}) taken from the bill month; the bill prints only day and month")
        try:
            set_field(bill, "due_date", date(year, month, int(m["d"])), "karnataka:Last Date of Payment")
        except ValueError:
            pass

    @staticmethod
    def _main_meter(lines: list[str], bill: ParsedBill) -> None:
        idx = next((i for i, line in enumerate(lines) if _MAIN_METER_RE.match(line)), None)
        if idx is None:
            return
        row = _MAIN_METER_RE.match(lines[idx])
        clear_meter_fields(bill)
        # Column order comes from the header ("Final Reading" normally left of "Initial")
        header = "\n".join(lines[max(0, idx - 4): idx])
        final_pos = min((len(line.split("Final")[0]) for line in header.split("\n") if "Final" in line), default=0)
        initial_pos = min((len(line.split("Initial")[0]) for line in header.split("\n") if "Initial" in line),
                          default=1)
        final, initial = (row["a"], row["b"]) if final_pos <= initial_pos else (row["b"], row["a"])
        set_field(bill, "current_reading", num(final), "karnataka:Main MR Final Reading")
        set_field(bill, "previous_reading", num(initial), "karnataka:Main MR Initial Reading")
        set_field(bill, "multiplying_factor", num(row["mc"]), "karnataka:Meter Constant")
        set_field(bill, "units_consumed", num(row["cons"]), "karnataka:Main MR Consumption")

    @staticmethod
    def _amount(lines: list[str], bill: ParsedBill) -> None:
        # "Say Rs." is the rounded amount actually payable; Grand Total is the fallback
        for rx, source in ((_SAY_RE, "karnataka:Say Rs."), (_GRAND_TOTAL_RE, "karnataka:Grand Total")):
            idx = find_line(lines, rx.pattern)
            if idx is not None and (m := rx.search(lines[idx])) and (amount := parse_amount(m["v"])) is not None:
                set_field(bill, "net_amount_due", amount, source)
                return


def _period_month(period: str | None) -> tuple[int, int] | None:
    parsed = parse_billing_period(period) if period else None
    if not parsed:
        return None
    _, start, end = parsed
    if end:
        return end.year, end.month
    m = re.match(r"([A-Za-z]+) (\d{4})", parsed[0])
    return (int(m.group(2)), MONTHS[m.group(1).lower()]) if m else None

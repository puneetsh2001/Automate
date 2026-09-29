"""Entry point for bill parsing.

Provider-specific parsers (subclasses of BaseBillParser with a `detect`
method) are registered in PROVIDER_PARSERS and tried first; the generic
label-based parser handles everything else. Units consumed that the bill
doesn't print are then derived from the meter readings.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal

from app.core.config import get_settings
from app.services.parsing.base import DERIVED_UNITS_SOURCE, BaseBillParser, ParsedBill
from app.services.parsing.generic import GenericBillParser
from app.services.parsing.providers import APDCLParser, KarnatakaEscomParser, RajasthanDiscomParser

logger = logging.getLogger(__name__)

# Tried in order; the first whose detect() matches handles the bill.
PROVIDER_PARSERS: list[type[GenericBillParser]] = [APDCLParser, RajasthanDiscomParser, KarnatakaEscomParser]


def derive_units(bill: ParsedBill) -> None:
    """Fill in units consumed from the readings when the bill doesn't print them.

    units = (current - previous) x MF - open-access units. The source is marked
    as derived so validation doesn't count it as an independent cross-check.
    """
    prev, curr = bill.previous_reading, bill.current_reading
    if bill.units_consumed is not None or prev is None or curr is None or curr < prev:
        return
    factor = bill.multiplying_factor if bill.multiplying_factor and bill.multiplying_factor > 0 else Decimal(1)
    units = (curr - prev) * factor - (bill.open_access_units or 0)
    if units < 0:
        return
    if units.as_tuple().exponent < -3:  # type: ignore[operator]
        units = units.quantize(Decimal("0.001"))  # stored as NUMERIC(14, 3)
    bill.units_consumed = units
    bill.sources["units_consumed"] = DERIVED_UNITS_SOURCE


class BillParser:
    def __init__(self, extra_aliases_file: str | None = None):
        if extra_aliases_file is None:
            extra_aliases_file = get_settings().LABEL_ALIASES_FILE
        self.extra_aliases_file = extra_aliases_file
        self.generic = GenericBillParser(extra_aliases_file)

    def select(self, raw_text: str) -> BaseBillParser:
        for parser_cls in PROVIDER_PARSERS:
            if parser_cls.detect(raw_text):
                return parser_cls(self.extra_aliases_file)
        return self.generic

    def parse(self, raw_text: str) -> ParsedBill:
        start = time.perf_counter()
        parser = self.select(raw_text)
        logger.info("Parsing started (parser=%s, %d chars)", parser.name, len(raw_text or ""))
        result = parser.parse(raw_text or "")
        derive_units(result)
        missing = result.missing_fields()
        logger.info(
            "Parsing finished in %.0fms: %d/8 required fields found%s",
            (time.perf_counter() - start) * 1000, 8 - len(missing),
            f", missing={missing}" if missing else "",
        )
        return result

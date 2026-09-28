"""Entry point for bill parsing.

Provider-specific parsers (subclasses of BaseBillParser with a `detect`
method) are registered in PROVIDER_PARSERS and tried first; the generic
label-based parser handles everything else.
"""

from __future__ import annotations

import logging
import time

from app.core.config import get_settings
from app.services.parsing.base import BaseBillParser, ParsedBill
from app.services.parsing.generic import GenericBillParser
from app.services.parsing.providers import APDCLParser, KarnatakaEscomParser, RajasthanDiscomParser

logger = logging.getLogger(__name__)

# Tried in order; the first whose detect() matches handles the bill.
PROVIDER_PARSERS: list[type[GenericBillParser]] = [APDCLParser, RajasthanDiscomParser, KarnatakaEscomParser]


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
        missing = result.missing_fields()
        logger.info(
            "Parsing finished in %.0fms: %d/8 required fields found%s",
            (time.perf_counter() - start) * 1000, 8 - len(missing),
            f", missing={missing}" if missing else "",
        )
        return result

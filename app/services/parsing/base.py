"""Parser contract shared by the generic and any provider-specific parsers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal

REQUIRED_FIELDS = (
    "consumer_name",
    "account_number",
    "billing_period",
    "due_date",
    "previous_reading",
    "current_reading",
    "units_consumed",
    "net_amount_due",
)


@dataclass
class ParsedBill:
    consumer_name: str | None = None
    account_number: str | None = None
    billing_period: str | None = None
    billing_period_start: date | None = None
    billing_period_end: date | None = None
    due_date: date | None = None
    previous_reading: Decimal | None = None
    current_reading: Decimal | None = None
    units_consumed: Decimal | None = None
    net_amount_due: Decimal | None = None
    # Optional helper fields used by the meter-reading check
    multiplying_factor: Decimal | None = None  # meter multiplier / CT-PT ratio / meter constant
    open_access_units: Decimal | None = None   # units wheeled via open access, not billed as grid consumption

    parser_name: str = ""
    # field -> how it was found, e.g. "key_value:Consumer No." / "table:Previous Reading"
    sources: dict[str, str] = field(default_factory=dict)
    # Transparency notes from the parser, e.g. how a TOD total or a date's year was derived
    notes: list[str] = field(default_factory=list)

    def missing_fields(self) -> list[str]:
        return [f for f in REQUIRED_FIELDS if getattr(self, f) is None]

    def to_dict(self) -> dict:
        return asdict(self)


class BaseBillParser(ABC):
    """Subclass for a specific utility when the generic parser is not enough.

    `detect` decides whether this parser should handle a document (e.g. by
    looking for the utility's name); `parse` returns a ParsedBill with None
    for anything that could not be read confidently.
    """

    name: str = "base"

    @classmethod
    def detect(cls, text: str) -> bool:
        return False

    @abstractmethod
    def parse(self, raw_text: str) -> ParsedBill: ...

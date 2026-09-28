"""Label aliases per field, in priority order (most specific first).

Different state utilities print the same field under different labels.
Add new labels here, or without code changes via a JSON file referenced
by LABEL_ALIASES_FILE, e.g. {"due_date": ["Pay Till"], "account_number": ["BP No"]}.
Extra aliases are tried before the built-in ones.

Matching is case-insensitive, whitespace-tolerant, and a '.' in an alias is
optional ("Consumer No." also matches "Consumer No" / "ConsumerNo").
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

FIELD_ALIASES: dict[str, list[str]] = {
    "consumer_name": [
        "Name of Consumer", "Name of the Consumer", "Consumer Name", "Customer Name",
        "Name of Customer", "Name of the Firm", "Name of Firm", "Registered Consumer", "Consumer's Name", "Customer",
        "Consumer", "Name",
    ],
    "account_number": [
        "Consumer Number", "Consumer No.", "Account Number", "Account No.", "Acct No.",
        "CA Number", "CA No.", "Contract Account No.", "K Number", "K No.",
        "Service Connection Number", "Service Connection No.", "Service Number",
        "Service No.", "SC No.", "Connection No.", "BP Number", "BP No.",
        "Consumer ID", "Customer ID", "Account ID", "R.R. No.", "RR No.", "USC No.",
    ],
    "billing_period": [
        "Billing Period", "Bill Period", "Billing Cycle", "Bill Cycle", "Billing Month",
        "Bill Month", "Bill for the Month", "Month of Bill", "Period",
    ],
    "due_date": [
        "Payment Due Date", "Due Date for Payment", "Due Date Of Payment", "Due Date", "Last Date of Payment",
        "Last Date for Payment", "Last Date", "Pay By", "Pay Before", "Pay On or Before",
    ],
    "previous_reading": [
        "Previous Meter Reading", "Previous Reading", "Prev. Meter Reading", "Prev. Reading",
        "Prev. Rdg.", "Opening Reading", "Past Reading", "Last Reading", "Initial Reading",
        "Previous", "Prev.", "Opening",
    ],
    "current_reading": [
        "Current Meter Reading", "Current Reading", "Present Meter Reading", "Present Reading",
        "Curr. Reading", "Curr. Rdg.", "Closing Reading", "Final Reading", "Current",
        "Present", "Curr.", "Closing",
    ],
    "units_consumed": [
        "Total Units Consumed", "Units Consumed", "Energy Consumption", "Units Billed",
        "Billed Units", "Net Units", "Total Units", "Total Consumption", "Consumption",
        "Units", "kWh",
    ],
    "net_amount_due": [
        "Net Amount Due", "Net Amount Payable", "Net Payable Amount", "Net Payable",
        "Total Amount Payable", "Total Amount Due", "Amount Payable", "Payable Amount",
        "Amount Due", "Total Bill Amount", "Bill Amount", "Total Amount", "Net Amount",
    ],
    "multiplying_factor": [
        "Multiplying Factor", "Multiplication Factor", "Meter Multiplier", "Multiplier", "MF", "M.F.",
    ],
}

# Aliases that are too generic to be trusted in "Label: value" form; they are
# only used as table column headers (a header row directly above values).
TABLE_ONLY_ALIASES: set[str] = {
    "previous", "prev.", "opening", "current", "present", "curr.", "closing", "units", "kwh",
    "mf", "m.f.",
}


def alias_pattern(alias: str) -> str:
    """Regex for an alias: flexible whitespace, optional dots, optional apostrophes."""
    parts = []
    for word in alias.split():
        w = re.escape(word).replace(r"\.", r"\.?").replace("'", "'?")
        parts.append(w)
    return r"\s*".join(parts)


def _load_extra(path: str | None) -> dict[str, list[str]]:
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return {k: [str(a) for a in v] for k, v in data.items() if k in FIELD_ALIASES}
    except Exception:
        logger.exception("Could not load LABEL_ALIASES_FILE=%s; using built-in aliases", path)
        return {}


@lru_cache
def get_aliases(extra_file: str | None = None) -> dict[str, list[str]]:
    extra = _load_extra(extra_file)
    return {field: extra.get(field, []) + aliases for field, aliases in FIELD_ALIASES.items()}

"""Label-driven parser that works across utilities without coordinates.

Two strategies, tried in this order for every field:

1. key/value  - "<label> [(unit)] [:|=|-] <value>" on one line, where the
   label starts a text cell (line start or after a column gap). The value
   is the next cell, or the next line if the label ends with a separator.
2. table      - a row of column headers ("Previous Reading  Current Reading")
   with the value in the same column on one of the following lines. Also
   handles transposed tables where the row is labelled ("Reading  4870  5320").

Each candidate value must pass a strict type parser; otherwise the search
continues and the field ends up None (never guessed).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.services.parsing.aliases import TABLE_ONLY_ALIASES, alias_pattern, get_aliases
from app.services.parsing.base import BaseBillParser, ParsedBill
from app.services.parsing.normalization import (
    normalize_text,
    parse_account_number,
    parse_amount,
    parse_billing_period,
    parse_date,
    parse_name,
    parse_number,
    split_cells,
)

ValueParser = Callable[[str], Any]

_UNIT_SUFFIX = r"(?:\s*\((?:kwh|kvah|units?|rs\.?|₹|inr|in rs\.?|in ₹)\))?"
_SEPARATOR = r"\s*(?P<sep>:-|:|=|-(?!\d)|\.(?!\d))?\s*"
# Row labels in transposed tables that identify a meter-reading row
_READING_ROW_RE = re.compile(r"read|rdg|kwh|units?|consumption", re.IGNORECASE)
_DATE_ROW_RE = re.compile(r"\bdate\b|\bdt\b", re.IGNORECASE)
# Words that mark a cell as a header/label rather than a value
_ALPHA_RE = re.compile(r"[A-Za-z]{3,}")
# Column-numbering rows under table headers: "1  2  3  4  (3-4)=5  6  (5 x 6)=7"
_COLUMN_NUMBER_RE = re.compile(r"^\(?\d{1,2}(?:\s*[-+x*]\s*\d{1,2})*\)?(?:\s*=\s*\d{1,2})?$", re.IGNORECASE)
# Payment instructions print the *utility's* bank account; never take an account number from there
_BANK_CONTEXT_RE = re.compile(r"\b(?:ifsc|rtgs|neft|beneficiary|bank)\b", re.IGNORECASE)
# Single generic words are too ambiguous as table headers for a name ("Consumer  Mob No.")
_NAME_HEADER_MIN_WORDS = 2


class GenericBillParser(BaseBillParser):
    name = "generic"

    # field -> (value parser, label must be followed by an explicit separator,
    #           also try the remainder of the line when the first cell fails)
    FIELD_SPECS: dict[str, tuple[ValueParser, bool, bool]] = {
        "consumer_name": (parse_name, True, False),
        "account_number": (parse_account_number, False, False),
        "billing_period": (parse_billing_period, False, True),
        "due_date": (parse_date, False, False),
        "previous_reading": (parse_number, False, False),
        "current_reading": (parse_number, False, False),
        "units_consumed": (parse_number, False, False),
        "net_amount_due": (parse_amount, False, False),
        "multiplying_factor": (parse_number, False, False),
    }

    def __init__(self, extra_aliases_file: str | None = None):
        self.aliases = get_aliases(extra_aliases_file)
        self._kv_patterns: dict[str, list[tuple[str, re.Pattern[str]]]] = {}
        self._header_patterns: dict[str, list[tuple[str, re.Pattern[str]]]] = {}
        for fld, aliases in self.aliases.items():
            self._kv_patterns[fld] = [
                (a, re.compile(rf"(?:^\s*|(?<=\s\s)){alias_pattern(a)}(?![A-Za-z]){_UNIT_SUFFIX}{_SEPARATOR}",
                               re.IGNORECASE))
                for a in aliases if a.lower() not in TABLE_ONLY_ALIASES
            ]
            self._header_patterns[fld] = [
                (a, re.compile(rf"^{alias_pattern(a)}{_UNIT_SUFFIX}\s*:?$", re.IGNORECASE))
                for a in aliases
                if fld != "consumer_name" or len(a.split()) >= _NAME_HEADER_MIN_WORDS
            ]

    # ------------------------------------------------------------ public
    def parse(self, raw_text: str) -> ParsedBill:
        lines = normalize_text(raw_text).split("\n")
        bill = ParsedBill(parser_name=self.name)
        for fld, (parser, require_sep, try_rest) in self.FIELD_SPECS.items():
            found = self._find_key_value(lines, fld, parser, require_sep, try_rest)
            if found is None:
                found = self._find_in_table(lines, fld, parser)
            if found is None:
                continue
            value, source = found
            if fld == "billing_period":
                bill.billing_period, bill.billing_period_start, bill.billing_period_end = value
            else:
                setattr(bill, fld, value)
            bill.sources[fld] = source
        return bill

    # --------------------------------------------------------- key/value
    def _find_key_value(self, lines: list[str], fld: str, parser: ValueParser,
                        require_sep: bool, try_rest: bool) -> tuple[Any, str] | None:
        for alias, pattern in self._kv_patterns[fld]:
            for idx, line in enumerate(lines):
                for m in pattern.finditer(line):
                    if require_sep and not m.group("sep"):
                        continue
                    if fld == "account_number" and _BANK_CONTEXT_RE.search(" ".join(lines[max(0, idx - 2): idx + 1])):
                        continue
                    rest = line[m.end():]
                    candidates: list[str] = []
                    cells = split_cells(rest)
                    if len(cells) >= 3 and all(parser(c[2]) is not None for c in cells[:3]):
                        continue  # "Bill Month  202507  202506  202505": a history series, not this bill
                    if cells:
                        # value must start right after the label (or one column gap later)
                        candidates.append(cells[0][2])
                        if try_rest:
                            candidates.append(rest.strip())
                    elif m.group("sep"):
                        nxt = self._next_nonblank(lines, idx)
                        if nxt is not None:
                            first = split_cells(nxt)
                            if first:
                                candidates.append(first[0][2])
                    for cand in candidates:
                        value = parser(cand)
                        if value is not None:
                            return value, f"key_value:{alias}"
        return None

    @staticmethod
    def _next_nonblank(lines: list[str], idx: int) -> str | None:
        for line in lines[idx + 1: idx + 3]:
            if line.strip():
                return line
        return None

    # ------------------------------------------------------------- table
    def _header_alias(self, fld: str, cell: str) -> str | None:
        for alias, pattern in self._header_patterns[fld]:
            if pattern.match(cell.strip()):
                return alias
        return None

    def _find_in_table(self, lines: list[str], fld: str, parser: ValueParser) -> tuple[Any, str] | None:
        for i, line in enumerate(lines):
            headers = split_cells(line)
            if len(headers) < 2:
                continue
            for col, (hs, he, htext) in enumerate(headers):
                alias = self._header_alias(fld, htext)
                if alias is None:
                    continue
                generic_header = alias.lower() in TABLE_ONLY_ALIASES
                value = self._value_below(lines, i, headers, col, parser, generic_header)
                if value is not None:
                    return value, f"table:{alias}"
        return None

    def _value_below(self, lines: list[str], header_idx: int, headers: list[tuple[int, int, str]],
                     col: int, parser: ValueParser, generic_header: bool) -> Any:
        left_edge = headers[0][0]
        for j in range(header_idx + 1, min(header_idx + 6, len(lines))):
            row = split_cells(lines[j])
            if not row:
                continue
            if all(_COLUMN_NUMBER_RE.match(c[2]) for c in row):
                continue  # "1  2  3  (3-4)=5" column numbering, not values
            # Cells entirely left of the first header are row labels (transposed tables)
            label_cells = [c for c in row if c[1] <= left_edge]
            value_cells = [c for c in row if c[1] > left_edge]
            row_label = " ".join(c[2] for c in label_cells)
            if row_label and _DATE_ROW_RE.search(row_label):
                continue
            # Bare "Previous"/"Present" headers: a labelled row must say it holds
            # readings ("Reading", "kWh"); unlabelled rows are accepted.
            if generic_header and row_label and not _READING_ROW_RE.search(row_label):
                continue
            cell = self._cell_under(value_cells, headers, col)
            if cell is None:
                if self._looks_like_text_row(row):
                    return None  # reached another header/section
                continue
            value = parser(cell)
            if value is not None:
                return value
            if self._looks_like_text_row(row):
                return None
        return None

    @staticmethod
    def _looks_like_text_row(row: list[tuple[int, int, str]]) -> bool:
        return all(_ALPHA_RE.search(c[2]) for c in row)

    @staticmethod
    def _cell_under(value_cells: list[tuple[int, int, str]], headers: list[tuple[int, int, str]],
                    col: int) -> str | None:
        """Return the value cell that belongs to header column `col`.

        Each value cell is assigned to the header it overlaps most, or else
        the header with the nearest centre, so right-aligned numbers still
        land in the right column and never in a neighbour's.
        """
        best: tuple[float, str] | None = None
        for vs, ve, vtext in value_cells:
            vc = (vs + ve) / 2
            scores = []
            for k, (hs, he, _) in enumerate(headers):
                overlap = min(ve, he) - max(vs, hs)
                dist = abs(vc - (hs + he) / 2)
                scores.append((overlap if overlap > 0 else 0, -dist, k))
            owner = max(scores)
            if owner[2] != col:
                continue
            # Don't accept a cell far away from its header (e.g. a trailing note)
            hs, he, _ = headers[col]
            if owner[0] <= 0 and -owner[1] > max(8, (he - hs)):
                continue
            key = (owner[0], owner[1])
            if best is None or key > best[0]:
                best = (key, vtext)  # type: ignore[assignment]
        return best[1] if best else None

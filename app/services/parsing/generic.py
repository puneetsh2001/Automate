"""Label-driven parser that works across utilities without coordinates.

Three strategies, tried in this order for every field:

1. key/value  - "<label> [(unit)] [:|=|-] <value>" on one line, where the
   label starts a text cell (line start, after a column gap, or after the
   '/' of a compound label like "Account / Consumer No."). The value is the
   next cell, or the next line if the label ends with a separator.
2. table      - a row of column headers ("Previous Reading  Current Reading")
   with the value in the same column on one of the following lines. Also
   handles transposed tables where the row is labelled ("Reading  4870  5320")
   and, for the consumer name, a label printed above its value ("BILLED TO").
3. inference  - consumer name only, when the bill prints no label for it:
   an organisation name recognised by its form ("M/S ...", "... Pvt. Ltd.").

Each candidate value must pass a strict type parser; otherwise the search
continues and the field ends up None (never guessed).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.services.parsing.aliases import TABLE_ONLY_ALIASES, alias_pattern, get_aliases
from app.services.parsing.base import REQUIRED_FIELDS, BaseBillParser, ParsedBill
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
Cell = tuple[int, int, str]

# "(kWh)", "(Rs.)", and the rupee sign as OCR reads it: "(2)", "(%)", "(z)"
_UNIT_SUFFIX = r"(?:\s*\([^()\n]{1,8}\))?"
_CELL_START = r"(?:^\s*|(?<=\s\s)|(?<=/\s)|(?<=/))"
_SEPARATOR = r"(?P<gap>\s*)(?P<sep>:-|:|=|-(?!\d)|\.(?!\d))?\s*"
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
# A value this many characters away from every header column is not part of the table
_SKIP_VALUE_COST = 40

# --- consumer name
# Without an explicit ':' the text after "Consumer Name" may be the next label of a
# header row ("Consumer Name   Tariff Category"); names never contain these words.
_LABEL_WORDS_RE = re.compile(
    r"\b(?:tariff|category|status|load|demand|meter|reading|period|date|month|bill|billing|account|number|no|"
    r"address|amount|units?|consumption|voltage|contract|sanctioned|connection|due|previous|current|present|"
    r"mobile|phone|e-?mail|type|class|code|circle|division|details|particulars)\b",
    re.IGNORECASE,
)
_ORG_PREFIX_RE = re.compile(r"^(?:M/s|Messrs)\.?\s*[A-Za-z]", re.IGNORECASE)
_LEGAL_SUFFIX_RE = re.compile(r"\b(?:ltd|limited|llp|inc|pvt|private)\.?$", re.IGNORECASE)
_COMPLETE_NAME_RE = re.compile(r"\b(?:ltd|limited|llp|inc)\.?$", re.IGNORECASE)
# Second line of a wrapped company name: "M/S Western Ghats Pharma Pvt." / "Ltd."
_NAME_TAIL_RE = re.compile(
    r"^(?:(?:\(?(?:india|p)\)?|pvt|private|ltd|limited|llp|co|company|corporation|inc)\.?\s*)+$", re.IGNORECASE
)
# The utility's own name on the letterhead is never the consumer
_UTILITY_WORDS_RE = re.compile(
    r"\b(?:vidyut|vitr?an|vitaran|bijli|power|electricity|electric|energy|grid|distribution|discoms?|nigam|"
    r"transmission|urja|supply|board|escoms?|utility|utilities)\b",
    re.IGNORECASE,
)
# Only infer an unlabelled name on documents that are clearly bills
_MIN_FIELDS_FOR_NAME_INFERENCE = 3
_LETTERHEAD_LINES = 2


def _parse_unlabelled_name(value: str | None) -> str | None:
    """parse_name for values not introduced by ':' (column gap / header above)."""
    name = parse_name(value)
    if name is None:
        return None
    if _ORG_PREFIX_RE.match(name) or not _LABEL_WORDS_RE.search(name):
        return name
    return None


class GenericBillParser(BaseBillParser):
    name = "generic"

    # field -> (value parser, label must be followed by an explicit separator,
    #           also try the remainder of the line when the first cell fails)
    # For the consumer name a column gap also counts as a separator after a
    # multi-word label ("Consumer Name     M/S ..."); single words ("Name") need ':'.
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
        self._full_patterns: dict[str, list[re.Pattern[str]]] = {}
        for fld, aliases in self.aliases.items():
            self._kv_patterns[fld] = [
                (a, re.compile(rf"{_CELL_START}{alias_pattern(a)}(?![A-Za-z]){_UNIT_SUFFIX}{_SEPARATOR}",
                               re.IGNORECASE))
                for a in aliases if a.lower() not in TABLE_ONLY_ALIASES
            ]
            specific = [a for a in aliases if fld != "consumer_name" or len(a.split()) >= _NAME_HEADER_MIN_WORDS]
            self._header_patterns[fld] = [
                (a, re.compile(rf"^{alias_pattern(a)}{_UNIT_SUFFIX}\s*[:.]?$", re.IGNORECASE)) for a in specific
            ]
            self._full_patterns[fld] = [re.compile(rf"^{alias_pattern(a)}$", re.IGNORECASE) for a in specific]

    # ------------------------------------------------------------ public
    def parse(self, raw_text: str) -> ParsedBill:
        lines = normalize_text(raw_text).split("\n")
        bill = ParsedBill(parser_name=self.name)
        for fld, (parser, require_sep, try_rest) in self.FIELD_SPECS.items():
            found = self._find_key_value(lines, fld, parser, require_sep, try_rest)
            if found is None:
                table_parser = _parse_unlabelled_name if fld == "consumer_name" else parser
                found = self._find_in_table(lines, fld, table_parser)
            if found is None:
                continue
            value, source, line_idx, cols = found
            if fld == "billing_period":
                bill.billing_period, bill.billing_period_start, bill.billing_period_end = value
            else:
                if fld == "consumer_name":
                    value = self._join_wrapped_name(lines, value, line_idx, cols)
                setattr(bill, fld, value)
            bill.sources[fld] = source
        if bill.consumer_name is None:
            self._infer_consumer_name(lines, bill)
        return bill

    # --------------------------------------------------------- key/value
    def _find_key_value(self, lines: list[str], fld: str, parser: ValueParser,
                        require_sep: bool, try_rest: bool) -> tuple[Any, str, int, tuple[int, ...]] | None:
        for alias, pattern in self._kv_patterns[fld]:
            gap_is_separator = fld == "consumer_name" and len(alias.split()) >= 2
            for idx, line in enumerate(lines):
                for m in pattern.finditer(line):
                    has_sep = bool(m.group("sep"))
                    gap_only = not has_sep and gap_is_separator and len(m.group("gap")) >= 2
                    if require_sep and not has_sep and not gap_only:
                        continue
                    if fld == "account_number" and _BANK_CONTEXT_RE.search(" ".join(lines[max(0, idx - 2): idx + 1])):
                        continue
                    if self._compound_conflict(line, m.start(), fld):
                        continue
                    label_col = m.start() + len(m.group()) - len(m.group().lstrip())
                    value_parser = _parse_unlabelled_name if gap_only else parser
                    rest = line[m.end():]
                    candidates: list[tuple[str, int, int]] = []  # (text, line index, start column)
                    cells = split_cells(rest)
                    if len(cells) >= 3 and all(parser(c[2]) is not None for c in cells[:3]):
                        continue  # "Bill Month  202507  202506  202505": a history series, not this bill
                    if cells:
                        # value must start right after the label (or one column gap later)
                        candidates.append((cells[0][2], idx, m.end() + cells[0][0]))
                        if try_rest:
                            candidates.append((rest.strip(), idx, m.end() + cells[0][0]))
                    elif has_sep:
                        nxt = self._next_nonblank(lines, idx)
                        if nxt is not None:
                            first = split_cells(lines[nxt])
                            if first:
                                candidates.append((first[0][2], nxt, first[0][0]))
                    for text, line_idx, col in candidates:
                        value = value_parser(text)
                        if value is not None:
                            return value, f"key_value:{alias}", line_idx, (label_col, col)
        return None

    @staticmethod
    def _next_nonblank(lines: list[str], idx: int) -> int | None:
        for j in range(idx + 1, min(idx + 3, len(lines))):
            if lines[j].strip():
                return j
        return None

    def _label_fields(self, text: str) -> set[str]:
        """Fields that have `text` as one of their labels."""
        text = text.strip(" :.")
        return {fld for fld, patterns in self._full_patterns.items() if any(p.match(text) for p in patterns)}

    def _compound_conflict(self, line: str, start: int, fld: str) -> bool:
        """A label after '/' whose cell also names another field: 'Previous/Current Reading'.

        'Account / Consumer No.' or 'Initial / Previous' are synonyms and fine;
        two different fields in one cell leave it unclear which value is which.
        """
        before = line[:start].rstrip()
        if not before.endswith("/"):
            return False
        cells = split_cells(before)
        parts = cells[-1][2].rstrip("/").split("/") if cells else []
        return any(self._label_fields(p) - {fld} for p in parts if p.strip())

    # ------------------------------------------------------------- table
    def _header_alias(self, fld: str, cell: str) -> str | None:
        cell = cell.strip()
        for alias, pattern in self._header_patterns[fld]:
            if pattern.match(cell):
                return alias
        # Compound headers: "Initial / Previous", "Final / Current", "Difference / Units"
        parts = [p.strip() for p in cell.split("/")]
        if len(parts) < 2 or any(self._label_fields(p) - {fld} for p in parts if p):
            return None
        for part in parts:
            for alias, pattern in self._header_patterns[fld]:
                if pattern.match(part):
                    return alias
        return None

    def _find_in_table(self, lines: list[str], fld: str,
                       parser: ValueParser) -> tuple[Any, str, int, tuple[int, ...]] | None:
        # A name label can stand alone above its value ("Consumer Name & Address" / "M/S ...")
        min_cells = 1 if fld == "consumer_name" else 2
        for i, line in enumerate(lines):
            headers = split_cells(line)
            if len(headers) < min_cells:
                continue
            for col, (hs, he, htext) in enumerate(headers):
                alias = self._header_alias(fld, htext)
                if alias is None:
                    continue
                generic_header = alias.lower() in TABLE_ONLY_ALIASES
                found = self._value_below(lines, i, headers, col, parser, generic_header)
                if found is not None:
                    value, row_idx, cell_start = found
                    return value, f"table:{alias}", row_idx, (hs, cell_start)
        return None

    def _value_below(self, lines: list[str], header_idx: int, headers: list[Cell],
                     col: int, parser: ValueParser, generic_header: bool) -> tuple[Any, int, int] | None:
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
            cell = self._align(value_cells, headers).get(col)
            if cell is None:
                if self._looks_like_text_row(row):
                    return None  # reached another header/section
                continue
            value = parser(cell[2])
            if value is not None:
                return value, j, cell[0]
            if self._looks_like_text_row(row):
                return None
        return None

    @staticmethod
    def _looks_like_text_row(row: list[Cell]) -> bool:
        return all(_ALPHA_RE.search(c[2]) for c in row)

    @staticmethod
    def _align_cost(value: Cell, header: Cell) -> float:
        """How far a value cell sits from a header column under left, centre or right alignment."""
        vs, ve, _ = value
        hs, he, _ = header
        if (hs <= vs and ve <= he) or (vs <= hs and he <= ve):
            return 0.0
        return min(abs(vs - hs), abs(ve - he), abs((vs + ve) / 2 - (hs + he) / 2))

    @classmethod
    def _align(cls, value_cells: list[Cell], headers: list[Cell]) -> dict[int, Cell]:
        """Assign value cells to header columns -> {header index: cell}.

        Order-preserving alignment with minimal total misalignment: values
        stay in left-to-right order, a header may have no value (empty cell),
        and a value far from every header is left out. Right-aligned numbers
        under left-aligned headers therefore still land in their own column
        instead of drifting into the neighbour's.
        """
        n, m = len(value_cells), len(headers)
        # dp[i][j]: best cost placing the first i values under the first j headers
        dp = [[(0.0, 0)] * (m + 1)] + [[(float("inf"), 0)] * (m + 1) for _ in range(n)]
        step: list[list[str]] = [[""] * (m + 1) for _ in range(n + 1)]
        for i in range(1, n + 1):
            dp[i][0], step[i][0] = (i * _SKIP_VALUE_COST, 0), "skip_value"
            for j in range(1, m + 1):
                # ties prefer matching, then leaving a header empty
                dp[i][j], step[i][j] = min(
                    ((dp[i - 1][j - 1][0] + cls._align_cost(value_cells[i - 1], headers[j - 1]), 0), "match"),
                    ((dp[i][j - 1][0], 1), "skip_header"),
                    ((dp[i - 1][j][0] + _SKIP_VALUE_COST, 2), "skip_value"),
                    key=lambda option: option[0],
                )
        assigned: dict[int, Cell] = {}
        i, j = n, m
        while i > 0 and j > 0:
            if step[i][j] == "match":
                assigned[j - 1] = value_cells[i - 1]
                i, j = i - 1, j - 1
            elif step[i][j] == "skip_header":
                j -= 1
            else:
                i -= 1
        return assigned

    @classmethod
    def _cell_under(cls, value_cells: list[Cell], headers: list[Cell], col: int) -> str | None:
        """Text of the value cell that belongs to header column `col`, if any."""
        cell = cls._align(value_cells, headers).get(col)
        return cell[2] if cell else None

    # ----------------------------------------------------- consumer name
    @staticmethod
    def _join_wrapped_name(lines: list[str], name: str, line_idx: int, cols: tuple[int, ...]) -> str:
        """Append a company suffix wrapped onto the next line ('... Pharma Pvt.' / 'Ltd.')."""
        if _COMPLETE_NAME_RE.search(name) or line_idx + 1 >= len(lines):
            return name
        # Join the printed text, not the parsed name, so 'Pvt.' keeps its dot
        value_cell = split_cells(lines[line_idx][cols[-1]:])
        base = value_cell[0][2] if value_cell and parse_name(value_cell[0][2]) == name else name
        for start, _, text in split_cells(lines[line_idx + 1]):
            if any(abs(start - c) <= 2 for c in cols) and _NAME_TAIL_RE.match(text):
                return parse_name(f"{base} {text}") or name
        return name

    def _infer_consumer_name(self, lines: list[str], bill: ParsedBill) -> None:
        """Consumer name for bills that print it without any label.

        Taken only on documents that are recognisably bills, from a cell that
        reads as an organisation: "M/S <name>" (utilities never address
        themselves that way), else a name ending in Ltd/Limited/Pvt/LLP that is
        not the utility's own letterhead.
        """
        found = sum(getattr(bill, f) is not None for f in REQUIRED_FIELDS if f != "consumer_name")
        if found < _MIN_FIELDS_FOR_NAME_INFERENCE:
            return
        best: tuple[str, str, int, int] | None = None  # (name, source, line index, column)
        nonblank = 0
        for idx, line in enumerate(lines):
            if not line.strip():
                continue
            nonblank += 1
            if _BANK_CONTEXT_RE.search(line):
                continue
            for start, _, text in split_cells(line):
                name = parse_name(text)
                if name is None:
                    continue
                if _ORG_PREFIX_RE.match(name):
                    best = (name, "inferred:M/S name", idx, start)
                    break
                if (best is None and nonblank > _LETTERHEAD_LINES and _LEGAL_SUFFIX_RE.search(name)
                        and len(name.split()) >= 2 and not _UTILITY_WORDS_RE.search(name)
                        and not _LABEL_WORDS_RE.search(name)):
                    best = (name, "inferred:company name", idx, start)
            if best and best[1] == "inferred:M/S name":
                break
        if best is None:
            return
        name, source, idx, start = best
        bill.consumer_name = self._join_wrapped_name(lines, name, idx, (start,))
        bill.sources["consumer_name"] = source
        bill.notes.append("The bill prints no 'Consumer Name' label; the name was identified "
                          "from the company name on the bill - please verify")

"""Run the full extraction pipeline over reference bills and compare with ground truth.

Every bill file (pdf/png/jpg/jpeg) with a sibling `<name>.expected.json` is
evaluated. Put real bills + expected JSON in sample_bills/private/ (git-ignored).

Usage:
    python scripts/evaluate_reference_bills.py                 # all sample_bills/**
    python scripts/evaluate_reference_bills.py path/to/dir -v  # -v prints raw OCR text
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.bill_parser import BillParser  # noqa: E402
from app.services.document_processor import DocumentProcessor  # noqa: E402
from app.services.validation_service import ValidationService  # noqa: E402
from app.utils.file_utils import sniff_file_type  # noqa: E402

FIELDS = ["consumer_name", "account_number", "billing_period", "due_date", "previous_reading",
          "current_reading", "units_consumed", "net_amount_due", "validation_status"]
BILL_EXTS = {".pdf", ".png", ".jpg", ".jpeg"}


def comparable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return float(value)
    return value


def evaluate(bill_path: Path, expected: dict, processor, parser, validator, verbose: bool) -> tuple[int, int]:
    data = bill_path.read_bytes()
    doc = processor.process(data, sniff_file_type(data))
    parsed = parser.parse(doc.text)
    validation = validator.validate(parsed, doc.ocr_confidence)
    actual = {f: getattr(parsed, f, None) for f in FIELDS}
    actual["validation_status"] = validation.status.value

    print(f"\n=== {bill_path.name}  (method={doc.method}, pages={len(doc.pages)}, "
          f"ocr_conf={doc.ocr_confidence}, {doc.duration_ms} ms)")
    if verbose:
        print("--- raw text ---\n" + doc.text + "\n----------------")
    ok = 0
    checked = 0
    for f in FIELDS:
        if f not in expected:
            continue
        checked += 1
        got, exp = comparable(actual[f]), comparable(expected[f])
        match = got == exp
        ok += match
        print(f"  {'PASS' if match else 'FAIL'}  {f:18} got={got!r}" + ("" if match else f"  expected={exp!r}"))
    for w in validation.errors + validation.warnings:
        print(f"        note: {w}")
    return ok, checked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("directory", nargs="?", default=str(ROOT / "sample_bills"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    bills = sorted(p for p in Path(args.directory).rglob("*")
                   if p.suffix.lower() in BILL_EXTS and p.with_suffix(".expected.json").exists())
    if not bills:
        print(f"No bills with .expected.json found under {args.directory}")
        return 1
    processor, parser, validator = DocumentProcessor(), BillParser(), ValidationService()
    total_ok = total = 0
    for bill in bills:
        expected = json.loads(bill.with_suffix(".expected.json").read_text(encoding="utf-8"))
        ok, checked = evaluate(bill, expected, processor, parser, validator, args.verbose)
        total_ok += ok
        total += checked
    print(f"\nTOTAL: {total_ok}/{total} fields correct across {len(bills)} bills")
    return 0 if total_ok == total else 2


if __name__ == "__main__":
    sys.exit(main())

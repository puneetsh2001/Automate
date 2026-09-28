"""End-to-end accuracy check on every reference bill that has a ground-truth JSON.

Drop real bills into sample_bills/private/ with a matching
<name>.expected.json and they are picked up automatically.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.bill_parser import BillParser
from app.services.document_processor import DocumentProcessor
from app.services.validation_service import ValidationService
from app.utils.file_utils import sniff_file_type
from tests.conftest import SAMPLE_DIR, tesseract_available

FIELDS = ["consumer_name", "account_number", "billing_period", "due_date", "previous_reading",
          "current_reading", "units_consumed", "net_amount_due"]

BILLS = sorted(p for p in SAMPLE_DIR.rglob("*")
               if p.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg"}
               and p.with_suffix(".expected.json").exists())


def _cmp(v):
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, (Decimal, int, float)):
        return float(v)
    if isinstance(v, date):
        return v.isoformat()
    return v


@pytest.mark.parametrize("bill_path", BILLS, ids=lambda p: p.name)
def test_reference_bill(bill_path: Path):
    expected = json.loads(bill_path.with_suffix(".expected.json").read_text(encoding="utf-8"))
    if expected.get("extraction_method", "ocr") != "text" and not tesseract_available():
        pytest.skip("Tesseract OCR not installed")
    data = bill_path.read_bytes()
    doc = DocumentProcessor().process(data, sniff_file_type(data))
    parsed = BillParser(extra_aliases_file="").parse(doc.text)
    validation = ValidationService().validate(parsed, doc.ocr_confidence)

    mismatches = {f: (_cmp(getattr(parsed, f)), expected[f]) for f in FIELDS
                  if f in expected and _cmp(getattr(parsed, f)) != _cmp(expected[f])}
    assert not mismatches, f"got vs expected: {mismatches}\n--- text ---\n{doc.text}"
    if "validation_status" in expected:
        assert validation.status.value == expected["validation_status"]
    if "extraction_method" in expected:
        assert doc.method == expected["extraction_method"]

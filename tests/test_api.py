import io

import fitz
import pytest
from PIL import Image, ImageDraw, ImageFont

from tests.conftest import requires_tesseract


def upload(client, name, data, content_type):
    return client.post("/api/bills/upload", files={"file": (name, data, content_type)})


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["database"] == "ok"


def test_frontend_is_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Electricity Bill OCR" in r.text


def test_openapi_documents_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    for p in ["/api/bills/upload", "/api/bills", "/api/bills/{bill_id}",
              "/api/bills/{bill_id}/raw-text", "/api/bills/{bill_id}/tariff-input", "/health"]:
        assert p in paths


def test_upload_text_pdf_full_lifecycle(client, sample_dir, settings):
    data = (sample_dir / "bill_01_text_form.pdf").read_bytes()
    r = upload(client, "my bill.pdf", data, "application/pdf")
    assert r.status_code == 201, r.text
    bill = r.json()
    assert bill["consumer_name"] == "RAMESH KUMAR SHARMA"
    assert bill["account_number"] == "170012345678"
    assert bill["billing_period"] == "2025-05-01 to 2025-05-31"
    assert bill["due_date"] == "2025-06-16"
    assert bill["previous_reading"] == 10250
    assert bill["current_reading"] == 10600
    assert bill["units_consumed"] == 350
    assert bill["net_amount_due"] == 2450.5
    assert bill["validation_status"] == "VALID"
    assert bill["validation_details"]["meter_reading_check"] is True
    assert bill["warnings"] == []
    assert bill["extraction_method"] == "text"
    assert "Net Amount Due" in bill["raw_ocr_text"]
    assert bill["original_filename"] == "my bill.pdf"
    # server paths / stored names are never exposed
    assert "stored_filename" not in bill
    assert str(settings.upload_path) not in r.text
    assert len(list(settings.upload_path.glob("*.pdf"))) >= 1

    bill_id = bill["id"]
    assert client.get(f"/api/bills/{bill_id}").json()["account_number"] == "170012345678"

    listing = client.get("/api/bills", params={"validation_status": "VALID"}).json()
    assert any(item["id"] == bill_id for item in listing["items"])
    assert "raw_ocr_text" not in listing["items"][0]

    raw = client.get(f"/api/bills/{bill_id}/raw-text").json()
    assert raw["extraction_method"] == "text" and "Consumer Name" in raw["raw_ocr_text"]

    tariff = client.get(f"/api/bills/{bill_id}/tariff-input").json()
    assert tariff == {
        "bill_id": bill_id, "account_number": "170012345678",
        "billing_period": "2025-05-01 to 2025-05-31", "billing_period_start": "2025-05-01",
        "billing_period_end": "2025-05-31", "units_consumed": 350.0, "net_amount_due": 2450.5,
        "validation_status": "VALID",
    }

    assert client.delete(f"/api/bills/{bill_id}").status_code == 204
    assert client.get(f"/api/bills/{bill_id}").status_code == 404


def test_get_missing_bill_returns_404(client):
    r = client.get("/api/bills/999999")
    assert r.status_code == 404
    assert r.json()["error"] == "bill_not_found"


@pytest.mark.parametrize("name, data, ctype, status, code", [
    ("bill.txt", b"hello", "text/plain", 415, "unsupported_file_type"),
    ("bill.exe", b"MZ\x90\x00", "application/octet-stream", 415, "unsupported_file_type"),
    ("bill.pdf", b"", "application/pdf", 400, "empty_file"),
    ("bill.pdf", b"not a pdf at all", "application/pdf", 415, "unsupported_file_type"),
    ("bill.png", b"\x89PNG\r\n\x1a\nxxxx", "application/pdf", 415, "unsupported_file_type"),  # MIME mismatch
    ("bill", b"%PDF-1.4", "application/pdf", 415, "unsupported_file_type"),                   # no extension
])
def test_invalid_uploads_rejected(client, name, data, ctype, status, code):
    r = upload(client, name, data, ctype)
    assert r.status_code == status, r.text
    assert r.json()["error"] == code


def test_extension_content_mismatch(client):
    png = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(png, "PNG")
    r = upload(client, "bill.jpg", png.getvalue(), "application/octet-stream")
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_upload"


def test_path_traversal_filename_is_neutralised(client, sample_dir, settings):
    data = (sample_dir / "bill_01_text_form.pdf").read_bytes()
    r = upload(client, "..\\..\\evil/../../x.pdf", data, "application/pdf")
    assert r.status_code == 201
    assert r.json()["original_filename"] == "x.pdf"
    assert not (settings.upload_path.parent / "x.pdf").exists()


def test_file_too_large(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_MB", 0.001)  # ~1 KB
    r = upload(client, "big.pdf", b"%PDF-1.4" + b"0" * 5000, "application/pdf")
    assert r.status_code == 413
    assert r.json()["error"] == "file_too_large"


def test_corrupt_pdf_returns_422_without_stack_trace(client, settings):
    before = set(settings.upload_path.glob("*"))
    r = upload(client, "broken.pdf", b"%PDF-1.4\n garbage without structure", "application/pdf")
    assert r.status_code == 422
    assert r.json()["error"] == "document_processing_failed"
    assert "Traceback" not in r.text
    # failed uploads don't leave files behind
    assert set(settings.upload_path.glob("*")) == before


def _text_pdf(lines: list[str]) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((50, 72), "\n".join(lines), fontsize=11)
    return doc.tobytes()


def test_bill_with_mismatch_is_stored_with_warning(client):
    pdf = _text_pdf([
        "Consumer Name: TEST USER", "Account No: 123456789",
        "Billing Period: 01/05/2025 to 31/05/2025", "Due Date: 15/06/2025",
        "Previous Reading: 1000", "Current Reading: 1350", "Units Consumed: 300",
        "Net Amount Due: Rs. 2,000.00",
    ])
    r = upload(client, "mismatch.pdf", pdf, "application/pdf")
    assert r.status_code == 201, r.text
    bill = r.json()
    assert bill["validation_status"] == "WARNING"
    assert bill["units_consumed"] == 300
    assert bill["validation_details"]["meter_reading_check"] is False
    assert bill["validation_details"]["calculated_units"] == 350
    assert any("do not reconcile" in w for w in bill["warnings"])
    # still retrievable from the database
    assert client.get(f"/api/bills/{bill['id']}").json()["validation_status"] == "WARNING"


def test_bill_with_missing_fields_lists_them(client):
    pdf = _text_pdf(["Consumer Name: ONLY NAME HERE", "Net Amount Due: Rs. 500.00",
                     "Some other text to make the page text layer long enough."])
    r = upload(client, "partial.pdf", pdf, "application/pdf")
    assert r.status_code == 201, r.text
    bill = r.json()
    assert bill["due_date"] is None and bill["account_number"] is None
    assert bill["validation_status"] == "WARNING"
    assert "due_date" in bill["validation_details"]["missing_fields"]
    assert "Due date could not be confidently extracted" in bill["warnings"]


def test_same_file_twice_is_rejected_and_can_be_replaced(client, sample_dir, settings):
    data = (sample_dir / "bill_01_text_form.pdf").read_bytes()
    first = upload(client, "Bill-2.pdf", data, "application/pdf")
    assert first.status_code == 201, first.text
    bill_id = first.json()["id"]
    stored_before = set(settings.upload_path.glob("*"))

    again = upload(client, "renamed.pdf", data, "application/pdf")   # matched by content, not name
    assert again.status_code == 409
    body = again.json()
    assert body["error"] == "duplicate_file"
    assert body["existing_bill_id"] == bill_id
    assert f"already uploaded as bill #{bill_id}" in body["message"]
    assert set(settings.upload_path.glob("*")) == stored_before      # nothing stored, no OCR run
    assert client.get("/api/bills").json()["total"] == 1

    replaced = client.post("/api/bills/upload", params={"replace": "true"},
                           files={"file": ("renamed.pdf", data, "application/pdf")})
    assert replaced.status_code == 200, replaced.text
    bill = replaced.json()
    assert bill["id"] == bill_id                                      # re-processed in place
    assert bill["original_filename"] == "renamed.pdf"
    assert bill["validation_status"] == "VALID"                       # not a duplicate of itself
    assert bill["validation_details"]["possible_duplicate_of"] == []
    assert client.get("/api/bills").json()["total"] == 1
    stored_after = set(settings.upload_path.glob("*"))
    assert len(stored_after) == len(stored_before) and stored_after != stored_before  # old file swapped out

    # Once deleted, the same file can be uploaded again
    assert client.delete(f"/api/bills/{bill_id}").status_code == 204
    assert upload(client, "Bill-2.pdf", data, "application/pdf").status_code == 201


def test_replace_with_a_new_file_just_creates_the_bill(client, sample_dir):
    data = (sample_dir / "bill_01_text_form.pdf").read_bytes()
    r = client.post("/api/bills/upload", params={"replace": "true"},
                    files={"file": ("bill.pdf", data, "application/pdf")})
    assert r.status_code == 201


def test_concurrent_upload_of_same_file_returns_409(client, sample_dir, monkeypatch):
    """Both requests pass the pre-check; the unique file_hash index stops the second insert."""
    from app.services.bill_service import BillService

    data = (sample_dir / "bill_01_text_form.pdf").read_bytes()
    first_id = upload(client, "a.pdf", data, "application/pdf").json()["id"]
    real_find = BillService._find_by_hash
    calls = []

    def pre_check_misses_concurrent_insert(self, file_hash):
        calls.append(file_hash)
        return None if len(calls) == 1 else real_find(self, file_hash)

    monkeypatch.setattr(BillService, "_find_by_hash", pre_check_misses_concurrent_insert)
    r = upload(client, "b.pdf", data, "application/pdf")
    assert r.status_code == 409, r.text
    assert r.json()["existing_bill_id"] == first_id
    assert client.get("/api/bills").json()["total"] == 1


MAY_BILL = ["Consumer Name: TEST USER", "Account No: 123456789", "Billing Period: 01/05/2025 to 31/05/2025",
            "Due Date: 15/06/2025", "Previous Reading: 1000", "Current Reading: 1350", "Units Consumed: 350",
            "Net Amount Due: Rs. 2,000.00"]


def test_same_bill_in_a_different_file_is_flagged_not_blocked(client):
    first = upload(client, "may.pdf", _text_pdf(MAY_BILL), "application/pdf").json()
    assert first["validation_status"] == "VALID"

    rescan = upload(client, "may-scan.pdf", _text_pdf(MAY_BILL + ["Scanned copy"]), "application/pdf")
    assert rescan.status_code == 201, rescan.text
    bill = rescan.json()
    assert bill["validation_status"] == "WARNING"
    assert bill["validation_details"]["possible_duplicate_of"] == [first["id"]]
    assert any(f"Possible duplicate of bill #{first['id']}" in w for w in bill["warnings"])

    june = [line.replace("01/05/2025 to 31/05/2025", "01/06/2025 to 30/06/2025").replace("15/06/2025", "15/07/2025")
            for line in MAY_BILL]
    r = upload(client, "june.pdf", _text_pdf(june), "application/pdf")
    assert r.json()["validation_status"] == "VALID"               # next month of the same account is fine


def _render_bill_image(fmt: str) -> bytes:
    img = Image.new("RGB", (1400, 800), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 30)
    except OSError:
        font = ImageFont.load_default(size=30)
    lines = ["Consumer Name : ASHA VERMA", "Account Number : 556677889900",
             "Bill Period : 01/04/2025 to 30/04/2025", "Due Date : 20/05/2025",
             "Previous Reading : 2000", "Current Reading : 2150", "Units Consumed : 150",
             "Net Amount Due : Rs. 1,234.00"]
    for i, line in enumerate(lines):
        draw.text((60, 60 + i * 80), line, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


@requires_tesseract
@pytest.mark.parametrize("fmt, name, ctype", [("PNG", "bill.png", "image/png"),
                                              ("JPEG", "bill.jpg", "image/jpeg"),
                                              ("JPEG", "bill.jpeg", "image/jpeg")])
def test_image_upload_ocr(client, fmt, name, ctype):
    r = upload(client, name, _render_bill_image(fmt), ctype)
    assert r.status_code == 201, r.text
    bill = r.json()
    assert bill["extraction_method"] == "ocr"
    assert bill["consumer_name"] == "ASHA VERMA"
    assert bill["account_number"] == "556677889900"
    assert bill["due_date"] == "2025-05-20"
    assert bill["units_consumed"] == 150
    assert bill["net_amount_due"] == 1234.0
    assert bill["validation_status"] == "VALID"


def test_ocr_errors_are_not_masked_as_generic_pdf_errors(client, monkeypatch):
    """Regression: an OCR timeout on a scanned PDF page was reported as 'Failed to read PDF page 1'."""
    from app.core.exceptions import OCRProcessingError
    from app.services.ocr_service import OCRService

    def slow_ocr(self, image):
        raise OCRProcessingError("OCR timed out after 300s")

    monkeypatch.setattr(OCRService, "extract", slow_ocr)
    doc = fitz.open()
    doc.new_page()  # blank page: no text layer, so it goes to OCR
    r = upload(client, "scan.pdf", doc.tobytes(), "application/pdf")
    assert r.status_code == 500
    assert r.json() == {"error": "ocr_failed", "message": "OCR timed out after 300s"}

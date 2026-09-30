import json
from datetime import date
from decimal import Decimal

import pytest

from app.services.bill_parser import BillParser
from app.services.parsing.generic import GenericBillParser
from app.services.parsing.normalization import (
    normalize_text,
    parse_account_number,
    parse_amount,
    parse_billing_period,
    parse_date,
    parse_name,
    parse_number,
)


@pytest.fixture(scope="module")
def parser():
    return BillParser(extra_aliases_file="")


# ------------------------------------------------------------ numbers
@pytest.mark.parametrize("raw, expected", [
    ("10250", Decimal("10250")),
    ("8,245", Decimal("8245")),
    ("2,110.5", Decimal("2110.5")),
    ("350 kWh", Decimal("350")),
    ("1O25O", Decimal("10250")),      # OCR: letter O for zero
    ("l350", Decimal("1350")),        # OCR: lowercase L for one
    ("abc", None),
    ("", None),
    (None, None),
    ("12/05/2025", None),             # a date is not a reading
])
def test_parse_number(raw, expected):
    assert parse_number(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("Rs. 2,450.50", Decimal("2450.50")),
    ("Rs 1,045.75", Decimal("1045.75")),
    ("₹ 3,812.00", Decimal("3812.00")),
    ("INR 1519", Decimal("1519.00")),
    ("= 5,126.00", Decimal("5126.00")),
    ("1,00,000.00", Decimal("100000.00")),   # Indian digit grouping
    ("& 5,126.00", Decimal("5126.00")),      # OCR junk for the rupee sign
    ("% 5,126.00", Decimal("5126.00")),
    ("-1,200.00", Decimal("-1200.00")),      # credit balance keeps its sign
    ("® 26,35,910.00", Decimal("2635910.00")),  # OCR reads '₹' as any symbol
    ("I 18,76,540.00", Decimal("1876540.00")),  # font without a '₹' glyph
    ("2450.505", None),                      # 3 decimals -> not a money value
    ("Rs.", None),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


# -------------------------------------------------------------- dates
@pytest.mark.parametrize("raw, expected", [
    ("16-06-2025", date(2025, 6, 16)),
    ("20/06/2025", date(2025, 6, 20)),
    ("05.06.2025", date(2025, 6, 5)),       # day-first (Indian format)
    ("25 May 2025", date(2025, 5, 25)),
    ("25-May-25", date(2025, 5, 25)),
    ("May 25, 2025", date(2025, 5, 25)),
    ("2025-06-16", date(2025, 6, 16)),
    ("3O-O4-2025", date(2025, 4, 30)),       # OCR letter O
    ("31-02-2025", None),                    # impossible date
    ("next week", None),
])
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("01-05-2025 to 31-05-2025", ("2025-05-01 to 2025-05-31", date(2025, 5, 1), date(2025, 5, 31))),
    ("12 Apr 2025 - 11 May 2025", ("2025-04-12 to 2025-05-11", date(2025, 4, 12), date(2025, 5, 11))),
    ("MAY-2025", ("May 2025", None, None)),
    ("05/2025", ("May 2025", None, None)),
    ("31-05-2025 to 01-05-2025", None),      # reversed range is not accepted
    ("sometime", None),
])
def test_parse_billing_period(raw, expected):
    assert parse_billing_period(raw) == expected


def test_parse_account_number_keeps_leading_zeros_and_fixes_ocr():
    assert parse_account_number("0098765432") == "0098765432"
    assert parse_account_number("45-678-901-234") == "45-678-901-234"
    assert parse_account_number("17OO12345678") == "170012345678"
    assert parse_account_number("ABCDEF") is None
    assert parse_account_number("") is None


def test_parse_account_number_accepts_structured_alphanumeric_ids():
    assert parse_account_number("CSPEC-HT-784521") == "CSPEC-HT-784521"
    assert parse_account_number("WGESC/IND/552190") == "WGESC/IND/552190"
    assert parse_account_number("HT-5") is None             # too few digits
    assert parse_account_number("HT-MTR-AB12") is None      # no numeric part


def test_parse_name_rejects_labels():
    assert parse_name("RAMESH KUMAR SHARMA") == "RAMESH KUMAR SHARMA"
    assert parse_name("Smt. SUNITA DEVI") == "Smt. SUNITA DEVI"
    assert parse_name("No.: 1234") is None
    assert parse_name("12345") is None


def test_normalize_text_preserves_columns_and_fixes_unicode():
    raw = "Units Consumed :  350\r\n\r\n\r\nNet Amount – Rs. 10\r\n"
    assert normalize_text(raw) == "Units Consumed :  350\n\nNet Amount - Rs. 10"


# ------------------------------------------------------- full parsing
KEY_VALUE_BILL = """
MAHA STATE ELECTRICITY DISTRIBUTION
Consumer Name: RAMESH KUMAR SHARMA          Consumer No.: 170012345678
Billing Period: 01-05-2025 to 31-05-2025    Bill Date: 02-06-2025
Due Date: 16-06-2025
Previous Reading: 1000
Current Reading: 1350
Units Consumed: 350
Net Amount Due: Rs. 2,450.50
Amount Payable After Due Date: Rs. 2,480.00
"""


def test_parse_key_value_bill(parser):
    bill = parser.parse(KEY_VALUE_BILL)
    assert bill.consumer_name == "RAMESH KUMAR SHARMA"
    assert bill.account_number == "170012345678"
    assert bill.billing_period == "2025-05-01 to 2025-05-31"
    assert bill.billing_period_start == date(2025, 5, 1)
    assert bill.due_date == date(2025, 6, 16)
    assert bill.previous_reading == Decimal("1000")
    assert bill.current_reading == Decimal("1350")
    assert bill.units_consumed == Decimal("350")
    assert bill.net_amount_due == Decimal("2450.50")
    assert bill.missing_fields() == []


TABLE_BILL = """
Customer Name : SUNITA DEVI
Father/Husband Name : RAJESH PRASAD
K No. : 210345678901
Bill Month : MAY-2025
Last Date of Payment : 20/06/2025
Amount Payable : 3,812.00
Amount Payable After Last Date : 3,890.00

Meter No.      Previous Reading    Current Reading    MF    Units Consumed
MT458812       4870                5320               1     450
Previous Reading Date: 05/05/2025
"""


def test_parse_table_bill_and_ignore_distractors(parser):
    bill = parser.parse(TABLE_BILL)
    assert bill.consumer_name == "SUNITA DEVI"          # not the father's name
    assert bill.account_number == "210345678901"
    assert bill.billing_period == "May 2025"
    assert bill.due_date == date(2025, 6, 20)           # not the "after last date" line
    assert bill.previous_reading == Decimal("4870")     # not the reading date
    assert bill.current_reading == Decimal("5320")
    assert bill.units_consumed == Decimal("450")
    assert bill.net_amount_due == Decimal("3812.00")    # not the late-payment amount
    assert bill.multiplying_factor == Decimal("1")
    assert bill.sources["previous_reading"] == "table:Previous Reading"


TRANSPOSED_BILL = """
METER READING
                     Previous          Present
  Date               05/05/2025        05/06/2025
  Reading            4870              5320
Units Billed : 450
"""


def test_parse_transposed_table(parser):
    bill = parser.parse(TRANSPOSED_BILL)
    assert bill.previous_reading == Decimal("4870")
    assert bill.current_reading == Decimal("5320")
    assert bill.units_consumed == Decimal("450")


NOISY_OCR_BILL = """
Name : MOHAMMED IQBAL
Service Connection No : 45-678-9O1-234
Bill Period : 12 Apr 2025 - 11 May 2025
Pay By : 25 May 2O25
Present Reading (kWh) : 8,245
Previous Reading (kWh) : 7,9O5
Billed Units : 34O
Total Amount Payable : = 5,126.00
"""


def test_parse_noisy_ocr_text(parser):
    bill = parser.parse(NOISY_OCR_BILL)
    assert bill.consumer_name == "MOHAMMED IQBAL"
    assert bill.account_number == "45-678-901-234"
    assert bill.due_date == date(2025, 5, 25)
    assert bill.previous_reading == Decimal("7905")
    assert bill.units_consumed == Decimal("340")
    assert bill.net_amount_due == Decimal("5126.00")


def test_missing_fields_are_none_not_guessed(parser):
    bill = parser.parse("Consumer Name: PRIYA NAIR\nBill Amount Rs 1,045.75\n")
    assert bill.consumer_name == "PRIYA NAIR"
    assert bill.net_amount_due == Decimal("1045.75")
    assert set(bill.missing_fields()) == {
        "account_number", "billing_period", "due_date",
        "previous_reading", "current_reading", "units_consumed",
    }


def test_empty_text_returns_all_missing(parser):
    assert len(parser.parse("").missing_fields()) == 8


def test_extra_aliases_file(tmp_path):
    alias_file = tmp_path / "aliases.json"
    alias_file.write_text(json.dumps({"due_date": ["Pay Till"], "account_number": ["Meter Account Ref"]}))
    parser = GenericBillParser(str(alias_file))
    bill = parser.parse("Meter Account Ref : 99887766\nPay Till : 01/07/2025\n")
    assert bill.account_number == "99887766"
    assert bill.due_date == date(2025, 7, 1)


# ------------------------------------------ layouts of generated HT bills
# Tesseract output of a bill image: no ':' after labels, '₹' read as "(2)" / "=".
OCR_IMAGE_BILL = """
                           Central State Power & Electricity Corporation
                                                  HT ELECTRICITY ENERGY BILL
 Consumer Name                  Example Steel Manufacturing Pvt. Ltd
 Account Number                  CSPEC-HT-700001
 Billing Period                  01 Aug 2026 - 31 Aug 2026
 Due Date                        15 Sep 2026
 Tariff Category                 HT-I Industrial
METER READING / CONSUMPTION DETAILS
                         Particulars                                Previous                Current              Units Consumed
                         Main Meter                                 100,000                112,500                 12,500 kWh
CHARGES SUMMARY
 Energy Charges                                                 12,500 kWh                Variable                      1500000.00
 Previous Balance                                                                                                             0.00
 NET AMOUNT DUE (2)                                                                                           = 15,00,000.00
"""


def test_ocr_image_layout_without_separators(parser):
    bill = parser.parse(OCR_IMAGE_BILL)
    assert bill.consumer_name == "Example Steel Manufacturing Pvt. Ltd"
    assert bill.account_number == "CSPEC-HT-700001"
    assert bill.billing_period == "2026-08-01 to 2026-08-31"
    assert bill.previous_reading == Decimal("100000")      # not the "Previous Balance" row
    assert bill.current_reading == Decimal("112500")
    assert bill.units_consumed == Decimal("12500")
    assert bill.net_amount_due == Decimal("1500000.00")
    assert bill.missing_fields() == []


COMPOUND_LABEL_BILL = """
              Consumer / Corporate Name              Example Cement Works Limited
              Registered Address                     Industrial Area, Raipur
              Account / Consumer No.                 NSEDA-HT-100002
              Sanctioned / Contract Demand           5,000 kVA
              Bill Period                            July 2026
              Due Date                               10 Aug 2026
 lars                               Initial / Previous       Final / Current          Difference               Meter Constant     Consumption Units   Remarks
 eter                                              200,000                  210,000                    10,000             2.000              20,000
           Net Amount Due                                                                                                 2000000.00
"""


def test_compound_labels_and_right_aligned_table_with_empty_column(parser):
    bill = parser.parse(COMPOUND_LABEL_BILL)
    assert bill.consumer_name == "Example Cement Works Limited"
    assert bill.account_number == "NSEDA-HT-100002"
    assert bill.previous_reading == Decimal("200000")
    assert bill.current_reading == Decimal("210000")
    assert bill.multiplying_factor == Decimal("2.000")
    assert bill.units_consumed == Decimal("20000")        # empty "Remarks" column doesn't shift values
    assert bill.missing_fields() == []


def test_table_values_offset_from_their_headers(parser):
    """Right-aligned numbers drifting under the next header must stay in their own column."""
    text = """
       Meter Reading Details                         Previous Reading            Current Reading            Units Consumed
       Main HT Meter                                                    78,000                      91,500                        13,500
"""
    bill = parser.parse(text)
    assert bill.previous_reading == Decimal("78000")
    assert bill.current_reading == Decimal("91500")
    assert bill.units_consumed == Decimal("13500")


def test_name_label_printed_above_the_name(parser):
    billed_to = """
     BILLED TO                                                                 NET AMOUNT DUE (Rs.)
     M/S Example Steel Works Ltd.
                                                                               1,00,000.00
     Survey 118, Industrial Estate, Hyderabad 502319
"""
    assert parser.parse(billed_to).consumer_name == "M/S Example Steel Works Ltd"
    stacked = """
     Consumer Name & Address
     M/S Example Precision Components Pvt. Ltd.
     Plot 41, Industrial Area Phase II, Bhiwadi, Rajasthan 301019
"""
    bill = parser.parse(stacked)
    assert bill.consumer_name == "M/S Example Precision Components Pvt. Ltd"
    assert bill.sources["consumer_name"] == "table:Consumer Name & Address"


def test_wrapped_company_name_is_joined(parser):
    text = """
    Consumer Name: M/S Example Pharma Pvt.              Consumer Number: 003900000001
    Ltd.                                                Meter Number: S8100001
    Address: Some MIDC, Ratnagiri,                      Connected Load (KW): 502.00
"""
    assert parser.parse(text).consumer_name == "M/S Example Pharma Pvt. Ltd"


UNLABELLED_NAME_BILL = """
     Example Grid Distribution Company Ltd.
     Grid Bhavan, Hyderabad
     HT CONSUMER - TAX INVOICE / ELECTRICITY BILL
     {name}
     Survey 118, Industrial Estate, Hyderabad 502319
      Account Number         18800001                              Bill Number          870000001
      Billing Period         01-Nov-2025 - 30-Nov-2025             Bill Date            08-Dec-2025
      NET AMOUNT DUE                                                                   1,00,000.00
"""


@pytest.mark.parametrize("printed, expected, source", [
    ("M/S Example Steel Works Ltd.", "M/S Example Steel Works Ltd", "inferred:M/S name"),
    ("Example Steel Works Ltd.", "Example Steel Works Ltd", "inferred:company name"),
])
def test_unlabelled_consumer_name_is_inferred(parser, printed, expected, source):
    bill = parser.parse(UNLABELLED_NAME_BILL.format(name=printed))
    assert bill.consumer_name == expected                  # not the utility on the letterhead
    assert bill.sources["consumer_name"] == source
    assert any("no 'Consumer Name' label" in n for n in bill.notes)


def test_unlabelled_name_is_not_inferred_from_letterhead_only(parser):
    bill = parser.parse(UNLABELLED_NAME_BILL.format(name="Survey Office"))
    assert bill.consumer_name is None


def test_header_row_label_is_not_taken_as_name(parser):
    text = """
Consumer Name        Tariff Category      Bill Date
RAMESH KUMAR         LT-1 Domestic        01-05-2025
"""
    assert parser.parse(text).consumer_name == "RAMESH KUMAR"


def test_compound_label_naming_two_fields_is_ambiguous(parser):
    bill = parser.parse("Previous/Current Reading : 4870 / 5320\n")
    assert bill.current_reading is None                    # 4870 is the previous reading


def test_units_derived_from_readings_when_not_printed(parser):
    from app.services.parsing.base import DERIVED_UNITS_SOURCE
    from app.services.validation_service import ValidationService, ValidationStatus

    text = """
Consumer Name: RAMESH KUMAR SHARMA          Consumer No.: 170012345678
Billing Period: 01-05-2025 to 31-05-2025    Due Date: 16-06-2025
Previous Reading: 1000.5
Current Reading: 1350.5
Net Amount Due: Rs. 2,450.50
"""
    bill = parser.parse(text + "Multiplying Factor: 20\n")
    assert bill.units_consumed == Decimal("7000")          # (1350.5 - 1000.5) x 20
    assert bill.sources["units_consumed"] == DERIVED_UNITS_SOURCE
    result = ValidationService().validate(bill)
    assert result.status == ValidationStatus.VALID
    assert result.meter_reading_check is None              # derived, so not a cross-check
    assert any("not printed on the bill" in n for n in result.notes)

    bill = parser.parse(text)                              # no MF printed: flagged for review
    assert bill.units_consumed == Decimal("350.0")
    result = ValidationService().validate(bill)
    assert result.status == ValidationStatus.WARNING
    assert any("without a multiplying factor" in w for w in result.warnings)


def test_pdf_watermark_text_is_ignored():
    import fitz

    from app.services.document_processor import DocumentProcessor

    pdf = fitz.open()
    page = pdf.new_page()
    for i, line in enumerate(["Account Number: 18800001", "Meter Number: S3500001", "Due Date: 23-Dec-2025"]):
        page.insert_text((50, 100 + 20 * i), line, fontsize=11)
    pivot = fitz.Point(150, 300)
    page.insert_text(pivot, "SPECIMEN - TEST DATA", fontsize=48, morph=(pivot, fitz.Matrix(45)))
    text = DocumentProcessor().process(pdf.tobytes(), "pdf").text
    assert "S3500001" in text
    assert "SPECIMEN" not in text and "DATA" not in text


def test_large_glyphs_survive_table_line_removal():
    """Regression: stems of large glyphs were erased as vertical table lines."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    from app.services.image_preprocessing import light_pipeline

    img = Image.new("L", (1400, 300), 255)
    try:
        font = ImageFont.truetype("arial.ttf", 30)
    except OSError:
        font = ImageFont.load_default(size=30)
    ImageDraw.Draw(img).text((40, 100), "Net Amount 1111 lllll IIIII", fill=0, font=font)
    out = light_pipeline(img)
    # ink must remain where the text was drawn (upscaled by the pipeline)
    assert (np.asarray(out) < 128).sum() > 0.6 * (np.asarray(img.resize(out.shape[::-1])) < 128).sum()


def test_deskew_levels_rotated_page():
    import numpy as np
    from PIL import Image, ImageDraw

    from app.services.image_preprocessing import estimate_skew

    img = Image.new("L", (1200, 900), 255)
    draw = ImageDraw.Draw(img)
    for y in range(80, 850, 40):
        draw.text((60, y), "Consumer Name : SAMPLE TEXT LINE 12345 " * 2, fill=0)
    rotated = img.rotate(2.0, expand=False, fillcolor=255)
    assert abs(estimate_skew(np.asarray(rotated)) + 2.0) <= 0.3
    assert abs(estimate_skew(np.asarray(img))) <= 0.2


@pytest.mark.parametrize("confidence, passes", [(90.0, 1), (60.0, 2)])
def test_second_ocr_pass_only_when_first_is_unsure(confidence, passes):
    """The binarized pass doubles OCR time; it only runs when the first pass is unsure."""
    from PIL import Image

    from app.core.config import get_settings
    from app.services.ocr_service import OCRProvider, OCRResult, OCRService
    from app.utils.text_layout import Word

    class FakeProvider(OCRProvider):
        calls = 0

        def recognize(self, image):
            FakeProvider.calls += 1
            return OCRResult(text="x", words=[Word("Units", 0, 0, 10, 10, confidence)], mean_confidence=confidence)

    settings = get_settings().model_copy(update={"OCR_PREPROCESS_MODE": "auto", "OCR_TIMEOUT_SECONDS": 300})
    OCRService(FakeProvider(), settings).extract(Image.new("L", (200, 100), 255))
    assert FakeProvider.calls == passes


def test_slow_host_skips_second_ocr_pass(monkeypatch):
    """On a throttled CPU the binary pass is skipped once the first pass used half the timeout."""
    from PIL import Image

    from app.core.config import get_settings
    from app.services.ocr_service import OCRProvider, OCRResult, OCRService
    from app.utils.text_layout import Word

    class FakeProvider(OCRProvider):
        calls = 0

        def recognize(self, image):
            FakeProvider.calls += 1
            return OCRResult(text="x", words=[Word("Units", 0, 0, 10, 10, 90.0)], mean_confidence=90.0)

    settings = get_settings().model_copy(update={"OCR_TIMEOUT_SECONDS": 0, "OCR_PREPROCESS_MODE": "auto"})
    result = OCRService(FakeProvider(), settings).extract(Image.new("L", (200, 100), 255))
    assert FakeProvider.calls == 1
    assert result.preprocessing == "light"

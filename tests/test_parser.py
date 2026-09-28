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

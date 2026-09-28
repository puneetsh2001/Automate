"""Generate synthetic electricity bills + ground-truth JSON for testing.

All providers, names and numbers are fictional. The bills intentionally use
different label vocabularies, layouts (label:value, header/value tables,
transposed tables), file types and image degradations, so the generic
parser is exercised the way real state bills would exercise it.

Usage:  python scripts/generate_sample_bills.py [output_dir]
"""

from __future__ import annotations

import io
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import fitz
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "sample_bills" / "synthetic"

A4 = (595, 842)  # points
FONT_DIR = Path("C:/Windows/Fonts")


@dataclass
class T:  # text element, coordinates in points
    x: float
    y: float
    text: str
    size: float = 10
    bold: bool = False


@dataclass
class L:  # line element
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class BillSpec:
    name: str
    output: str  # text_pdf | scanned_pdf | mixed_pdf | png | jpg
    pages: list[list]
    expected: dict
    dpi: int = 200
    photo: bool = False
    scanned_pages: set[int] = field(default_factory=set)  # for mixed_pdf (0-based)


def _font(size_px: int, bold: bool) -> ImageFont.ImageFont:
    name = "arialbd.ttf" if bold else "arial.ttf"
    for candidate in (FONT_DIR / name, Path(name)):
        try:
            return ImageFont.truetype(str(candidate), size_px)
        except OSError:
            continue
    return ImageFont.load_default()


def render_page_image(elements: list, dpi: int) -> Image.Image:
    s = dpi / 72
    img = Image.new("L", (int(A4[0] * s), int(A4[1] * s)), 255)
    d = ImageDraw.Draw(img)
    for el in elements:
        if isinstance(el, T):
            d.text((el.x * s, (el.y - el.size) * s), el.text, fill=0, font=_font(int(el.size * s), el.bold))
        else:
            d.line([(el.x0 * s, el.y0 * s), (el.x1 * s, el.y1 * s)], fill=0, width=max(1, int(0.8 * s)))
    return img


def photo_effects(img: Image.Image, seed: int = 7) -> Image.Image:
    rng = np.random.default_rng(seed)
    img = img.rotate(0.6, resample=Image.BICUBIC, expand=True, fillcolor=255)
    arr = np.asarray(img).astype(np.float32)
    h, w = arr.shape
    # uneven illumination: darker towards bottom-right
    yy, xx = np.mgrid[0:h, 0:w]
    shade = 1.0 - 0.28 * ((xx / w) * 0.6 + (yy / h) * 0.4)
    arr = arr * shade + rng.normal(0, 9, arr.shape)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    return img.filter(ImageFilter.GaussianBlur(0.6))


def write_text_page(page: fitz.Page, elements: list) -> None:
    for el in elements:
        if isinstance(el, T):
            page.insert_text((el.x, el.y), el.text, fontsize=el.size, fontname="hebo" if el.bold else "helv")
        else:
            page.draw_line((el.x0, el.y0), (el.x1, el.y1), width=0.8)


def image_page(pdf: fitz.Document, img: Image.Image) -> None:
    buf = io.BytesIO()
    img.convert("L").save(buf, format="JPEG", quality=85)
    page = pdf.new_page(width=A4[0], height=A4[1])
    page.insert_image(page.rect, stream=buf.getvalue())


def build(spec: BillSpec, out_dir: Path) -> Path:
    ext = {"text_pdf": ".pdf", "scanned_pdf": ".pdf", "mixed_pdf": ".pdf", "png": ".png", "jpg": ".jpg"}[spec.output]
    path = out_dir / f"{spec.name}{ext}"
    if spec.output in ("text_pdf", "scanned_pdf", "mixed_pdf"):
        pdf = fitz.open()
        for i, elements in enumerate(spec.pages):
            scanned = spec.output == "scanned_pdf" or i in spec.scanned_pages
            if scanned:
                img = render_page_image(elements, spec.dpi)
                image_page(pdf, photo_effects(img, seed=i) if spec.photo else img)
            else:
                write_text_page(pdf.new_page(width=A4[0], height=A4[1]), elements)
        pdf.save(path)
    else:
        img = render_page_image(spec.pages[0], spec.dpi)
        if spec.photo:
            img = photo_effects(img)
        if spec.output == "jpg":
            img.convert("RGB").save(path, "JPEG", quality=72)
        else:
            img.save(path, "PNG")
    (out_dir / f"{spec.name}.expected.json").write_text(json.dumps(spec.expected, indent=2) + "\n")
    return path


# --------------------------------------------------------------------------- bills

def header(provider: str, subtitle: str) -> list:
    return [
        T(40, 50, provider, 15, True),
        T(40, 68, subtitle, 9),
        L(40, 78, 555, 78),
        T(230, 98, "ELECTRICITY BILL", 13, True),
    ]


def bill_01() -> BillSpec:
    """Text-layer PDF, label: value form + horizontal meter table."""
    els = header("Sunrise State Power Distribution Co. Ltd.", "Fictional utility - sample bill for OCR testing") + [
        T(40, 130, "Consumer Name: RAMESH KUMAR SHARMA"),
        T(330, 130, "Consumer No.: 170012345678"),
        T(40, 148, "Address: 12, Gandhi Nagar, Sector 4, Pune 411001"),
        T(40, 166, "Tariff: LT-I Residential"),
        T(330, 166, "Bill No.: B2506-889231"),
        T(40, 184, "Billing Period: 01-05-2025 to 31-05-2025"),
        T(330, 184, "Bill Date: 02-06-2025"),
        T(330, 202, "Due Date: 16-06-2025", 10, True),
        T(40, 202, "Sanctioned Load: 3 kW"),
        L(40, 222, 555, 222),
        T(40, 240, "METER READING DETAILS", 11, True),
        T(40, 262, "Meter No."), T(130, 262, "Previous Reading"), T(250, 262, "Current Reading"),
        T(370, 262, "MF"), T(420, 262, "Units Consumed"),
        L(40, 268, 555, 268),
        T(40, 284, "MT458812"), T(130, 284, "10250"), T(250, 284, "10600"), T(370, 284, "1"), T(420, 284, "350"),
        T(40, 302, "Previous Reading Date: 30-04-2025"), T(330, 302, "Current Reading Date: 31-05-2025"),
        L(40, 318, 555, 318),
        T(40, 336, "BILL DETAILS", 11, True),
        T(40, 356, "Energy Charges"), T(420, 356, "1,925.00"),
        T(40, 372, "Fixed Charges"), T(420, 372, "150.00"),
        T(40, 388, "Electricity Duty"), T(420, 388, "375.50"),
        T(40, 404, "Current Month Bill"), T(420, 404, "2,450.50"),
        T(40, 420, "Arrears"), T(420, 420, "0.00"),
        L(40, 430, 555, 430),
        T(40, 448, "Net Amount Due: Rs. 2,450.50", 12, True),
        T(40, 468, "Amount Payable After Due Date: Rs. 2,480.00"),
        T(40, 520, "Please pay before the due date to avoid late payment surcharge.", 8),
    ]
    return BillSpec("bill_01_text_form", "text_pdf", [els], {
        "consumer_name": "RAMESH KUMAR SHARMA",
        "account_number": "170012345678",
        "billing_period": "2025-05-01 to 2025-05-31",
        "billing_period_start": "2025-05-01",
        "billing_period_end": "2025-05-31",
        "due_date": "2025-06-16",
        "previous_reading": 10250,
        "current_reading": 10600,
        "units_consumed": 350,
        "net_amount_due": 2450.50,
        "validation_status": "VALID",
        "extraction_method": "text",
    })


def bill_02() -> BillSpec:
    """Scanned 2-page PDF; transposed meter table with borders on page 2."""
    p1 = header("Northern Grid Electricity Board", "Fictional utility - sample bill for OCR testing") + [
        T(40, 135, "Customer Name  :  SUNITA DEVI", 11),
        T(40, 155, "Father/Husband Name  :  RAJESH PRASAD", 11),
        T(40, 175, "K No.  :  210345678901", 11),
        T(40, 195, "Bill Month  :  MAY-2025", 11),
        T(330, 195, "Bill Date  :  06/06/2025", 11),
        T(40, 215, "Last Date of Payment  :  20/06/2025", 11),
        T(40, 235, "Category  :  Domestic", 11),
        L(40, 255, 555, 255),
        T(40, 280, "Amount Payable  :  3,812.00", 13, True),
        T(40, 300, "Amount Payable After Last Date  :  3,890.00", 11),
        T(40, 330, "Meter reading details overleaf.", 9),
    ]
    p2 = [
        T(40, 60, "METER READING", 12, True),
        L(40, 75, 400, 75), L(40, 95, 400, 95), L(40, 115, 400, 115), L(40, 135, 400, 135),
        L(40, 75, 40, 135), L(160, 75, 160, 135), L(280, 75, 280, 135), L(400, 75, 400, 135),
        T(185, 90, "Previous", 11, True), T(305, 90, "Present", 11, True),
        T(50, 110, "Date", 11), T(170, 110, "05/05/2025", 11), T(290, 110, "05/06/2025", 11),
        T(50, 130, "Reading", 11), T(185, 130, "4870", 11), T(305, 130, "5320", 11),
        T(40, 165, "Units Billed  :  450", 11),
        T(40, 185, "Energy Charges  :  3,150.00", 11),
        T(40, 205, "Fixed Charges  :  220.00", 11),
        T(40, 225, "Electricity Duty  :  442.00", 11),
    ]
    return BillSpec("bill_02_scanned_multipage", "scanned_pdf", [p1, p2], {
        "consumer_name": "SUNITA DEVI",
        "account_number": "210345678901",
        "billing_period": "May 2025",
        "billing_period_start": None,
        "billing_period_end": None,
        "due_date": "2025-06-20",
        "previous_reading": 4870,
        "current_reading": 5320,
        "units_consumed": 450,
        "net_amount_due": 3812.00,
        "validation_status": "VALID",
        "extraction_method": "ocr",
    }, dpi=200)


def bill_03() -> BillSpec:
    """Phone-photo style JPG with multiplying factor -> meter mismatch WARNING."""
    els = header("Coastal Power Supply Company", "Fictional utility - sample bill for OCR testing") + [
        T(40, 130, "Service Connection No : 45-678-901-234", 11),
        T(40, 150, "Name : MOHAMMED IQBAL", 11),
        T(40, 170, "Bill Period : 12 Apr 2025 - 11 May 2025", 11),
        T(40, 190, "Pay By : 25 May 2025", 11, True),
        L(40, 205, 555, 205),
        T(40, 225, "Present Reading (kWh) : 8,245", 11),
        T(40, 245, "Previous Reading (kWh) : 7,905", 11),
        T(40, 265, "Multiplying Factor : 2", 11),
        T(40, 285, "Billed Units : 680", 11),
        L(40, 300, 555, 300),
        T(40, 320, "Energy Charges : 4,420.00", 11),
        T(40, 340, "Customer Charges : 60.00", 11),
        T(40, 360, "Taxes : 646.00", 11),
        T(40, 390, "Total Amount Payable : \u20b9 5,126.00", 13, True),
    ]
    return BillSpec("bill_03_phone_photo", "jpg", [els], {
        "consumer_name": "MOHAMMED IQBAL",
        "account_number": "45-678-901-234",
        "billing_period": "2025-04-12 to 2025-05-11",
        "billing_period_start": "2025-04-12",
        "billing_period_end": "2025-05-11",
        "due_date": "2025-05-25",
        "previous_reading": 7905,
        "current_reading": 8245,
        "units_consumed": 680,
        "net_amount_due": 5126.00,
        "validation_status": "WARNING",
        "extraction_method": "ocr",
    }, dpi=150, photo=True)


def bill_04() -> BillSpec:
    """Clean PNG, decimal readings, no due date printed -> due_date null + WARNING."""
    els = header("Hill State Electricity Utility", "Fictional utility - sample bill for OCR testing") + [
        T(40, 130, "Consumer : PRIYA NAIR", 11),
        T(330, 130, "Account Number : 0098765432", 11),
        T(40, 150, "Billing Cycle : 15/04/2025 - 14/05/2025", 11),
        T(40, 170, "Supply : Single Phase", 11),
        L(40, 185, 555, 185),
        T(40, 205, "Opening Reading", 11), T(200, 205, "Closing Reading", 11), T(360, 205, "Consumption (kWh)", 11),
        T(40, 225, "2,110.5", 11), T(200, 225, "2,238.5", 11), T(360, 225, "128", 11),
        L(40, 240, 555, 240),
        T(40, 265, "Bill Amount  Rs 1,045.75", 13, True),
        T(40, 290, "Payment accepted at all collection centres.", 9),
    ]
    return BillSpec("bill_04_clean_png", "png", [els], {
        "consumer_name": "PRIYA NAIR",
        "account_number": "0098765432",
        "billing_period": "2025-04-15 to 2025-05-14",
        "billing_period_start": "2025-04-15",
        "billing_period_end": "2025-05-14",
        "due_date": None,
        "previous_reading": 2110.5,
        "current_reading": 2238.5,
        "units_consumed": 128,
        "net_amount_due": 1045.75,
        "validation_status": "WARNING",
        "extraction_method": "ocr",
    }, dpi=200)


def bill_05() -> BillSpec:
    """2-page PDF: page 1 text layer, page 2 scanned -> 'mixed' extraction."""
    p1 = header("Central Plains Power Corporation", "Fictional utility - sample bill for OCR testing") + [
        T(40, 130, "Name of Consumer : ANITA DESAI"),
        T(40, 148, "CA Number : 300045612"),
        T(40, 166, "Bill Period : 01/03/2025 To 31/03/2025"),
        T(40, 184, "Payment Due Date : 18/04/2025", 10, True),
        T(40, 202, "Bill Date : 03/04/2025"),
        T(40, 240, "Summary of charges on next page."),
    ]
    p2 = [
        T(40, 60, "CONSUMPTION DETAILS", 12, True),
        T(40, 90, "Prev. Reading", 11), T(160, 90, "Curr. Reading", 11), T(280, 90, "Energy Consumption", 11),
        T(40, 110, "15600", 11), T(160, 110, "15815", 11), T(280, 110, "215", 11),
        L(40, 125, 555, 125),
        T(40, 150, "Energy Charges  1,290.00", 11),
        T(40, 170, "Fixed Charges  100.00", 11),
        T(40, 190, "Duty  129.00", 11),
        T(40, 215, "Net Payable Amount  Rs. 1,519.00", 12, True),
    ]
    return BillSpec("bill_05_mixed_pdf", "mixed_pdf", [p1, p2], {
        "consumer_name": "ANITA DESAI",
        "account_number": "300045612",
        "billing_period": "2025-03-01 to 2025-03-31",
        "billing_period_start": "2025-03-01",
        "billing_period_end": "2025-03-31",
        "due_date": "2025-04-18",
        "previous_reading": 15600,
        "current_reading": 15815,
        "units_consumed": 215,
        "net_amount_due": 1519.00,
        "validation_status": "VALID",
        "extraction_method": "mixed",
    }, dpi=200, scanned_pages={1})


ALL_BILLS = [bill_01, bill_02, bill_03, bill_04, bill_05]


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    for factory in ALL_BILLS:
        print("generated", build(factory(), out_dir).relative_to(ROOT) if out_dir.is_relative_to(ROOT) else out_dir)


if __name__ == "__main__":
    main()

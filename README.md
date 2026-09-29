# Electricity Bill OCR

Upload an Indian electricity bill (PDF, PNG or JPG) and get back structured, validated data:

| Field | Example |
|---|---|
| Consumer name | `RAMESH KUMAR SHARMA` |
| Account / consumer number | `170012345678` |
| Billing period | `2025-05-01 to 2025-05-31` (or `May 2025`) |
| Due date | `2025-06-16` |
| Previous / current meter reading | `10250` / `10600` |
| Units consumed (kWh) | `350` |
| Net amount due (₹) | `2450.50` |

Every bill is stored in a relational database together with its **raw OCR text** (for auditing) and a **validation report** (`VALID` / `WARNING` / `INVALID`). If a value can't be read confidently, the app returns `null` plus a warning. It never guesses.

---

## Contents
1. [Architecture](#1-architecture)
2. [Prerequisites](#2-prerequisites)
3. [Setup on Windows (step by step)](#3-setup-on-windows-step-by-step)
4. [Configuration (.env)](#4-configuration-env)
5. [Running the app](#5-running-the-app)
6. [Using the UI](#6-using-the-ui)
7. [API](#7-api)
8. [How OCR works](#8-how-ocr-works)
9. [How parsing works](#9-how-parsing-works)
10. [Validation rules](#10-validation-rules)
11. [Testing and reference bills](#11-testing-and-reference-bills)
12. [Troubleshooting](#12-troubleshooting)
13. [Known limitations and future improvements](#13-known-limitations-and-future-improvements)
14. [Deploying (Render + Neon PostgreSQL)](#14-deploying-render--neon-postgresql)

---

## 1. Architecture

```
Browser (frontend/: HTML + CSS + vanilla JS)
   │  POST /api/bills/upload (multipart)
   ▼
FastAPI  app/api/bills.py
   ▼
BillService  app/services/bill_service.py   ── orchestrates the pipeline
   ├─ file_utils.validate_upload      extension + MIME + magic bytes + size, UUID filename
   ├─ DocumentProcessor               PDF: text layer per page, else render 300 DPI → OCR
   │     └─ OCRService                preprocessing variants → Tesseract → best result
   │           ├─ image_preprocessing grayscale, rescale, denoise, deskew, table-line removal, binarize
   │           └─ TesseractProvider   (OCRProvider interface: add Textract etc. later)
   ├─ BillParser                      picks provider parser, else GenericBillParser
   │     └─ parsing/                  aliases, normalization, key/value + table strategies
   ├─ ValidationService               meter check, value checks, missing fields → status
   └─ SQLAlchemy Bill model  ──►  PostgreSQL / MySQL / SQLite (Alembic migrations)
```

```
electricity_bill_ocr/
├── app/
│   ├── main.py                  FastAPI app, error handlers, /health, static UI
│   ├── api/bills.py             REST endpoints
│   ├── core/                    config (env vars), exceptions, logging
│   ├── db/                      engine/session, Bill model
│   ├── schemas/bill.py          Pydantic request/response models
│   ├── services/
│   │   ├── bill_service.py      pipeline orchestration + persistence
│   │   ├── document_processor.py PDF/image → text
│   │   ├── ocr_service.py       OCR abstraction + Tesseract provider
│   │   ├── image_preprocessing.py
│   │   ├── bill_parser.py       parser selection (provider-specific → generic)
│   │   ├── parsing/             aliases.py, normalization.py, generic.py, base.py
│   │   │   └── providers/       apdcl.py, rajasthan_discom.py, karnataka_escom.py
│   │   └── validation_service.py
│   └── utils/                   file_utils.py (upload security), text_layout.py
├── frontend/                    index.html, style.css, app.js
├── migrations/                  Alembic env + versions
├── sample_bills/
│   ├── synthetic/               5 generated reference bills + expected JSON
│   └── private/                 put REAL bills here (git-ignored)
├── scripts/                     evaluate_reference_bills.py, generate_sample_bills.py
├── tests/                       pytest suite
├── uploads/                     stored uploads (UUID names, git-ignored)
├── .env.example  alembic.ini  pytest.ini  requirements.txt  run.py
```

## 2. Prerequisites

| Tool | Version | Why |
|---|---|---|
| Python | 3.11+ (tested on 3.12) | application |
| Tesseract OCR | 5.x | OCR engine for scanned PDFs and images |
| PostgreSQL | 14+ (recommended) | database (MySQL or SQLite also work) |

## 3. Setup on Windows (step by step)

All commands are for **PowerShell in the VS Code terminal**, run from the `electricity_bill_ocr` folder.

### 3.1 Python
Install Python 3.11+ from https://www.python.org/downloads/ and tick **"Add python.exe to PATH"**. Check it with:
```powershell
python --version
```

### 3.2 Tesseract OCR
```powershell
winget install UB-Mannheim.TesseractOCR
```
Or download the installer from https://github.com/UB-Mannheim/tesseract/wiki. The app finds `C:\Program Files\Tesseract-OCR\tesseract.exe` automatically. If you installed it elsewhere, set `TESSERACT_CMD` in `.env`. Verify it with:
```powershell
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --version
```
For Hindi or other scripts, select the extra languages in the installer and set `OCR_LANGUAGE=eng+hin`.

### 3.3 PostgreSQL
Install it from https://www.postgresql.org/download/windows/ (remember the `postgres` password), then create the database:
```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -c "CREATE DATABASE electricity_bills;"
```
Adjust the version folder (`16`) to your install.

> **No PostgreSQL yet?** Set `DATABASE_URL=sqlite:///./electricity_bills.db` in `.env` and everything works with zero setup. You can switch later.
>
> **MySQL:** `pip install pymysql`, `CREATE DATABASE electricity_bills CHARACTER SET utf8mb4;`, then set
> `DATABASE_URL=mysql+pymysql://root:PASSWORD@localhost:3306/electricity_bills`.

### 3.4 Project
```powershell
cd electricity_bill_ocr
python -m venv venv
venv\Scripts\activate          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements-dev.txt   # app + test tools
copy .env.example .env         # then edit DATABASE_URL (password!) in .env
alembic upgrade head           # creates / updates the bills table (run again after pulling new migrations)
```

## 4. Configuration (.env)

All settings come from environment variables or `.env`. See `.env.example` for the full list.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | SQLite file in project | any SQLAlchemy URL (postgresql / mysql+pymysql / sqlite) |
| `UPLOAD_DIR` | `uploads` | where uploaded files are stored |
| `MAX_UPLOAD_SIZE_MB` | `10` | upload limit |
| `TESSERACT_CMD` | auto-detect | path to `tesseract.exe` |
| `OCR_LANGUAGE` | `eng` | Tesseract language(s), e.g. `eng+hin` |
| `OCR_PSM` | `3` | Tesseract page segmentation mode |
| `OCR_PREPROCESS_MODE` | `auto` | `auto` (try light + binary, keep best) / `light` / `binary` / `none` |
| `OCR_LOW_CONFIDENCE_THRESHOLD` | `60` | mean OCR confidence below this adds a warning |
| `PDF_RENDER_DPI` | `300` | DPI for rendering scanned PDF pages |
| `PDF_TEXT_MIN_CHARS` | `50` | a PDF page needs this many text chars to skip OCR |
| `PDF_MAX_PAGES` | `20` | reject longer PDFs |
| `METER_READING_TOLERANCE` | `1.0` | allowed kWh difference in the meter check |
| `LABEL_ALIASES_FILE` | – | JSON with extra labels, e.g. `{"due_date": ["Pay Till"]}` |
| `LOG_LEVEL` | `INFO` | logging level |

Relative SQLite paths are resolved against the project folder. Passwords are never hard-coded, and `.env` is git-ignored.

## 5. Running the app

```powershell
venv\Scripts\activate
python run.py                      # or: uvicorn app.main:app --reload
```

| URL | What |
|---|---|
| http://127.0.0.1:8000/ | Web UI |
| http://127.0.0.1:8000/docs | Swagger UI (interactive API docs) |
| http://127.0.0.1:8000/redoc | ReDoc |
| http://127.0.0.1:8000/health | health check (database + OCR) |

On startup the log says whether Tesseract was found. If it wasn't, the server still starts and `/health` explains what's missing.

## 6. Using the UI

1. Open http://127.0.0.1:8000/.
2. Drag a bill onto the drop zone, or click **browse**. Only `.pdf`, `.png`, `.jpg` and `.jpeg` are accepted.
3. Click **Upload & extract**. The progress list shows *Processing → OCR completed → Data extraction completed → Validation completed*. When it finishes, the drop zone is ready for the next file.
4. The result card shows every field, the validation badge, the meter-reading check, and any warnings. Missing fields are shown as *Not found*, and mismatched values are highlighted.
   - If that exact file is already stored, nothing is processed. You get *"This file was already uploaded as bill #N"* with **View existing bill** and **Replace** (extract again and overwrite bill #N, keeping its number).
   - If a different file has the same account number and billing period as a stored bill (a re-scan, or a photo of the printout), it is saved with a **WARNING** *"Possible duplicate of bill #N"* and a link to that bill, so someone can decide which one to keep.
5. Expand **Raw OCR text (debug)** to see exactly what the OCR read.
6. **Recent bills** lists stored bills. Click a row to reopen one, or use **Delete** to remove it.

## 7. API

All error responses share one shape: `{"error": "<code>", "message": "<human readable>"}`. Stack traces are never returned.

| Method | Path | Description | Success |
|---|---|---|---|
| POST | `/api/bills/upload` | upload + full pipeline, returns the stored bill. `?replace=true` re-processes the bill that already has this file | 201 (200 when replaced) |
| GET | `/api/bills` | list (`limit`, `offset`, `validation_status`, `account_number`) | 200 |
| GET | `/api/bills/{id}` | one bill incl. validation details and raw text | 200 |
| GET | `/api/bills/{id}/raw-text` | unmodified OCR / PDF text | 200 |
| GET | `/api/bills/{id}/tariff-input` | normalised input for a future tariff engine | 200 |
| DELETE | `/api/bills/{id}` | delete the bill and its stored file | 204 |
| GET | `/health` | database + OCR status | 200 |

Upload errors: `400 empty_file / invalid_upload` (content doesn't match the extension), `409 duplicate_file` (this exact file is already stored; the body also has `existing_bill_id`), `413 file_too_large`, `415 unsupported_file_type`, `422 document_processing_failed` (corrupt or password-protected), `503 ocr_unavailable / database_unavailable`, `500 ocr_failed`.

Duplicate files are recognised by content (SHA-256 of the bytes, stored in `bills.file_hash`), not by file name: a renamed copy is still a duplicate, and two different bills that happen to share a name are not. Bills uploaded before migration `0002` have no fingerprint, so only the account + billing period check covers them.

### Example request
```powershell
curl.exe -F "file=@sample_bills/synthetic/bill_03_phone_photo.jpg" http://127.0.0.1:8000/api/bills/upload
```

### Example response (abridged)
```json
{
  "id": 3,
  "original_filename": "bill_03_phone_photo.jpg",
  "file_type": "jpeg",
  "consumer_name": "MOHAMMED IQBAL",
  "account_number": "45-678-901-234",
  "billing_period": "2025-04-12 to 2025-05-11",
  "billing_period_start": "2025-04-12",
  "billing_period_end": "2025-05-11",
  "due_date": "2025-05-25",
  "previous_reading": 7905.0,
  "current_reading": 8245.0,
  "units_consumed": 680.0,
  "net_amount_due": 5126.0,
  "validation_status": "WARNING",
  "warnings": [
    "Current - Previous reading (340) does not match reported units (680); difference 340",
    "Reported units match the reading difference x multiplying factor 2 (340 x 2 = 680)"
  ],
  "validation_details": {
    "status": "WARNING",
    "meter_reading_check": false,
    "calculated_units": 340.0,
    "reported_units": 680.0,
    "difference": 340.0,
    "multiplying_factor": 2.0,
    "field_checks": [
      {"field": "consumer_name", "status": "ok", "message": null},
      {"field": "units_consumed", "status": "mismatch", "message": "Does not equal current reading - previous reading"}
    ],
    "missing_fields": [],
    "warnings": ["..."],
    "errors": []
  },
  "extraction_method": "ocr",
  "page_count": 1,
  "ocr_confidence": 93.84,
  "processing_time_ms": 3768,
  "parser_name": "generic",
  "field_sources": {"account_number": "key_value:Service Connection No.", "units_consumed": "key_value:Billed Units"},
  "raw_ocr_text": "Coastal Power Supply Company\n...",
  "created_at": "2026-09-28T17:43:51.116991Z",
  "updated_at": "2026-09-28T17:43:51.116991Z"
}
```

### Tariff-ready output
`GET /api/bills/{id}/tariff-input` returns the normalised subset a future `TariffCalculationService.calculate(bill_data)` needs:
```json
{"bill_id": 3, "account_number": "45-678-901-234", "billing_period": "2025-04-12 to 2025-05-11",
 "billing_period_start": "2025-04-12", "billing_period_end": "2025-05-11",
 "units_consumed": 680.0, "net_amount_due": 5126.0, "validation_status": "WARNING"}
```
No tariff rules are implemented. That work belongs to a later, state-specific module.

## 8. How OCR works

1. **Upload validation.** The extension, the declared MIME type and the file's magic bytes must agree. The file is stored as `uploads/<uuid>.<ext>`, and the client's filename is only kept as display text.
2. **PDFs** are handled page by page with PyMuPDF:
   - If a page has a usable text layer (≥ 50 visible characters, mostly alphanumeric), its words are read directly. This is exact and takes milliseconds. Diagonal or vertical text (watermarks like "DUPLICATE" / "SPECIMEN", stamps) is dropped so it can't land inside table rows.
   - Otherwise the page is rendered at 300 DPI and OCR'd. Mixed PDFs (some text pages, some scanned) are handled, and the bill is marked `extraction_method: "mixed"`.
3. **Image preprocessing** (`image_preprocessing.py`, each step a small function):
   `grayscale (EXIF-rotated, transparency flattened) → rescale to ~3000 px → median denoise → deskew (±8°) → remove table ruling lines`.
   A second variant also binarizes the image (Otsu, or adaptive thresholding for unevenly lit photos). In `auto` mode both variants are OCR'd and the one with the higher confidence-weighted word score wins, so aggressive thresholding is never forced on an image it would damage.
4. **Tesseract** (`--oem 1 --psm 3`) returns words with bounding boxes and confidences.
5. **Layout reconstruction** (`text_layout.py`) puts the words back into visual rows. Column gaps become 2+ spaces, so table headers stay aligned above their values. This is what the table parser relies on.
6. The raw text is stored untouched in `raw_ocr_text`.

To add another OCR engine (e.g. AWS Textract), implement `OCRProvider.recognize()` and select it in `get_ocr_service()`.

## 9. How parsing works

`BillParser` first asks each registered provider-specific parser (`PROVIDER_PARSERS`) whether it recognises the document. If none does, `GenericBillParser` handles it.

**Normalisation** (`parsing/normalization.py`) runs on a copy of the text. It normalises line endings, unicode dashes and spaces, and table pipes. Inside numeric tokens only, it fixes common OCR confusions (`O→0`, `l/I→1`, `S→5`, `B→8`). Values are then normalised:
- amounts: strips `Rs.`, `₹`, `INR` and whatever OCR makes of the rupee sign (`%`, `&`, `®`, `(2)`, a lone `I`); supports Indian grouping (`1,00,000.00`); returns `Decimal` with 2 dp
- dates: day-first Indian formats (`16-06-2025`, `20/06/25`, `25 May 2025`, `25-May-25`, ISO)
- billing period: date ranges (`01-05-2025 to 31-05-2025`, `12 Apr 2025 - 11 May 2025`) or a month (`MAY-2025` → `May 2025`)
- account numbers stay strings (leading zeros and dashes are kept); structured IDs like `CSPEC-HT-784521` are accepted

**Label aliases** (`parsing/aliases.py`): each field has an ordered list of labels, for example *Consumer No / Account Number / CA Number / K No / Service Connection No / BP No …*. Matching ignores case and extra whitespace, and dots are optional. Compound labels are matched part by part (*Account / Consumer No.*, *Initial / Previous*), unless the parts name two different fields (*Previous/Current Reading*). Add labels there, or without code changes via `LABEL_ALIASES_FILE`.

**Extraction strategies**, tried in this order for each field:
1. **Key/value**: `Label [(kWh)] [:|=|-] value`. The label must start a text cell (line start, after a column gap, or after the `/` of a compound label). This is why *Father/Husband Name* is not mistaken for the consumer's name, and why *Amount Payable After Last Date* doesn't win over *Amount Payable*. For the consumer name, a column gap also works as the separator after a multi-word label (`Consumer Name      M/S …`, as OCR prints it), but the value is rejected if it reads like another label (`Consumer Name   Tariff Category`).
2. **Table**: a header row (`Previous Reading   Current Reading   Units Consumed`) with values in the same columns on the following lines. Values are assigned to columns by an order-preserving alignment. Right-aligned numbers under left-aligned headers, and empty columns (`Remarks`), therefore don't shift values into a neighbouring column. Transposed tables (`Previous  Present` header with a `Reading  4870  5320` row) are supported too. Rows labelled *Date* are skipped. A name label may stand alone above its value (`BILLED TO` / `Consumer Name & Address` with the name on the next line).
3. **Inference** (consumer name only, when the bill prints no label for it): on a document where at least 3 other fields were found, the first `M/S …` name, else a name ending in *Ltd / Limited / Pvt / LLP* that isn't the utility's own letterhead. The source is `inferred:…` and a note asks the user to verify it.

A company name wrapped onto a second line (`M/S Example Pharma Pvt.` / `Ltd.`) is joined back together.

Every candidate must pass a strict type parser (a date is never accepted as a reading, a label is never accepted as a name). If nothing passes, the field is `null`. `field_sources` records which label and strategy found each value, which helps with debugging.

When the bill prints the readings but not the units, units are calculated as `(current - previous) x MF - open-access units`, with source `derived:meter readings` (see §10).

The generic parser also refuses the traps found in real bills: history rows (`Bill Month 202507 202506 …`), column-numbering rows (`1 2 3 (3-4)=5`), and the utility's own bank account in payment instructions.

### Provider-specific parsers
HT/industrial bills with multi-register meters need layout knowledge. These parsers run first when their `detect()` matches, and fall back to the generic logic for simple labelled fields:

| Parser | Detects | Handles |
|---|---|---|
| `apdcl` | "Assam Power Distribution" / apdcl.org | TOD registers (Solar/Peak/Normal): readings = sum of the registers, units = sum of "Unit Consumed", open-access units, names wrapped onto a second line |
| `rajasthan_discom` | "Vidyut Vitran Nigam" (JVVNL; same system as AVVNL/JdVVNL) | K No as account, `202508` billing month, 2-line headers, KWH register row with MF |
| `karnataka_escom` | GESCOM/BESCOM/HESCOM/MESCOM/CESC + "R.R. No" | scanned letter layout, Main MR row x meter constant, due date printed without a year (year taken from the bill month, and noted), "Say Rs." payable amount |

Each was built and verified against one real bill format (see §11). AVVNL/JdVVNL and the other Karnataka ESCOMs are detected by shared naming but haven't been verified on a real bill yet.

**Adding a provider-specific parser:** subclass `GenericBillParser`, implement `detect(text)` (e.g. look for the utility's name) and override `parse(text)` (call `super().parse()` first, then replace the fields your layout needs), then add the class to `PROVIDER_PARSERS` in `app/services/bill_parser.py`. The files in `app/services/parsing/providers/` are small, commented examples.

## 10. Validation rules

| Check | Result if it fails |
|---|---|
| Required field missing | `WARNING`, `{"field": "...", "status": "missing"}`, message "*X could not be confidently extracted*" |
| `units == (current - previous) x MF - open-access units` (MF / open-access only when printed; tolerance = max(`METER_READING_TOLERANCE`, MF x reading precision)) | Passes: stays `VALID`, and the formula is shown in `calculation` with an explanatory `note`. Fails: `WARNING` (not rejected: adjustments, estimates and net metering are legitimate reasons). |
| Readings missing, so no cross-check possible | `meter_reading_check: null` plus a warning |
| Same account number and billing period as a stored bill (different file) | `WARNING` "*Possible duplicate of bill #N*", ids in `possible_duplicate_of`. Not rejected, since utilities issue revised bills. |
| Units not printed, so calculated from the readings | `meter_reading_check: null` (nothing independent to compare) and a note with the formula; stays `VALID` if the bill prints an MF, otherwise `WARNING` "*calculated without a multiplying factor*" |
| `current < previous` | `INVALID` (possible meter replacement or rollover; needs review) |
| Negative units / amount / readings | `INVALID` |
| Billing period end before start | `INVALID` |
| Due date before billing period end | `WARNING` |
| Mean OCR confidence < threshold | `WARNING` |
| Nothing extracted at all | `INVALID` |

Bills are **always stored**, whatever the status. Validation never drops extracted data.

`validation_details.notes` explains how derived values were obtained (TOD sums, MF, open-access units, a due-date year inferred from the bill month). Notes are informational and don't change the status. The UI shows them under **How values were derived**.

## 11. Testing and reference bills

```powershell
python -m pytest                                   # full suite (≈20 s)
python scripts/evaluate_reference_bills.py         # field-by-field accuracy report
python scripts/evaluate_reference_bills.py -v      # also prints the raw OCR text
```

The suite covers number/amount/date/period/name parsing, noisy OCR text, table and transposed-table layouts, alias extension, every validation rule (including 1000 / 1350 / 350 → `VALID` and 1000 / 1350 / 300 → `WARNING`), the full API lifecycle, upload security (bad extension, MIME mismatch, content mismatch, empty, oversize, path traversal, corrupt PDF), PNG/JPG OCR uploads, and every reference bill end to end. Tests use a temporary SQLite database, so your real DB is never touched. OCR tests are skipped automatically if Tesseract is missing.

**Reference bills.** `sample_bills/synthetic/` contains 5 generated (fictional) bills, each with a different layout, vocabulary and input type:

| Bill | Type | Exercises |
|---|---|---|
| bill_01_text_form.pdf | text PDF | label:value plus a meter table with MF column |
| bill_02_scanned_multipage.pdf | scanned 2-page PDF | Hindi-belt vocabulary (K No, Bill Month), transposed reading table on page 2 |
| bill_03_phone_photo.jpg | skewed/noisy photo | multiplying factor 2: (8245 − 7905) × 2 = 680, VALID |
| bill_04_clean_png.png | PNG | no due date on the bill, so `null` plus a warning |
| bill_05_mixed_pdf.pdf | text page + scanned page | mixed extraction |

Current result: **45/45 fields and statuses correct.** Regenerate the bills with `python scripts/generate_sample_bills.py`.

**Real reference bills** (verified during development; the files are not kept in the project, so add your own to `sample_bills/private/` to re-run them):

| File | Utility | Result |
|---|---|---|
| Energy Bill Mar-26 SCNEL.pdf | APDCL HT, text PDF | 8/8 fields, VALID, 0 warnings |
| Energy Bill Mar-26 SCL.pdf | APDCL HT, text PDF | 8/8 fields, VALID, 0 warnings |
| Electricity Bill July'25.pdf | JVVNL HT-5, text PDF | 8/8 fields, VALID, 0 warnings |
| EB BILL_06JUN2025.pdf | GESCOM EHT, scanned | 8/8 fields, VALID, 0 warnings |
| EM6400RegMap_V01.01.02.pdf | not a bill (meter register map) | negative test: nothing extracted, INVALID |

`tests/test_providers.py` covers the same layouts with fictional values, so those tests also run on machines without the private files.

**Adding real bills:** copy them to `sample_bills/private/` (git-ignored, because they contain personal data), and next to each one create `<same name>.expected.json` with the true values (same keys as the synthetic examples). The evaluation script and `tests/test_reference_bills.py` pick them up automatically.

## 12. Troubleshooting

| Symptom | Fix |
|---|---|
| `/health` shows `ocr: unavailable`, upload returns 503 `ocr_unavailable` | Install Tesseract (§3.2) or set `TESSERACT_CMD=C:\path\to\tesseract.exe` in `.env`, then restart. |
| `Tesseract language data missing for: hin` | Re-run the Tesseract installer and tick the language, or set `OCR_LANGUAGE=eng`. |
| 503 `database_unavailable` | Check that PostgreSQL is running (`services.msc` → postgresql), check `DATABASE_URL` and the password, and run `alembic upgrade head`. |
| `no such table: bills` / `relation "bills" does not exist` | Run `alembic upgrade head`. |
| `no such column: bills.file_hash` / `column bills.file_hash does not exist` | The database is older than the code. Run `alembic upgrade head` (Render does this on every start). |
| `password authentication failed` | Fix the password in `DATABASE_URL`. Special characters must be URL-encoded (`@` → `%40`). |
| `venv\Scripts\activate` is blocked | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `ModuleNotFoundError: app` when running alembic or scripts | Run commands from the `electricity_bill_ocr` folder with the venv activated. |
| A field is `null` but visible on the bill | Open the raw OCR text. If the text is right, the label is probably unknown: add it to `parsing/aliases.py` or `LABEL_ALIASES_FILE`. If the text is garbled, try a sharper scan (≥ 300 DPI) or `OCR_PREPROCESS_MODE=binary`. |
| On the hosted app, a scanned bill fails with `ocr_failed` / "OCR timed out" | The free server's CPU is much slower than a PC. The Dockerfile already runs Tesseract single-threaded with a 300 s timeout. Check the Render logs for `OCR pass variant=... took Ns`; if it is still too slow, set `OCR_PREPROCESS_MODE=binary` (one pass) or `PDF_RENDER_DPI=200` in Render → Environment. |
| Very slow OCR | Scanned PDFs take ~2–3 s per page. Reduce `PDF_RENDER_DPI` to 200, or set `OCR_PREPROCESS_MODE=light` (one OCR pass instead of two). |
| Port 8000 in use | `$env:PORT=8001; python run.py` |

Server logs (console) show each step: upload, per-page OCR timing, parse results (which fields were missing), validation status, DB save, and errors with tracebacks.

## 13. Known limitations and future improvements

**Limitations**
- Verified on 4 real HT bills (APDCL ×2, JVVNL, GESCOM), 5 synthetic bills, degraded image variants (79/80 fields), and 18 AI-generated HT bills in 4 other layouts (12 PDFs + 6 PNG images: 18/18 VALID, all fields correct). Other utilities, and domestic/LT bills from these utilities, fall back to the generic parser. Expect to add aliases or a provider parser for each new format.
- An inferred (unlabelled) consumer name is recognised by its form (`M/S …`, `… Pvt. Ltd.`). A private individual's name printed without any label is not inferred.
- OCR can misread a digit into another valid-looking value (e.g. `11 May` → `14 May` on a heavily rotated photo). The parser can't detect that. The meter check catches such errors in readings and units, but not in dates or names.
- English OCR only by default. Bilingual Hindi/regional bills need `OCR_LANGUAGE=eng+hin` (etc.) and the matching Tesseract language packs.
- Processing is synchronous (a request waits for OCR). That's fine for single uploads, but not for bulk.
- No authentication (MVP scope). Don't expose it to the internet as is.

**Next steps**
1. Collect 3–5 real bills per target utility, add them with expected JSON under `sample_bills/private/`, and tune aliases or add provider parsers (MSEDCL, TANGEDCO, BESCOM, TPDDL, …).
2. Per-field confidence scores (OCR word confidence of the extracted value), plus a UI to correct fields that saves the corrections back.
3. Tariff engine: `TariffCalculationService.calculate(TariffInput)` with versioned, state-specific slab tables.
4. Background processing for bulk uploads, duplicate detection (same account + period).
5. Optional cloud OCR provider (AWS Textract / Azure Document Intelligence) behind the existing `OCRProvider` interface for low-quality photos.
6. Authentication, and encryption / retention policy for stored bills (they contain personal data).

## 14. Deploying (Render + Neon PostgreSQL)

The app runs as one Docker container (FastAPI + Tesseract) on **Render**'s free plan and stores bills in a free **Neon** PostgreSQL database. Both are free and need no credit card. Result: a public `https://<name>.onrender.com` link.

```
Browser ──https──► Render (Docker: FastAPI + Tesseract + UI) ──TLS──► Neon PostgreSQL
```

### 14.1 Create the database (Neon)
1. Sign up at https://neon.tech → **New project** → region **AWS Asia Pacific (Singapore)** (same region as the app) → Postgres 16.
2. On the project dashboard, click **Connect** and copy the connection string. It looks like
   `postgresql://neondb_owner:••••@ep-xxxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`
3. **Local use:** put it in `.env` as `DATABASE_URL=...` and run `alembic upgrade head`. Your PC then uses the same cloud database. Keep this string secret: never commit it or paste it into chat.

### 14.2 Put the code on GitHub
Create an **empty private** repository at https://github.com/new (no README), then from the project folder:
```powershell
git remote add origin https://github.com/<your-user>/electricity-bill-ocr.git
git push -u origin main
```
`.gitignore` keeps `.env`, the database, uploads and `sample_bills/private/` (real customer bills) out of git.

### 14.3 Deploy the app (Render)
1. Sign up at https://render.com with your GitHub account.
2. **New → Blueprint** → select the repository. Render reads `render.yaml`.
3. When asked for **DATABASE_URL**, paste the Neon connection string → **Apply**.
4. The first build takes ~5–10 minutes (it installs Tesseract). On every start the container runs `alembic upgrade head` automatically.
5. Open the service → copy its URL (`https://electricity-bill-ocr-xxxx.onrender.com`) and check `/health` shows `"database":"ok","ocr":"ok"`.

Every `git push` to `main` redeploys automatically.

### 14.4 Free-tier behaviour to know
| | |
|---|---|
| Sleep | The Render free service sleeps after 15 min without traffic; the next visit takes ~50 s to wake. Open the link once before a demo. |
| Uploaded files | The container disk is temporary: original PDFs/images are lost on redeploy/restart. **All extracted data and raw OCR text are in PostgreSQL and are kept.** |
| Access | No login: anyone with the link can upload, view and delete bills. Don't leave real customer bills on it longer than needed. |
| Limits | 512 MB RAM, shared CPU: a scanned page takes a few seconds; keep `PDF_MAX_PAGES` modest. Neon free: 0.5 GB storage (thousands of bills). |

### 14.5 Deployment files
| File | Purpose |
|---|---|
| `Dockerfile` | Python 3.12-slim + `tesseract-ocr`, non-root user, runs migrations then uvicorn on `$PORT` |
| `.dockerignore` | keeps venv, `.env`, databases, tests and **all sample/private bills** out of the image |
| `render.yaml` | Render Blueprint: free Docker web service in Singapore, health check `/health`, `DATABASE_URL` as a secret |
| `requirements.txt` / `requirements-dev.txt` | runtime-only vs. runtime + pytest/httpx |

With `ENVIRONMENT=production` (set in the Dockerfile), the app refuses to start if `DATABASE_URL` is missing or points to SQLite, so a misconfigured deploy can't silently lose data. To run the test suite against a PostgreSQL test database: `$env:TEST_DATABASE_URL="postgresql://…/empty_test_db"; python -m pytest` (its tables are dropped afterwards).


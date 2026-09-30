from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.database import get_db
from app.schemas.bill import (
    BillListResponse,
    BillResponse,
    ErrorResponse,
    RawTextResponse,
    TariffInput,
)
from app.services.bill_service import BillService, UploadOutcome, to_response, to_summary, to_tariff_input

router = APIRouter(prefix="/api/bills", tags=["bills"])

_ERRORS = {
    404: {"model": ErrorResponse, "description": "Bill not found"},
    503: {"model": ErrorResponse, "description": "Database unavailable"},
}


def get_bill_service(db: Session = Depends(get_db)) -> BillService:
    return BillService(db)


@router.post(
    "/upload",
    response_model=BillResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a bill and extract its data",
    description=(
        "Accepts a PDF, PNG or JPEG electricity bill (multipart field `file`). "
        "Runs the full pipeline - text extraction / OCR, parsing, normalisation and "
        "validation - stores the result and returns it. Fields that could not be "
        "extracted confidently are `null` and listed in `warnings`; such a bill is stored "
        "with `validation_status` WARNING.\n\n"
        "**INVALID** documents (nothing extractable, e.g. not a bill, or contradictory values) "
        "are returned with 200 and `saved: false`, `id: null`: the extracted data is shown, "
        "but neither the data nor the file is stored.\n\n"
        "**Duplicates:** uploading a file that is already stored returns 409 `duplicate_file` with "
        "`existing_bill_id`; repeat with `replace=true` to re-process that bill in place (200, same id). "
        "A different file with the same account number and billing period as a stored bill is saved "
        "with a WARNING and `validation_details.possible_duplicate_of`."
    ),
    responses={
        200: {"model": BillResponse,
              "description": "INVALID document, not stored (`saved: false`); or, with `replace=true`, "
                             "the existing bill was re-processed"},
        400: {"model": ErrorResponse, "description": "Empty file or content/extension mismatch"},
        409: {"model": ErrorResponse, "description": "This file is already stored (see existing_bill_id)"},
        413: {"model": ErrorResponse, "description": "File larger than MAX_UPLOAD_SIZE_MB"},
        415: {"model": ErrorResponse, "description": "Unsupported file type"},
        422: {"model": ErrorResponse, "description": "Corrupt or unreadable document"},
        500: {"model": ErrorResponse, "description": "OCR or unexpected processing failure"},
        503: {"model": ErrorResponse, "description": "Tesseract or database unavailable"},
    },
)
def upload_bill(
    response: Response,
    file: UploadFile = File(..., description="Bill document (.pdf, .png, .jpg, .jpeg)"),
    replace: bool = Query(False, description="If this file is already stored, re-process that bill"),
    service: BillService = Depends(get_bill_service),
) -> BillResponse:
    # Read at most limit+1 bytes so oversized uploads are rejected without loading them fully
    data = file.file.read(get_settings().max_upload_bytes + 1)
    bill, outcome = service.process_upload(file.filename, file.content_type, data, replace=replace)
    if outcome is not UploadOutcome.CREATED:
        response.status_code = status.HTTP_200_OK
    return to_response(bill, saved=outcome is not UploadOutcome.NOT_SAVED)


@router.get(
    "",
    response_model=BillListResponse,
    summary="List processed bills",
    description="Newest first. Raw OCR text is not included; fetch a single bill for that.",
    responses={503: _ERRORS[503]},
)
def list_bills(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    validation_status: Literal["VALID", "WARNING", "INVALID"] | None = Query(None),
    account_number: str | None = Query(None, max_length=64),
    search: str | None = Query(None, max_length=100,
                               description="Part of the consumer name, account number or file name"),
    service: BillService = Depends(get_bill_service),
) -> BillListResponse:
    total, items = service.list(limit, offset, validation_status, account_number, search)
    return BillListResponse(total=total, limit=limit, offset=offset, items=[to_summary(b) for b in items])


@router.get("/{bill_id}", response_model=BillResponse, summary="Get one bill", responses=_ERRORS)
def get_bill(bill_id: int, service: BillService = Depends(get_bill_service)) -> BillResponse:
    return to_response(service.get(bill_id))


@router.get(
    "/{bill_id}/raw-text",
    response_model=RawTextResponse,
    summary="Get the raw OCR / PDF text of a bill",
    description="Unmodified text as produced by the PDF text layer or Tesseract, for debugging/auditing.",
    responses=_ERRORS,
)
def get_raw_text(bill_id: int, service: BillService = Depends(get_bill_service)) -> RawTextResponse:
    bill = service.get(bill_id)
    return RawTextResponse(id=bill.id, extraction_method=bill.extraction_method, raw_ocr_text=bill.raw_ocr_text)


_FILE_ERRORS = {
    **_ERRORS,
    404: {"model": ErrorResponse, "description": "Bill, stored file (file_not_available) or page not found"},
}
# Versioned URLs (?v=<updated_at>) let browsers cache page images; nosniff keeps uploads from being reinterpreted
_FILE_HEADERS = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}
_MEDIA_TYPES = {"pdf": "application/pdf", "png": "image/png", "jpeg": "image/jpeg"}


@router.get(
    "/{bill_id}/preview",
    response_class=Response,
    summary="Image of one page of the original bill",
    description="PNG of a PDF page (rendered server-side, so no PDF viewer is needed) or a downscaled "
                "JPEG of an uploaded photo. `file_not_available` if the host no longer has the file.",
    responses={200: {"content": {"image/png": {}, "image/jpeg": {}}, "description": "Page image"}, **_FILE_ERRORS},
)
def get_preview(bill_id: int, page: int = Query(1, ge=1, le=100),
                service: BillService = Depends(get_bill_service)) -> Response:
    content, media_type = service.preview(bill_id, page)
    return Response(content, media_type=media_type, headers=_FILE_HEADERS)


@router.get(
    "/{bill_id}/file",
    response_class=FileResponse,
    summary="The original uploaded file",
    responses={200: {"content": {"application/pdf": {}, "image/png": {}, "image/jpeg": {}},
                     "description": "Original file"}, **_FILE_ERRORS},
)
def get_file(bill_id: int, service: BillService = Depends(get_bill_service)) -> FileResponse:
    bill, path = service.stored_file(bill_id)
    return FileResponse(path, media_type=_MEDIA_TYPES.get(bill.file_type, "application/octet-stream"),
                        filename=bill.original_filename, content_disposition_type="inline", headers=_FILE_HEADERS)


@router.get(
    "/{bill_id}/tariff-input",
    response_model=TariffInput,
    summary="Get the normalised data needed for tariff calculation",
    description="Structured input for a future TariffCalculationService.calculate(bill_data).",
    responses=_ERRORS,
)
def get_tariff_input(bill_id: int, service: BillService = Depends(get_bill_service)) -> TariffInput:
    return to_tariff_input(service.get(bill_id))


@router.delete(
    "/{bill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a bill and its stored file",
    responses=_ERRORS,
)
def delete_bill(bill_id: int, service: BillService = Depends(get_bill_service)) -> Response:
    service.delete(bill_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status
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
from app.services.bill_service import BillService, to_response, to_summary, to_tariff_input

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
        "extracted confidently are `null` and listed in `warnings`. A bill whose "
        "values fail validation is still stored, with `validation_status` WARNING or INVALID.\n\n"
        "**Duplicates:** uploading a file that is already stored returns 409 `duplicate_file` with "
        "`existing_bill_id`; repeat with `replace=true` to re-process that bill in place (200, same id). "
        "A different file with the same account number and billing period as a stored bill is saved "
        "with a WARNING and `validation_details.possible_duplicate_of`."
    ),
    responses={
        200: {"model": BillResponse, "description": "`replace=true`: the existing bill was re-processed"},
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
    bill, replaced = service.process_upload(file.filename, file.content_type, data, replace=replace)
    if replaced:
        response.status_code = status.HTTP_200_OK
    return to_response(bill)


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
    service: BillService = Depends(get_bill_service),
) -> BillListResponse:
    total, items = service.list(limit, offset, validation_status, account_number)
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

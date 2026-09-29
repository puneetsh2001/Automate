"""API request/response models."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

# Serialise Decimal as a JSON number (2450.5) instead of a string ("2450.50")
JsonDecimal = Annotated[
    Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")
]

ValidationStatusLiteral = Literal["VALID", "WARNING", "INVALID"]


class FieldCheck(BaseModel):
    field: str
    status: Literal["ok", "missing", "invalid", "mismatch"]
    message: str | None = None


class ValidationDetails(BaseModel):
    status: ValidationStatusLiteral
    meter_reading_check: bool | None = Field(
        None,
        description="True if (current - previous) x MF - open-access units == reported units "
                    "(within tolerance); null if not checkable",
    )
    calculated_units: float | None = None
    reported_units: float | None = None
    difference: float | None = Field(None, description="reported_units - calculated_units")
    multiplying_factor: float | None = None
    open_access_units: float | None = None
    calculation: str | None = Field(None, description="Formula used for calculated_units")
    notes: list[str] = Field(default_factory=list, description="Informational; do not affect the status")
    possible_duplicate_of: list[int] = Field(
        default_factory=list, description="Other bills with the same account number and billing period"
    )
    field_checks: list[FieldCheck] = []
    missing_fields: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []


class TariffInput(BaseModel):
    """Normalised subset of a bill for a future TariffCalculationService.calculate(bill_data)."""

    bill_id: int
    account_number: str | None
    billing_period: str | None
    billing_period_start: date | None
    billing_period_end: date | None
    units_consumed: JsonDecimal | None
    net_amount_due: JsonDecimal | None
    validation_status: ValidationStatusLiteral


class BillSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    file_type: str
    consumer_name: str | None
    account_number: str | None
    billing_period: str | None
    due_date: date | None
    units_consumed: JsonDecimal | None
    net_amount_due: JsonDecimal | None
    validation_status: ValidationStatusLiteral
    created_at: datetime


class BillResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    file_type: str

    consumer_name: str | None
    account_number: str | None
    billing_period: str | None
    billing_period_start: date | None
    billing_period_end: date | None
    due_date: date | None
    previous_reading: JsonDecimal | None
    current_reading: JsonDecimal | None
    units_consumed: JsonDecimal | None
    net_amount_due: JsonDecimal | None

    validation_status: ValidationStatusLiteral
    warnings: list[str] = Field(default_factory=list, description="Validation warnings and errors")
    validation_details: ValidationDetails

    extraction_method: str | None = Field(None, description="text | ocr | mixed")
    page_count: int | None
    ocr_confidence: float | None = Field(None, description="Mean Tesseract word confidence (0-100)")
    processing_time_ms: int | None
    parser_name: str | None
    field_sources: dict[str, str] = Field(default_factory=dict, description="How each field was located")

    raw_ocr_text: str | None = Field(None, description="Included on upload and single-bill fetch")
    created_at: datetime
    updated_at: datetime


class BillListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[BillSummary]


class RawTextResponse(BaseModel):
    id: int
    extraction_method: str | None
    raw_ocr_text: str


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    ocr: Literal["ok", "unavailable"]
    ocr_detail: str | None = None
    version: str


class ErrorResponse(BaseModel):
    error: str = Field(..., description="Machine-readable error code")
    message: str = Field(..., description="Human-readable explanation")
    existing_bill_id: int | None = Field(None, description="duplicate_file only: the bill that has this file")

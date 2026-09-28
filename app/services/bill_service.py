"""Orchestrates the full pipeline: validate upload -> store file -> extract
text (PDF text layer / OCR) -> parse -> validate -> persist."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import BillNotFoundError, DatabaseUnavailableError
from app.db.models import Bill
from app.schemas.bill import BillResponse, BillSummary, TariffInput, ValidationDetails
from app.services.bill_parser import BillParser
from app.services.document_processor import DocumentProcessor
from app.services.validation_service import ValidationService
from app.utils.file_utils import delete_upload, generate_stored_filename, save_upload, validate_upload

logger = logging.getLogger(__name__)


def _db_error(exc: SQLAlchemyError) -> DatabaseUnavailableError:
    if isinstance(exc, OperationalError):
        return DatabaseUnavailableError(
            "The database is unavailable. Check DATABASE_URL and that migrations have been run "
            "('alembic upgrade head')."
        )
    return DatabaseUnavailableError("A database error occurred while saving the bill.")


class BillService:
    def __init__(
        self,
        db: Session,
        settings: Settings | None = None,
        processor: DocumentProcessor | None = None,
        parser: BillParser | None = None,
        validator: ValidationService | None = None,
    ):
        self.db = db
        self.settings = settings or get_settings()
        self.processor = processor or DocumentProcessor(settings=self.settings)
        self.parser = parser or BillParser()
        self.validator = validator or ValidationService(self.settings)

    # ------------------------------------------------------------ upload
    def process_upload(self, filename: str | None, content_type: str | None, data: bytes) -> Bill:
        start = time.perf_counter()
        upload = validate_upload(filename, content_type, data, self.settings.max_upload_bytes)
        stored_filename = generate_stored_filename(upload.extension)
        logger.info("Upload accepted: type=%s size=%d bytes stored_as=%s",
                    upload.file_type, len(data), stored_filename)
        save_upload(self.settings.upload_path, stored_filename, data)

        try:
            doc = self.processor.process(data, upload.file_type)
            parsed = self.parser.parse(doc.text)
            validation = self.validator.validate(parsed, doc.ocr_confidence)

            bill = Bill(
                original_filename=upload.original_filename,
                stored_filename=stored_filename,
                file_type=upload.file_type,
                consumer_name=parsed.consumer_name,
                account_number=parsed.account_number,
                billing_period=parsed.billing_period,
                billing_period_start=parsed.billing_period_start,
                billing_period_end=parsed.billing_period_end,
                due_date=parsed.due_date,
                previous_reading=parsed.previous_reading,
                current_reading=parsed.current_reading,
                units_consumed=parsed.units_consumed,
                net_amount_due=parsed.net_amount_due,
                raw_ocr_text=doc.text,
                extraction_method=doc.method,
                page_count=len(doc.pages),
                ocr_confidence=doc.ocr_confidence,
                processing_time_ms=int((time.perf_counter() - start) * 1000),
                parser_name=parsed.parser_name,
                validation_status=validation.status.value,
                validation_details=validation.to_dict(),
                field_sources=parsed.sources,
            )
            self.db.add(bill)
            self.db.commit()
            self.db.refresh(bill)
        except SQLAlchemyError as exc:
            self.db.rollback()
            delete_upload(self.settings.upload_path, stored_filename)
            logger.exception("Database error while saving bill")
            raise _db_error(exc) from exc
        except Exception:
            # Don't leave orphaned files for uploads that could not be processed
            delete_upload(self.settings.upload_path, stored_filename)
            raise

        logger.info("Bill saved: id=%d status=%s total_time=%dms",
                    bill.id, bill.validation_status, bill.processing_time_ms)
        return bill

    # ------------------------------------------------------------- reads
    def get(self, bill_id: int) -> Bill:
        try:
            bill = self.db.get(Bill, bill_id)
        except SQLAlchemyError as exc:
            raise _db_error(exc) from exc
        if bill is None:
            raise BillNotFoundError(f"Bill {bill_id} not found.")
        return bill

    def list(self, limit: int, offset: int, status: str | None = None,
             account_number: str | None = None) -> tuple[int, list[Bill]]:
        query = select(Bill)
        if status:
            query = query.where(Bill.validation_status == status)
        if account_number:
            query = query.where(Bill.account_number == account_number)
        try:
            total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
            items = self.db.scalars(
                query.order_by(Bill.created_at.desc(), Bill.id.desc()).limit(limit).offset(offset)
            ).all()
        except SQLAlchemyError as exc:
            raise _db_error(exc) from exc
        return total, list(items)

    def delete(self, bill_id: int) -> None:
        bill = self.get(bill_id)
        stored = bill.stored_filename
        try:
            self.db.delete(bill)
            self.db.commit()
        except SQLAlchemyError as exc:
            self.db.rollback()
            raise _db_error(exc) from exc
        delete_upload(self.settings.upload_path, stored)
        logger.info("Bill deleted: id=%d", bill_id)


# ------------------------------------------------------------ mapping
def _utc(value: datetime) -> datetime:
    # SQLite drops tzinfo; timestamps are always written in UTC
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def to_response(bill: Bill, include_raw_text: bool = True) -> BillResponse:
    details = ValidationDetails.model_validate(bill.validation_details or {"status": bill.validation_status})
    return BillResponse(
        id=bill.id,
        original_filename=bill.original_filename,
        file_type=bill.file_type,
        consumer_name=bill.consumer_name,
        account_number=bill.account_number,
        billing_period=bill.billing_period,
        billing_period_start=bill.billing_period_start,
        billing_period_end=bill.billing_period_end,
        due_date=bill.due_date,
        previous_reading=bill.previous_reading,
        current_reading=bill.current_reading,
        units_consumed=bill.units_consumed,
        net_amount_due=bill.net_amount_due,
        validation_status=bill.validation_status,
        warnings=details.errors + details.warnings,
        validation_details=details,
        extraction_method=bill.extraction_method,
        page_count=bill.page_count,
        ocr_confidence=bill.ocr_confidence,
        processing_time_ms=bill.processing_time_ms,
        parser_name=bill.parser_name,
        field_sources=bill.field_sources or {},
        raw_ocr_text=bill.raw_ocr_text if include_raw_text else None,
        created_at=_utc(bill.created_at),
        updated_at=_utc(bill.updated_at),
    )


def to_summary(bill: Bill) -> BillSummary:
    summary = BillSummary.model_validate(bill)
    summary.created_at = _utc(summary.created_at)
    return summary


def to_tariff_input(bill: Bill) -> TariffInput:
    return TariffInput(
        bill_id=bill.id,
        account_number=bill.account_number,
        billing_period=bill.billing_period,
        billing_period_start=bill.billing_period_start,
        billing_period_end=bill.billing_period_end,
        units_consumed=bill.units_consumed,
        net_amount_due=bill.net_amount_due,
        validation_status=bill.validation_status,
    )

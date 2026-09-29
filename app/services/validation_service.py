"""Business validation of parsed bills.

Validation never discards data: it produces a status plus details that are
stored next to the extracted values.

  VALID    all required fields present and every consistency check passes
  WARNING  something needs human review: missing fields, units that don't
           reconcile with the readings (adjustments, estimates, net metering...),
           low OCR confidence

Meter check:  units = (current - previous) x MF - open-access units
(MF and open-access units only when printed on the bill). A bill that
reconciles exactly under this formula is VALID; the formula used is
recorded in `calculation` / `notes`.
  INVALID  the extracted values contradict each other or are impossible
           (negative units/amount, current < previous) or nothing usable
           was extracted
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from app.core.config import Settings, get_settings
from app.services.parsing.base import DERIVED_UNITS_SOURCE, REQUIRED_FIELDS, ParsedBill

logger = logging.getLogger(__name__)


class ValidationStatus(str, Enum):
    VALID = "VALID"
    WARNING = "WARNING"
    INVALID = "INVALID"


FIELD_LABELS = {
    "consumer_name": "Consumer name",
    "account_number": "Account number",
    "billing_period": "Billing period",
    "due_date": "Due date",
    "previous_reading": "Previous meter reading",
    "current_reading": "Current meter reading",
    "units_consumed": "Units consumed",
    "net_amount_due": "Net amount due",
}


def _num(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _fmt(value: Decimal) -> str:
    """Human-readable decimal for messages: 10.0000 -> 10, 1234.500000 -> 1234.5 (never 1E+6)."""
    return format(value.normalize(), "f")


@dataclass
class ValidationResult:
    status: ValidationStatus
    meter_reading_check: bool | None = None  # None = could not be checked
    calculated_units: Decimal | None = None
    reported_units: Decimal | None = None
    difference: Decimal | None = None
    multiplying_factor: Decimal | None = None
    open_access_units: Decimal | None = None
    calculation: str | None = None
    notes: list[str] = field(default_factory=list)  # informational, do not affect status
    field_checks: list[dict[str, str]] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form stored in bills.validation_details."""
        return {
            "status": self.status.value,
            "meter_reading_check": self.meter_reading_check,
            "calculated_units": _num(self.calculated_units),
            "reported_units": _num(self.reported_units),
            "difference": _num(self.difference),
            "multiplying_factor": _num(self.multiplying_factor),
            "open_access_units": _num(self.open_access_units),
            "calculation": self.calculation,
            "notes": self.notes,
            "field_checks": self.field_checks,
            "missing_fields": self.missing_fields,
            "warnings": self.warnings,
            "errors": self.errors,
        }


class ValidationService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def validate(self, bill: ParsedBill, ocr_confidence: float | None = None) -> ValidationResult:
        result = ValidationResult(status=ValidationStatus.VALID)
        result.notes.extend(bill.notes)
        self._check_presence(bill, result)
        self._check_values(bill, result)
        self._check_meter_readings(bill, result)
        self._check_dates(bill, result)
        if ocr_confidence is not None and ocr_confidence < self.settings.OCR_LOW_CONFIDENCE_THRESHOLD:
            result.warnings.append(
                f"Low OCR confidence ({ocr_confidence:.0f}%). Please verify the extracted values."
            )

        if result.errors or len(result.missing_fields) == len(REQUIRED_FIELDS):
            if len(result.missing_fields) == len(REQUIRED_FIELDS):
                result.errors.append("No bill fields could be extracted from the document.")
            result.status = ValidationStatus.INVALID
        elif result.warnings:
            result.status = ValidationStatus.WARNING
        logger.info(
            "Validation result: status=%s meter_check=%s missing=%d warnings=%d errors=%d",
            result.status.value, result.meter_reading_check, len(result.missing_fields),
            len(result.warnings), len(result.errors),
        )
        return result

    # ------------------------------------------------------------------
    @staticmethod
    def _set_field_status(result: ValidationResult, fld: str, status: str, message: str = "") -> None:
        for check in result.field_checks:
            if check["field"] == fld:
                check["status"] = status
                if message:
                    check["message"] = message
                return
        entry = {"field": fld, "status": status}
        if message:
            entry["message"] = message
        result.field_checks.append(entry)

    def _check_presence(self, bill: ParsedBill, result: ValidationResult) -> None:
        for fld in REQUIRED_FIELDS:
            value = getattr(bill, fld)
            if value is None or (isinstance(value, str) and not value.strip()):
                result.missing_fields.append(fld)
                self._set_field_status(result, fld, "missing")
                result.warnings.append(f"{FIELD_LABELS[fld]} could not be confidently extracted")
            else:
                self._set_field_status(result, fld, "ok")

    def _check_values(self, bill: ParsedBill, result: ValidationResult) -> None:
        for fld in ("previous_reading", "current_reading", "units_consumed", "net_amount_due"):
            value = getattr(bill, fld)
            if value is not None and value < 0:
                msg = f"{FIELD_LABELS[fld]} cannot be negative ({value})"
                result.errors.append(msg)
                self._set_field_status(result, fld, "invalid", msg)

    @staticmethod
    def _resolution(*values: Decimal) -> Decimal:
        """Smallest printed step of the readings, e.g. 0.001 for '100.500'."""
        exponent = min(v.as_tuple().exponent for v in values)  # type: ignore[type-var]
        return Decimal(1).scaleb(min(int(exponent), 0))

    def _check_meter_readings(self, bill: ParsedBill, result: ValidationResult) -> None:
        prev, curr, units = bill.previous_reading, bill.current_reading, bill.units_consumed
        mf, open_access = bill.multiplying_factor, bill.open_access_units
        result.reported_units = units
        result.multiplying_factor = mf
        result.open_access_units = open_access
        if prev is None or curr is None:
            result.meter_reading_check = None
            if units is not None:
                result.warnings.append("Meter readings incomplete; units consumed could not be cross-checked")
            return

        if curr < prev:
            # Meter rollover / replacement is possible but must be reviewed
            msg = f"Current reading ({curr}) is lower than previous reading ({prev})"
            result.errors.append(msg)
            self._set_field_status(result, "current_reading", "invalid", msg)
            result.meter_reading_check = False
            result.calculated_units = curr - prev
            return

        factor = mf if mf is not None and mf > 0 else Decimal(1)
        calculated = (curr - prev) * factor
        formula = f"({_fmt(curr)} - {_fmt(prev)})"
        if factor != 1:
            formula += f" x {_fmt(factor)}"
        if open_access:
            calculated -= open_access
            formula += f" - {_fmt(open_access)} open-access units"
        result.calculated_units = calculated
        result.calculation = f"{formula} = {_fmt(calculated)}"
        if units is None:
            result.meter_reading_check = None
            return
        if bill.sources.get("units_consumed") == DERIVED_UNITS_SOURCE:
            # Calculated from these same readings, so there is nothing independent to compare
            result.meter_reading_check = None
            result.notes.append(f"Units consumed is not printed on the bill; calculated from the meter readings: "
                                f"{result.calculation}")
            if mf is None:
                result.warnings.append("Units consumed was calculated without a multiplying factor (none found "
                                       "on the bill); please verify")
            return

        # A reading printed to 0.001 multiplied by a large MF is only accurate to MF x 0.001
        tolerance = max(Decimal(str(self.settings.METER_READING_TOLERANCE)),
                        factor * self._resolution(prev, curr))
        difference = units - calculated
        result.difference = difference
        if abs(difference) <= tolerance:
            result.meter_reading_check = True
            if factor != 1:
                result.notes.append(f"Units include the meter multiplying factor {_fmt(factor)}")
            if open_access:
                result.notes.append(f"{_fmt(open_access)} open-access units are excluded from billed consumption")
            return

        result.meter_reading_check = False
        result.warnings.append(
            f"Meter readings do not reconcile with reported units: {result.calculation}, "
            f"but the bill reports {_fmt(units)} (difference {_fmt(difference)})"
        )
        self._set_field_status(result, "units_consumed", "mismatch",
                               "Does not equal (current - previous) x MF - open-access units")

    def _check_dates(self, bill: ParsedBill, result: ValidationResult) -> None:
        start, end = bill.billing_period_start, bill.billing_period_end
        if start and end and end < start:
            msg = "Billing period end date is before its start date"
            result.errors.append(msg)
            self._set_field_status(result, "billing_period", "invalid", msg)
        if bill.due_date and end and bill.due_date < end:
            result.warnings.append("Due date is before the end of the billing period")
        today = date.today()
        if bill.due_date and bill.due_date.year > today.year + 1:
            result.warnings.append("Due date is unusually far in the future; please verify")

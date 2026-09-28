import json
from datetime import date
from decimal import Decimal

import pytest

from app.services.parsing.base import ParsedBill
from app.services.validation_service import ValidationService, ValidationStatus


def make_bill(**overrides) -> ParsedBill:
    values = dict(
        consumer_name="RAMESH KUMAR", account_number="170012345678",
        billing_period="2025-05-01 to 2025-05-31",
        billing_period_start=date(2025, 5, 1), billing_period_end=date(2025, 5, 31),
        due_date=date(2025, 6, 16), previous_reading=Decimal("1000"),
        current_reading=Decimal("1350"), units_consumed=Decimal("350"),
        net_amount_due=Decimal("2450.50"),
    )
    values.update(overrides)
    return ParsedBill(**values)


@pytest.fixture()
def validator():
    return ValidationService()


def test_matching_readings_is_valid(validator):
    result = validator.validate(make_bill())
    assert result.status == ValidationStatus.VALID
    assert result.meter_reading_check is True
    assert result.calculated_units == Decimal("350")
    assert result.difference == Decimal("0")
    assert result.warnings == [] and result.errors == []


def test_mismatched_units_is_warning_not_rejected(validator):
    result = validator.validate(make_bill(units_consumed=Decimal("300")))
    assert result.status == ValidationStatus.WARNING
    assert result.meter_reading_check is False
    assert result.calculated_units == Decimal("350")
    assert result.reported_units == Decimal("300")
    assert result.difference == Decimal("-50")
    assert any("do not reconcile with reported units" in w for w in result.warnings)
    assert {"field": "units_consumed", "status": "mismatch",
            "message": "Does not equal (current - previous) x MF - open-access units"} in result.to_dict()["field_checks"]


def test_within_tolerance_is_valid(validator):
    assert validator.validate(make_bill(units_consumed=Decimal("350.4"))).status == ValidationStatus.VALID


def test_multiplying_factor_is_part_of_the_formula(validator):
    result = validator.validate(make_bill(units_consumed=Decimal("700"), multiplying_factor=Decimal("2")))
    assert result.status == ValidationStatus.VALID
    assert result.meter_reading_check is True
    assert result.calculation == "(1350 - 1000) x 2 = 700"
    assert any("multiplying factor 2" in n for n in result.notes)
    assert result.warnings == []


def test_multiplying_factor_mismatch_still_warns(validator):
    result = validator.validate(make_bill(units_consumed=Decimal("650"), multiplying_factor=Decimal("2")))
    assert result.status == ValidationStatus.WARNING
    assert result.difference == Decimal("-50")


def test_large_meter_constant_uses_reading_precision():
    """GESCOM-style: readings to 0.001 x meter constant 175000 -> accurate to 175 units."""
    bill = make_bill(previous_reading=Decimal("100.000"), current_reading=Decimal("100.500"),
                     multiplying_factor=Decimal("175000"), units_consumed=Decimal("87500"))
    result = ValidationService().validate(bill)
    assert result.status == ValidationStatus.VALID
    assert result.calculated_units == Decimal("87500")


def test_open_access_units_are_excluded(validator):
    """APDCL-style: (current - previous) x MF - open-access units = units consumed."""
    bill = make_bill(previous_reading=Decimal("6000.000"), current_reading=Decimal("7800.000"),
                     multiplying_factor=Decimal("1.000"), open_access_units=Decimal("1400.000"),
                     units_consumed=Decimal("400.000"))
    result = validator.validate(bill)
    assert result.status == ValidationStatus.VALID
    assert result.difference == 0
    assert any("open-access" in n for n in result.notes)


def test_parser_notes_are_passed_through(validator):
    bill = make_bill()
    bill.notes.append("Due date year (2025) taken from the bill month")
    result = validator.validate(bill)
    assert result.status == ValidationStatus.VALID
    assert result.to_dict()["notes"] == ["Due date year (2025) taken from the bill month"]


def test_current_less_than_previous_is_invalid(validator):
    result = validator.validate(make_bill(previous_reading=Decimal("1400")))
    assert result.status == ValidationStatus.INVALID
    assert result.meter_reading_check is False
    assert any("lower than previous" in e for e in result.errors)


@pytest.mark.parametrize("fld", ["units_consumed", "net_amount_due"])
def test_negative_values_are_invalid(validator, fld):
    assert validator.validate(make_bill(**{fld: Decimal("-5")})).status == ValidationStatus.INVALID


def test_missing_field_produces_warning_and_field_status(validator):
    result = validator.validate(make_bill(due_date=None))
    assert result.status == ValidationStatus.WARNING
    assert result.missing_fields == ["due_date"]
    assert {"field": "due_date", "status": "missing"} in result.field_checks
    assert "Due date could not be confidently extracted" in result.warnings


def test_blank_account_number_is_flagged(validator):
    result = validator.validate(make_bill(account_number="  "))
    assert "account_number" in result.missing_fields
    assert result.status == ValidationStatus.WARNING


def test_missing_readings_cannot_be_checked(validator):
    result = validator.validate(make_bill(previous_reading=None))
    assert result.meter_reading_check is None
    assert result.status == ValidationStatus.WARNING


def test_nothing_extracted_is_invalid(validator):
    result = validator.validate(ParsedBill())
    assert result.status == ValidationStatus.INVALID
    assert len(result.missing_fields) == 8


def test_low_ocr_confidence_warns(validator):
    result = validator.validate(make_bill(), ocr_confidence=35.0)
    assert result.status == ValidationStatus.WARNING
    assert any("Low OCR confidence" in w for w in result.warnings)


def test_reversed_billing_period_is_invalid(validator):
    assert validator.validate(make_bill(billing_period_start=date(2025, 6, 1))).status == ValidationStatus.INVALID


def test_to_dict_is_json_serialisable(validator):
    json.dumps(validator.validate(make_bill(units_consumed=Decimal("300"))).to_dict())

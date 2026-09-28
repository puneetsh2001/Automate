"""Provider-parser tests on fictional text laid out like the real utility bills.

The real reference bills live in sample_bills/private/ (git-ignored); these
snippets reproduce their layouts with made-up values so the logic stays
tested on any machine.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.services.bill_parser import BillParser
from app.services.validation_service import ValidationService, ValidationStatus


@pytest.fixture(scope="module")
def parser():
    return BillParser(extra_aliases_file="")


APDCL_TEXT = """
                                         Assam Power Distribution Company Limited
website : www.apdcl.org
 Consumer Name: ACME STEELS NORTH-EAST PRIVATE    Consumer Number: 006099990001                  Bill Amount: 1234567.00
 Ltd
 Address: Somewhere, Kamrup (M)                   Old Consumer Number: HT-II/IND/S-999           Due Date: 27-April-2026
                                                                                                 Bill Period: 01-Mar-2026 To 31-Mar-2026
 Reading Type     Meter Number     MF              Previous         Previous Export  Current Reading  Current Export  Difference
 KWH(Solar)       Q0000001         1.000           1000.000         0                1600.000         0                600.000          0
 KWH(Peak)        Q0000001         1.000           2000.000         0                2300.000         0                300.000          0
 KWH(Normal)      Q0000001         1.000           3000.000         0                3900.000         0                900.000          0
 Open Access Units Solar  500.000              Open Access Units Peak   200.000               Open Access Units        700.000
                                                                                               Normal
 Unit Consumed      PF Penalty/Rebate  LT Metering        DTR Penalty       Billable Units in
 Solar | 100.000    -3.000             0                  0                 94.000
 Peak | 100.000     -3.000             0                  0                 94.000
 Normal |           -6.000             0                  0                 188.000
 200.000
 Unit Consumed     235969.9  177463.0   533645.4   461401.1  359132.0   326544.1
 Current Demand               Outstanding Amount          Net Bill Amount
 1234567.00                   0                           1234567.00
 Payable amount before due date                     1234567.000
"""


def test_apdcl_tod_bill(parser):
    bill = parser.parse(APDCL_TEXT)
    assert bill.parser_name == "apdcl"
    assert bill.consumer_name == "ACME STEELS NORTH-EAST PRIVATE Ltd"   # wrapped name joined
    assert bill.account_number == "006099990001"
    assert bill.billing_period == "2026-03-01 to 2026-03-31"
    assert bill.due_date == date(2026, 4, 27)
    assert bill.previous_reading == Decimal("6000.000")    # sum of TOD registers
    assert bill.current_reading == Decimal("7800.000")
    assert bill.open_access_units == Decimal("1400.000")
    assert bill.units_consumed == Decimal("400.000")        # 100 + 100 + wrapped 200
    assert bill.net_amount_due == Decimal("1234567.00")
    result = ValidationService().validate(bill)
    assert result.status == ValidationStatus.VALID          # 1800 x 1 - 1400 = 400
    assert result.calculated_units == Decimal("400")


def test_apdcl_missing_zone_value_gives_null_not_partial_sum(parser):
    text = APDCL_TEXT.replace(" Normal |           -6.000", " Normal |    ???    -6.000").replace(" 200.000\n", "\n")
    assert parser.parse(text).units_consumed is None


JVVNL_TEXT = """
                                                    JAIPUR VIDYUT VITRAN NIGAM LIMITED.
    K No:       210000000001       Acc No:      90000001    Consumer Status:       R         Bill No:    080000001
 Billing Month  Tariff Code  Area code  Ind.cod     M.Class     Reading Date     Bill Issue   Due Date Of     Bill Duration  Consumer Name & Address.
                                           e       Accuracy                        Date         Payment
                                                                                                                            M/S Example Forgings Ltd. null
   202508         8000          U         25         0.5s       01-Aug-2025    04-08-2025     14-08-2025        1.0000
     Meter No.       Nature Of Meter     Present Reading      Last Reading        Difference             MF             Consumption
         1                  2                   3                  4                (3-4)=5               6               (5 x 6)=7
      123456 1            KWH              1500.5000           1000.5000           500.0000            10.0000           5000.0000
      123456 2            KVAH             1600.0000           1050.0000           550.0000            10.0000           5500.0000
   Billing Demand        Av. P.F         Test/Open access     DS/NDS/ML/      Net KWH Cons. To
                                              Units        LOCKADJ.UNITS        Bill at LIP rate
      225.0000            0.990               0.000               0.00             5000.00
         NET ND                    NET ED                  NET W.C.C.                 NET UC                   NET TCS               Net Payable Amount
        50000.00                   2000.00                   500.00                    0.00                      0.00                     52500
 Bill Month      202507     202506     202505     202504
 Consumption     4900.00    5100.00    4800.00    5000.00
 Bank Details for           Beneficiary : JVVNL
 payment through        IFSC Code : YESB0CMSNOC
  RTGS/NEFT           Account No. : JVVNL1210000000001
"""


def test_rajasthan_discom_bill(parser):
    bill = parser.parse(JVVNL_TEXT)
    assert bill.parser_name == "rajasthan_discom"
    assert bill.consumer_name == "M/S Example Forgings Ltd"
    assert bill.account_number == "210000000001"          # K No, not the bank account
    assert bill.billing_period == "August 2025"            # not the history row's 202507
    assert bill.due_date == date(2025, 8, 14)              # header wraps onto 2 lines
    assert bill.previous_reading == Decimal("1000.5000")   # KWH row, not the column numbers
    assert bill.current_reading == Decimal("1500.5000")
    assert bill.multiplying_factor == Decimal("10.0000")
    assert bill.units_consumed == Decimal("5000.0000")
    assert bill.net_amount_due == Decimal("52500.00")
    assert ValidationService().validate(bill).status == ValidationStatus.VALID


GESCOM_TEXT = """
                               Gulbarga Electricity Supply Company Limited
                                                            E HT BILL
Bill for the supply of Electrical Energy for the Month of                                  December      2025
Name of the Firm:M/S Example Cements Private LTD @ Some Road in Some Sub Divn
R.R. No:       EHT 9                                      Last Date of Payment:                    16th of   January
  Particulars                      Initial                    Meter     Consumption in             Remarks
                Final Reading                 Difference    Constant         Units
                                 Reading
Main MR               100.500        100.000        0.500       175000           87500
Zone-4                 50.200         50.000        0.200       175000           35000            22.00 to 6.00 hrs
 15. Grand Totat: ( item No. 5+6+7+8+9+10+11)|                                                           Rs.     950000.40
               |              |             |                                               Say          Rs.       950000
"""


def test_karnataka_escom_bill(parser):
    bill = parser.parse(GESCOM_TEXT)
    assert bill.parser_name == "karnataka_escom"
    assert bill.consumer_name == "M/S Example Cements Private LTD"
    assert bill.account_number == "EHT 9"
    assert bill.billing_period == "December 2025"
    assert bill.due_date == date(2026, 1, 16)              # January after a December bill
    assert any("taken from the bill month" in n for n in bill.notes)
    assert bill.previous_reading == Decimal("100.000")
    assert bill.current_reading == Decimal("100.500")
    assert bill.multiplying_factor == Decimal("175000")
    assert bill.units_consumed == Decimal("87500")
    assert bill.net_amount_due == Decimal("950000.00")     # "Say Rs." rounded payable
    assert ValidationService().validate(bill).status == ValidationStatus.VALID


def test_non_bill_document_extracts_nothing(parser):
    text = """CONZERV Systems Pvt. Ltd.        EM 6400 DigitAN Series Register Map
Sl.No.   Name        Description             Address   Datatype
1.01     SxNy_VA     Apparent Power - avg    43901     Float
1.02     SxNy_W      Active Power -avg       43903     Float
"""
    bill = parser.parse(text)
    assert len(bill.missing_fields()) == 8
    assert ValidationService().validate(bill).status == ValidationStatus.INVALID

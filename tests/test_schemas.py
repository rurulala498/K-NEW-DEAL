import json
from decimal import Decimal
from pathlib import Path

import pytest

from schemas import EngineResult, ExtractionResult

FIXTURES = Path(__file__).with_name("fixtures")


@pytest.mark.parametrize(
    ("file_name", "hourly_wage", "actual_holiday_pay", "status"),
    [
        ("normal_case.json", 10500, 182490, "ok"),
        ("review_case.json", 10000, 0, "needs_review"),
    ],
)
def test_case_fixture(file_name, hourly_wage, actual_holiday_pay, status):
    case = json.loads((FIXTURES / file_name).read_text(encoding="utf-8"))
    extraction = ExtractionResult.model_validate(case["extraction"])
    engine = EngineResult.model_validate(case["engine"])

    assert extraction.user_confirmed is True
    assert extraction.contract.hourly_wage.value == Decimal(hourly_wage)
    assert extraction.contract.weekly_hours.value == Decimal(20)
    assert extraction.extra_answers.workplace_size.value == "5_or_more"

    holiday_pay = next(item for item in engine.comparison if item.item == "주휴수당")
    assert holiday_pay.actual == Decimal(actual_holiday_pay)
    assert holiday_pay.status == status
    assert holiday_pay.difference == holiday_pay.expected - holiday_pay.actual
    assert engine.checks[0].status == status


def test_missing_values_and_unclassified_item():
    case = json.loads((FIXTURES / "normal_case.json").read_text(encoding="utf-8"))
    extraction = case["extraction"]
    extraction["contract"]["hourly_wage"] = {"value": None, "confidence": "low"}
    item = extraction["payslip"]["items"]["value"][0]
    item["name"] = {"value": "수당", "confidence": "low"}
    item["category"] = {"value": "unclassified", "confidence": "low"}
    extraction["extra_answers"]["workplace_size"]["value"] = "unknown"
    extraction["user_confirmed"] = False

    result = ExtractionResult.model_validate(extraction)

    assert result.contract.hourly_wage.value is None
    assert result.contract.hourly_wage.confidence == "low"
    assert result.payslip.items.value[0].category.value == "unclassified"
    assert result.extra_answers.workplace_size.value == "unknown"
    assert result.user_confirmed is False


def test_expected_pay_accepts_both_workplace_scenarios():
    case = json.loads((FIXTURES / "normal_case.json").read_text(encoding="utf-8"))
    engine = case["engine"]
    scenario = engine["expected_pay"][0]
    engine["expected_pay"].append({**scenario, "workplace_size": "under_5"})

    result = EngineResult.model_validate(engine)

    assert [scenario.workplace_size for scenario in result.expected_pay] == [
        "5_or_more",
        "under_5",
    ]

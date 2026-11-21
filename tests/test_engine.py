import json
from decimal import Decimal
from pathlib import Path

import pytest

from engine import run_engine
from schemas import ExtractionResult

FIXTURES = Path(__file__).with_name("fixtures")


def load_extraction(case="normal_case"):
    data = json.loads((FIXTURES / f"{case}.json").read_text(encoding="utf-8"))
    return data["extraction"]


def run(data, year=2026):
    return run_engine(ExtractionResult.model_validate(data), year)


def check(result, key):
    return next(row for row in result.checks if key in row.evidence_ids)


def amount(result, item, period="weekly", scenario=0):
    return next(
        row.amount
        for row in result.expected_pay[scenario].items
        if row.item == item and row.period == period
    )


def use_night_schedule(data):
    data["contract"]["work_start"]["value"] = "21:00"
    data["contract"]["work_end"]["value"] = "02:00"
    data["contract"]["break_minutes"]["value"] = 0


def test_normal_monthly_pay_and_calculation_details():
    result = run(load_extraction())

    assert amount(result, "기본급") == 210000
    assert amount(result, "주휴수당") == 42000
    assert amount(result, "합계", "monthly") == 1094940
    assert all(row.status == "ok" for row in result.checks + result.comparison)
    assert any(row.reason == "기준 확인 필요" for row in result.limitations)
    for row in result.expected_pay[0].items:
        assert row.formula
        assert row.applied_condition
        assert row.standards_year == 2026


def test_minimum_wage_shortfall():
    result = run(load_extraction("review_case"))

    diagnosis = check(result, "minimum_wage")
    assert diagnosis.status == "needs_review"
    assert "320원" in diagnosis.description


@pytest.mark.parametrize(("weekly_hours", "expected"), [(14, 0), (15, 31500), (50, 84000)])
def test_weekly_holiday_threshold_and_cap(weekly_hours, expected):
    data = load_extraction()
    data["contract"]["weekly_hours"]["value"] = weekly_hours

    assert amount(run(data), "주휴수당") == expected


@pytest.mark.parametrize(("daily", "weekly", "days", "weekly_premium"), [
    (9, 36, ["월", "화", "수", "목"], 21000),
    (8, 48, ["월", "화", "수", "목", "금", "토"], 42000),
    (9, 45, ["월", "화", "수", "목", "금"], 26250),
])
def test_overtime_uses_daily_or_weekly_excess_without_double_counting(
    daily, weekly, days, weekly_premium
):
    data = load_extraction()
    data["contract"]["daily_hours"]["value"] = daily
    data["contract"]["weekly_hours"]["value"] = weekly
    data["contract"]["work_days"]["value"] = days

    assert amount(run(data), "연장 가산") == weekly_premium


def test_under_five_does_not_apply_premiums():
    data = load_extraction()
    use_night_schedule(data)
    data["contract"]["daily_hours"]["value"] = 9
    data["extra_answers"]["workplace_size"]["value"] = "under_5"
    result = run(data)

    assert amount(result, "야간 가산") == 0
    assert amount(result, "연장 가산") == 0


def test_unknown_workplace_size_includes_both_scenarios():
    data = load_extraction()
    use_night_schedule(data)
    data["extra_answers"]["workplace_size"]["value"] = "unknown"
    result = run(data)

    assert [row.workplace_size for row in result.expected_pay] == ["5_or_more", "under_5"]
    assert amount(result, "야간 가산", scenario=0) == 84000
    assert amount(result, "야간 가산", scenario=1) == 0
    assert check(result, "overtime_night").status == "needs_review"
    assert {row.workplace_size for row in result.comparison} == {"5_or_more", "under_5"}


@pytest.mark.parametrize(("start", "end", "break_minutes", "premium"), [
    ("05:00", "10:00", 0, 21000),
    ("23:00", "04:00", 30, 94500),
    ("06:00", "11:00", 0, 0),
])
def test_night_window_boundaries(start, end, break_minutes, premium):
    data = load_extraction()
    data["contract"]["work_start"]["value"] = start
    data["contract"]["work_end"]["value"] = end
    data["contract"]["break_minutes"]["value"] = break_minutes

    assert amount(run(data), "야간 가산") == premium


@pytest.mark.parametrize(("daily", "minutes", "status"), [
    (4, 29, "needs_review"),
    (4, 30, "ok"),
    (8, 59, "needs_review"),
    (8, 60, "ok"),
])
def test_break_time_threshold(daily, minutes, status):
    data = load_extraction()
    data["contract"]["daily_hours"]["value"] = daily
    data["contract"]["break_minutes"]["value"] = minutes

    assert check(run(data), "break_time").status == status


def test_missing_required_terms_are_named():
    data = load_extraction()
    data["contract"]["workplace"]["value"] = None
    data["contract"]["annual_leave"]["value"] = None
    diagnosis = check(run(data), "required_terms")

    assert diagnosis.status == "needs_review"
    assert "근무 장소" in diagnosis.description
    assert "연차유급휴가" in diagnosis.description


@pytest.mark.parametrize("term_type", ["penalty", "wage_deduction"])
def test_special_terms_are_flagged(term_type):
    data = load_extraction()
    data["contract"]["special_terms"]["value"] = [{
        "type": {"value": term_type, "confidence": "high"},
        "text": {"value": "가상의 특약", "confidence": "high"},
    }]

    assert check(run(data), "special_terms").status == "needs_review"


def test_unpaid_weekly_holiday_pay_is_compared():
    result = run(load_extraction("review_case"))
    row = next(item for item in result.comparison if item.item == "주휴수당")

    assert (row.expected, row.actual, row.difference, row.status) == (
        Decimal(173800), Decimal(0), Decimal(173800), "needs_review"
    )


@pytest.mark.parametrize(("difference", "status"), [(100, "ok"), (101, "needs_review"), (-1, "ok")])
def test_comparison_respects_configured_tolerance(difference, status):
    data = load_extraction()
    data["payslip"]["items"]["value"][0]["amount"]["value"] = 912450 - difference
    row = next(item for item in run(data).comparison if item.item == "기본급")

    assert row.difference == difference
    assert row.status == status


def test_unclassified_payslip_item_is_excluded():
    data = load_extraction()
    data["payslip"]["items"]["value"][1]["category"]["value"] = "unclassified"
    result = run(data)
    row = next(item for item in result.comparison if item.item == "주휴수당")

    assert row.actual is None and row.difference is None
    assert any("분류 불가" in row.reason for row in result.limitations)


def test_missing_wage_excludes_calculation():
    data = load_extraction()
    data["contract"]["hourly_wage"]["value"] = None
    result = run(data)

    assert all(row.item not in ("기본급", "합계") for row in result.expected_pay[0].items)
    assert any(row.item == "기본급" for row in result.limitations)


def test_unconfirmed_input_returns_without_calculation():
    data = load_extraction()
    data["user_confirmed"] = False
    result = run(data)

    assert not result.checks and not result.expected_pay and not result.comparison
    assert "사용자 확인" in result.limitations[0].reason


def test_missing_standards_year_returns_limitation():
    result = run(load_extraction(), year=9999)

    assert not result.expected_pay
    assert result.limitations[0].reason == "등록되지 않은 기준 연도"


@pytest.mark.parametrize("size", ["5_or_more", "unknown"])
def test_contract_only_keeps_checks_and_pay_without_flagging_comparison(size):
    data = load_extraction("review_case")
    data["extra_answers"]["workplace_size"]["value"] = size
    baseline = run(data)
    data["documents_provided"] = {"contract": True, "payslip": False}
    result = run(data)

    assert result.checks == baseline.checks
    assert result.expected_pay == baseline.expected_pay
    assert [
        (row.workplace_size, row.item, row.expected) for row in result.comparison
    ] == [
        (scenario.workplace_size, row.item, row.amount)
        for scenario in result.expected_pay for row in scenario.items if row.period == "monthly"
    ]
    assert all(row.status == "not_compared" and row.actual is None and row.difference is None
               for row in result.comparison)
    assert sum(row.status == "needs_review" for row in result.checks + result.comparison) == sum(
        row.status == "needs_review" for row in baseline.checks
    )
    assert [row.reason for row in result.limitations if row.item == "지급액 대조"] == [
        "급여명세서를 올리지 않아 실제 지급액과 비교하지 않았습니다.",
    ]


def test_submitted_payslip_without_values_still_requires_review():
    data = load_extraction()
    for field in data["payslip"].values():
        field["value"] = None

    extraction = ExtractionResult.model_validate(data)
    assert extraction.documents_provided.contract and extraction.documents_provided.payslip
    result = run_engine(extraction)
    assert result.comparison and all(row.status == "needs_review" for row in result.comparison)
    assert any("해당 지급액을 확인할 수 없습니다" in row.reason for row in result.limitations)

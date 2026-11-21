from datetime import time

from config import load_standards
from schemas import EngineResult, ExtractionResult
from schemas.engine import Limitation, PayBasis, ScheduleDetail

from .checks import check_contract, diagnose
from .comparison import compare_pay
from .pay import calculate_pay
from .schedule import hours_text, measure_shift, schedule_hours, valid_schedule_days


def _without_calculation(reason: str) -> EngineResult:
    return EngineResult(
        checks=[], expected_pay=[], comparison=[],
        limitations=[Limitation(item="급여 계산", reason=reason)],
    )


def run_engine(extraction: ExtractionResult, year: int = 2026) -> EngineResult:
    if not extraction.user_confirmed:
        return _without_calculation("추출값과 추가 답변에 대한 사용자 확인이 필요합니다.")

    standards = load_standards(year)
    if isinstance(standards, str):
        return _without_calculation(standards)

    checks = check_contract(extraction.contract, standards)
    size = extraction.extra_answers.workplace_size.value
    if size in ("5_or_more", "under_5"):
        sizes = (size,)
    else:
        sizes = ("5_or_more", "under_5")
        threshold = standards["premium_min_workplace_size"]["value"]
        checks.append(diagnose(
            "사업장 규모", "overtime_night",
            f"사업장 규모 확인이 필요합니다. {threshold}인 이상과 미만을 모두 계산했습니다.",
            needs_review=True,
        ))

    limitations = []
    details = []
    shifts = [measure_shift(entry) for entry in extraction.contract.work_schedule.value or []]
    if valid_schedule_days(shifts):
        window = standards["night_work_hours"]["value"]
        start, end = time.fromisoformat(window["start"]), time.fromisoformat(window["end"])
        for shift in shifts:
            night, upper = shift.night(start, end)
            details.append(ScheduleDetail(
                days=list(shift.days), work_hours=shift.net_hours,
                break_minutes=None if shift.break_seconds is None else shift.break_seconds / 60,
                night_hours=night, night_is_upper_bound=upper,
            ))
        total = schedule_hours(shifts)
        weekly_hours = extraction.contract.weekly_hours.value
        if total is not None and weekly_hours is not None and total != weekly_hours:
            limitations.append(Limitation(
                item="요일별 근무 시간",
                reason=f"요일별 시간을 더하면 주 {hours_text(total)}시간이에요. 계약서의 주 소정근로시간 "
                       f"{hours_text(weekly_hours)}시간과 달라요. 기본급과 주휴수당은 주 소정근로시간을 사용해요.",
            ))
    elif shifts:
        limitations.append(Limitation(item="요일별 근무 시간", reason="근무 시각, 휴게 구간 또는 중복 요일을 확인해 주세요."))
    bases: tuple[PayBasis, ...] = ("contract",)
    wage = extraction.contract.hourly_wage.value
    if wage is not None and wage < standards["minimum_hourly_wage"]["value"]:
        bases = ("contract", "minimum_wage")
        limitations.append(Limitation(
            item="최저임금 기준 예상 급여",
            reason="수습 감액, 최저임금 적용 제외, 최저임금에 포함되는 수당이 있으면 금액이 달라질 수 있어요.",
        ))
    expected_pay = [
        calculate_pay(extraction.contract, scenario_size, standards, year, limitations, basis=basis)
        for scenario_size in sizes
        for basis in bases
    ]
    comparison = compare_pay(
        expected_pay, extraction.payslip if extraction.documents_provided.payslip else None,
        standards["comparison_tolerance_won"]["value"], limitations,
    )
    unique_limitations = list({(row.item, row.reason): row for row in limitations}.values())
    return EngineResult(
        checks=checks,
        expected_pay=expected_pay,
        comparison=comparison,
        limitations=unique_limitations,
        schedule_details=details,
    )

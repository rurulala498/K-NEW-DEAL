from schemas.engine import CheckResult
from schemas.extraction import Contract

REQUIRED_TERMS = {
    "hourly_wage": "임금",
    "weekly_hours": "소정근로시간",
    "holidays": "휴일",
    "annual_leave": "연차유급휴가",
    "workplace": "근무 장소",
    "job_description": "업무 내용",
}

SPECIAL_TERM_TYPES = {
    "penalty": "위약금 예정",
    "wage_deduction": "임금 공제",
}


def diagnose(item, key, description, *, needs_review=False):
    return CheckResult(
        item=item,
        status="needs_review" if needs_review else "ok",
        description=description,
        evidence_ids=[key],
    )


def _check_minimum_wage(contract, minimum):
    wage = contract.hourly_wage.value
    if wage is None:
        return diagnose("최저임금", "minimum_wage", "시급을 확인해 주세요.", needs_review=True)
    if wage < minimum:
        return diagnose(
            "최저임금", "minimum_wage",
            f"계약 시급이 기준 {minimum:,}원보다 시간당 {minimum - wage:,}원 낮습니다.",
            needs_review=True,
        )
    return diagnose("최저임금", "minimum_wage", f"계약 시급이 기준 {minimum:,}원 이상입니다.")


def _check_break_time(contract, rules):
    hours = contract.daily_hours.value
    minutes = contract.break_minutes.value
    if hours is None:
        return diagnose("휴게시간", "break_time", "하루 근로시간을 확인해 주세요.", needs_review=True)
    minimum = max(
        (rule["break_minutes"] for rule in rules if hours >= rule["work_hours"]),
        default=0,
    )
    if minimum == 0:
        return diagnose("휴게시간", "break_time", "입력한 근로시간은 휴게시간 점검 기준 미만입니다.")
    if minutes is None:
        return diagnose("휴게시간", "break_time", "휴게시간을 확인해 주세요.", needs_review=True)
    return diagnose(
        "휴게시간", "break_time",
        f"계약서의 휴게시간은 {minutes}분이고, 해당 근로시간의 기준은 {minimum}분입니다.",
        needs_review=minutes < minimum,
    )


def _check_required_terms(contract):
    missing = [label for field, label in REQUIRED_TERMS.items() if getattr(contract, field).value is None]
    description = f"확인할 기재사항: {', '.join(missing)}" if missing else "점검 대상 기재사항이 모두 있습니다."
    return diagnose("필수 기재사항", "required_terms", description, needs_review=bool(missing))


def _check_special_terms(contract):
    terms = contract.special_terms.value
    if terms is None or any(term.type.value is None for term in terms):
        return diagnose("특약", "special_terms", "특약 내용을 확인해 주세요.", needs_review=True)
    flagged = [
        label for term_type, label in SPECIAL_TERM_TYPES.items()
        if any(term.type.value == term_type for term in terms)
    ]
    description = f"확인할 특약 유형: {', '.join(flagged)}" if flagged else "점검 대상 특약 유형이 없습니다."
    return diagnose("특약", "special_terms", description, needs_review=bool(flagged))


def check_contract(contract: Contract, standards: dict) -> list[CheckResult]:
    return [
        _check_minimum_wage(contract, standards["minimum_hourly_wage"]["value"]),
        _check_break_time(contract, standards["break_time"]["value"]),
        _check_required_terms(contract),
        _check_special_terms(contract),
    ]

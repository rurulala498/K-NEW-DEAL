from schemas.engine import CheckResult
from schemas.extraction import Contract

BASIS_KEYS = (
    "minimum_wage", "minimum_wage_2026", "break_time", "weekly_holiday_pay",
    "required_terms", "overtime_night", "overtime_limit", "part_time",
    "penalty_clause", "wage_deduction", "wage_payment", "working_hours",
    "annual_leave", "workplace_size", "part_time_overtime",
)

CHECK_BASIS = {
    "minimum_wage": ("minimum_wage", "minimum_wage_2026"),
    "break_time": ("break_time",),
    "required_terms": ("required_terms", "wage_payment", "working_hours", "weekly_holiday_pay", "annual_leave"),
    "overtime_night": ("overtime_night",),
}
SPECIAL_TERM_BASIS = {"penalty": "penalty_clause", "wage_deduction": "wage_deduction"}
PAY_BASIS = {
    "기본급": ("minimum_wage",),
    "주휴수당": ("weekly_holiday_pay", "part_time"),
    "연장 가산": ("overtime_night", "overtime_limit", "part_time_overtime"),
    "야간 가산": ("overtime_night",),
    "합계": ("wage_payment",),
}


CARD_TERM_ORDER = {
    "필수 기재사항": ("required_terms", ("근로조건 명시", "근로계약서")),
    "합계": ("wage_payment", ("임금 지급 원칙", "임금명세서")),
}

TERM_BASIS = {
    "주휴수당": ("weekly_holiday_pay",),
    "소정근로시간": ("working_hours", "required_terms", "part_time", "weekly_holiday_pay"),
    "기본급": ("minimum_wage", "wage_payment"),
    "휴게시간": ("break_time",),
    "최저임금": ("minimum_wage", "minimum_wage_2026"),
    "연장근로": ("overtime_night", "overtime_limit"),
    "야간근로": ("overtime_night",),
}


def check_basis_keys(check: CheckResult, contract: Contract) -> tuple[str, ...]:
    keys = []
    if check.item == "사업장 규모":
        keys.append("workplace_size")
    for evidence in check.evidence_ids:
        if evidence == "special_terms":
            present = {term.type.value for term in contract.special_terms.value or []}
            matched = [key for term_type, key in SPECIAL_TERM_BASIS.items() if term_type in present]
            # Without a flagged type, display both reference provisions for the special-terms check.
            keys.extend(matched or SPECIAL_TERM_BASIS.values())
        elif evidence in CHECK_BASIS:
            keys.extend(CHECK_BASIS[evidence])
        elif evidence in BASIS_KEYS:
            keys.append(evidence)
    return tuple(dict.fromkeys(keys))


def pay_basis_keys(item: str) -> tuple[str, ...]:
    return PAY_BASIS.get(item, ())

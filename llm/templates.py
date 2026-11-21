from decimal import Decimal
import re

from app.format import WORKPLACE_LABELS, ReviewItem, amount_range, monthly_total, number, pay_by_workplace, won
from schemas import EngineResult
from schemas.engine import CheckResult, ComparisonItem
from schemas.explanation import EmployerMessages, Explanation, ItemExplanation, SummaryExplanation, TermExplanation

TERM_MEANINGS = {
    "소정근로시간": "계약서에서 일하기로 정한 시간이에요.",
    "기본급": "급여 항목 중 기본이 되는 금액이에요.",
    "휴게시간": "일하는 도중 쉴 수 있는 시간이에요.",
    "주휴수당": "주휴일에 대해 지급되는 수당이에요.",
    "최저임금": "임금의 최저 기준을 뜻해요.",
    "연장근로": "기준 근로시간을 넘겨 일하는 것을 뜻해요.",
    "야간근로": "밤 시간대에 일하는 것을 뜻해요.",
}


def cause_label(total: ComparisonItem, rows: list[ComparisonItem]) -> str:
    if total.contract_difference is None or total.contract_difference <= 0:
        return "계약 대비 차이"
    details = [row for row in rows if row.workplace_size == total.workplace_size and row.item != "합계"
               and row.contract_difference not in (None, 0)]
    if len(details) == 1 and details[0].contract_difference == total.contract_difference and details[0].actual == 0:
        return f"{details[0].item} 미지급"
    return "계약 대비 미지급분"


def minimum_cause_text(result: EngineResult) -> str:
    totals = [row for row in result.comparison if row.item == "합계" and row.basis == "minimum_wage"
              and row.contract_difference is not None and row.minimum_wage_difference is not None]
    multiple_sizes = len({scenario.workplace_size for scenario in result.expected_pay}) > 1
    parts = []
    for total in totals:
        size = f"{WORKPLACE_LABELS[total.workplace_size]}는 " if multiple_sizes and total.workplace_size else ""
        parts.append(f"{size}{cause_label(total, result.comparison)} {won(total.contract_difference)}"
                     f"과 최저임금 차이 {won(total.minimum_wage_difference)}")
    return " · ".join(parts)


def _summary(result: EngineResult, items: list[ReviewItem], confirmed: dict) -> SummaryExplanation:
    groups = pay_by_workplace(result.expected_pay)
    actual_rows = [row for row in result.comparison if row.item == "합계"]
    totals = [amount for group in groups if (amount := monthly_total(group.for_comparison)) is not None]
    shortages = [row.difference for row in actual_rows if row.status == "needs_review"
                 and row.difference is not None and row.difference > 0]
    minimum = any(group.minimum_wage is not None for group in groups)
    size_reason = "사업장 규모를 몰라 5인 이상과 5인 미만 두 경우로 계산했어요."
    if not confirmed["documents_provided"]["payslip"]:
        basis = "최저임금 기준으로 계산하면" if minimum else "계약대로라면"
        headline = (f"{basis} 매달 약 {amount_range(totals)}을 받아요" if totals
                    else "예상 급여를 계산하려면 계약 내용을 더 확인해 주세요")
        reason = items[0].description if items else "계약서를 바탕으로 받을 금액을 계산했어요."
    elif items:
        headline = "다시 살펴볼 항목이 있어요"
        if shortages:
            context = "최저임금 기준으로 계산하면 " if minimum else "가산수당이 적용되면 " if len(groups) > 1 else ""
            headline = f"{context}{amount_range(shortages)} 덜 받았을 수 있어요"
        cause = minimum_cause_text(result)
        rows = [row for row in result.comparison if row.item != "합계" and row.status == "needs_review"
                and row.difference is not None and row.difference > 0]
        if cause:
            reason = f"최저임금 기준으로 계산하면 {cause}을 확인해 볼 수 있어요."
        elif rows and not minimum:
            row = max(rows, key=lambda entry: entry.difference)
            reason = f"명세서에 {row.item} 지급액이 {won(row.actual)}이에요."
        else:
            reason = items[0].description
    elif totals and any(row.actual is not None for row in actual_rows):
        headline = "계약 내용과 지급액이 기준에 맞습니다"
        reason = "계산한 금액과 명세서에 나온 금액을 확인했어요."
    else:
        headline = "급여를 비교하려면 정보가 더 필요해요"
        reason = "계약 내용과 입력한 금액을 확인해 주세요."
    if confirmed["workplace_size"] in (None, "unknown") and groups and not ("5인 이상" in reason and "5인 미만" in reason):
        reason = f"{reason} {size_reason}"
    return SummaryExplanation(headline=headline, reason=reason)


def _item(item_id: str, item: ReviewItem, row: CheckResult | ComparisonItem, keys: list[str]) -> ItemExplanation:
    explanation = item.description
    if isinstance(row, ComparisonItem):
        if row.contract_expected is not None and row.expected is not None:
            explanation = (f"{item.title}은 계약 시급 기준 {won(row.contract_expected)}, "
                           f"최저임금 기준으로 계산하면 {won(row.expected)}이에요. {explanation}")
        action = f"명세서의 {item.title} 산정 내역을 사장님께 확인해 보세요."
    else:
        action = f"계약서의 {item.title} 내용을 사장님께 확인해 보세요."
    return ItemExplanation(item_id=item_id, easy_explanation=explanation, next_action=action, basis_keys=keys)


def _short_clock(clock: str) -> str:
    return clock[:-3] if clock.endswith(":00") and len(clock) == 8 else clock


def _schedule_breaks(schedule: list[dict]) -> str:
    groups = []
    for row in schedule:
        if not row["days"]:
            continue
        label = "·".join(row["days"])
        start, end = row.get("break_start"), row.get("break_end")
        if start and end:
            groups.append(f"{label} {_short_clock(start)}~{_short_clock(end)}")
        elif row.get("break_minutes") is not None:
            groups.append(f"{label} {number(Decimal(row['break_minutes']))}분")
    return ", ".join(groups)


def _term_contexts(confirmed: dict, result: EngineResult, schedule: list[dict]) -> dict[str, str]:
    weekly = confirmed.get("weekly_hours")
    wage = confirmed.get("hourly_wage")
    minutes = confirmed.get("break_minutes")
    contexts = {}
    if weekly is not None:
        contexts["소정근로시간"] = f"확인한 계약은 주 {number(Decimal(weekly))}시간이에요."
    if breaks := _schedule_breaks(schedule):
        contexts["휴게시간"] = f"확인한 계약서의 요일별 휴게는 {breaks}예요."
    elif minutes is not None:
        contexts["휴게시간"] = f"확인한 계약서에는 {minutes}분으로 적혀 있어요."
    if wage is not None:
        contexts["최저임금"] = f"확인한 계약 시급은 {won(Decimal(wage))}이에요."
    if (holiday := confirmed.get("weekly_holiday_hours")) is not None:
        contexts["주휴수당"] = f"계약 시급 기준으로는 주 {holiday}시간분으로 계산했어요."

    contract = next((scenario for scenario in result.expected_pay if scenario.basis == "contract"), None)
    if contract is None:
        return contexts
    rows = {row.item: row for row in contract.items if row.period == "weekly"}
    base = rows.get("기본급")
    if base is not None and wage is not None and weekly is not None:
        contexts["기본급"] = (
            f"계약 기준으로 시급 {won(Decimal(wage))} × 주 {number(Decimal(weekly))}시간 = 주 {won(base.amount)}이에요."
        )

    # The engine records hours in the formula. Quote those operands without recalculating hours or pay.
    prefix = "5인 이상이면 " if confirmed.get("workplace_size") in (None, "unknown") else ""
    overtime = rows.get("연장 가산")
    if overtime is not None and overtime.amount > 0:
        hours = re.match(r"max\(([^,]+), ([^)]+)\) × ", overtime.formula)
        if schedule:
            contexts["연장근로"] = f"{prefix}요일별 초과 시간을 반영한 계약 기준 주 연장 가산은 {won(overtime.amount)}이에요."
        elif hours:
            daily, weekly_excess = (number(Decimal(value)) for value in hours.groups())
            contexts["연장근로"] = (
                f"{prefix}하루 기준 초과분 {daily}시간과 주 기준 초과분 {weekly_excess}시간 중 큰 값으로 계산한 "
                f"계약 기준 주 연장 가산은 {won(overtime.amount)}이에요."
            )
    night = rows.get("야간 가산")
    if night is not None and night.amount > 0:
        limit = "최대 " if "상한" in night.applied_condition else ""
        if schedule:
            hours = ", ".join(
                f"{'·'.join(row['days'])} {'최대 ' if row.get('night_is_upper_bound') else ''}"
                f"{number(Decimal(row['night_hours']))}시간"
                for row in schedule if row.get("night_hours") and Decimal(row["night_hours"]) > 0
            )
            if hours:
                contexts["야간근로"] = f"{prefix}야간근로 {hours}에 대해 계약 기준 주 야간 가산을 {limit}{won(night.amount)}으로 계산했어요."
        else:
            hours = night.formula.partition(" × ")[0]
            contexts["야간근로"] = (
                f"{prefix}근무일마다 야간 {limit}{hours}시간에 대해 계약 기준 주 야간 가산을 "
                f"{limit}{won(night.amount)}으로 계산했어요."
            )
    return contexts


def _terms(names: list[str], dictionary: dict[str, str], confirmed: dict, result: EngineResult,
           schedule: list[dict]) -> list[TermExplanation]:
    contexts = _term_contexts(confirmed, result, schedule)
    return [
        TermExplanation(term=name, general_meaning=dictionary.get(name) or TERM_MEANINGS.get(name),
                        user_context=contexts.get(name, ""))
        for name in names
    ]


def default_messages(
    result: EngineResult, items: list[ReviewItem], rows: list[CheckResult | ComparisonItem],
    confirmed: dict, *, concise: bool = False,
) -> EmployerMessages | None:
    if not items:
        return None
    payslip = confirmed["documents_provided"]["payslip"]
    cause = minimum_cause_text(result) if payslip else ""
    if cause:
        detail = f"월 급여를 최저임금 기준으로 계산하면 {cause}을 확인해 보고 싶습니다."
    elif payslip:
        detail = next((item.description for item, row in zip(items, rows) if isinstance(row, ComparisonItem)
                       and row.basis == "contract"), "")
        detail += f" 지급일은 {confirmed['pay_day']}로 확인했습니다." if confirmed.get("pay_day") and detail else ""
    else:
        detail = ""
    if not detail:
        detail = f"계약서의 {items[0].title} 항목을 확인하고 싶습니다."
        if items[0].title == "최저임금" and confirmed["hourly_wage"] is not None:
            detail += f" 계약서의 시급은 {won(Decimal(confirmed['hourly_wage']))}입니다."
    document = "관련 계약서나 명세서" if payslip else "계약서"
    if concise:
        return EmployerMessages(
            employer_question=f"사장님, {detail} 산정 근거 확인 부탁드립니다.",
            revision_request=f"사장님, {detail} 필요한 경우 {document}에 반영 부탁드립니다.",
        )
    return EmployerMessages(
        employer_question=f"사장님, 안녕하세요. {detail} 산정 근거를 알려주실 수 있을까요?",
        revision_request=f"사장님, 안녕하세요. {detail} 확인한 내용이 다르면 {document}에 반영해 주실 수 있을까요?",
    )


def basic_explanation(
    result: EngineResult, items: list[ReviewItem], rows: list[CheckResult | ComparisonItem],
    payload: dict,
) -> Explanation:
    confirmed = payload["confirmed"]
    return Explanation(
        summary=_summary(result, items, confirmed),
        items=[_item(record["item_id"], item, row, record["basis_keys"])
               for record, item, row in zip(payload["review_items"], items, rows)],
        terms=_terms(payload["term_candidates"], payload["dictionary"], confirmed, result, payload.get("work_schedule", [])),
        messages=default_messages(result, items, rows, confirmed),
    )

from collections import defaultdict
from decimal import Decimal

from schemas.engine import ComparisonItem, ExpectedPayScenario, Limitation
from schemas.extraction import Payslip

from .pay import BASE_PAY, HOLIDAY_PAY, NIGHT_PAY, OVERTIME_PAY, TOTAL_PAY, ZERO

PAYSLIP_CATEGORIES = {
    "base_pay": BASE_PAY,
    "weekly_holiday_pay": HOLIDAY_PAY,
    "overtime": OVERTIME_PAY,
    "night": NIGHT_PAY,
    "total": TOTAL_PAY,
}

PAYSLIP_NAMES = {
    "기본급": BASE_PAY,
    "기본임금": BASE_PAY,
    "주휴수당": HOLIDAY_PAY,
    "연장": OVERTIME_PAY,
    "연장수당": OVERTIME_PAY,
    "연장근로수당": OVERTIME_PAY,
    "연장가산": OVERTIME_PAY,
    "연장근로가산": OVERTIME_PAY,
    "야간": NIGHT_PAY,
    "야간수당": NIGHT_PAY,
    "야간근로수당": NIGHT_PAY,
    "야간가산": NIGHT_PAY,
    "야간근로가산": NIGHT_PAY,
    "합계": TOTAL_PAY,
    "총지급액": TOTAL_PAY,
    "지급합계": TOTAL_PAY,
}


def _paid_by_item(payslip: Payslip, limitations: list[Limitation]) -> dict[str, Decimal | None]:
    grouped = defaultdict(list)
    for index, line in enumerate(payslip.items.value or [], start=1):
        name = line.name.value
        category = line.category.value
        label = PAYSLIP_CATEGORIES.get(category) or PAYSLIP_NAMES.get("".join((name or "").split()))
        if category == "unclassified" or label is None:
            limitations.append(Limitation(
                item=name or f"급여명세서 항목 {index}", reason="분류 불가 항목이라 대조에서 제외했습니다.",
            ))
            continue
        grouped[label].append(line.amount.value)

    paid = {
        label: None if None in amounts else sum(amounts, ZERO)
        for label, amounts in grouped.items()
    }
    if payslip.total_paid.value is not None:
        paid[TOTAL_PAY] = payslip.total_paid.value
    return paid


def compare_pay(
    scenarios: list[ExpectedPayScenario], payslip: Payslip, tolerance: Decimal,
    limitations: list[Limitation],
) -> list[ComparisonItem]:
    paid = _paid_by_item(payslip, limitations)
    comparison = []
    for scenario in scenarios:
        for row in scenario.items:
            if row.period != "monthly":
                continue
            actual = paid.get(row.item)
            if actual is None and row.amount == ZERO:
                continue
            if actual is None:
                limitations.append(Limitation(
                    item=row.item, reason="급여명세서에서 해당 지급액을 확인할 수 없습니다.",
                ))
            difference = None if actual is None else row.amount - actual
            status = "needs_review" if difference is None or difference > tolerance else "ok"
            comparison.append(ComparisonItem(
                item=row.item,
                expected=row.amount,
                actual=actual,
                difference=difference,
                status=status,
                workplace_size=scenario.workplace_size,
            ))
    return comparison

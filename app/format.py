import re
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

from rag.basis_map import check_basis_keys, pay_basis_keys
from schemas import EngineResult, ExtractionResult
from schemas.engine import CheckResult, ComparisonItem, ExpectedPayScenario

WORKPLACE_LABELS = {"5_or_more": "5인 이상", "under_5": "5인 미만", "unknown": "모름"}
PAY_BASIS_LABELS = {"contract": "계약 시급 기준", "minimum_wage": "최저임금 기준"}
REQUIRED_TERMS = {
    "임금": "hourly_wage",
    "소정근로시간": "weekly_hours",
    "휴일": "holidays",
    "연차유급휴가": "annual_leave",
    "근무 장소": "workplace",
    "업무 내용": "job_description",
}
PREMIUMS = {"연장 가산", "야간 가산"}


class WorkplacePay(NamedTuple):
    contract: ExpectedPayScenario
    minimum_wage: ExpectedPayScenario | None

    @property
    def for_comparison(self) -> ExpectedPayScenario:
        return self.minimum_wage or self.contract


class ReviewItem(NamedTuple):
    title: str
    description: str
    formula: str | None
    scenario_name: str | None = None
    basis_keys: tuple[str, ...] = ()


def won(amount: Decimal | int | None) -> str:
    if amount is None:
        return "정보 없음"
    return f"{Decimal(amount).quantize(Decimal('1'), rounding=ROUND_HALF_UP):,}원"


def number(value: Decimal) -> str:
    return f"{value.normalize():f}"


def conversational(description: str) -> str:
    for before, after in (
        ("낮습니다.", "낮아요."),
        ("높습니다.", "높아요."),
        ("없습니다.", "없어요."),
        ("있습니다.", "있어요."),
        ("미만입니다.", "미만이에요."),
        ("입니다.", "이에요."),
        ("필요합니다.", "필요해요."),
        ("계산했습니다.", "계산했어요."),
        ("제외했습니다.", "제외했어요."),
    ):
        description = description.replace(before, after)
    return description


def amount_range(amounts: list[Decimal]) -> str:
    if not amounts:
        return won(None)
    lower, upper = min(amounts), max(amounts)
    return won(lower) if lower == upper else f"{won(lower)}~{won(upper)}"


def monthly_total(scenario: ExpectedPayScenario) -> Decimal | None:
    return next((row.amount for row in scenario.items if row.period == "monthly" and row.item == "합계"), None)


def pay_by_workplace(scenarios: list[ExpectedPayScenario]) -> list[WorkplacePay]:
    minimum = {scenario.workplace_size: scenario for scenario in scenarios if scenario.basis == "minimum_wage"}
    return [WorkplacePay(scenario, minimum.get(scenario.workplace_size))
            for scenario in scenarios if scenario.basis == "contract"]


def review_rows(rows: list[ComparisonItem]) -> list[ComparisonItem]:
    flagged = [row for row in rows if row.status == "needs_review"]
    details = [row for row in flagged if row.item != "합계"]
    # A separately flagged total is useful when the detail rows do not explain it.
    explained = {row.workplace_size for row in details}
    return details + [row for row in flagged if row.item == "합계" and row.workplace_size not in explained]


def comparison_description(row: ComparisonItem) -> str:
    if row.actual is None:
        return f"명세서에서 {row.item} 지급액을 확인할 수 없어요."
    if row.difference is not None and row.difference > 0:
        return f"명세서에 {row.item} 지급액이 {won(row.actual)}이에요."
    return f"{row.item} 지급 내역을 다시 확인해 주세요."


def _pay_formula(row: ComparisonItem, scenario: ExpectedPayScenario | None, extraction: ExtractionResult) -> str | None:
    if not scenario:
        return None
    monthly = next((line for line in scenario.items if line.item == row.item and line.period == "monthly"), None)
    weekly = next((line for line in scenario.items if line.item == row.item and line.period == "weekly"), None)
    wage = extraction.contract.hourly_wage.value
    if row.basis == "contract" and row.item == "주휴수당" and monthly and weekly and wage:
        # Show the effective hours using the already-calculated weekly amount.
        hours = number(weekly.amount / wage)
        return f"{won(wage)} × {hours}시간 = 주 {won(weekly.amount)} → 월 {won(monthly.amount)}"
    if monthly:
        context = "최저임금 기준으로 계산하면 " if row.basis == "minimum_wage" else ""
        return f"{context}{monthly.formula} = 월 {won(monthly.amount)}"
    return None


def _check_formula(check: CheckResult, extraction: ExtractionResult) -> str | None:
    if check.item == "최저임금" and extraction.contract.hourly_wage.value is not None:
        amounts = re.findall(r"[\d,]+원", check.description)
        if len(amounts) == 2:
            return f"기준 {amounts[0]} − 내 시급 {won(extraction.contract.hourly_wage.value)} = {amounts[1]}"
    return None


def review_items(result: EngineResult, extraction: ExtractionResult) -> list[ReviewItem]:
    items = [
        ReviewItem(
            check.item, conversational(check.description), _check_formula(check, extraction),
            basis_keys=check_basis_keys(check, extraction.contract),
        )
        for check in result.checks if check.status == "needs_review"
    ]
    scenarios = {(scenario.workplace_size, scenario.basis): scenario for scenario in result.expected_pay}
    has_size_choices = len({size for size, _ in scenarios}) > 1
    for row in review_rows(result.comparison):
        sized_label = WORKPLACE_LABELS.get(row.workplace_size) if has_size_choices else None
        scenario = scenarios.get((row.workplace_size, row.basis))
        items.append(ReviewItem(
            row.item, comparison_description(row), _pay_formula(row, scenario, extraction),
            sized_label, pay_basis_keys(row.item),
        ))
    return items

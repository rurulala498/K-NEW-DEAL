from config import load_standards
from schemas import EngineResult, ExtractionResult
from schemas.engine import Limitation

from .checks import check_contract, diagnose
from .comparison import compare_pay
from .pay import calculate_pay


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
    expected_pay = [
        calculate_pay(extraction.contract, scenario_size, standards, year, limitations)
        for scenario_size in sizes
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
    )

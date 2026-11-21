from typing import Literal

from pydantic import BaseModel, Field

from .extraction import Amount, KnownWorkplaceSize

ResultStatus = Literal["ok", "needs_review"]
PayBasis = Literal["contract", "minimum_wage"]


class CheckResult(BaseModel):
    item: str
    status: ResultStatus
    description: str
    evidence_ids: list[str]


class ExpectedPayItem(BaseModel):
    item: str
    period: Literal["weekly", "monthly"]
    amount: Amount
    formula: str
    applied_condition: str
    standards_year: int


class ExpectedPayScenario(BaseModel):
    workplace_size: KnownWorkplaceSize
    items: list[ExpectedPayItem]
    basis: PayBasis = "contract"


class ComparisonItem(BaseModel):
    item: str
    expected: Amount | None
    actual: Amount | None
    difference: Amount | None = Field(description="예상 지급액에서 실제 지급액을 뺀 금액")
    status: Literal["ok", "needs_review", "not_compared"]
    workplace_size: KnownWorkplaceSize | None = None
    basis: PayBasis = "contract"
    contract_expected: Amount | None = None
    contract_difference: Amount | None = Field(
        default=None, description="계약 기준 예상 지급액에서 실제 지급액을 뺀 금액",
    )
    minimum_wage_difference: Amount | None = Field(
        default=None, description="최저임금 기준에서 계약 기준을 뺀 금액",
    )


class Limitation(BaseModel):
    item: str
    reason: str


class EngineResult(BaseModel):
    checks: list[CheckResult]
    expected_pay: list[ExpectedPayScenario]
    comparison: list[ComparisonItem]
    limitations: list[Limitation]

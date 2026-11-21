from datetime import date, time
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, NonNegativeInt, StrictBool, StringConstraints

Confidence = Literal["high", "low"]
KnownWorkplaceSize = Literal["5_or_more", "under_5"]
WorkplaceSize = Literal["5_or_more", "under_5", "unknown"]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Amount = Annotated[Decimal, Field(allow_inf_nan=False)]
NonNegativeNumber = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
T = TypeVar("T")


class ExtractedField(BaseModel, Generic[T]):
    value: T | None
    confidence: Confidence


class SpecialTerm(BaseModel):
    text: ExtractedField[Text]
    type: ExtractedField[Text]


class Contract(BaseModel):
    hourly_wage: ExtractedField[NonNegativeNumber]
    daily_hours: ExtractedField[NonNegativeNumber]
    weekly_hours: ExtractedField[NonNegativeNumber]
    work_days: ExtractedField[list[Text]]
    work_start: ExtractedField[time]
    work_end: ExtractedField[time]
    break_minutes: ExtractedField[NonNegativeInt]
    contract_start: ExtractedField[date]
    contract_end: ExtractedField[date]
    pay_day: ExtractedField[Text]
    pay_method: ExtractedField[Text]
    holidays: ExtractedField[Text]
    annual_leave: ExtractedField[Text]
    workplace: ExtractedField[Text]
    job_description: ExtractedField[Text]
    special_terms: ExtractedField[list[SpecialTerm]]


class PayslipItem(BaseModel):
    name: ExtractedField[Text]
    amount: ExtractedField[Amount]
    category: ExtractedField[Text] = Field(
        description='분류가 모호하면 value에 "unclassified"를 기록합니다.'
    )


class Payslip(BaseModel):
    pay_period_start: ExtractedField[date]
    pay_period_end: ExtractedField[date]
    items: ExtractedField[list[PayslipItem]]
    total_paid: ExtractedField[Amount]


class ExtraAnswers(BaseModel):
    workplace_size: ExtractedField[WorkplaceSize]
    pay_period_rule: ExtractedField[Text]


class ExtractionResult(BaseModel):
    contract: Contract
    payslip: Payslip
    extra_answers: ExtraAnswers
    user_confirmed: StrictBool

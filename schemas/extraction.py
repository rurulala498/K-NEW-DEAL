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
    source_text: str | None = None


class SpecialTerm(BaseModel):
    text: ExtractedField[Text]
    type: ExtractedField[Text]


class ScheduleEntry(BaseModel):
    days: ExtractedField[list[Text]]
    start: ExtractedField[time]
    end: ExtractedField[time]
    break_start: ExtractedField[time]
    break_end: ExtractedField[time]
    break_minutes: ExtractedField[NonNegativeInt]
    source_text: str | None = None


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
    work_schedule: ExtractedField[list[ScheduleEntry]] = Field(
        default_factory=lambda: ExtractedField(value=None, confidence="low")
    )

    def schedule_entries(self) -> list[ScheduleEntry]:
        if self.work_schedule.value:
            return self.work_schedule.value
        if not any(field.value is not None for field in (
            self.work_days, self.work_start, self.work_end, self.break_minutes,
        )):
            return []
        return [ScheduleEntry(
            days=self.work_days, start=self.work_start, end=self.work_end,
            break_start=ExtractedField(value=None, confidence="low"),
            break_end=ExtractedField(value=None, confidence="low"),
            break_minutes=self.break_minutes,
        )]

    def sync_legacy_schedule(self) -> None:
        entries = self.work_schedule.value or []
        if len(entries) == 1:
            entry = entries[0]
            self.work_days, self.work_start, self.work_end = entry.days, entry.start, entry.end
            self.break_minutes = entry.break_minutes
            return
        days = list(dict.fromkeys(day for entry in entries for day in entry.days.value or []))
        self.work_days = ExtractedField(value=days or None, confidence=self.work_schedule.confidence)
        self.work_start = ExtractedField(value=None, confidence="low")
        self.work_end = ExtractedField(value=None, confidence="low")
        self.break_minutes = ExtractedField(value=None, confidence="low")


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


class DocumentsProvided(BaseModel):
    contract: StrictBool = True
    payslip: StrictBool = True


class ExtractionResult(BaseModel):
    contract: Contract
    payslip: Payslip
    extra_answers: ExtraAnswers
    user_confirmed: StrictBool
    documents_provided: DocumentsProvided = Field(default_factory=DocumentsProvided)

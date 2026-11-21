from pydantic import BaseModel, ConfigDict


class ExplanationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SummaryExplanation(ExplanationModel):
    headline: str
    reason: str


class ItemExplanation(ExplanationModel):
    item_id: str
    easy_explanation: str
    next_action: str
    basis_keys: list[str]


class TermExplanation(ExplanationModel):
    term: str
    general_meaning: str | None
    user_context: str


class EmployerMessages(ExplanationModel):
    employer_question: str
    revision_request: str


class Explanation(ExplanationModel):
    summary: SummaryExplanation
    items: list[ItemExplanation]
    terms: list[TermExplanation]
    messages: EmployerMessages | None

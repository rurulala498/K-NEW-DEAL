from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool

AnswerType = Literal["term", "law", "personal_amount", "out_of_scope", "hypothetical"]


class ChatAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    cited_ids: list[str]
    answer_type: AnswerType
    needs_official_check: StrictBool

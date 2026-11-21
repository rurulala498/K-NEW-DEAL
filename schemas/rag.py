from datetime import date
from typing import Annotated

from pydantic import AfterValidator, BaseModel, HttpUrl, TypeAdapter

_HTTP_URL = TypeAdapter(HttpUrl)


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("내용을 입력해 주세요.")
    return value


def _http_url(value: str) -> str:
    _HTTP_URL.validate_python(value)
    return value


NonBlank = Annotated[str, AfterValidator(_nonblank)]
SourceURL = Annotated[NonBlank, AfterValidator(_http_url)]


class LawRecord(BaseModel):
    basis_key: NonBlank
    law_name: NonBlank
    article_no: NonBlank
    title: NonBlank
    text: NonBlank
    source_url: SourceURL
    checked_date: date


class UpcomingLawRecord(LawRecord):
    effective_date: date


class TermRecord(BaseModel):
    term: NonBlank
    aliases: list[NonBlank]
    easy_explanation: NonBlank
    related_basis_keys: list[NonBlank]
    source_url: SourceURL
    checked_date: date


class HelpChannel(BaseModel):
    name: NonBlank
    contact: NonBlank
    url: SourceURL
    what_for: NonBlank
    checked_date: date

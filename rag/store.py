from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
import json
import logging
from pathlib import Path
import re
from typing import TypeVar
from unicodedata import normalize

from pydantic import BaseModel, ValidationError

from rag.basis_map import BASIS_KEYS
from schemas.rag import HelpChannel, LawRecord, TermRecord

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
LOGGER = logging.getLogger(__name__)
WORDS = re.compile(r"[가-힣a-z0-9]+")
PARTICLES = ("에서는", "에게서", "으로는", "에는", "에도", "부터", "까지", "으로", "에서",
             "에게", "은", "는", "이", "가", "을", "를", "에", "의", "와", "과", "로", "도")
STOP_WORDS = {"무엇", "무슨", "뭐예요", "무엇인가요", "어떻게", "알려", "주세요", "알려주세요"}
Model = TypeVar("Model", bound=BaseModel)


@dataclass(frozen=True)
class DataIssue:
    kind: str
    file: str
    row: int | None
    field: str
    detail: str


@dataclass(frozen=True)
class SearchHit:
    record: LawRecord | TermRecord
    score: int

    @property
    def source_url(self) -> str:
        return self.record.source_url


def _normalized(value: str) -> str:
    return normalize("NFKC", value).casefold()


def _compact(value: str) -> str:
    return "".join(WORDS.findall(_normalized(value)))


def _question_words(question: str) -> tuple[str, ...]:
    tokens = []
    for word in WORDS.findall(_normalized(question)):
        if word in STOP_WORDS:
            continue
        for particle in PARTICLES:
            if len(word) >= len(particle) + 2 and word.endswith(particle):
                word = word[:-len(particle)]
                break
        if word not in STOP_WORDS:
            tokens.append(word)
    return tuple(dict.fromkeys(tokens))


def _score(question: str, words: tuple[str, ...], record: LawRecord | TermRecord) -> int:
    if isinstance(record, LawRecord):
        phrases = (record.title, record.law_name, record.article_no)
        lead = " ".join((*phrases, record.basis_key))
        body = record.text
    else:
        phrases = (record.term, *record.aliases)
        lead = " ".join(phrases)
        body = record.easy_explanation

    lead = _compact(lead)
    body = _compact(body)
    score = sum(4 if word in lead else 1 if word in body else 0 for word in words)
    if any((phrase := _compact(value)) and phrase in question for value in phrases):
        score += 10 if isinstance(record, TermRecord) else 7
    return score


def _issue(issues, kind, name, row, field, detail):
    issues.append(DataIssue(kind, name, row, field, detail))


def _load_records(
    data_dir: Path, name: str, schema: type[Model], issues: list[DataIssue], *, identity: str | None = None,
) -> tuple[Model, ...]:
    try:
        source = json.loads((data_dir / name).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        _issue(issues, "파일 없음", name, None, "", "데이터 파일을 찾을 수 없어요.")
        return ()
    except (OSError, UnicodeError, json.JSONDecodeError):
        _issue(issues, "형식 오류", name, None, "", "UTF-8 JSON 파일을 읽을 수 없어요.")
        return ()
    if not isinstance(source, list):
        _issue(issues, "형식 오류", name, None, "", "최상위 값은 JSON 목록이어야 해요.")
        return ()

    records = []
    seen = set()
    accepted = set()
    for row, entry in enumerate(source, start=1):
        identifier = ""
        if isinstance(entry, dict):
            for field in ("source_url", "checked_date"):
                if field in schema.model_fields:
                    value = entry.get(field)
                    if value is None or isinstance(value, str) and not value.strip():
                        _issue(issues, "빈 필드", name, row, field, "값이 비었어요.")
            value = entry.get(identity) if identity else None
            if isinstance(value, str):
                identifier = value if identity == "basis_key" else _compact(value)
                if identifier and identifier in seen:
                    _issue(issues, "중복", name, row, identity, value)
                if identifier:
                    seen.add(identifier)
        try:
            record = schema.model_validate(entry)
        except ValidationError as error:
            for detail in error.errors(include_input=False, include_url=False, include_context=False):
                field = ".".join(map(str, detail["loc"]))
                _issue(issues, "형식 오류", name, row, field, detail["msg"])
            continue
        if identifier and identifier in accepted:
            continue
        if identifier:
            accepted.add(identifier)
        if isinstance(record, TermRecord):
            for key in record.related_basis_keys:
                if key not in BASIS_KEYS:
                    _issue(issues, "미등록 연결 키", name, row, "related_basis_keys", key)
        records.append(record)
    return tuple(records)


class RagStore:
    def __init__(
        self, laws: Iterable[LawRecord] = (), terms: Iterable[TermRecord] = (),
        help_channels: Iterable[HelpChannel] = (), issues: Iterable[DataIssue] = (),
    ):
        self.laws = tuple(laws)
        self.terms = tuple(terms)
        self.help_channels = tuple(help_channels)
        self.issues = tuple(issues)
        self._law_by_key = {law.basis_key: law for law in self.laws}
        self._term_by_name = {_compact(term.term): term for term in self.terms}
        for term in self.terms:
            for alias in term.aliases:
                self._term_by_name.setdefault(_compact(alias), term)

    def get_law(self, basis_key: str) -> LawRecord | None:
        return self._law_by_key.get(basis_key)

    def get_terms(self, basis_key: str) -> tuple[TermRecord, ...]:
        return tuple(term for term in self.terms if basis_key in term.related_basis_keys)

    def get_term(self, name: str) -> TermRecord | None:
        return self._term_by_name.get(_compact(name))

    def search(self, question: str, k: int = 5) -> list[SearchHit]:
        """Return scored official data records. User documents are never indexed."""
        words = _question_words(question)
        if not words or k <= 0:
            return []
        compact = "".join(words)
        matches = []
        for record in (*self.laws, *self.terms):
            score = _score(compact, words, record)
            if score > 0:
                matches.append(SearchHit(record, score))
        return sorted(matches, key=lambda hit: hit.score, reverse=True)[:k]


def load_store(data_dir: Path = DATA_DIR) -> RagStore:
    issues: list[DataIssue] = []
    laws = _load_records(data_dir, "laws.json", LawRecord, issues, identity="basis_key")
    terms = _load_records(data_dir, "terms.json", TermRecord, issues, identity="term")
    help_channels = _load_records(data_dir, "help_channels.json", HelpChannel, issues)
    for issue in issues:
        LOGGER.warning(
            "%s %s행 %s %s: %s", issue.file, issue.row or "-", issue.field, issue.kind, issue.detail,
        )
    return RagStore(laws, terms, help_channels, issues)


@lru_cache(maxsize=1)
def default_store() -> RagStore:
    return load_store()


def get_law(basis_key: str) -> LawRecord | None:
    return default_store().get_law(basis_key)


def get_terms(basis_key: str) -> tuple[TermRecord, ...]:
    return default_store().get_terms(basis_key)


def get_term(name: str) -> TermRecord | None:
    return default_store().get_term(name)


def search(question: str, k: int = 5) -> list[SearchHit]:
    return default_store().search(question, k)

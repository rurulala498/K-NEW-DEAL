from collections import OrderedDict
from dataclasses import dataclass
from datetime import time
from decimal import Decimal
import json
from pathlib import Path
import re
from threading import Lock
from unicodedata import normalize

from pydantic import BaseModel, ValidationError

from engine.comparison import PAYSLIP_CATEGORIES, PAYSLIP_NAMES
from llm.client import ApiSettings, ExtractionClient, TokenUsage
from llm.documents import DocumentTexts
from llm.samples import sample_texts
from schemas.extraction import Contract, ExtractionResult, ExtractedField, Payslip

PROMPT_VERSION = "v1"
PROMPT_DIR = Path(__file__).with_name("prompts")
NUMBER = re.compile(r"(?<!\d)[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?!\d)")
TERM_TYPES = {"penalty", "wage_deduction", "other"}


class DocumentExtraction(BaseModel):
    contract: Contract
    payslip: Payslip


@dataclass(frozen=True)
class ExtractionOutcome:
    result: ExtractionResult
    usage: tuple[TokenUsage, ...] = ()
    validation_failed: bool = False


def _unanswered() -> dict:
    return {
        "workplace_size": {"value": "unknown", "confidence": "low"},
        "pay_period_rule": {"value": None, "confidence": "low"},
    }


def blank_result() -> ExtractionResult:
    return ExtractionResult.model_validate({
        "contract": {name: {"value": None, "confidence": "low"} for name in Contract.model_fields},
        "payslip": {name: {"value": None, "confidence": "low"} for name in Payslip.model_fields},
        "extra_answers": _unanswered(),
        "user_confirmed": False,
    })


def _numeric_value_in_quote(value: object, quote: str) -> bool:
    normalized = normalize("NFKC", quote)
    if isinstance(value, (Decimal, int)):
        return any(Decimal(match.group().replace(",", "")) == value for match in NUMBER.finditer(normalized))
    if isinstance(value, time):
        hour = str(value.hour)
        minute = f"{value.minute:02d}"
        return bool(re.search(rf"(?<!\d)0?{hour}\s*(?::|시\s*)\s*{minute}(?!\d)", normalized))
    return True


def _audit_fields(model: BaseModel, original: str):
    for name in type(model).model_fields:
        field: ExtractedField = getattr(model, name)
        value, quote = field.value, field.source_text
        if value is None:
            field.source_text = None
            field.confidence = "low"
            continue

        if isinstance(value, list):
            for row in value:
                if isinstance(row, BaseModel):
                    _audit_fields(row, original)
        if not quote or not quote.strip():
            field.source_text = None
            field.confidence = "low"
            # Lists of structured rows can still have citations on the individual cells.
            if not (isinstance(value, list) and value and isinstance(value[0], BaseModel)):
                field.value = None
        elif quote not in original or not _numeric_value_in_quote(value, quote):
            field.confidence = "low"


def _audit_terms(contract: Contract, original: str):
    for term in contract.special_terms.value or []:
        value = term.text.value
        source = term.text.source_text
        if value is None or value not in original or not source or value not in source:
            term.text.value = source if source and source in original else None
            term.text.confidence = "low"
        if term.type.value not in TERM_TYPES or not term.text.value:
            term.type.value = "other"
            term.type.confidence = "low"


def _audit_categories(payslip: Payslip):
    for line in payslip.items.value or []:
        name = "".join((line.name.value or "").split())
        label = PAYSLIP_NAMES.get(name)
        if not label or PAYSLIP_CATEGORIES.get(line.category.value) != label:
            line.category.value = "unclassified"
            line.category.confidence = "low"


def _validated_reply(payload) -> DocumentExtraction:
    if isinstance(payload, str):
        return DocumentExtraction.model_validate_json(payload)
    return DocumentExtraction.model_validate(payload)


def extract_documents(
    texts: DocumentTexts,
    settings: ApiSettings,
    *,
    client: ExtractionClient | None = None,
    prompt_version: str = PROMPT_VERSION,
) -> ExtractionOutcome:
    """Extract one pair of documents without retaining or caching the inputs."""
    prompt = (PROMPT_DIR / f"extraction_{prompt_version}.txt").read_text(encoding="utf-8")
    payload = json.dumps(texts._asdict(), ensure_ascii=False)
    active_client = client if client is not None else ExtractionClient(settings)
    all_usage = []
    for attempt in range(2):
        instruction = prompt
        if attempt:
            instruction += "\n이전 응답의 형식을 읽을 수 없었다. 지정된 JSON 형식과 모든 필드를 다시 확인한다."
        try:
            reply = active_client.read(instruction, payload, DocumentExtraction)
            if reply.usage is not None:
                all_usage.append(reply.usage)
            fields = _validated_reply(reply.payload)
        except ValidationError:
            continue
        _audit_fields(fields.contract, texts.contract)
        _audit_fields(fields.payslip, texts.payslip)
        _audit_terms(fields.contract, texts.contract)
        _audit_categories(fields.payslip)
        result = ExtractionResult.model_validate({
            **fields.model_dump(), "extra_answers": _unanswered(), "user_confirmed": False,
        })
        return ExtractionOutcome(result, tuple(all_usage))
    return ExtractionOutcome(blank_result(), tuple(all_usage), validation_failed=True)


_SAMPLE_CACHE_LIMIT = 8
_sample_cache: OrderedDict[tuple[DocumentTexts, str, str], ExtractionOutcome] = OrderedDict()
_sample_cache_lock = Lock()


def extract_sample(
    sample_name: str,
    settings: ApiSettings,
    *,
    client: ExtractionClient | None = None,
) -> ExtractionOutcome:
    """Cache only the bundled, whitelisted sample texts and successful responses."""
    texts = sample_texts(sample_name)
    cache_key = (texts, settings.model, PROMPT_VERSION)
    with _sample_cache_lock:
        result = _sample_cache.get(cache_key)
        if result is not None:
            _sample_cache.move_to_end(cache_key)
            return ExtractionOutcome(result.result.model_copy(deep=True))

    outcome = extract_documents(texts, settings, client=client, prompt_version=PROMPT_VERSION)
    if not outcome.validation_failed:
        with _sample_cache_lock:
            _sample_cache[cache_key] = ExtractionOutcome(outcome.result.model_copy(deep=True))
            _sample_cache.move_to_end(cache_key)
            if len(_sample_cache) > _SAMPLE_CACHE_LIMIT:
                _sample_cache.popitem(last=False)
    return outcome

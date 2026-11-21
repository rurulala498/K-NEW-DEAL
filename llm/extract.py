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
from schemas.extraction import Contract, DocumentsProvided, ExtractionResult, ExtractedField, Payslip

PROMPT_VERSION = "v3"
PROMPT_DIR = Path(__file__).with_name("prompts")
NUMBER = re.compile(r"(?<!\d)[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?!\d)")
TERM_TYPES = {"penalty", "wage_deduction", "other"}
GROSS_TOTAL = re.compile(
    r"(?<!실)(?:총지급액|지급총액|지급(?:액)?합계|지급액계|총급여액|급여총액|급여합계)"
    r"|(?:세전|공제전)(?:지급총액|총지급액|급여총액|총급여액|지급액|총급여|급여|합계|총액)"
    r"|(?:지급액|급여|합계|총액)\((?:세전|공제전)\)"
)
NET_PAY = re.compile(r"실수령(?:액)?|실지급(?:액|총액|합계)|차인(?:지급)?액|차감(?:지급)?액|공제후|세후")
DEDUCTION = re.compile(
    r"공제|세금|(?:근로)?소득세|지방소득세|주민세|4대(?:사회)?보험|국민연금|(?:국민)?건강보험|"
    r"(?:노인)?장기요양(?:보험)?|고용보험|산재보험"
)
SUMMARY_NAME = re.compile(r"^(?:합계|총액|총합계|소계)(?:\(.*\))?$")
HEADCOUNT = re.compile(
    r"상시\s*근로자(?:\s*수)?(?:는|은)?\s*[:：=]?\s*(\d{1,3}(?:,\d{3})*|\d+)\s*(?:명|인)"
    r"(?!\s*(?:이상|이하|미만|초과|[~～-]))"
)


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


def blank_result(documents_provided: DocumentsProvided | None = None) -> ExtractionResult:
    return ExtractionResult.model_validate({
        "contract": {name: {"value": None, "confidence": "low"} for name in Contract.model_fields},
        "payslip": {name: {"value": None, "confidence": "low"} for name in Payslip.model_fields},
        "extra_answers": _unanswered(),
        "user_confirmed": False,
        "documents_provided": documents_provided or DocumentsProvided(),
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
        field = getattr(model, name)
        if not isinstance(field, ExtractedField):
            continue
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


def workplace_size_hint(text: str) -> tuple[int, str] | None:
    counts = {int(match[1].replace(",", "")): match[0] for match in HEADCOUNT.finditer(text)}
    if len(counts) != 1:
        return None
    return next(iter(counts.items()))


def _audit_schedule(contract: Contract, original: str):
    for row in contract.work_schedule.value or []:
        quote = row.source_text
        if not quote or quote not in original:
            row.source_text = None
            continue
        for name in type(row).model_fields:
            cell = getattr(row, name)
            if isinstance(cell, ExtractedField) and cell.value is not None and not cell.source_text:
                cell.source_text = quote


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


def _gross_amount_quoted(field: ExtractedField, original: str) -> bool:
    quote = field.source_text
    if field.value is None or not quote or quote not in original:
        return False
    for line in original.splitlines():
        if not line.strip() or (quote not in line and line not in quote):
            continue
        compact = "".join(normalize("NFKC", line).split())
        for label in GROSS_TOTAL.finditer(compact):
            prefix = compact[:label.start()]
            previous_net = list(NET_PAY.finditer(prefix))
            if previous_net and not NUMBER.search(prefix[previous_net[-1].end():]):
                continue
            tail = compact[label.end():]
            number = NUMBER.search(tail)
            if not number or NET_PAY.search(tail[:number.start()]):
                continue
            if DEDUCTION.search(tail[:number.start()].replace("공제전", "")):
                continue
            if re.match(r"(?:원)?\((?:공제후|세후)\)", tail[number.end():]):
                continue
            if Decimal(number.group().replace(",", "")) == field.value:
                return _numeric_value_in_quote(field.value, quote)
    return False


def _audit_payslip(payslip: Payslip, original: str) -> None:
    gross = payslip.total_paid if _gross_amount_quoted(payslip.total_paid, original) else None
    included = []
    for line in payslip.items.value or []:
        name = "".join(normalize("NFKC", line.name.value or "").split())
        if NET_PAY.search(name) or DEDUCTION.search(name.replace("공제전", "")) or line.category.value == "deduction":
            continue
        if line.category.value == "total" or GROSS_TOTAL.search(name) or SUMMARY_NAME.fullmatch(name):
            if gross is None and _gross_amount_quoted(line.amount, original):
                gross = line.amount
            continue
        included.append(line)
    if payslip.items.value is not None:
        payslip.items.value = included
    payslip.total_paid = gross if gross is not None else ExtractedField(value=None, confidence="low")
    _audit_fields(payslip, original)
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
    retry_validation: bool = True,
) -> ExtractionOutcome:
    """Extract the supplied documents without retaining or caching the inputs."""
    prompt = (PROMPT_DIR / f"extraction_{prompt_version}.txt").read_text(encoding="utf-8")
    if texts.payslip is None:
        prompt += "\n급여명세서 없음. payslip의 모든 필드는 value와 source_text를 null로, confidence를 low로 둔다."
    if texts.contract is None:
        prompt += "\n근로계약서 없음. contract의 모든 필드는 value와 source_text를 null로, confidence를 low로 둔다."

    documents = {name: text for name, text in texts._asdict().items() if text is not None}
    payload = json.dumps(documents, ensure_ascii=False)
    provided = DocumentsProvided(contract="contract" in documents, payslip="payslip" in documents)
    result = blank_result(provided)
    active_client = client if client is not None else ExtractionClient(settings)
    all_usage = []
    for attempt in range(2 if retry_validation else 1):
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
        if texts.contract is not None:
            _audit_schedule(fields.contract, texts.contract)
            _audit_fields(fields.contract, texts.contract)
            _audit_terms(fields.contract, texts.contract)
            if fields.contract.work_schedule.value:
                fields.contract.sync_legacy_schedule()
            result.contract = fields.contract
            if hint := workplace_size_hint(texts.contract):
                count, quote = hint
                result.extra_answers.workplace_size = ExtractedField(
                    value="5_or_more" if count >= 5 else "under_5", confidence="high", source_text=quote,
                )
        if texts.payslip is not None:
            _audit_payslip(fields.payslip, texts.payslip)
            result.payslip = fields.payslip
        return ExtractionOutcome(result, tuple(all_usage))
    return ExtractionOutcome(result, tuple(all_usage), validation_failed=True)


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

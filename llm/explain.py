from dataclasses import dataclass, field, replace
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ValidationError

from app.format import ReviewItem, review_items, review_rows
from config import load_standards
from engine.schedule import WEEKDAYS, hours_text, weekday
from llm.client import ApiSettings, ExtractionClient, ExtractionRequestError, TokenUsage, configuration_message, read_settings
from llm.guards import NumericFacts, numeric_tokens, private_text, safe_style_request, safety_issue, sentences
from llm.templates import basic_explanation, cause_label, default_messages
from rag.store import RagStore
from schemas import EngineResult, ExtractionResult
from schemas.engine import CheckResult, ComparisonItem
from schemas.explanation import EmployerMessages, Explanation, ItemExplanation, SummaryExplanation, TermExplanation

EXPLAIN_VERSION = "v3"
REWRITE_VERSION = "v2"
MINIMUM_CONTEXT = re.compile(r"최저\s*임금[^.!?]{0,22}(?:계산|적용|가정|기준이라면|기준이면)")
PROMPTS = Path(__file__).with_name("prompts")
SAMPLE_REPORTS = Path(__file__).resolve().parents[1] / "data" / "samples"
SAMPLE_NAMES = ("normal_case", "review_case")
CONTRACT_NUMBERS = {"hourly_wage": "원", "daily_hours": "시간", "weekly_hours": "시간", "break_minutes": "분"}
PAY_DAY = re.compile(
    r"(?:매월|월|매달|매주|주)?\s*(?:[1-9]|[12]\d|3[01])일"
    r"|(?:매주|주)?\s*[월화수목금토일](?:요일)?|(?:매월|월|매달)?\s*(?:말일|마지막 날)"
)
SAFE_LIMIT_ITEMS = {
    "급여 계산", "기본급", "주휴수당", "연장 가산", "야간 가산", "합계",
    "단시간 근로자 연장 가산", "지급액 대조", "최저임금 기준 예상 급여", "요일별 근무 시간",
}


@dataclass(frozen=True)
class ExplanationContext:
    result: EngineResult
    payload: dict
    items: list[ReviewItem]
    rows: list[CheckResult | ComparisonItem]
    basic: Explanation
    workplace: str | None = field(repr=False)

    @property
    def cache_key(self) -> str:
        normalized = json.dumps(self.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(f"{EXPLAIN_VERSION}\n{normalized}".encode()).hexdigest()


@dataclass(frozen=True)
class Explained:
    content: Explanation
    ai_fields: frozenset[str] = frozenset()
    usage: tuple[TokenUsage, ...] = ()
    rejected_sentences: int = 0
    format_failures: int = 0
    mixed_fields: frozenset[str] = frozenset()
    rejection_reasons: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Rewritten:
    messages: EmployerMessages | None
    ai_fields: frozenset[str] = frozenset()
    usage: tuple[TokenUsage, ...] = ()
    rejected_sentences: int = 0


def _confirmed_values(extraction: ExtractionResult, result: EngineResult) -> dict:
    contract = extraction.contract
    fields = contract.model_dump(mode="json")
    values = {name: fields[name]["value"] for name in (*CONTRACT_NUMBERS, "work_start", "work_end")}
    day = contract.pay_day.value
    values["pay_day"] = day if day and (PAY_DAY.fullmatch(day) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)) else None
    if contract.work_schedule.value:
        for key in ("work_start", "work_end", "break_minutes"):
            values.pop(key)
    else:
        values["work_days"] = [day for day in contract.work_days.value or []
                               if day[0] in WEEKDAYS and day[1:] in ("", "요일")]
    values["special_terms"] = [
        text for term in contract.special_terms.value or [] if (text := term.text.value)
        and len(text) <= 300 and "\n" not in text and not private_text(text, contract.workplace.value)
    ][:5]
    values["workplace_size"] = extraction.extra_answers.workplace_size.value
    values["documents_provided"] = extraction.documents_provided.model_dump()

    wage = contract.hourly_wage.value
    if wage:
        holiday = next((row.amount for scenario in result.expected_pay if scenario.basis == "contract"
                        for row in scenario.items if row.item == "주휴수당" and row.period == "weekly"), None)
        if holiday is not None:
            values["weekly_holiday_hours"] = f"{(holiday / wage).normalize():f}"
    return values


def _schedule_values(extraction: ExtractionResult, result: EngineResult) -> list[dict]:
    details = {tuple(row.days): row for row in result.schedule_details}
    output = []
    for entry in extraction.contract.work_schedule.value or []:
        days = [weekday(day) for day in entry.days.value or [] if weekday(day) in WEEKDAYS]
        fields = entry.model_dump(mode="json")
        row = {"days": days, **{name: fields[name]["value"] for name in (
            "start", "end", "break_start", "break_end", "break_minutes",
        )}}
        if detail := details.get(tuple(days)):
            row.update(detail.model_dump(mode="json", exclude={"days"}))
        output.append(row)
    return output


def _term_candidates(result: EngineResult, store: RagStore, keys: list[str]) -> list[str]:
    checked = {check.item for check in result.checks}
    expected = {row.item for scenario in result.expected_pay for row in scenario.items if row.amount > 0}
    preferred = []
    if "주휴수당" in expected:
        preferred.append("주휴수당")
    if "최저임금" in checked:
        preferred.append("최저임금")
    if any(check.item == "휴게시간" and check.status == "needs_review" for check in result.checks):
        preferred.append("휴게시간")
    if "연장 가산" in expected:
        preferred.append("연장근로")
    if "야간 가산" in expected:
        preferred.append("야간근로")
    preferred.extend(("소정근로시간", "기본급", "휴게시간"))
    preferred.extend(term.term for key in keys for term in store.get_terms(key))

    names = {}
    for name in preferred:
        official = store.get_term(name)
        canonical = official.term if official else name
        if name != "예상 급여" and canonical != "예상 급여":
            names[canonical] = None
    return list(names)[:6]


def _standards_context(result: EngineResult) -> list[dict]:
    years = {line.standards_year for scenario in result.expected_pay for line in scenario.items}
    records = []
    for year in sorted(years):
        config = load_standards(year)
        if isinstance(config, str):
            continue
        breaks = config["break_time"]["value"]
        records.append({
            "year": year,
            "minimum_hourly_wage": config["minimum_hourly_wage"]["value"],
            "weekly_holiday_min_hours": config["weekly_holiday_min_hours"]["value"],
            "overtime_hours": config["overtime_hours"]["value"],
            "night_work_hours": config["night_work_hours"]["value"],
            "break_time": [
                {**rule, **({"break_hours": rule["break_minutes"] // 60} if rule["break_minutes"] % 60 == 0 else {})}
                for rule in breaks
            ],
            "premium_min_workplace_size": config["premium_min_workplace_size"]["value"],
        })
    return records


def build_context(result: EngineResult, extraction: ExtractionResult, store: RagStore) -> ExplanationContext:
    if not extraction.user_confirmed:
        raise ValueError("사용자가 확인한 결과만 설명할 수 있어요.")
    items = review_items(result, extraction)
    rows = [row for row in result.checks if row.status == "needs_review"] + review_rows(result.comparison)
    keys = list(dict.fromkeys(key for item in items for key in item.basis_keys))
    if not keys:
        keys = ["minimum_wage", "weekly_holiday_pay", "working_hours"]
    laws = [law.model_dump(include={"basis_key", "law_name", "article_no", "text"})
            for key in keys if (law := store.get_law(key))]
    available = {law["basis_key"] for law in laws}
    names = _term_candidates(result, store, keys)
    dictionary = {name: term.easy_explanation for name in names if (term := store.get_term(name))}
    confirmed = _confirmed_values(extraction, result)
    reviews = [
        {"item_id": f"item_{index}", "item": item.title, "workplace_size": getattr(row, "workplace_size", None),
         "kind": "comparison" if isinstance(row, ComparisonItem) else "contract",
         "description": item.description, "basis_keys": [key for key in item.basis_keys if key in available]}
        for index, (item, row) in enumerate(zip(items, rows), 1)
    ]
    totals = [row.model_dump(mode="json") for row in result.comparison if row.item == "합계"]
    cause_breakdown = [
        {
            "workplace_size": row.workplace_size,
            "contract_shortfall": {"label": cause_label(row, result.comparison), "amount_won": str(row.contract_difference)},
            "minimum_wage_gap": {"amount_won": str(row.minimum_wage_difference)},
            "total_difference_won": str(row.difference),
        }
        for row in result.comparison
        if row.item == "합계" and row.basis == "minimum_wage"
        and all(amount is not None for amount in (row.contract_difference, row.minimum_wage_difference, row.difference))
    ]
    payload = {
        "confirmed": confirmed,
        "checks": [row.model_dump(exclude={"evidence_ids"}) for row in result.checks],
        "expected_pay": [row.model_dump(mode="json") for row in result.expected_pay],
        "comparison": [row.model_dump(mode="json") for row in result.comparison],
        "summary_facts": totals,
        "cause_breakdown": cause_breakdown,
        "standards": _standards_context(result),
        "limitations": [
            {"item": row.item if row.item.removesuffix("(월)") in SAFE_LIMIT_ITEMS else "기타 지급 항목",
             "reason": row.reason}
            for row in result.limitations
        ],
        "review_items": reviews,
        "term_candidates": names,
        "dictionary": dictionary,
        "laws": laws,
    }
    if extraction.contract.work_schedule.value:
        payload["work_schedule"] = _schedule_values(extraction, result)
    basic = basic_explanation(result, items, rows, payload)
    if not dictionary:
        payload.pop("dictionary")
    return ExplanationContext(result, payload, items, rows, basic, extraction.contract.workplace.value)


def _confirmed_numeric_facts(
    confirmed: dict, standards: list[dict], schedule: list[dict], *, include_special_terms: bool,
) -> NumericFacts:
    facts = NumericFacts()
    for name, unit in CONTRACT_NUMBERS.items():
        facts.add(confirmed.get(name), unit)
    facts.add(confirmed.get("weekly_holiday_hours"), "시간")
    if include_special_terms:
        for term in confirmed["special_terms"]:
            for unit, value in numeric_tokens(term):
                if unit == "원":
                    facts.add(value, unit)
    for standard in standards:
        facts.add(standard["year"], "년")
        facts.add(standard["minimum_hourly_wage"], "원")
        facts.add(standard["weekly_holiday_min_hours"], "시간")
        facts.add(standard["premium_min_workplace_size"], "인")
        for value in standard["overtime_hours"].values():
            facts.add(value, "시간")
        for clock in standard["night_work_hours"].values():
            facts.read(clock)
        for rule in standard["break_time"]:
            facts.add(rule["work_hours"], "시간")
            facts.add(rule["break_minutes"], "분")
            facts.add(rule.get("break_hours"), "시간")
    for name in ("work_start", "work_end", "pay_day"):
        if confirmed.get(name):
            facts.read(confirmed[name])
    for row in schedule:
        for name in ("start", "end", "break_start", "break_end"):
            if row.get(name):
                facts.read(row[name])
        for name, unit in (("work_hours", "시간"), ("night_hours", "시간"), ("break_minutes", "분")):
            if row.get(name) is not None:
                value = Decimal(row[name])
                facts.add(value, unit)
                facts.add(hours_text(value), unit)
    return facts


def _facts(context: ExplanationContext, path: str, item: CheckResult | ComparisonItem | None, *,
           include_special_terms: bool = True) -> NumericFacts:
    facts = _confirmed_numeric_facts(
        context.payload["confirmed"], context.payload["standards"], context.payload.get("work_schedule", []),
        include_special_terms=include_special_terms,
    )
    for limit in context.result.limitations:
        if limit.item == "요일별 근무 시간":
            facts.read(limit.reason)
    facts.add(len(context.items), "건")
    for check in context.result.checks:
        facts.read(check.description)

    # Monthly causes and per-item entitlements are different inputs, even when the same term appears in both.
    totals_only = path.startswith(("summary.", "messages."))
    for scenario in context.result.expected_pay:
        for line in scenario.items:
            if totals_only and (line.item != "합계" or line.period != "monthly"):
                continue
            if isinstance(item, ComparisonItem) and (line.item != item.item or scenario.workplace_size != item.workplace_size):
                continue
            facts.add(line.amount, "원")
            if not totals_only:
                facts.read(line.applied_condition)
    comparisons = [entry for entry in context.result.comparison if entry.item == "합계"] if totals_only else (
        [item] if isinstance(item, ComparisonItem) else []
    )
    for entry in comparisons:
        for name in ("expected", "actual", "difference", "contract_expected", "contract_difference", "minimum_wage_difference"):
            facts.add(getattr(entry, name), "원")
    if totals_only:
        for cause in context.payload["cause_breakdown"]:
            facts.add(cause["contract_shortfall"]["amount_won"], "원")
            facts.add(cause["minimum_wage_gap"]["amount_won"], "원")
            facts.add(cause["total_difference_won"], "원")
    return facts


def _has_wage_split(context: ExplanationContext) -> bool:
    return any(row.basis == "minimum_wage" for row in context.result.expected_pay)


def _won_amounts(text: str) -> set[Decimal]:
    return {amount for unit, amount in numeric_tokens(text) if unit == "원"}


@dataclass(frozen=True)
class SentenceRequirements:
    amounts: frozenset[Decimal] = frozenset()
    phrases: tuple[str, ...] = ()
    conditional_minimum: bool = False

    def missing(self, text: str) -> set[Decimal | str]:
        missing = set(self.amounts - _won_amounts(text))
        missing.update(phrase for phrase in self.phrases if phrase not in text)
        if self.conditional_minimum and not MINIMUM_CONTEXT.search(text):
            missing.add("최저임금 가정")
        return missing

    def fulfilled_by(self, text: str) -> set[Decimal | str]:
        return (set(self.amounts) | set(self.phrases) |
                ({"최저임금 가정"} if self.conditional_minimum else set())) - self.missing(text)


class _ReviewGuard:
    def __init__(self, context: ExplanationContext, *, include_special_terms: bool = True):
        self.context = context
        self.include_special_terms = include_special_terms
        self.generated: set[str] = set()
        self.mixed: set[str] = set()
        self.rejections: list[tuple[str, str]] = []

    def text(self, candidate: str, default: str, path: str, *,
             row: CheckResult | ComparisonItem | None = None,
             required: SentenceRequirements = SentenceRequirements(),
             max_sentences: int = 1) -> str:
        facts = _facts(self.context, path, row, include_special_terms=self.include_special_terms)
        payslip = self.context.payload["confirmed"]["documents_provided"]["payslip"]
        parts = sentences(candidate)
        if not parts:
            if default:
                self.rejections.append((path, "빈 문장"))
            return default

        kept = {}
        for index, part in enumerate(parts):
            issue = safety_issue(part, facts, payslip=payslip, workplace=self.context.workplace)
            if issue or index >= max_sentences:
                self.rejections.append((path, issue or "허용 문장 수 초과"))
            else:
                kept[index] = part

        missing = required.missing(" ".join(kept.values()))
        if missing and len(kept) == len(parts):
            # Replace the sentence attempting the required content, keeping unrelated sentences.
            index = max(kept, key=lambda i: (len(required.fulfilled_by(kept[i])), i))
            kept.pop(index)
            self.rejections.append((path, "필수 금액 또는 맥락 누락"))
            missing = required.missing(" ".join(kept.values()))
        if not kept:
            return default
        self.generated.add(path)
        if len(kept) == len(parts):
            return candidate.strip()

        self.mixed.add(path)
        defaults = sentences(default)
        # Use the default sentence that carries any missing facts before using positional fallbacks.
        output = []
        for index in range(min(len(parts), max_sentences)):
            if index in kept:
                output.append(kept[index])
                continue
            remaining = [part for part in defaults if part not in kept.values() and part not in output]
            preferred = [part for part in remaining if required.fulfilled_by(part) & missing]
            if preferred:
                output.extend(preferred)
                missing -= required.fulfilled_by(" ".join(preferred))
            elif remaining:
                positional = defaults[min(index, len(defaults) - 1)]
                output.append(positional if positional in remaining else remaining[0])
        if missing:
            output.extend(part for part in defaults if part not in output and required.fulfilled_by(part) & missing)
        return " ".join(output)


def _cause_amounts(context: ExplanationContext) -> frozenset[Decimal]:
    return frozenset(
        Decimal(amount)
        for cause in context.payload["cause_breakdown"]
        for amount in (cause["contract_shortfall"]["amount_won"], cause["minimum_wage_gap"]["amount_won"])
    )


def _checked_messages(guard: _ReviewGuard, candidate: EmployerMessages | None,
                      basic: EmployerMessages | None) -> EmployerMessages | None:
    if basic is None or candidate is None:
        return basic
    amounts = _cause_amounts(guard.context)
    requirements = SentenceRequirements(amounts, ("사장님",), conditional_minimum=bool(amounts))
    return EmployerMessages(**{
        key: guard.text(getattr(candidate, key), getattr(basic, key), f"messages.{key}",
                        required=requirements, max_sentences=5)
        for key in EmployerMessages.model_fields
    })


def _validate_output(context: ExplanationContext, model: Explanation) -> Explained:
    basic = context.basic
    guard = _ReviewGuard(context)
    headline_amounts = frozenset(_won_amounts(basic.summary.headline)) if _has_wage_split(context) else frozenset()
    cause_amounts = _cause_amounts(context)
    expected_sizes = ("5인 이상", "5인 미만") if all(
        size in basic.summary.reason for size in ("5인 이상", "5인 미만")
    ) else ()
    summary = SummaryExplanation(
        headline=guard.text(
            model.summary.headline, basic.summary.headline, "summary.headline",
            required=SentenceRequirements(headline_amounts, conditional_minimum=bool(headline_amounts)),
        ),
        reason=guard.text(
            model.summary.reason, basic.summary.reason, "summary.reason",
            required=SentenceRequirements(cause_amounts, expected_sizes), max_sentences=max(1, len(sentences(basic.summary.reason)) if expected_sizes else 1),
        ),
    )
    by_id = {item.item_id: item for item in model.items}
    items = []
    for default, row in zip(basic.items, context.rows):
        item = by_id.get(default.item_id)
        if item is None:
            items.append(default)
            continue
        requirements = SentenceRequirements()
        if isinstance(row, ComparisonItem) and row.item == "주휴수당" and None not in (row.contract_expected, row.expected):
            requirements = SentenceRequirements(frozenset((row.contract_expected, row.expected)), conditional_minimum=True)
        prefix = f"items.{default.item_id}"
        items.append(ItemExplanation(
            item_id=default.item_id,
            easy_explanation=guard.text(item.easy_explanation, default.easy_explanation,
                                        f"{prefix}.easy_explanation", row=row, required=requirements, max_sentences=2),
            next_action=guard.text(item.next_action, default.next_action, f"{prefix}.next_action", row=row),
            basis_keys=list(dict.fromkeys(key for key in item.basis_keys if key in default.basis_keys)),
        ))
    dictionary = context.payload.get("dictionary", {})
    by_name = {term.term: term for term in model.terms}
    terms = []
    for default in basic.terms:
        term = by_name.get(default.term)
        if term is None:
            terms.append(default)
            continue
        prefix = f"terms.{term.term}"
        meaning = default.general_meaning
        if term.term not in dictionary:
            meaning = guard.text(term.general_meaning or "", meaning or "", f"{prefix}.general_meaning")
        detail = guard.text(term.user_context, default.user_context, f"{prefix}.user_context")
        terms.append(TermExplanation(term=term.term, general_meaning=meaning, user_context=detail))
    messages = _checked_messages(guard, model.messages, basic.messages)
    return Explained(
        Explanation(summary=summary, items=items, terms=terms, messages=messages),
        frozenset(guard.generated), rejected_sentences=len(guard.rejections),
        mixed_fields=frozenset(guard.mixed), rejection_reasons=tuple(guard.rejections),
    )


def _request(settings: ApiSettings, payload: dict, prompt_file: str, schema: type[BaseModel], client, *,
             retry_validation: bool = True):
    usage = []
    failures = 0
    if configuration_message(settings):
        return None, tuple(usage), failures
    prompt = (PROMPTS / prompt_file).read_text(encoding="utf-8")
    try:
        active = client if client is not None else ExtractionClient(settings)
        for attempt in range(2 if retry_validation else 1):
            note = "\n이전 응답은 형식 검증에 실패했다. 정해진 JSON 필드를 모두 채운다." if attempt else ""
            try:
                reply = active.read(prompt + note, json.dumps(payload, ensure_ascii=False), schema)
                if reply.usage is not None:
                    usage.append(reply.usage)
                parsed = schema.model_validate_json(reply.payload) if isinstance(reply.payload, str) else (
                    schema.model_validate(reply.payload)
                )
            except ValidationError:
                failures += 1
                continue
            return parsed, tuple(usage), failures
    except ExtractionRequestError:
        pass
    return None, tuple(usage), failures


def generate(context: ExplanationContext, settings: ApiSettings, *, client=None,
             retry_validation: bool = True) -> Explained:
    model, usage, failures = _request(
        settings, context.payload, f"explain_{EXPLAIN_VERSION}.txt", Explanation, client,
        retry_validation=retry_validation,
    )
    if model is None:
        return Explained(context.basic, usage=usage, format_failures=failures)
    return replace(_validate_output(context, model), usage=usage, format_failures=failures)


def rewrite(context: ExplanationContext, settings: ApiSettings, tone: Literal["정중하게", "간단하게"],
            request: str, *, client=None) -> Rewritten:
    hints = safe_style_request(request)
    basic = default_messages(context.result, context.items, context.rows, context.payload["confirmed"],
                             concise=tone == "간단하게" or "짧고 간결하게" in hints)
    if basic is None:
        return Rewritten(None)
    payload = {key: context.payload[key] for key in ("summary_facts", "cause_breakdown", "standards", "review_items")}
    payload["confirmed"] = {key: value for key, value in context.payload["confirmed"].items() if key != "special_terms"}
    if "work_schedule" in context.payload:
        payload["work_schedule"] = context.payload["work_schedule"]
    payload["style"] = tone
    payload["style_hints"] = hints
    model, usage, _ = _request(settings, payload, f"rewrite_{REWRITE_VERSION}.txt", EmployerMessages, client)
    guard = _ReviewGuard(context, include_special_terms=False)
    messages = _checked_messages(guard, model, basic)
    return Rewritten(messages, frozenset(guard.generated), usage, len(guard.rejections))


def saved_sample(name: str, context: ExplanationContext) -> Explained:
    if name not in SAMPLE_NAMES:
        return Explained(context.basic)
    try:
        record = json.loads((SAMPLE_REPORTS / f"{name}_explanation.json").read_text(encoding="utf-8"))
        if record["cache_key"] != context.cache_key:
            return Explained(context.basic)
        checked = _validate_output(context, Explanation.model_validate(record["explanation"]))
        ai = checked.ai_fields.intersection(record["ai_fields"])
        mixed = ai.intersection(checked.mixed_fields.union(record.get("mixed_fields", [])))
        return Explained(checked.content, ai, mixed_fields=mixed)
    except (OSError, ValueError, KeyError, TypeError):
        return Explained(context.basic)

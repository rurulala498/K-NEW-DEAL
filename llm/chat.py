from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from unicodedata import normalize

from pydantic import ValidationError

from app.format import WORKPLACE_LABELS, won
from llm.chat_web import ChatClient, WebLookup, WebSearchUnavailable
from llm.client import ApiSettings, ExtractionRequestError, TokenUsage, configuration_message
from llm.explain import ExplanationContext, _confirmed_numeric_facts
from llm.guards import NumericFacts, PAYSLIP_LANGUAGE, private_text, safety_issue, sentences
from rag.basis_map import TERM_BASIS, pay_basis_keys
from rag.store import RagStore
from schemas.chat import AnswerType, ChatAnswer
from schemas.engine import ComparisonItem, ExpectedPayItem, ExpectedPayScenario, PayBasis
from schemas.extraction import KnownWorkplaceSize
from schemas.rag import LawRecord, TermRecord

SESSION_KEY = "receipt_chat"
QUESTION_LIMIT = 20
WEB_SEARCH_LIMIT = 3
QUESTION_LENGTH = 500
MIN_SEARCH_SCORE = 7
PROMPT = Path(__file__).with_name("prompts") / "chat_v1.txt"

NO_ANSWER = "자료에서 정확한 답을 찾지 못했어요."
OFFICIAL_HELP = "공식 상담 기관에 확인해 보세요."
CHANGE_VALUES = "② 내용 확인에서 값을 고치면 다시 계산돼요."
CONTRACT_ONLY = "계약서에서 확인한 조건과 예상 급여를 안내해요. 받을 금액 명세에서 계산식을 확인해 보세요."
OUTSIDE_NOTICE = "이 질문은 받았수당의 급여·근로계약서 점검 범위 밖이에요."

OUTSIDE_TOPICS = re.compile(
    r"퇴직금|퇴직급여|퇴직연금|해고|산재|산업재해|괴롭힘|실업급여"
    r"|(?=.*(?:4대\s*(?:사회\s*)?보험|사회보험))(?=.*(?:계산|얼마|보험료))"
)
UNRELATED_TOPICS = re.compile(r"날씨|기온|주식\s*시세")
TERM_INTENT = re.compile(r"뭐예요|뭐에요|뭔가요|뭔지|무슨\s*뜻|뜻이|뜻을|의미|정의|무엇(?:인가요|이에요|일까요|인지)|이란")
AMOUNT_INTENT = re.compile(r"얼마|계산|금액|급여|시급|수당|액수|차액|받(?:을|은|아요|나요|게)")
PERSONAL_REFERENCE = re.compile(
    r"(?:^|\s)(?:내|제)(?=\s|급여|월급|시급|주휴|기본급|수당|예상|영수증|계약|야간|연장)"
    r"|제가|저는|저의|나는|나의|우리\s*(?:가게|사업장)"
)
RECEIPT_REFERENCE = re.compile(r"예상\s*(?:급여|금액)|영수증|지급액\s*대조|받은\s*금액")
ITEM_MONEY_REASON = re.compile(r"(?:왜|이유|어떻게).*\d[\d,]*\s*원|\d[\d,]*\s*원.*(?:이(?:에요|예요)|인\s*이유|인가요)")
CHANGED_INPUT = re.compile(
    r"\d(?:[\d,.]*\d)?\s*(?:만\s*원|원|시간|일|분)\s*"
    r"(?:이?면|이라면|일\s*때|일하면|근무\s*(?:하면|시)|하면|으로\s*(?:바꾸|바꿔|변경))"
    r"|(?:시급|근무|근로|휴게|임금|시간|요일).{0,24}(?:바꾸면|바꿔서|변경하면|높이면|올리면|줄이면|늘리면|오르면|바뀌면)"
)
INJECTION = re.compile(
    r"(?:시스템|개발자|이전|위의|앞의|모든)\s*(?:프롬프트|지시|규칙|명령).{0,15}(?:무시|잊|폐기|덮어쓰)"
    r"|(?:역할|페르소나).{0,12}(?:바꿔|변경해|무시)|(?:system|developer|assistant)\s*:"
    r"|\b(?:ignore|disregard|override|forget)\b.{0,50}\b(?:instructions?|rules?|prompts?)\b",
    re.IGNORECASE,
)
KOREAN_ID_LABEL = re.compile(r"주민\s*번호|성명\s*(?:은|이)\s*[가-힣]{2,4}(?=\W|$)")
ACTUAL_PAY_QUESTION = re.compile(r"대조|미지급|못\s*받|받지\s*못|지급\s*받은|안\s*(?:들어|받)|실수령|실제\s*(?:금액|수령|입금)|입금(?:된|액)")
PAY_TOPICS = (
    ("주휴수당", re.compile(r"주휴")),
    ("야간 가산", re.compile(r"야간|심야")),
    ("연장 가산", re.compile(r"연장|초과\s*근무")),
    ("기본급", re.compile(r"기본\s*급|기본\s*임금")),
)
CHECK_QUESTIONS = {
    "최저임금": "최저임금 점검에서 무엇을 확인해요?",
    "휴게시간": "휴게시간은 어떤 기준으로 확인해요?",
    "필수 기재사항": "근로계약서에는 무엇을 적어야 해요?",
    "특약": "근로계약서의 위약금이나 임금 공제 특약은 어떻게 확인해요?",
    "사업장 규모": "5인 이상과 5인 미만의 적용 기준은 어디서 확인해요?",
}
CONFIRMED_FIELDS = (
    "hourly_wage", "daily_hours", "weekly_hours", "work_days", "work_start", "work_end",
    "break_minutes", "pay_day", "workplace_size", "weekly_holiday_hours",
)
INVISIBLE_SPACES = str.maketrans("", "", "\u200b\u200c\u200d\ufeff")
COMPARISON_AMOUNTS = (
    "expected", "actual", "difference", "contract_expected", "contract_difference", "minimum_wage_difference",
)


@dataclass(frozen=True)
class ChatSource:
    record: LawRecord | TermRecord
    score: int | None = None

    @property
    def id(self) -> str:
        if isinstance(self.record, LawRecord):
            return f"law:{self.record.basis_key}"
        return f"term:{self.record.term}"

    @property
    def label(self) -> str:
        if isinstance(self.record, LawRecord):
            return f"{self.record.law_name} {self.record.article_no}"
        return self.record.term

    def input_record(self) -> dict:
        if isinstance(self.record, LawRecord):
            return {
                "id": self.id, "law_name": self.record.law_name, "article_no": self.record.article_no,
                "title": self.record.title, "text": self.record.text,
            }
        return {"id": self.id, "term": self.record.term, "easy_explanation": self.record.easy_explanation}


@dataclass(frozen=True)
class QuestionPlan:
    answer_type: AnswerType
    sources: tuple[ChatSource, ...]
    pay_item: str | None = None


@dataclass(frozen=True)
class Calculation:
    row: ExpectedPayItem
    basis: PayBasis
    workplace_size: KnownWorkplaceSize


@dataclass(frozen=True)
class ChatReply:
    answer: str
    answer_type: AnswerType | None = None
    sources: tuple[ChatSource, ...] = ()
    calculations: tuple[Calculation, ...] = ()
    needs_official_check: bool = False
    web: WebLookup | None = None
    validation: str = "local"
    issues: tuple[str, ...] = ()
    usage: tuple[TokenUsage, ...] = ()


@dataclass
class ChatSession:
    questions_used: int = 0
    web_searches_used: int = 0
    web_search_unavailable: bool = False
    context_key: str | None = None
    history: dict[str, ChatReply] = field(default_factory=dict)

    def clear_conversation(self) -> None:
        self.context_key = None
        self.history.clear()

    def use_context(self, context_key: str) -> None:
        if self.context_key != context_key:
            self.clear_conversation()
            self.context_key = context_key


def _pay_sources(
    store: RagStore, item: str, scenarios: list[ExpectedPayScenario] | None,
) -> tuple[ChatSource, ...]:
    items = (item,) if scenarios is None else dict.fromkeys(
        row.item for scenario in scenarios for row in scenario.items if item == "합계" or row.item == item
    )
    # The total's own evidence key concerns payment procedure. Its calculation uses the component keys.
    keys = dict.fromkeys(key for name in items if name != "합계" for key in pay_basis_keys(name))
    laws = [store.get_law(key) for key in keys]
    terms = [store.get_term(name) for name, related in TERM_BASIS.items() if keys.keys() & related]
    return tuple(ChatSource(record) for record in (*laws, *terms) if record is not None)


def plan_question(
    question: str, store: RagStore, *, expected_pay: list[ExpectedPayScenario] | None = None,
) -> QuestionPlan:
    scored = [hit for hit in store.search(question, k=8) if hit.score >= MIN_SEARCH_SCORE]
    terms = [hit for hit in scored if isinstance(hit.record, TermRecord)]
    term_question = bool(terms and TERM_INTENT.search(question))
    if OUTSIDE_TOPICS.search(question) or UNRELATED_TOPICS.search(question):
        answer_type = "out_of_scope"
    elif CHANGED_INPUT.search(question) and AMOUNT_INTENT.search(question):
        answer_type = "hypothetical"
    elif term_question:
        answer_type = "term"
    else:
        pay_item = next((item for item, pattern in PAY_TOPICS if pattern.search(question)), None)
        if RECEIPT_REFERENCE.search(question) or (
            PERSONAL_REFERENCE.search(question) and AMOUNT_INTENT.search(question)
        ) or (pay_item and ITEM_MONEY_REASON.search(question)):
            item = pay_item or "합계"
            return QuestionPlan("personal_amount", _pay_sources(store, item, expected_pay), item)
        answer_type = "law"

    sources = {}
    for hit in scored:
        # A definition has one primary term. A shorter alias can match inside that term's full name.
        if term_question and isinstance(hit.record, TermRecord) and hit != terms[0]:
            continue
        source = ChatSource(hit.record, hit.score)
        sources[source.id] = source

    for source in tuple(sources.values()):
        if isinstance(source.record, TermRecord):
            for key in source.record.related_basis_keys:
                if law := store.get_law(key):
                    linked = ChatSource(law)
                    sources.setdefault(linked.id, linked)
                    # If the law names a dictionary term, include that definition as well.
                    for term in store.get_terms(key):
                        if term.term in law.law_name:
                            linked_term = ChatSource(term)
                            sources.setdefault(linked_term.id, linked_term)

    return QuestionPlan(answer_type, tuple(sources.values()))


def suggestions(context: ExplanationContext, store: RagStore) -> tuple[str, ...]:
    choices = {}
    terms = [term.term for term in context.basic.terms if store.get_term(term.term)]
    if terms:
        choices[f"{terms[0]}이 뭐예요?"] = None
    for item, row in zip(context.items, context.rows):
        if item.title in CHECK_QUESTIONS:
            choices[CHECK_QUESTIONS[item.title]] = None
        elif isinstance(row, ComparisonItem):
            label = "급여 차이" if item.title == "합계" else item.title
            final = label[-1]
            particle = "은" if "가" <= final <= "힣" and (ord(final) - ord("가")) % 28 else "는"
            choices[f"내 {label}{particle} 어떻게 계산됐어요?"] = None
    for question in (
        "내 예상 급여는 어떻게 계산됐어요?", *(f"{term}이 뭐예요?" for term in terms[1:]),
        "휴게시간은 어떤 기준으로 확인해요?", "근로계약서에는 무엇을 적어야 해요?",
    ):
        choices.setdefault(question, None)
    return tuple(choices)[:3]


def _build_payload(question: str, plan: QuestionPlan, context: ExplanationContext) -> dict:
    original = context.payload
    confirmed = {key: original["confirmed"][key] for key in CONFIRMED_FIELDS if key in original["confirmed"]}
    if "work_schedule" in original:
        confirmed["work_schedule"] = original["work_schedule"]
    documents = original["confirmed"]["documents_provided"]
    engine = {key: original[key] for key in ("checks", "expected_pay", "limitations")}
    if documents["payslip"]:
        engine.update(comparison=original["comparison"], cause_breakdown=original["cause_breakdown"])
    else:
        engine["limitations"] = [row for row in engine["limitations"] if row["item"] != "지급액 대조"]

    if plan.pay_item and plan.pay_item != "합계":
        engine["expected_pay"] = [
            {**group, "items": [item for item in group["items"] if item["item"] == plan.pay_item]}
            for group in engine["expected_pay"] if any(item["item"] == plan.pay_item for item in group["items"])
        ]
        if documents["payslip"]:
            engine["comparison"] = [row for row in engine["comparison"] if row["item"] == plan.pay_item]
            engine.pop("cause_breakdown")

    return {
        "question": question, "question_type": plan.answer_type, "confirmed": confirmed,
        "documents_provided": documents, "engine_result": engine, "standards": original["standards"],
        "evidence": [source.input_record() for source in plan.sources],
    }


def _numeric_facts(payload: dict) -> NumericFacts:
    confirmed = payload["confirmed"]
    facts = _confirmed_numeric_facts(
        confirmed, payload["standards"], confirmed.get("work_schedule", []), include_special_terms=False,
    )
    engine = payload["engine_result"]
    for check in engine["checks"]:
        facts.read(check["description"])
    for note in engine["limitations"]:
        facts.read(note["reason"])
    for group in engine["expected_pay"]:
        for line in group["items"]:
            facts.add(line["amount"], "원")
            facts.add(line["standards_year"], "년")
            facts.read(line["formula"])
            facts.read(line["applied_condition"])
    for row in engine.get("comparison", []):
        for key in COMPARISON_AMOUNTS:
            facts.add(row[key], "원")
    for source in payload["evidence"]:
        for key in ("title", "text", "term", "easy_explanation"):
            if key in source:
                facts.read(source[key])
    return facts


def _calculation_rows(scenarios: list[ExpectedPayScenario], item: str | None) -> tuple[Calculation, ...]:
    if item is None:
        return ()
    return tuple(
        Calculation(row, scenario.basis, scenario.workplace_size)
        for scenario in scenarios for row in scenario.items if item == "합계" or row.item == item
    )


def _amount_fallback(
    question: str, context: ExplanationContext, plan: QuestionPlan, calculations: tuple[Calculation, ...],
    issues: tuple[str, ...], usage: tuple[TokenUsage, ...] = (),
) -> ChatReply:
    scenarios = context.result.expected_pay
    multiple_sizes = len({scenario.workplace_size for scenario in scenarios}) > 1

    def scenario_label(size: KnownWorkplaceSize | None, basis: PayBasis) -> str:
        prefix = f"{WORKPLACE_LABELS[size]}인 경우 " if multiple_sizes and size else ""
        return prefix + ("최저임금 기준으로 계산하면" if basis == "minimum_wage" else "계약 시급 기준으로")

    parts = []
    for scenario in scenarios:
        amounts = ", ".join(
            f"{row.item} {won(row.amount)}" for row in scenario.items
            if row.period == "monthly" and (
                row.item == plan.pay_item or (plan.pay_item == "합계" and row.amount != 0)
            )
        )
        if amounts:
            label = scenario_label(scenario.workplace_size, scenario.basis)
            parts.append(f"{label} 한 달 {amounts}으로 계산했어요.")

    payslip = context.payload["confirmed"]["documents_provided"]["payslip"]
    if payslip and (re.search(r"차이|차액", question) or ACTUAL_PAY_QUESTION.search(question) or PAYSLIP_LANGUAGE.search(question)):
        for row in context.result.comparison:
            if row.item == plan.pay_item and row.actual is not None and row.difference is not None:
                label = scenario_label(row.workplace_size, row.basis)
                parts.append(f"{label} {row.item}의 실제 지급액은 {won(row.actual)}이고 차이는 {won(row.difference)}이에요.")

    if not parts:
        parts.append("이 항목의 금액을 계산하려면 내용 확인에서 계약 값을 확인해 주세요.")
    if calculations:
        parts.append("아래 ‘엔진 계산식 보기’에서 식을 확인할 수 있어요.")
    return ChatReply(
        " ".join(parts), "personal_amount", plan.sources, calculations,
        validation="fallback", issues=issues, usage=usage,
    )


def _answer_safety(text: str, facts: NumericFacts, *, payslip: bool, workplace: str | None) -> str | None:
    issue = safety_issue(text, facts, payslip=payslip, workplace=workplace)
    if issue is None and not payslip and ACTUAL_PAY_QUESTION.search(text):
        return "명세서 없이 지급액 언급"
    return issue


def _answer_issue(
    model: ChatAnswer, plan: QuestionPlan, sources: tuple[ChatSource, ...], facts: NumericFacts, *,
    payslip: bool, workplace: str | None,
) -> str | None:
    if model.answer_type != plan.answer_type:
        return "질문 유형 불일치"
    if not 2 <= len(sentences(model.answer)) <= 5:
        return "문장 수 오류"
    if issue := _answer_safety(model.answer, facts, payslip=payslip, workplace=workplace):
        return issue
    if plan.answer_type == "term" and not any(isinstance(source.record, TermRecord) for source in sources):
        return "용어 근거 없음"
    if plan.answer_type == "law" and not sources:
        return "법·기준 근거 없음"
    return None


def _request_answer(
    question: str, plan: QuestionPlan, context: ExplanationContext, client,
) -> ChatReply:
    payload = _build_payload(question, plan, context)
    serialized = json.dumps(payload, ensure_ascii=False)
    facts = _numeric_facts(payload)
    prompt = PROMPT.read_text(encoding="utf-8")
    calculations = _calculation_rows(context.result.expected_pay, plan.pay_item)
    by_id = {source.id: source for source in plan.sources}
    usage = []
    issues = []
    payslip = payload["documents_provided"]["payslip"]
    for attempt in range(2):
        retry = "\n이전 응답은 검증을 통과하지 못했다. 요구 형식과 근거, 숫자, 금지 표현을 다시 확인한다." if attempt else ""
        try:
            reply = client.read(prompt + retry, serialized, ChatAnswer)
            if reply.usage is not None:
                usage.append(reply.usage)
            model = ChatAnswer.model_validate_json(reply.payload) if isinstance(reply.payload, str) else (
                ChatAnswer.model_validate(reply.payload)
            )
        except ValidationError:
            issues.append("응답 형식 오류")
            continue
        except ExtractionRequestError:
            issues.append("API 응답 실패")
            break

        sources = tuple(by_id[key] for key in dict.fromkeys(model.cited_ids) if key in by_id)
        if any(key not in by_id for key in model.cited_ids):
            issues.append("제공되지 않은 근거 ID 제거")
        if issue := _answer_issue(model, plan, sources, facts, payslip=payslip, workplace=context.workplace):
            issues.append(issue)
            continue
        return ChatReply(model.answer.strip(), plan.answer_type, sources, calculations, model.needs_official_check,
                         validation="passed", issues=tuple(issues), usage=tuple(usage))

    if plan.answer_type == "personal_amount":
        return _amount_fallback(question, context, plan, calculations, tuple(issues), tuple(usage))
    return ChatReply(NO_ANSWER, plan.answer_type, plan.sources, calculations, needs_official_check=True,
                     validation="fallback", issues=tuple(issues), usage=tuple(usage))


def _search_reference(
    question: str, settings: ApiSettings, session: ChatSession, client, *, payslip: bool, workplace: str | None,
) -> tuple[WebLookup | None, tuple[TokenUsage, ...], tuple[str, ...]]:
    if (not settings.enable_web_search or configuration_message(settings) or session.web_search_unavailable
            or session.web_searches_used >= WEB_SEARCH_LIMIT or UNRELATED_TOPICS.search(question)):
        return None, (), ()

    session.web_searches_used += 1
    try:
        lookup = (client if client is not None else ChatClient(settings)).search_official(question)
    except WebSearchUnavailable:
        session.web_search_unavailable = True
        return None, (), ("도메인 제한 웹 검색 미지원",)
    except ExtractionRequestError:
        return None, (), ("공식 자료 검색 실패",)
    usage = (lookup.usage,) if lookup.usage is not None else ()
    if not lookup.sources:
        return None, usage, ("허용된 출처 없음",)
    if not lookup.summary or _answer_safety(
        lookup.summary, NumericFacts(), payslip=payslip, workplace=workplace,
    ):
        return WebLookup("관련 공식 자료를 찾았어요. 적용 기준은 출처에서 확인해 보세요.", lookup.sources), usage, (
            "웹 요약을 기본 안내로 교체",
        )
    return lookup, usage, ()


def _respond(
    question: str, context: ExplanationContext, store: RagStore, settings: ApiSettings, session: ChatSession, client,
) -> ChatReply:
    plan = plan_question(question, store, expected_pay=context.result.expected_pay)
    if plan.answer_type == "hypothetical":
        return ChatReply(CHANGE_VALUES, plan.answer_type)
    payslip = context.payload["confirmed"]["documents_provided"]["payslip"]
    if not payslip and (PAYSLIP_LANGUAGE.search(question) or ACTUAL_PAY_QUESTION.search(question)):
        return ChatReply(CONTRACT_ONLY, plan.answer_type)

    if plan.answer_type == "out_of_scope" or (not plan.sources and plan.answer_type != "personal_amount"):
        answer = OUTSIDE_NOTICE if plan.answer_type == "out_of_scope" else NO_ANSWER
        web, usage, issues = (None, (), ()) if plan.sources else _search_reference(
            question, settings, session, client, payslip=payslip, workplace=context.workplace,
        )
        return ChatReply(answer, plan.answer_type, needs_official_check=True, web=web,
                         validation="web" if web is not None else "fallback", issues=issues, usage=usage)

    notice = configuration_message(settings)
    if plan.answer_type == "personal_amount":
        calculations = _calculation_rows(context.result.expected_pay, plan.pay_item)
        if notice or not calculations:
            issues = (notice or "해당 급여 항목의 엔진 계산식 없음",)
            return _amount_fallback(question, context, plan, calculations, issues)
    if notice:
        return ChatReply(NO_ANSWER, plan.answer_type, plan.sources,
                         needs_official_check=True, validation="fallback", issues=(notice,))
    return _request_answer(question, plan, context, client if client is not None else ChatClient(settings))


def ask(
    question: str, context: ExplanationContext, store: RagStore, settings: ApiSettings, session: ChatSession, *,
    client=None,
) -> ChatReply:
    text = " ".join(normalize("NFKC", question).translate(INVISIBLE_SPACES).split())
    if not text or len(question) > QUESTION_LENGTH or len(text) > QUESTION_LENGTH:
        return ChatReply("질문을 1~500자로 적어 주세요.", validation="blocked", issues=("질문 길이",))
    if private_text(text, context.workplace) or KOREAN_ID_LABEL.search(text):
        return ChatReply("개인정보는 빼고 질문해 주세요", validation="blocked", issues=("개인정보",))
    if INJECTION.search(text):
        return ChatReply("급여나 근로계약서에 관한 질문을 적어 주세요.", validation="blocked", issues=("질문에 지시 포함",))

    session.use_context(context.cache_key)
    if text in session.history:
        cached = session.history.pop(text)
        session.history[text] = cached
        return cached
    if session.questions_used >= QUESTION_LIMIT:
        return ChatReply("이 세션에서 질문 20개를 모두 사용했어요.", validation="blocked", issues=("질문 한도",))
    session.questions_used += 1
    answer = _respond(text, context, store, settings, session, client)
    session.history[text] = answer
    return answer

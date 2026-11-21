from datetime import time
from decimal import Decimal
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from engine import run_engine
from llm import explain
from llm.client import ApiSettings, ExtractionRequestError, ModelReply, TokenUsage
from llm.guards import NumericFacts, is_safe, private_text
from rag.store import RagStore, default_store
from schemas.explanation import EmployerMessages, Explanation
from schemas.extraction import SpecialTerm
from schemas.rag import TermRecord
from scripts.preview_explanations import CASES, confirmed_sample

SETTINGS = ApiSettings("fake-key", "fake-model")
APP = Path(__file__).resolve().parents[1] / "app" / "main.py"


class FakeClient:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.calls = []

    def read(self, prompt, payload, schema):
        self.calls.append((prompt, json.loads(payload), schema))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return ModelReply(reply, TokenUsage(41, 20))


def context(case="review_case", store=None):
    extraction = confirmed_sample(case)
    return explain.build_context(run_engine(extraction), extraction, store if store is not None else default_store())


def ai_example(report):
    payload = report.basic.model_dump()
    payload["summary"]["headline"] = "최저임금 기준으로 계산하면 207,170원 차이를 확인해 볼 수 있어요."
    payload["items"][0]["next_action"] = "사장님께 계약서의 시급부터 확인해 보세요."
    return payload


@pytest.mark.parametrize("text", [
    "임금을 확인해 보세요.",
    "임금은 매달 지급돼요.",
    "정해진 지급일에 맞춰 주실 수 있을까요?",
    "전액을 지급해 주셨는지 확인 부탁드려요.",
    "장소는 계약서에 적혀 있어요.",
    "한도를 확인해 보세요.",
    "조정이 필요한지 여쭤보고 싶어요.",
    "장부를 확인해 보세요.",
])
def test_common_words_are_not_treated_as_names(text):
    assert not private_text(text, workplace=None)
    assert is_safe(text, NumericFacts(), payslip=True, workplace=None)


@pytest.mark.parametrize(("text", "workplace"), [
    ("대표자 홍길동", None),
    ("근로자: 김철수", None),
    ("성명: 홍길동", None),
    ("010-1234-5678", None),
    ("900101-1234567", None),
    ("비공개식당에서 일해요.", "비공개 식당"),
    ("example@example.com", None),
    ("계좌 123-456-789", None),
    ("123456789", None),
    ("https://example.com", None),
])
def test_labelled_names_and_known_identifiers_are_still_private(text, workplace):
    assert private_text(text, workplace=workplace)


def test_generated_next_action_with_wage_word_is_kept():
    report = context()
    payload = ai_example(report)
    action = "임금을 확인해 보세요."
    payload["items"][0]["next_action"] = action
    result = explain.generate(report, SETTINGS, client=FakeClient(payload))
    assert result.content.items[0].next_action == action
    assert "items.item_1.next_action" in result.ai_fields


def test_context_sends_only_confirmed_facts_and_relevant_public_sources():
    extraction = confirmed_sample("review_case")
    extraction.contract.workplace.value = "비공개 식당"
    extraction.contract.job_description.value = "보내지 않을 업무 내용"
    extraction.contract.pay_day.value = "매월 10일, 전화 010-1234-9876"
    extraction.contract.hourly_wage.source_text = "전체 원문의 개인정보"
    extraction.extra_answers.pay_period_rule.value = "보내지 않을 정산 메모"
    extraction.contract.special_terms.value = [SpecialTerm.model_validate({
        "text": {"value": text, "confidence": "high"}, "type": {"value": "other", "confidence": "high"},
    }) for text in (
        "근무복을 반납합니다.", "중도 퇴사 시 임금을 공제한다",
        "예금주: 홍길동, 010-1234-9876", "대표자 홍길동", "비공개식당 근무",
    )]
    data = explain.build_context(run_engine(extraction), extraction, RagStore(laws=default_store().laws)).payload
    serialized = json.dumps(data, ensure_ascii=False)
    assert set(data["confirmed"]) == {
        "hourly_wage", "daily_hours", "weekly_hours", "work_days", "work_start", "work_end",
        "break_minutes", "pay_day", "special_terms", "workplace_size", "documents_provided", "weekly_holiday_hours",
    }
    assert data["confirmed"]["special_terms"] == ["근무복을 반납합니다.", "중도 퇴사 시 임금을 공제한다"]
    assert data["confirmed"]["pay_day"] is None and "dictionary" not in data
    for private in ("비공개 식당", "비공개식당", "010-1234-9876", "홍길동", "전체 원문", "보내지 않을", "source_text"):
        assert private not in serialized
    assert any(law["basis_key"] == "minimum_wage" and law["text"] for law in data["laws"])
    assert data["summary_facts"][0]["contract_difference"] == "173800"


def test_guards_reject_new_amounts_dates_legal_language_and_unknown_basis_keys():
    report = context()
    payload = ai_example(report)
    payload["summary"]["headline"] = "999,999원을 덜 받았을 수 있어요."
    payload["items"][0].update(easy_explanation="최저임금 위반이에요.", basis_keys=["made_up", "minimum_wage"])
    payload["items"][0]["next_action"] = "2027-11-10에 사장님께 확인해 보세요."
    payload["terms"][0]["user_context"] = "주 37시간을 일해요."
    report_out = explain.generate(report, SETTINGS, client=FakeClient(payload))

    assert report_out.content.summary.headline == report.basic.summary.headline
    assert report_out.content.items[0].easy_explanation == report.basic.items[0].easy_explanation
    assert report_out.content.items[0].next_action == report.basic.items[0].next_action
    assert report_out.content.items[0].basis_keys == ["minimum_wage"]
    assert report_out.content.terms[0].user_context == report.basic.terms[0].user_context
    assert report_out.rejected_sentences >= 4
    assert report_out.usage == (TokenUsage(41, 20),)


def test_scoped_wage_numbers_cannot_be_swapped_between_causes_and_items():
    report = context()
    payload = report.basic.model_dump()
    payload["summary"]["reason"] = "최저임금 기준으로 계산하면 주휴수당 미지급 179,362원과 최저임금 차이 33,370원이에요."
    holiday = next(index for index, item in enumerate(report.items) if item.title == "주휴수당")
    payload["items"][holiday]["easy_explanation"] = "주휴수당은 계약 시급 기준 173,800원과 최저임금 기준 207,170원이에요."
    out = explain.generate(report, SETTINGS, client=FakeClient(payload))
    assert out.content.summary.reason == report.basic.summary.reason
    assert "173,800원" in out.content.items[holiday].easy_explanation
    assert "179,362원" in out.content.items[holiday].easy_explanation
    assert "207,170원" not in out.content.items[holiday].easy_explanation


def test_dictionary_definition_is_used_verbatim_and_missing_terms_are_filled():
    dictionary = TermRecord(term="주휴수당", aliases=[], easy_explanation="테스트용 사전의 정의예요.",
                            related_basis_keys=["weekly_holiday_pay"], source_url="https://example.com/term",
                            checked_date="2026-01-01")
    report = context(store=RagStore(terms=[dictionary]))
    payload = report.basic.model_dump()
    payload["terms"] = [{"term": "주휴수당", "general_meaning": "사전과 다른 설명이에요.",
                         "user_context": "계약 시급 기준으로 주 4시간분을 계산했어요."}]
    result = explain.generate(report, SETTINGS, client=FakeClient(payload))
    term = next(term for term in result.content.terms if term.term == "주휴수당")
    assert term.general_meaning == dictionary.easy_explanation
    assert "주 4시간분" in term.user_context
    assert "terms.주휴수당.user_context" in result.ai_fields
    assert "terms.주휴수당.general_meaning" not in result.ai_fields
    assert 3 <= len(result.content.terms) <= 6


def test_format_retries_once_and_missing_keys_or_api_errors_keep_baseline():
    report = context()
    fake = FakeClient({}, None)
    result = explain.generate(report, SETTINGS, client=fake)
    assert len(fake.calls) == 2 and "형식 검증에 실패" in fake.calls[1][0]
    assert result.content == report.basic and not result.ai_fields
    assert result.format_failures == 2
    missing = FakeClient()
    assert explain.generate(report, ApiSettings("", ""), client=missing).content == report.basic
    assert not missing.calls
    assert explain.generate(report, SETTINGS, client=FakeClient(ExtractionRequestError("테스트 실패"))).content == report.basic


def test_contract_only_falls_back_on_payment_comparison_and_mentions_size_options():
    extraction = confirmed_sample("contract_only")
    extraction.extra_answers.workplace_size.value = "unknown"
    report = explain.build_context(run_engine(extraction), extraction, default_store())
    payload = report.basic.model_dump()
    payload["summary"]["reason"] = "명세서에서 확인한 실제 지급액이 모자라요."
    payload["messages"]["employer_question"] = "사장님, 실제 받은 금액이 모자라서 확인해 주세요."
    result = explain.generate(report, SETTINGS, client=FakeClient(payload))
    assert result.content.summary.reason == report.basic.summary.reason
    assert "5인 이상과 5인 미만" in result.content.summary.reason
    assert result.content.messages.employer_question == report.basic.messages.employer_question
    assert "명세서" not in result.content.messages.employer_question
    assert all(row["status"] == "not_compared" for row in report.payload["comparison"])
    assert all(item["kind"] == "contract" for item in report.payload["review_items"])


def test_app_renders_ai_reuses_session_cache_and_rewrites_only_messages(monkeypatch):
    report = context()
    draft = report.basic.messages.model_dump()
    draft["employer_question"] = draft["employer_question"].replace(
        "산정 근거를 알려주실 수 있을까요?", "확인해 주실 수 있을까요?"
    )
    fake = FakeClient(ai_example(report), draft)
    monkeypatch.setattr(explain, "read_settings", lambda: SETTINGS)
    monkeypatch.setattr(explain, "ExtractionClient", lambda settings: fake)
    app = AppTest.from_file(str(APP), default_timeout=20)
    extraction = confirmed_sample("review_case")
    app.session_state["step"] = 3
    app.session_state["confirmed"] = extraction
    app.session_state["result"] = run_engine(extraction)
    app.run()

    assert not app.exception
    assert len(fake.calls) == 1 and fake.calls[0][2] is Explanation
    assert any("207,170원 차이를 확인해 볼 수 있어요" in row.value for row in app.header)
    assert any("AI가 작성한 설명이에요" in row.value for row in app.caption)
    assert any("요약의 금액은 원인별, 표의 금액은 항목별" in row.value for row in app.caption)
    app.run()
    assert len(fake.calls) == 1
    summary_before = app.header[0].value
    widget = next(field for field in app.text_input if field.label == "다시 쓰기 요청")
    widget.set_value("카톡으로 짧게, 홍길동 01012349876")
    app.button(key="rewrite-employer-messages").click().run()

    assert not app.exception and len(fake.calls) == 2 and fake.calls[1][2] is EmployerMessages
    assert "홍길동" not in json.dumps(fake.calls[1][1], ensure_ascii=False)
    assert fake.calls[1][1]["style_hints"] == ["메신저로 보낼 수 있게", "짧고 간결하게"]
    assert "summary" not in fake.calls[1][1] and "special_terms" not in fake.calls[1][1]["confirmed"]
    assert app.header[0].value == summary_before
    assert app.text_area[0].value == draft["employer_question"]
    app.text_area[0].set_value("사장님, 제가 직접 고쳤습니다.").run()
    assert app.code[0].value == "사장님, 제가 직접 고쳤습니다."
    assert len(fake.calls) == 2


def test_a_safe_sentence_is_kept_when_the_other_sentence_falls_back():
    report = context()
    payload = report.basic.model_dump()
    payload["items"][0]["easy_explanation"] = "계약서의 시급을 확인해 볼 수 있어요. 30,000원으로 바뀌었어요."
    result = explain.generate(report, SETTINGS, client=FakeClient(payload))
    assert "계약서의 시급을 확인해 볼 수 있어요." in result.content.items[0].easy_explanation
    assert report.basic.items[0].easy_explanation in result.content.items[0].easy_explanation
    assert "30,000원" not in result.content.items[0].easy_explanation
    assert "items.item_1.easy_explanation" in result.mixed_fields


def test_sample_flow_and_message_rewrites_do_not_call_the_api(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(explain, "read_settings", lambda: SETTINGS)
    monkeypatch.setattr(explain, "ExtractionClient", lambda settings: fake)
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    app.button(key="begin-check").click().run()
    app.button(key="consent").click().run()
    app.button(key="show-sample-demo").click().run()
    app.button(key="sample-review_case").click().run()
    app.form_submit_button[0].click().run()
    tone = next(field for field in app.selectbox if field.label == "말투")
    tone.set_value("간단하게")
    app.button(key="rewrite-employer-messages").click().run()

    assert not app.exception
    assert not fake.calls
    assert "산정 근거 확인 부탁드립니다" in app.text_area[0].value
    assert any(row.value == "기본 설명" for row in app.caption)


def test_basic_term_context_uses_engine_amounts_and_omits_missing_situations():
    normal = context("normal_case", RagStore())
    assert "예상 급여" not in normal.payload["term_candidates"]
    terms = {term.term: term for term in normal.basic.terms}
    assert terms["기본급"].user_context == "계약 기준으로 시급 10,500원 × 주 20시간 = 주 210,000원이에요."
    assert "연장근로" not in terms and "야간근로" not in terms

    extraction = confirmed_sample("normal_case")
    extraction.contract.daily_hours.value = Decimal("9")
    extraction.contract.work_start.value = time(21)
    extraction.contract.work_end.value = time(2)
    calculation = explain.build_context(run_engine(extraction), extraction, RagStore())
    terms = {term.term: term for term in calculation.basic.terms}
    assert "하루 기준 초과분 4시간과 주 기준 초과분 0시간" in terms["연장근로"].user_context
    assert "21,000원" in terms["연장근로"].user_context
    assert "야간 최대 4시간" in terms["야간근로"].user_context
    assert "84,000원" in terms["야간근로"].user_context

    extraction.contract.weekly_hours.value = None
    incomplete = explain.build_context(run_engine(extraction), extraction, RagStore())
    assert next(term for term in incomplete.basic.terms if term.term == "기본급").user_context == ""
    assert all("관련 항목을 확인할 수 있어요." not in term.user_context for term in incomplete.basic.terms)


def test_seeded_samples_are_available_without_api_and_live_demo_has_four_confirmed_cases():
    for case in CASES:
        report = context(case)
        assert report.basic.summary.headline
        if case in explain.SAMPLE_NAMES:
            record = json.loads((explain.SAMPLE_REPORTS / f"{case}_explanation.json").read_text(encoding="utf-8"))
            assert len(record["cache_key"]) == 64
            assert Explanation.model_validate(record["explanation"]).summary.headline
            saved = explain.saved_sample(case, report)
            assert saved.content == report.basic and not saved.ai_fields


def test_config_and_confirmed_clause_amounts_are_included_and_allowed():
    report = context("tricky")
    payload = report.basic.model_dump()
    special_id = next(index for index, item in enumerate(report.items) if item.title == "특약")
    clause = "계약서에 적힌 50,000원 특약은 확인이 필요해요."
    minimum = "2026년 기준 시급은 10,320원이고 계약 시급은 10,000원이에요."
    hours = "주 15시간, 하루 8시간과 주 40시간, 22:00부터 06:00, 30분과 1시간, 5인 기준을 참고했어요."
    payload["items"][special_id]["easy_explanation"] = clause
    term_index = next(index for index, term in enumerate(report.basic.terms) if term.term == "최저임금")
    payload["terms"][term_index]["user_context"] = minimum
    result = explain.generate(report, SETTINGS, client=FakeClient(payload))

    assert result.content.items[special_id].easy_explanation == clause
    assert result.content.terms[term_index].user_context == minimum
    facts = explain._facts(report, "terms.휴게시간.user_context", None)
    assert facts.permits(hours)
    assert not facts.permits(hours.replace("06:00", "07:00"))
    assert not facts.permits("51,000원")
    assert report.payload["standards"][0]["year"] == 2026


def test_cause_amounts_allow_paraphrases_and_replaces_only_rejected_message_sentences():
    report = context()
    breakdown = report.payload["cause_breakdown"][0]
    assert breakdown["contract_shortfall"]["amount_won"] == "173800"
    assert breakdown["minimum_wage_gap"]["amount_won"] == "33370"
    assert breakdown["total_difference_won"] == "207170"
    payload = report.basic.model_dump()
    paraphrase = "차이는 최저임금 시급에 따른 33,370원과 주휴수당으로 계산한 173,800원으로 나뉘어요."
    cause = "최저임금을 적용해 계산한 월 차액에는 주휴수당 쪽 173,800원과 시급 차이에 따른 33,370원이 들어갑니다."
    greeting = "사장님, 잠시 여쭤봐도 될까요?"
    closing = "계산 내역을 알려주실 수 있을까요?"
    payload["summary"]["reason"] = paraphrase
    payload["messages"]["employer_question"] = f"{greeting} {cause} 999,999원을 보내 주세요. {closing}"

    result = explain.generate(report, SETTINGS, client=FakeClient(payload))
    question = result.content.messages.employer_question
    assert result.content.summary.reason == paraphrase
    assert greeting in question and cause in question and closing in question
    assert "999,999원" not in question
    assert "messages.employer_question" in result.mixed_fields
    assert ("messages.employer_question", "입력에 없는 숫자") in result.rejection_reasons


def test_missing_cause_amounts_are_replaced_without_losing_other_sentences():
    report = context()
    greeting = "사장님, 시급을 확인해 보고 싶습니다."
    candidate = {"employer_question": f"{greeting} 최저임금 기준으로 계산한 차이 207,170원이에요.",
                 "revision_request": report.basic.messages.revision_request}
    result = explain.rewrite(report, SETTINGS, "정중하게", "", client=FakeClient(candidate))
    question = result.messages.employer_question
    assert greeting in question
    assert "173,800원" in question and "33,370원" in question
    assert result.rejected_sentences == 1

from datetime import time
from decimal import Decimal
import json
from pathlib import Path
import re

from streamlit.testing.v1 import AppTest

from app.components.evidence import evidence_markup
from app.components.schedule import EDITOR_KEY
from app.steps import upload
from engine import run_engine
from llm import explain, extract
from llm.client import ApiSettings, ModelReply
from llm.guards import is_safe
from llm.samples import load_sample
from rag.basis_map import check_basis_keys, pay_basis_keys
from rag.store import RagStore, default_store
from schemas import ExtractionResult
from schemas.extraction import ExtractedField
from scripts.eval_extraction import expected_fields, field_values, read_fixture_pair

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ApiSettings("fake-key", "fake-model")


class ScheduleClient:
    def read(self, prompt, payload, schema):
        assert "서류 추출 프롬프트 v3" in prompt
        assert set(json.loads(payload)) == {"contract"}
        return ModelReply(expected_fields("schedule").model_dump(mode="json"))


def extracted_schedule():
    return extract.extract_documents(read_fixture_pair("schedule"), SETTINGS, client=ScheduleClient()).result


def pay(result, item, period="weekly", *, basis="contract", size="5_or_more"):
    scenario = next(row for row in result.expected_pay if row.basis == basis and row.workplace_size == size)
    return next(row for row in scenario.items if row.item == item and row.period == period)


def test_schedule_fixture_extraction_highlight_values_and_example_pay():
    outcome = extracted_schedule()
    assert outcome.documents_provided.contract and not outcome.documents_provided.payslip
    assert outcome.extra_answers.workplace_size.value == "5_or_more"
    assert outcome.extra_answers.workplace_size.source_text == "상시근로자 7명"
    rows = outcome.contract.work_schedule.value
    assert rows[0].days.value == ["월", "수", "목"]
    assert rows[1].end.value == time(0, 30)
    assert rows[1].source_text.endswith("휴게 22:15~22:45")
    assert rows[1].break_minutes.value is None
    assert outcome.contract.work_start.value is None

    outcome.user_confirmed = True
    result = run_engine(outcome)
    expected = {
        "기본급": (230000, 999350), "주휴수당": (46000, 199870),
        "야간 가산": (11500, 49968), "합계": (287500, 1249188),
    }
    for item, (weekly, monthly) in expected.items():
        assert pay(result, item).amount == weekly
        assert pay(result, item, "monthly").amount == monthly
    assert "금 2시간 × 11,500원" in pay(result, "야간 가산").formula
    assert "금 2시간" in pay(result, "야간 가산", "monthly").formula
    assert next(row for row in result.checks if row.item == "휴게시간").status == "ok"
    assert all(row.status == "not_compared" for row in result.comparison)
    assert not any(row.item == "요일별 근무 시간" for row in result.limitations)
    truth = dict(field_values(expected_fields("schedule").model_dump()))
    assert truth["contract.work_schedule.2.end"] == "00:30"
    assert "contract.work_schedule.2.source_text" not in truth


def test_minutes_only_night_is_upper_bound_and_mismatch_does_not_change_base_pay():
    outcome = extracted_schedule()
    outcome.user_confirmed = True
    friday = outcome.contract.work_schedule.value[1]
    friday.break_start.value = friday.break_end.value = None
    friday.break_minutes.value = 30
    outcome.contract.weekly_hours.value = Decimal(19)
    result = run_engine(outcome)
    assert pay(result, "야간 가산").amount == Decimal("14375")
    assert "상한" in pay(result, "야간 가산").applied_condition
    assert any(row.item == "야간 가산" and "금" in row.reason for row in result.limitations)
    assert any("주 20시간" in row.reason and "19시간" in row.reason for row in result.limitations)
    assert pay(result, "기본급").amount == 218500


def test_schedule_overtime_and_break_checks_run_per_day_for_both_wage_bases():
    outcome = extracted_schedule()
    outcome.user_confirmed = True
    outcome.contract.hourly_wage.value = Decimal(10000)
    outcome.extra_answers.workplace_size.value = "unknown"
    monday = outcome.contract.work_schedule.value[0]
    monday.start.value, monday.end.value = time(9), time(20)
    friday = outcome.contract.work_schedule.value[1]
    friday.break_start.value = friday.break_end.value = None
    friday.break_minutes.value = 15
    result = run_engine(outcome)

    assert pay(result, "연장 가산").amount == 37500
    assert pay(result, "연장 가산", basis="minimum_wage").amount == 38700
    assert pay(result, "연장 가산", size="under_5").amount == 0
    assert pay(result, "연장 가산", "monthly").formula.startswith("(max(월·수·목 각 2.5시간")
    found = next(row for row in result.checks if row.item == "휴게시간")
    assert found.status == "needs_review" and "월·수·목" in found.description and "금" in found.description
    assert all(row.status == "not_compared" and row.basis == "minimum_wage" for row in result.comparison)


def test_legacy_contract_synthesizes_one_row_without_changing_normal_total():
    contract = ExtractionResult.model_validate(load_sample("normal_case"))
    entries = contract.contract.schedule_entries()
    assert len(entries) == 1 and entries[0].start.value == time(9)
    assert entries[0].break_minutes.value == 30
    assert pay(run_engine(contract), "합계", "monthly").amount == 1094940


def test_explanation_supplies_schedule_values_and_allows_only_known_shift_numbers():
    extracted = extracted_schedule()
    extracted.user_confirmed = True
    context = explain.build_context(run_engine(extracted), extracted, RagStore())
    assert "work_start" not in context.payload["confirmed"]
    assert Decimal(context.payload["work_schedule"][1]["night_hours"]) == 2
    assert "source_text" not in json.dumps(context.payload)
    term = next(term for term in context.basic.terms if term.term == "휴게시간")
    assert "월·수·목 18:00~18:30" in term.user_context and "금 22:15~22:45" in term.user_context
    facts = explain._facts(context, "terms.야간근로.user_context", None)
    assert is_safe("금요일 야간근로는 2시간이에요.", facts, payslip=False, workplace=None)
    assert not is_safe("금요일 야간근로는 3시간이에요.", facts, payslip=False, workplace=None)


def open_schedule_review(monkeypatch):
    monkeypatch.setattr(upload, "read_settings", lambda: SETTINGS)
    monkeypatch.setattr(extract, "ExtractionClient", lambda settings: ScheduleClient())
    app = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=20).run()
    app.button(key="begin-check").click().run()
    app.button(key="consent").click().run()
    fixture = ROOT / "tests" / "fixtures" / "docs" / "schedule_contract.pdf"
    app.file_uploader[0].upload(fixture.name, fixture.read_bytes(), "application/pdf").run()
    app.button(key="read-uploads").click().run()
    assert not app.exception
    return app


def test_review_table_edit_changes_pay_and_keeps_headcount_recommendation(monkeypatch):
    app = open_schedule_review(monkeypatch)
    assert app.selectbox(key="review-workplace-size").value == "5_or_more"
    assert any("상시근로자 7명" in item.value for item in app.caption)
    assert any("금 근무 종료는 익일" in item.value for item in app.caption)
    assert any("요일별 시간을 더하면 주 20시간" in item.value for item in app.caption)
    assert any('<mark class="sample-mark">금 19:00~익일 00:30 휴게 22:15~22:45</mark>' in row.proto.body
               for row in app.get("html"))

    app.session_state[EDITOR_KEY] = {"edited_rows": {1: {"end": "익일 01:30"}}, "added_rows": [], "deleted_rows": []}
    app.run()
    assert any("주 21시간" in item.value for item in app.warning)
    next(button for button in app.form_submit_button if button.label == "결과 보기").click().run()

    assert not app.exception
    assert app.session_state.confirmed.contract.work_schedule.value[1].end.value == time(1, 30)
    assert pay(app.session_state.result, "야간 가산").amount == 17250
    assert app.metric[1].value == "비교 안 함"


def test_review_can_remove_a_shift_and_add_another(monkeypatch):
    app = open_schedule_review(monkeypatch)
    app.session_state[EDITOR_KEY] = {
        "edited_rows": {},
        "deleted_rows": [1],
        "added_rows": [{"days": "토", "start": "22:00", "end": "23:00", "break_minutes": "0"}],
    }
    app.run()
    assert any("주 16시간" in item.value for item in app.warning)
    next(button for button in app.form_submit_button if button.label == "결과 보기").click().run()

    assert not app.exception
    entries = app.session_state.confirmed.contract.work_schedule.value
    assert [entry.days.value for entry in entries] == [["월", "수", "목"], ["토"]]
    assert entries[0].source_text and entries[1].source_text is None
    assert pay(app.session_state.result, "야간 가산").amount == 5750


def test_headcount_suggests_only_a_stated_count():
    assert extract.workplace_size_hint("상시 근로자 수: 4인") == (4, "상시 근로자 수: 4인")
    assert extract.workplace_size_hint("상시근로자 4명 이상") is None
    assert extract.workplace_size_hint("상시근로자 4명, 상시근로자 7명") is None
    assert extract.workplace_size_hint("근무 요일은 금요일입니다.") is None


def test_required_terms_and_total_chips_prioritize_direct_topics():
    extracted = ExtractionResult.model_validate(load_sample("review_case"))
    result = run_engine(extracted)
    store = default_store()
    context = explain.build_context(result, extracted, store)
    explained = explain.Explained(context.basic)
    required = next(row for row in result.checks if row.item == "필수 기재사항")
    pattern = r"<summary>([^<]+) 뜻 보기</summary>"
    assert re.findall(pattern, evidence_markup(
        store, check_basis_keys(required, extracted.contract), required.item, explained,
    )) == ["근로조건 명시", "근로계약서"]
    assert re.findall(pattern, evidence_markup(
        store, pay_basis_keys("합계"), "합계", explained,
    )) == ["임금 지급 원칙", "임금명세서"]

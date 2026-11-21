from html import escape
import json
import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.components.evidence import evidence_markup
from llm import explain
from scripts.preview_explanations import confirmed_sample
from app.steps import upload
from engine import run_engine
from llm.client import ApiSettings, read_settings
from rag import store as rag
from rag.basis_map import BASIS_KEYS, check_basis_keys, pay_basis_keys
from schemas import ExtractionResult
from schemas.engine import CheckResult
from schemas.extraction import SpecialTerm
from scripts.check_rag_data import check_rows, format_table

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).with_name("fixtures")
RAG_FIXTURES = FIXTURES / "rag"


def extraction(sample="normal_case"):
    payload = json.loads((FIXTURES / f"{sample}.json").read_text(encoding="utf-8"))
    return ExtractionResult.model_validate(payload["extraction"])


def review_app(monkeypatch, store):
    monkeypatch.setattr(rag, "default_store", lambda: store)
    monkeypatch.setattr(upload, "read_settings", lambda: ApiSettings("", ""))
    app = AppTest.from_file(str(ROOT / "app" / "main.py"), default_timeout=20).run()
    app.button(key="begin-check").click().run()
    app.button(key="consent").click().run()
    app.button(key="sample-review_case").click().run()
    next(button for button in app.form_submit_button if button.label == "결과 보기").click().run()
    assert not app.exception
    return app


def test_current_team_laws_pass_format_validation():
    path = ROOT / "data" / "laws.json"
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    store = rag.load_store(ROOT / "data")

    assert raw
    assert len(store.laws) == len(raw), tuple(issue for issue in store.issues if issue.file == "laws.json")
    for entry in raw:
        law = store.get_law(entry["basis_key"])
        assert law is not None
        assert law.text == entry["text"]


def test_store_looks_up_keys_aliases_and_same_text_under_distinct_keys():
    store = rag.load_store(RAG_FIXTURES)

    assert not store.issues
    assert store.get_law("wage_payment").text == store.get_law("wage_deduction").text
    assert store.get_law("wage_payment").basis_key != store.get_law("wage_deduction").basis_key
    terms = store.get_terms("weekly_holiday_pay")
    assert terms and store.get_term("주휴 수당") == terms[0]
    assert store.get_term("주휴수당") == terms[0]
    assert store.get_law("missing_key") is None


def test_search_ranks_alias_with_particles_and_returns_source_fields():
    store = rag.load_store(RAG_FIXTURES)
    hits = store.search("주휴 수당이 뭐예요?", k=2)

    assert len(hits) == 2
    assert hits[0].record.term == "테스트용 주휴수당"
    assert hits[0].source_url == "https://example.com/test/term"
    assert hits[1].record.law_name == "테스트용 가상 자료"
    assert hits[1].record.article_no == "테스트용 항목 B"
    assert hits[1].source_url == "https://example.com/test/holiday"
    assert hits[0].score >= hits[1].score
    assert store.search("???", 3) == []
    assert store.search("주휴 수당", 0) == []


def test_basis_map_covers_active_engine_result_items():
    data = extraction()
    data.extra_answers.workplace_size.value = "unknown"
    result = run_engine(data)
    seen = set()
    for check in result.checks:
        keys = check_basis_keys(check, data.contract)
        assert keys, check.item
        seen.update(keys)
    for item in [*(item for scenario in result.expected_pay for item in scenario.items), *result.comparison]:
        keys = pay_basis_keys(item.item)
        assert keys, item.item
        seen.update(keys)
    assert seen == set(BASIS_KEYS)
    minimum = next(row for row in result.checks if row.item == "최저임금")
    assert check_basis_keys(minimum, data.contract) == ("minimum_wage", "minimum_wage_2026")


@pytest.mark.parametrize(("types", "keys"), [
    (["penalty"], ("penalty_clause",)),
    (["wage_deduction"], ("wage_deduction",)),
    (["penalty", "wage_deduction"], ("penalty_clause", "wage_deduction")),
])
def test_special_terms_map_by_confirmed_type(types, keys):
    data = extraction()
    data.contract.special_terms.value = [
        SpecialTerm.model_validate({
            "text": {"value": "테스트용 특약", "confidence": "high"},
            "type": {"value": term_type, "confidence": "high"},
        })
        for term_type in types
    ]
    special = CheckResult(item="특약", status="needs_review", description="테스트용", evidence_ids=["special_terms"])
    assert check_basis_keys(special, data.contract) == keys


def test_invalid_rows_skipped_and_checker_reports_all_required_diagnostics(tmp_path, caplog):
    laws = json.loads((RAG_FIXTURES / "laws.json").read_text(encoding="utf-8"))
    terms = json.loads((RAG_FIXTURES / "terms.json").read_text(encoding="utf-8"))
    help_data = json.loads((RAG_FIXTURES / "help_channels.json").read_text(encoding="utf-8"))
    duplicate = {**laws[0], "text": "테스트용 중복 행"}
    broken = {**laws[0], "basis_key": "break_time", "source_url": "", "checked_date": ""}
    unknown_term = {**terms[0], "term": "테스트용 다른 용어", "related_basis_keys": ["unknown_key"]}
    files = {
        "laws.json": [*laws, duplicate, broken],
        "terms.json": [*terms, terms[0], unknown_term],
        "help_channels.json": [*help_data, {**help_data[0], "checked_date": "잘못된 날짜"}],
    }
    for name, value in files.items():
        (tmp_path / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    # An unreadable upcoming file must never enter the app loader.
    (tmp_path / "laws_upcoming.json").write_text("invalid", encoding="utf-8")

    store = rag.load_store(tmp_path)
    assert len(store.laws) == len(laws)
    assert len(store.terms) == 2
    assert len(store.help_channels) == 1
    assert store.get_law("break_time") is None
    assert not any(issue.file == "laws_upcoming.json" for issue in store.issues)
    empty_fields = {issue.field for issue in store.issues if issue.kind == "빈 필드"}
    assert empty_fields == {"source_url", "checked_date"}
    assert "형식 오류" in caplog.text
    assert {issue.file for issue in store.issues if issue.kind == "중복"} == {"laws.json", "terms.json"}
    assert "unknown_key" in format_table(check_rows(store))
    assert "누락 키" in format_table(check_rows(store))


def test_article_markup_preserves_newlines_and_displays_both_wage_sources():
    store = rag.load_store(RAG_FIXTURES)
    law = store.get_law("minimum_wage")
    html = evidence_markup(store, ("minimum_wage", "minimum_wage_2026"), "최저임금")

    assert f'<pre class="rag-original"><span>{escape(law.text)}</span></pre>' in html
    assert '<summary>근거 조문</summary>' in html
    assert 'https://example.com/test/wage' in html and 'https://example.com/test/2026' in html
    assert "2026-01-01" in html


def test_empty_data_renders_preparation_chips_and_generic_help(monkeypatch):
    app = review_app(monkeypatch, rag.RagStore())
    content = "\n".join(element.proto.body for element in app.get("html"))

    assert "근거 조문 준비 중" in content
    assert "용어 설명 준비 중" not in content
    assert "<summary>주휴수당 뜻 보기</summary>" in content
    assert "<summary>최저임금 뜻 보기</summary>" in content
    assert "<summary>휴게시간 뜻 보기</summary>" in content
    assert any(heading.value == "더 확인하고 싶다면" for heading in app.subheader)
    assert any(note.value == "공식 상담 기관에 확인해 보세요." for note in app.caption)


def test_card_terms_follow_basis_and_put_the_card_name_first():
    store = rag.RagStore()
    extracted = confirmed_sample("review_case")
    context = explain.build_context(run_engine(extracted), extracted, store)
    report = explain.Explained(context.basic)
    for title, keys, expected in (
        ("주휴수당", ("weekly_holiday_pay", "part_time"), ["주휴수당", "소정근로시간"]),
        ("최저임금", ("minimum_wage", "minimum_wage_2026"), ["최저임금", "기본급"]),
        ("휴게시간", ("break_time",), ["휴게시간"]),
        ("필수 기재사항", ("weekly_holiday_pay", "required_terms", "minimum_wage"), ["주휴수당", "최저임금"]),
        ("특약", ("penalty_clause",), []),
    ):
        html = evidence_markup(store, keys, title, report)
        assert re.findall(r"<summary>([^<]+) 뜻 보기</summary>", html) == expected
        assert "용어 설명 준비 중" not in html
    assert "근거 조문 준비 중" in evidence_markup(store, ("penalty_clause",), "특약", report)


def test_card_uses_dictionary_meaning_source_and_explanation_context():
    store = rag.load_store(RAG_FIXTURES)
    extracted = confirmed_sample("review_case")
    context = explain.build_context(run_engine(extracted), extracted, store)
    term = next(term for term in context.basic.terms if term.term == "테스트용 주휴수당")
    term.general_meaning = "이 설명은 사전 뜻으로 대체돼요."
    report = explain.Explained(context.basic, frozenset({"terms.테스트용 주휴수당.user_context"}))
    term.user_context = "테스트용 내 상황 문장이에요."
    html = evidence_markup(store, ("weekly_holiday_pay",), "주휴수당", report)

    assert "<summary>테스트용 주휴수당 뜻 보기</summary>" in html
    assert store.terms[0].easy_explanation in html
    assert "이 설명은 사전 뜻으로 대체돼요." not in html
    assert "https://example.com/test/term" in html
    assert "테스트용 내 상황 문장이에요." in html
    assert "뜻: 용어 사전 · 내 상황: AI 설명" in html
    assert "<small>기본 설명</small> ·" not in html
    term.user_context = ""
    without_context = evidence_markup(store, ("weekly_holiday_pay",), "주휴수당", report)
    assert "<small>뜻: 용어 사전</small>" in without_context


def test_result_uses_fixture_terms_laws_and_help_channels(monkeypatch):
    store = rag.load_store(RAG_FIXTURES)
    app = review_app(monkeypatch, store)
    content = "\n".join(element.proto.body for element in app.get("html"))

    assert escape(store.laws[0].text) in content
    assert store.terms[0].easy_explanation in content
    assert store.help_channels[0].name in content
    assert store.help_channels[0].url in content
    assert any(heading.value == "더 확인하고 싶다면" for heading in app.subheader)


def test_dev_tools_flag_reads_only_explicit_true(tmp_path, monkeypatch):
    monkeypatch.delenv("SHOW_DEV_TOOLS", raising=False)
    dotenv = tmp_path / ".env"
    assert not read_settings(dotenv).show_dev_tools
    dotenv.write_text("SHOW_DEV_TOOLS=true\n", encoding="utf-8")
    assert read_settings(dotenv).show_dev_tools
    dotenv.write_text("SHOW_DEV_TOOLS=false\n", encoding="utf-8")
    assert not read_settings(dotenv).show_dev_tools

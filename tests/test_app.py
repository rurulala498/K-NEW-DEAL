from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.steps.upload import ReviewDraft
from llm import chat
from llm.extract import blank_result
from llm.samples import load_sample
from rag import store as rag_store

APP = Path(__file__).resolve().parents[1] / "app" / "main.py"
DISCLAIMER = "법률 자문이 아니며 최종 판단은 관계 기관에서 이루어집니다."


def start():
    return AppTest.from_file(str(APP), default_timeout=20).run()


def check_footer(app):
    assert any(DISCLAIMER in row.value for row in app.caption)


@pytest.mark.parametrize(("sample", "monthly_total", "needs_review"), [
    ("normal_case", "1,094,940원", False),
    ("review_case", "1,076,170원", True),
])
def test_sample_requires_confirmation_and_shows_results(sample, monthly_total, needs_review, monkeypatch):
    store = rag_store.default_store()
    monkeypatch.setattr(rag_store, "default_store", lambda: rag_store.RagStore(laws=store.laws, terms=store.terms))
    app = start()
    assert not app.exception
    assert not app.get("file_uploader")
    assert not app.metric
    check_footer(app)

    app.button(key="begin-check").click().run()
    app.button(key="consent").click().run()
    assert len(app.get("file_uploader")) == 2
    assert app.button(key=f"sample-{sample}")
    app.button(key=f"sample-{sample}").click().run()

    assert not app.exception
    assert not app.metric
    assert any(row.label.startswith("나머지 항목 ") for row in app.expander)
    assert any("신뢰도가 낮아요" in row.label for row in app.text_input) is needs_review
    check_footer(app)

    next(button for button in app.form_submit_button if button.label == "결과 보기").click().run()

    assert not app.exception
    assert app.metric[0].value == monthly_total
    assert any(row.label == "항목별 근거·금액 그래프 보기" for row in app.expander)
    assert any(row.value == "사장님께 보낼 문구" for row in app.subheader) is needs_review
    receipt = next(row.proto.body for row in app.get("html") if '<table class="pay-table">' in row.proto.body)
    assert ('<th scope="col">최저임금 기준</th>' in receipt) is needs_review
    if needs_review:
        title = next(row.proto.body for row in app.get("html") if 'class="report-summary-title"' in row.proto.body)
        assert "최저임금 기준으로 계산하면" in title and "headline-amount" in title and "207,170원" in title
        reason = next(row.proto.body for row in app.get("html") if 'class="report-summary-reason"' in row.proto.body)
        assert all(value in reason for value in ("주휴수당 미지급", "173,800원", "최저임금 차이", "33,370원"))
        assert "pay-row-diff" in receipt and '<summary>계산식</summary>' in receipt
        assert any(row.value == "계약대로 1,042,800원 · 최저임금 기준 1,076,170원" for row in app.caption)
        assert all(amount in receipt for amount in ("1,042,800원", "1,076,170원", "869,000원", "207,170원"))
        assert any('class="contract-label">계약 시급 기준' in row.proto.body
                   and 'class="expected-label">최저임금 기준' in row.proto.body for row in app.get("html"))
    check_footer(app)

    app.button(key="chat-prompt-0").click().run()
    assert not app.exception
    assert any(row.value == chat.NO_ANSWER for row in app.text)
    assert any(row.value == chat.OFFICIAL_HELP for row in app.caption)


def test_edited_input_is_calculated_and_restart_clears_previous_result():
    app = start()
    app.button(key="begin-check").click().run()
    app.button(key="consent").click().run()
    app.button(key="sample-normal_case").click().run()
    app.text_input(key="review-contract-hourly_wage").set_value("10000")
    app.selectbox(key="review-workplace-size").set_value("unknown")
    next(button for button in app.form_submit_button if button.label == "결과 보기").click().run()

    assert not app.exception
    assert app.metric[0].value == "1,076,170원"
    assert [row.value for row in app.metric if row.label == "월 받을 금액"] == ["1,076,170원", "1,076,170원"]
    assert [tab.label for tab in app.get("tab")] == ["5인 이상", "5인 미만"]

    app.button(key="restart").click().run()
    assert not app.exception
    assert not app.metric
    assert not app.get("file_uploader")
    assert "result" not in app.session_state
    check_footer(app)
    app.button(key="begin-check").click().run()
    app.button(key="consent").click().run()
    app.button(key="sample-normal_case").click().run()
    assert app.text_input(key="review-contract-hourly_wage").value == "10500"


def test_contract_only_receipt_shows_both_wage_bases_without_payment_columns():
    extraction = load_sample("review_case")
    extraction["payslip"] = blank_result().payslip.model_dump(mode="json")
    extraction["documents_provided"] = {"contract": True, "payslip": False}
    app = start()
    app.session_state["step"] = 2
    app.session_state["draft"] = ReviewDraft(extraction, sample_name="review_case")
    app.run()
    next(button for button in app.form_submit_button if button.label == "결과 보기").click().run()

    assert not app.exception
    assert [metric.value for metric in app.metric[:2]] == ["1,076,170원", "비교 안 함"]
    assert any("최저임금 기준으로 계산하면 매달 약" in row.proto.body and "1,076,170원" in row.proto.body
               and "headline-amount" not in row.proto.body for row in app.get("html"))
    receipt = next(row.proto.body for row in app.get("html") if 'class="pay-table has-wage-bases"' in row.proto.body)
    assert all(label in receipt for label in ("계약 시급 기준", "최저임금 기준", "1,042,800원", "1,076,170원"))
    assert "받은 금액</th>" not in receipt


def test_invalid_wage_keeps_review_step_and_names_the_field():
    app = start()
    app.button(key="begin-check").click().run()
    app.button(key="consent").click().run()
    app.button(key="sample-normal_case").click().run()
    app.text_input(key="review-contract-hourly_wage").set_value("숫자 아님")
    next(button for button in app.form_submit_button if button.label == "결과 보기").click().run()
    assert not app.exception
    assert not app.metric
    assert any("시급 (원)의 형식을 확인해 주세요." in row.value for row in app.markdown)


def test_review_displays_gross_total_hint_and_submits_legacy_times():
    extraction = load_sample("review_case")
    extraction["contract"]["work_start"]["value"] = "09:00Z"
    extraction["contract"]["work_end"]["value"] = "14:30:00+09:00"
    extraction["payslip"]["total_paid"]["value"] = None
    app = AppTest.from_file(str(APP), default_timeout=20)
    app.session_state["step"] = 2
    app.session_state["draft"] = ReviewDraft(sample_name="review_case", extraction=extraction)
    app.run()

    assert not app.exception
    assert any("명세서의 지급 총액(세전)을 확인해 주세요" in row.value for row in app.warning)
    gross = app.text_input(key="review-payslip-total_paid")
    assert gross.value == "" and "세전" in gross.label
    times = app.dataframe[0].value
    assert times.loc[0, "start"] == "09:00" and times.loc[0, "end"] == "14:30"
    gross.set_value("869000")
    next(button for button in app.form_submit_button if button.label == "결과 보기").click().run()

    assert not app.exception
    assert app.session_state.confirmed.payslip.total_paid.value == 869000
    assert app.session_state.confirmed.contract.work_start.value.tzinfo is None
    assert app.metric[0].value == "1,076,170원"


def test_landing_shows_calculated_example_and_routes_to_consent():
    app = start()
    landing = " ".join(row.proto.body for row in app.get("html"))
    assert "일한 만큼," in landing and "가상 서류로 만든 예시 화면이에요." in landing
    assert "1,076,170원" in landing and "207,170원" in landing
    assert not app.get("file_uploader")

    app.button(key="hero-examples").click().run()
    assert not app.get("file_uploader")
    app.button(key="consent").click().run()
    assert app.button(key="sample-review_case")
    app.button(key="brand-home").click().run()
    assert app.button(key="hero-check") and not app.get("file_uploader")


def test_review_preview_follows_core_values_without_confirming():
    app = start()
    app.button(key="begin-check").click().run()
    app.button(key="consent").click().run()
    app.button(key="sample-review_case").click().run()
    preview = next(row.proto.body for row in app.get("html") if 'class="review-live-row"' in row.proto.body)
    assert "1,042,800원" in preview and "1,076,170원" in preview
    assert "confirmed" not in app.session_state and "result" not in app.session_state

    wage = app.text_input(key="review-contract-hourly_wage")
    wage.set_value("11000").run()
    preview = next(row.proto.body for row in app.get("html") if 'class="review-live-row"' in row.proto.body)
    assert "1,147,080원" in preview and "최저임금 기준" not in preview
    assert "result" not in app.session_state
    app.text_input(key="review-contract-hourly_wage").set_value("값 없음").run()
    assert any("값을 확인하면 계산돼요" in row.proto.body for row in app.get("html"))
    next(button for button in app.form_submit_button if button.label == "이전으로").click().run()
    assert app.file_uploader and "result" not in app.session_state

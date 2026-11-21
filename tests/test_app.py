from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app" / "main.py"
DISCLAIMER = "법률 자문이 아니며 최종 판단은 관계 기관에서 이루어집니다."


def start():
    return AppTest.from_file(str(APP), default_timeout=20).run()


def check_footer(app):
    assert any(DISCLAIMER in row.value for row in app.caption)


@pytest.mark.parametrize(("sample", "monthly_total", "needs_review"), [
    ("normal_case", "1,094,940원", False),
    ("review_case", "1,042,800원", True),
])
def test_sample_requires_confirmation_and_shows_results(sample, monthly_total, needs_review):
    app = start()
    assert not app.exception
    assert not app.get("file_uploader")
    assert not app.metric
    check_footer(app)

    app.button(key="consent").click().run()
    assert len(app.get("file_uploader")) == 2
    assert not any(button.key == f"sample-{sample}" for button in app.button)
    assert app.button(key="show-sample-demo").label == "예시로 체험하기"
    app.button(key="show-sample-demo").click().run()
    app.button(key=f"sample-{sample}").click().run()

    assert not app.exception
    assert not app.metric
    assert any(row.label == "전체 항목 보기" for row in app.expander)
    assert any("신뢰도가 낮아요" in row.label for row in app.text_input) is needs_review
    check_footer(app)

    app.form_submit_button[0].click().run()

    assert not app.exception
    assert app.metric[0].value == monthly_total
    assert any(row.label == "상세 영수증 보기" for row in app.expander)
    assert any(row.value == "사장님께 보낼 문구" for row in app.subheader) is needs_review
    check_footer(app)

    app.button(key="chat-prompt-0").click().run()
    assert not app.exception
    assert any("샘플 답변이에요" in row.value for row in app.markdown)


def test_edited_input_is_calculated_and_restart_clears_previous_result():
    app = start()
    app.button(key="consent").click().run()
    app.button(key="show-sample-demo").click().run()
    app.button(key="sample-normal_case").click().run()
    app.text_input(key="review-contract-hourly_wage").set_value("10000")
    app.selectbox(key="review-workplace-size").set_value("unknown")
    app.form_submit_button[0].click().run()

    assert not app.exception
    assert app.metric[0].value == "1,042,800원"
    assert [row.value for row in app.metric if row.label == "월 받을 금액"] == ["1,042,800원", "1,042,800원"]

    app.button(key="restart").click().run()
    assert not app.exception
    assert not app.metric
    assert not app.get("file_uploader")
    assert "result" not in app.session_state
    check_footer(app)
    app.button(key="consent").click().run()
    app.button(key="show-sample-demo").click().run()
    app.button(key="sample-normal_case").click().run()
    assert app.text_input(key="review-contract-hourly_wage").value == "10500"


def test_invalid_wage_keeps_review_step_and_names_the_field():
    app = start()
    app.button(key="consent").click().run()
    app.button(key="show-sample-demo").click().run()
    app.button(key="sample-normal_case").click().run()
    app.text_input(key="review-contract-hourly_wage").set_value("숫자 아님")
    app.form_submit_button[0].click().run()
    assert not app.exception
    assert not app.metric
    assert any("시급 (원)의 형식을 확인해 주세요." in row.value for row in app.markdown)

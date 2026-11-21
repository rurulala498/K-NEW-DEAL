from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "app" / "main.py"


@pytest.mark.parametrize(("sample", "monthly_total", "needs_review"), [
    ("정상 지급", "1,094,940원", False),
    ("주휴수당 미지급", "1,042,800원", True),
])
def test_sample_requires_confirmation_and_shows_results(sample, monthly_total, needs_review):
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    assert not app.exception
    assert not app.get("file_uploader")
    assert not app.chat_input

    app.checkbox(key="consent").check().run()
    assert len(app.get("file_uploader")) == 2
    app.radio(key="sample-choice").set_value(sample)
    app.button(key="load-sample").click().run()

    assert not app.exception
    assert app.chat_input
    assert not app.metric
    assert any("신뢰도 낮음" in row.value for row in app.warning) is needs_review

    app.form_submit_button[0].click().run()

    assert not app.exception
    assert app.metric[0].value == monthly_total
    assert len(app.dataframe) >= 3
    assert any(row.value == "대응 자료 · 샘플" for row in app.subheader) is needs_review
    assert any("법률 자문이 아니며 최종 판단은 관계 기관에서 이루어집니다." in row.value for row in app.caption)

    app.chat_input[0].set_value("주휴수당이 뭐예요?").run()
    assert not app.exception
    assert any("샘플 답변입니다." in row.value for row in app.markdown)


def test_engine_uses_edited_values_and_sample_reload_clears_results():
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    app.checkbox(key="consent").check().run()
    app.button(key="load-sample").click().run()
    app.text_input(key="sample-1-contract-hourly_wage").set_value("10000")
    app.selectbox(key="sample-1-workplace-size").set_value("unknown")
    app.form_submit_button[0].click().run()

    assert not app.exception
    assert [row.value for row in app.metric] == ["1,042,800원", "1,042,800원"]

    app.button(key="load-sample").click().run()

    assert not app.exception
    assert not app.metric
    assert app.text_input(key="sample-2-contract-hourly_wage").value == "10500"

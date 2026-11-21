from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.steps import upload
from llm import extract as extraction
from llm.client import ApiSettings, ExtractionRequestError, ModelReply
from llm.extract import blank_result

APP = Path(__file__).resolve().parents[1] / "app" / "main.py"
DOCS = APP.parents[1] / "tests" / "fixtures" / "docs"


def ready_app(monkeypatch, settings):
    monkeypatch.setattr(upload, "read_settings", lambda: settings)
    app = AppTest.from_file(str(APP), default_timeout=20).run()
    app.button(key="consent").click().run()
    assert not app.exception
    return app


def add_documents(app):
    for control, role in zip(app.file_uploader, ("contract", "payslip")):
        file = DOCS / f"normal_{role}.pdf"
        control.upload(file.name, file.read_bytes(), "application/pdf")
    app.run()


def test_no_key_message_keeps_precomputed_sample_available(monkeypatch):
    def unexpected_call(_settings):
        raise AssertionError("A precomputed sample requested the API")

    monkeypatch.setattr(extraction, "ExtractionClient", unexpected_call)
    app = ready_app(monkeypatch, ApiSettings("", ""))
    assert any(note.value == "API 키가 설정되지 않았어요" for note in app.info)
    assert app.button(key="read-uploads").disabled
    assert not any(button.key == "read-ai-sample" for button in app.button)

    app.button(key="show-sample-demo").click().run()
    app.button(key="sample-normal_case").click().run()
    assert not app.exception
    assert app.text_input(key="review-contract-hourly_wage").value == "10500"


def test_uploaded_pdfs_flow_to_review_with_source_highlighting(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, _settings):
            pass

        def read(self, prompt, documents, schema):
            calls.append((prompt, schema))
            payload = blank_result().model_dump(mode="json", include={"contract", "payslip"})
            payload["contract"]["hourly_wage"] = {
                "value": 1050, "confidence": "high", "source_text": "시급 10,500원",
            }
            return ModelReply(payload)

    monkeypatch.setattr(extraction, "ExtractionClient", FakeClient)
    app = ready_app(monkeypatch, ApiSettings("fake", "fake-model"))
    assert app.button(key="read-uploads").disabled
    add_documents(app)
    assert not app.button(key="read-uploads").disabled
    assert not calls
    app.button(key="read-uploads").click().run()

    assert not app.exception and len(calls) == 1
    assert "시급 10,500원" in app.session_state.draft.texts.contract
    assert app.text_input(key="review-contract-hourly_wage").value == "1050"
    assert "신뢰도가 낮아요" in app.text_input(key="review-contract-hourly_wage").label
    assert app.selectbox(key="review-workplace-size").value == "unknown"
    assert not app.metric
    assert any('class="sample-mark mark-low">시급 10,500원' in element.proto.body for element in app.get("html"))

    app.text_input(key="review-contract-hourly_wage").set_value("10500")
    app.form_submit_button[0].click().run()
    assert not app.exception
    wage = app.session_state.confirmed.contract.hourly_wage
    assert wage.value == 10500 and wage.source_text is None
    assert "draft" not in app.session_state


def test_request_error_offers_retry_manual_input_and_saved_samples(monkeypatch):
    class FailingClient:
        def __init__(self, _settings):
            pass

        def read(self, _prompt, _documents, _schema):
            raise ExtractionRequestError("AI 응답이 늦어졌어요.")

    monkeypatch.setattr(extraction, "ExtractionClient", FailingClient)
    app = ready_app(monkeypatch, ApiSettings("fake", "fake-model"))
    add_documents(app)
    app.button(key="read-uploads").click().run()

    assert not app.exception
    assert any("AI 응답이 늦어졌어요" in note.value for note in app.warning)
    assert app.button(key="read-uploads").label == "다시 시도"
    app.button(key="show-sample-demo").click().run()
    assert not app.button(key="sample-normal_case").disabled
    app.button(key="manual-entry").click().run()
    assert not app.exception
    assert app.text_input(key="review-contract-hourly_wage").value == ""
    assert "시급 10,500원" in app.session_state.draft.texts.contract


def test_two_format_failures_open_blank_review_with_original_documents(monkeypatch):
    calls = []

    class BrokenClient:
        def __init__(self, _settings):
            pass

        def read(self, _prompt, _documents, _schema):
            calls.append("invalid")
            return ModelReply({})

    monkeypatch.setattr(extraction, "ExtractionClient", BrokenClient)
    app = ready_app(monkeypatch, ApiSettings("fake", "fake-model"))
    add_documents(app)
    app.button(key="read-uploads").click().run()

    assert not app.exception and len(calls) == 2
    assert "시급 10,500원" in app.session_state.draft.texts.contract
    assert app.text_input(key="review-contract-hourly_wage").value == ""
    assert any("AI가 추출 결과의 형식" in note.value for note in app.warning)

def test_ai_sample_controls_are_visible_only_with_dev_tools(monkeypatch):
    app = ready_app(monkeypatch, ApiSettings("fake", "fake-model", show_dev_tools=True))
    assert any(row.label == "샘플 서류를 AI로 직접 읽어 보기" for row in app.expander)
    assert not app.button(key="read-ai-sample").disabled

    app = ready_app(monkeypatch, ApiSettings("fake", "fake-model"))
    assert not any(row.label == "샘플 서류를 AI로 직접 읽어 보기" for row in app.expander)
    assert not any(button.key == "read-ai-sample" for button in app.button)

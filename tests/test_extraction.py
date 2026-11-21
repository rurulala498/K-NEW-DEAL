from decimal import Decimal
from io import BytesIO
import json
import os
from pathlib import Path

from docx import Document
import httpx
from openai import OpenAI
from pypdf import PdfWriter
import pytest

from app.components.documents import highlight_document
from llm import client as api_client
from llm import extract as extractor
from llm.client import ApiSettings, ExtractionClient, ModelReply, TokenUsage, read_settings
from llm.documents import DocumentReadError, DocumentTexts, TEXT_REQUIRED, read_document
from llm.extract import DocumentExtraction, blank_result, extract_documents, extract_sample
from llm.samples import sample_texts
from scripts.eval_extraction import CASES, expected_fields, field_values, read_fixture_pair

SETTINGS = ApiSettings("unit-test-fake-key", "unit-test-extraction-model")
TEXTS = DocumentTexts(
    "시급 10,500원\n주 소정근로시간 20시간\n특약: 중도 퇴사하면 50,000원을 부담한다.",
    "기본급 912,450원\n주휴수당 0원\n총 지급액 912,450원",
)


def field(value, quote=None):
    return {"value": value, "confidence": "high", "source_text": quote}


def response_payload():
    response = blank_result().model_dump(include={"contract", "payslip"}, mode="json")
    response["contract"]["hourly_wage"] = field(10500, "시급 10,500원")
    response["contract"]["weekly_hours"] = field(20, "주 소정근로시간 20시간")
    response["payslip"]["items"] = field([{
        "name": field("주휴수당", "주휴수당 0원"),
        "amount": field(0, "주휴수당 0원"),
        "category": field("weekly_holiday_pay", "주휴수당 0원"),
    }], "주휴수당 0원")
    response["payslip"]["total_paid"] = field(912450, "총 지급액 912,450원")
    return response


class FakeClient:
    def __init__(self, *replies):
        self.replies = iter(replies)
        self.calls = []

    def read(self, prompt, documents, schema):
        self.calls.append((prompt, schema))
        assert set(json.loads(documents)) == {"contract", "payslip"}
        return ModelReply(next(self.replies), TokenUsage(120, 75))


def test_valid_response_preserves_zero_nulls_and_unanswered_questions():
    fake = FakeClient(response_payload())
    output = extract_documents(TEXTS, SETTINGS, client=fake)
    result = output.result

    assert result.contract.hourly_wage.value == Decimal(10500)
    assert result.contract.hourly_wage.source_text == "시급 10,500원"
    assert result.contract.hourly_wage.confidence == "high"
    assert result.contract.holidays.value is None
    assert result.contract.holidays.source_text is None
    assert result.payslip.items.value[0].amount.value == 0
    assert result.payslip.items.value[0].category.value == "weekly_holiday_pay"
    assert result.extra_answers.workplace_size.value == "unknown"
    assert result.extra_answers.pay_period_rule.value is None
    assert result.user_confirmed is False
    assert output.usage == (TokenUsage(120, 75),)
    assert not output.validation_failed


def test_validation_retry_uses_only_one_additional_request():
    fake = FakeClient({"contract": None}, json.dumps(response_payload(), ensure_ascii=False))
    outcome = extract_documents(TEXTS, SETTINGS, client=fake)

    assert len(fake.calls) == 2
    assert "이전 응답의 형식" in fake.calls[1][0]
    assert not outcome.validation_failed


def test_invalid_responses_open_a_blank_unconfirmed_result():
    fake = FakeClient("잘못된 형식", None)
    outcome = extract_documents(TEXTS, SETTINGS, client=fake)

    assert len(fake.calls) == 2
    assert outcome.validation_failed
    assert outcome.result == blank_result()
    assert not outcome.result.user_confirmed


def test_exact_source_and_numeric_checks():
    payload = response_payload()
    payload["contract"]["hourly_wage"] = field(1050, "시급 10,500원")
    payload["contract"]["weekly_hours"] = field(20, "주 근로시간이 20시간이에요")
    payload["contract"]["break_minutes"] = field(30)
    payload["payslip"]["total_paid"] = field(912, "총 지급액 912,450원")
    result = extract_documents(TEXTS, SETTINGS, client=FakeClient(payload)).result

    assert result.contract.hourly_wage.value == Decimal(1050)
    assert result.contract.hourly_wage.confidence == "low"
    assert result.contract.weekly_hours.confidence == "low"
    assert result.contract.break_minutes.value is None
    assert result.payslip.total_paid.confidence == "low"


def test_special_terms_keep_original_and_ambiguous_category_is_unclassified():
    payload = response_payload()
    exact_term = "중도 퇴사하면 50,000원을 부담한다."
    payload["contract"]["special_terms"] = field([{
        "text": field("중도 퇴사 부담", exact_term),
        "type": field("penalty", exact_term),
    }], exact_term)
    payload["payslip"]["items"] = field([{
        "name": field("기타 조정", "기타 조정 9,000원"),
        "amount": field(9000, "기타 조정 9,000원"),
        "category": field("base_pay", "기타 조정 9,000원"),
    }], "기타 조정 9,000원")
    documents = DocumentTexts(TEXTS.contract, TEXTS.payslip + "\n기타 조정 9,000원")
    result = extract_documents(documents, SETTINGS, client=FakeClient(payload)).result

    assert result.contract.special_terms.value[0].text.value == exact_term
    assert result.contract.special_terms.value[0].type.value == "penalty"
    assert result.payslip.items.value[0].category.value == "unclassified"


def test_only_known_sample_documents_use_the_cache(monkeypatch):
    monkeypatch.setattr(extractor, "_sample_cache", extractor.OrderedDict())
    fake = FakeClient(response_payload(), response_payload(), response_payload(), response_payload())
    first = extract_sample("normal_case", SETTINGS, client=fake)
    second = extract_sample("normal_case", SETTINGS, client=fake)
    assert len(fake.calls) == 1
    assert first.result == second.result
    assert second.usage == ()
    assert first.result is not second.result

    extract_sample("normal_case", ApiSettings("fake", "different-model"), client=fake)
    extract_documents(TEXTS, SETTINGS, client=fake)
    extract_documents(TEXTS, SETTINGS, client=fake)
    assert len(fake.calls) == 4


def test_highlight_uses_exact_quotes_and_escapes_original_markup():
    content = '<script>alert(1)</script> 시급 10,500원'
    html = highlight_document(content, {
        "wage": field(10500, "시급 10,500원"),
        "amount": {"value": 1050, "confidence": "low", "source_text": "10,500원"},
        "absent": field(40, "40시간"),
    })
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert '<mark class="sample-mark mark-low">10,500원</mark>' in html
    assert "40시간" not in html


def test_loading_env_from_file_never_requires_existing_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=local-fake\nOPENAI_MODEL=local-model\n", encoding="utf-8")
    settings = read_settings(env_file)
    assert settings.model == "local-model"
    assert bool(settings.api_key)
    assert "local-fake" not in repr(settings)


def test_sdk_uses_responses_structured_outputs_without_remote_storage(monkeypatch):
    content = json.dumps(response_payload(), ensure_ascii=False)

    def fake_response(request):
        body = json.loads(request.content)
        assert request.url.path == "/v1/responses"
        assert body["model"] == SETTINGS.model
        assert body["store"] is False
        schema = body["text"]["format"]["schema"]
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["strict"] is True
        assert set(schema["properties"]) == {"contract", "payslip"}
        assert schema["additionalProperties"] is False
        return httpx.Response(200, json={
            "id": "resp_fake", "object": "response", "created_at": 0, "status": "completed",
            "model": SETTINGS.model, "output": [{
                "id": "msg_fake", "type": "message", "status": "completed", "role": "assistant",
                "content": [{"type": "output_text", "text": content, "annotations": []}],
            }],
            "usage": {
                "input_tokens": 120, "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 75, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 195,
            },
        })

    monkeypatch.setattr(api_client, "OpenAI", lambda **kwargs: OpenAI(
        **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(fake_response)),
    ))
    result = extract_documents(TEXTS, SETTINGS, client=ExtractionClient(SETTINGS))
    assert result.result.contract.hourly_wage.value == 10500
    assert result.usage == (TokenUsage(120, 75),)


def test_docx_table_and_txt_are_read_in_memory():
    document = Document()
    document.add_paragraph("가상 근로계약서의 시급은 아래 표에 적혀 있어요.")
    row = document.add_table(rows=1, cols=2).rows[0]
    row.cells[0].text = "시급"
    row.cells[1].text = "10,500원"
    memory = BytesIO()
    document.save(memory)
    assert "시급 | 10,500원" in read_document("fixture.DOCX", memory.getvalue())
    line = "가상 계약서에 들어 있는 시급 10,500원입니다."
    assert read_document("fixture.txt", line.encode("cp949")) == line


def test_image_and_textless_pdf_get_same_guidance():
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=400, height=400)
    writer.write(buffer)
    for filename, contents in (("fixture.png", bytes(10)), ("blank.pdf", buffer.getvalue())):
        with pytest.raises(DocumentReadError, match=TEXT_REQUIRED):
            read_document(filename, contents)


@pytest.mark.parametrize("case", CASES)
def test_fixture_pdfs_are_readable_and_match_expected_text(case):
    texts = read_fixture_pair(case)
    expected = expected_fields(case)
    assert "근로계약서" in texts.contract and "급여명세서" in texts.payslip
    assert f"{expected.contract.hourly_wage.value:,}원" in texts.contract
    if case in ("review", "tricky"):
        assert "주휴수당 0원" in texts.payslip
    if case == "tricky":
        assert "위약금" in texts.contract
        assert "업무 내용" not in texts.contract and expected.contract.job_description.value is None


@pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="RUN_LIVE=1 환경에서만 가상 문서를 실제 API로 읽어요")
@pytest.mark.parametrize("case", CASES)
def test_live_extraction_on_synthetic_documents(case):
    settings = read_settings()
    if not settings.api_key or not settings.model:
        pytest.skip("실제 API 테스트에는 .env 키와 모델 설정이 필요해요")
    outcome = extract_documents(read_fixture_pair(case), settings)
    assert not outcome.validation_failed
    assert outcome.result.contract.hourly_wage.value is not None
    assert outcome.usage


@pytest.mark.parametrize("valid_reply", [True, False])
def test_contract_only_sends_no_payslip_and_discards_unsolicited_payslip_values(valid_reply):
    class ContractClient:
        def __init__(self):
            self.calls = 0

        def read(self, prompt, documents, schema):
            self.calls += 1
            assert "급여명세서 없음" in prompt
            assert json.loads(documents) == {"contract": TEXTS.contract}
            return ModelReply(response_payload() if valid_reply else {})

    client = ContractClient()
    outcome = extract_documents(DocumentTexts(TEXTS.contract), SETTINGS, client=client)

    assert client.calls == (1 if valid_reply else 2)
    assert outcome.validation_failed is not valid_reply
    assert outcome.result.documents_provided.model_dump() == {"contract": True, "payslip": False}
    assert outcome.result.contract.hourly_wage.value == (10500 if valid_reply else None)
    assert all(field["value"] is None and field["source_text"] is None and field["confidence"] == "low"
               for field in outcome.result.payslip.model_dump().values())


@pytest.mark.parametrize("clock", ["19:00Z", "19:00+09:00", "19:00:00"])
def test_extraction_and_evaluation_normalize_wall_clocks(clock):
    payload = response_payload()
    contract = payload["contract"]
    contract["work_start"] = field(clock, "19:00")
    contract["work_end"] = field("00:30-05:00", "00:30")
    contract["work_schedule"] = field([{
        "days": field(["금"], "금"), "start": field(clock, "19:00"),
        "end": field("00:30-05:00", "00:30"), "break_start": field("22:15:00Z", "22:15"),
        "break_end": field("22:45+09:00", "22:45"), "break_minutes": field(None),
    }], "금 19:00~00:30 휴게 22:15~22:45")
    documents = DocumentTexts("금 19:00~00:30 휴게 22:15~22:45", TEXTS.payslip)
    fake = FakeClient(payload)
    result = extract_documents(documents, SETTINGS, client=fake).result
    assert result.contract.work_start.value.tzinfo is None
    json_contract = result.model_dump(mode="json")["contract"]
    assert json_contract["work_start"]["value"] == "19:00"
    assert json_contract["work_end"]["value"] == "00:30"
    row = json_contract["work_schedule"]["value"][0]
    assert row["break_start"]["value"] == "22:15"
    assert row["break_end"]["value"] == "22:45"
    assert row["start"]["confidence"] == "high"
    assert dict(field_values(payload))["contract.work_start"] == "19:00"
    schema = fake.calls[0][1].model_json_schema()
    nodes = schema["$defs"][schema["$defs"]["Contract"]["properties"]["work_start"]["$ref"].split("/")[-1]]
    assert nodes["properties"]["value"]["anyOf"][0]["type"] == "string"
    assert "pattern" in nodes["properties"]["value"]["anyOf"][0]
    assert "시간대 없이 HH:MM" in fake.calls[0][0]


@pytest.mark.parametrize("includes_gross_total", [True, False])
def test_payslip_keeps_only_gross_total_and_pay_details(includes_gross_total):
    payload = response_payload()
    net_label = "실수령액"
    net_amount = 900000
    net = f"{net_label} {net_amount:,}원"
    gross = "공제 전 지급 총액 912,450원"
    rows = (
        ("기본급", 912450, "base_pay", "기본급 912,450원"),
        ("소득세", 8000, "base_pay", "소득세 8,000원"),
        ("국민연금", 4450, "unclassified", "국민연금 4,450원"),
        ("공제 합계", 12450, "total", "공제 합계 12,450원"),
        ("실수령액", net_amount, "total", net),
        ("합계", net_amount, "total", net),
    )
    if includes_gross_total:
        rows += (("지급 합계", 912450, "total", gross),)
    payload["payslip"]["items"] = field([
        {"name": field(name, quote), "amount": field(amount, quote), "category": field(category, quote)}
        for name, amount, category, quote in rows
    ])
    payload["payslip"]["total_paid"] = field(net_amount, net)
    texts = DocumentTexts(TEXTS.contract, "\n".join(quote for _, _, _, quote in rows))
    result = extract_documents(texts, SETTINGS, client=FakeClient(payload)).result.payslip
    assert [row.name.value for row in result.items.value] == ["기본급"]
    assert result.total_paid.value == (912450 if includes_gross_total else None)
    assert result.total_paid.source_text == (gross if includes_gross_total else None)


@pytest.mark.parametrize("quote", [
    "총 지급액 912,450원 (공제 후)", "실수령액 (총 지급액) 912,450원", "세전 기본급 912,450원",
])
def test_payslip_requires_evidence_of_a_gross_total(quote):
    payload = response_payload()
    payload["payslip"]["total_paid"] = field(912450, quote)
    result = extract_documents(
        DocumentTexts(TEXTS.contract, quote), SETTINGS, client=FakeClient(payload),
    ).result.payslip
    assert result.total_paid.value is None and result.total_paid.confidence == "low"

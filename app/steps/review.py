from html import escape

import streamlit as st
from pydantic import ValidationError

from app.components.documents import document_preview
from app.components.fields import confidence_label, editable_rows, edited_field, low_rows, text_field, text_fields
from app.components.schedule import edit_schedule
from app.format import WORKPLACE_LABELS
from engine.comparison import PAYSLIP_CATEGORIES
from llm.documents import DocumentTexts
from llm.extract import workplace_size_hint
from schemas import ExtractionResult
from schemas.extraction import Contract, DocumentsProvided

CORE_FIELDS = {
    "hourly_wage": "시급 (원)", "weekly_hours": "주 소정근로시간",
    "pay_day": "지급일",
}
OTHER_FIELDS = {
    "daily_hours": "하루 소정근로시간",
    "contract_start": "계약 시작일 (YYYY-MM-DD)", "contract_end": "계약 종료일 (YYYY-MM-DD)",
    "pay_method": "지급 방법", "holidays": "휴일", "annual_leave": "연차유급휴가",
    "workplace": "근무 장소", "job_description": "업무 내용",
}
PAYSLIP_FIELDS = {
    "pay_period_start": "정산 시작일 (YYYY-MM-DD)", "pay_period_end": "정산 종료일 (YYYY-MM-DD)",
    "total_paid": "총 지급액 (원)",
}
ITEM_FIELDS = {"name": "항목명", "amount": "금액 (원)", "category": "분류"}
TERM_FIELDS = {"text": "특약 내용", "type": "특약 유형"}
ITEM_CHOICES = {"category": {**PAYSLIP_CATEGORIES, "unclassified": "분류 불가"}}
TERM_CHOICES = {"type": {"penalty": "위약금 예정", "wage_deduction": "임금 공제", "other": "기타"}}
LABELS = {**CORE_FIELDS, **OTHER_FIELDS, **PAYSLIP_FIELDS, **ITEM_FIELDS, **TERM_FIELDS,
          "pay_period_rule": "임금 정산 기간", "workplace_size": "사업장 규모"}


def _priority_labels(source, labels):
    return {name: label for name, label in labels.items() if source[name]["confidence"] == "low" and source[name]["value"] is not None}


def _has_low_rows(field):
    return any(cell["confidence"] == "low" for row in field["value"] or [] for cell in row.values())


def _core_inputs(contract):
    values = {}
    for name, column in zip(("hourly_wage", "weekly_hours"), st.columns(2)):
        with column:
            values[name] = text_field(contract[name], CORE_FIELDS[name], f"review-contract-{name}")
    values["pay_day"] = text_field(contract["pay_day"], CORE_FIELDS["pay_day"], "review-contract-pay_day")
    return values


def _additional_fields(contract, payslip):
    contract_priority = _priority_labels(contract, OTHER_FIELDS)
    payslip_priority = _priority_labels(payslip, PAYSLIP_FIELDS) if payslip is not None else {}
    terms = contract["special_terms"]
    items = payslip["items"] if payslip is not None else None
    term_fixes, item_fixes = {}, {}
    contract_result, payslip_result = {}, {}
    if contract_priority or payslip_priority or _has_low_rows(terms) or (items and _has_low_rows(items)):
        st.markdown("**신뢰도가 낮은 값도 먼저 확인해요**")
        contract_result.update(text_fields(contract, contract_priority, "review-contract"))
        term_fixes = low_rows(terms, TERM_FIELDS, "특약", TERM_CHOICES)
        if payslip is not None:
            payslip_result.update(text_fields(payslip, payslip_priority, "review-payslip"))
            item_fixes = low_rows(items, ITEM_FIELDS, "급여 항목", ITEM_CHOICES)

    with st.expander("전체 항목 보기"):
        st.markdown("**계약서 세부 항목**")
        contract_result.update(text_fields(
            contract, {name: label for name, label in OTHER_FIELDS.items() if name not in contract_priority},
            "review-contract",
        ))
        st.markdown("**특약**")
        contract_result["special_terms"] = editable_rows(
            terms, TERM_FIELDS, "review-terms", choices=TERM_CHOICES, corrected=term_fixes,
        )
        if payslip is not None:
            st.markdown("**명세서 세부 항목**")
            payslip_result.update(text_fields(
                payslip, {name: label for name, label in PAYSLIP_FIELDS.items() if name not in payslip_priority},
                "review-payslip",
            ))
            st.markdown("**급여 항목**")
            payslip_result["items"] = editable_rows(
                items, ITEM_FIELDS, "review-items", choices=ITEM_CHOICES, corrected=item_fixes,
            )
    return contract_result, payslip_result


def _answers(extra):
    st.markdown("**추가 질문**")
    size_field = extra["workplace_size"]
    sizes = list(WORKPLACE_LABELS)
    size = st.selectbox(
        confidence_label("사업장 규모", size_field), sizes,
        index=sizes.index(size_field["value"] or "unknown"),
        format_func=WORKPLACE_LABELS.__getitem__, key="review-workplace-size",
    )
    if hint := workplace_size_hint(size_field.get("source_text") or ""):
        st.caption(f"계약서에 상시근로자 {hint[0]:,}명으로 적혀 있어요.")
    return {
        "workplace_size": edited_field(size_field, size),
        "pay_period_rule": text_field(extra["pay_period_rule"], "임금 정산 기간", "review-pay-period"),
    }


def _invalid_input(error: ValidationError):
    labels = []
    for issue in error.errors():
        path = issue["loc"]
        field = next((part for part in reversed(path) if part in LABELS), None)
        display = LABELS.get(field, "입력값")
        if "items" in path or "special_terms" in path:
            row = next((part + 1 for part in path if isinstance(part, int)), None)
            if row:
                display = f"{row}행 {display}"
        if display not in labels:
            labels.append(display)
    st.markdown(
        f'<p class="input-error" role="alert">{escape(", ".join(labels))}의 형식을 확인해 주세요.</p>',
        unsafe_allow_html=True,
    )


def review_step(sample_name: str | None, sample: dict, *, texts: DocumentTexts | None = None) -> ExtractionResult | None:
    provided = DocumentsProvided.model_validate(sample.get("documents_provided", {}))
    st.title("서류 내용을 확인해요")
    st.caption("원본과 추출값을 비교하고, 다른 값은 직접 고쳐 주세요.")
    if not provided.payslip:
        st.caption("급여명세서 없이 진행 중")
    with st.container(key="review-split"):
        document, editor = st.columns([5, 7], gap="large", vertical_alignment="top")
        with document:
            with st.container(border=True, key="documents-card"):
                document_preview(sample_name, sample, texts, payslip_provided=provided.payslip)
        with editor:
            st.subheader("추출값 확인")
            contract = _core_inputs(sample["contract"])
            schedule, valid_schedule = edit_schedule(
                Contract.model_validate(sample["contract"]), contract["weekly_hours"]["value"],
            )
            with st.form("review-form", border=True):
                remaining, payslip = _additional_fields(
                    sample["contract"], sample["payslip"] if provided.payslip else None,
                )
                extra_answers = _answers(sample["extra_answers"])
                submitted = st.form_submit_button("결과 보기", type="primary", width="stretch", disabled=not valid_schedule)
                if submitted:
                    try:
                        return ExtractionResult.model_validate({
                            "contract": {**schedule.model_dump(mode="json"), **contract, **remaining},
                            "payslip": payslip if provided.payslip else sample["payslip"],
                            "extra_answers": extra_answers, "user_confirmed": True,
                            "documents_provided": provided,
                        })
                    except ValidationError as error:
                        _invalid_input(error)
    return None

from html import escape

import streamlit as st
from pydantic import ValidationError

from app.components.documents import document_preview
from app.components.fields import confidence_label, editable_rows, edited_field, low_rows, text_field, text_fields
from app.components.schedule import edit_schedule
from app.format import WORKPLACE_LABELS, amount_range, monthly_total, pay_by_workplace
from engine import run_engine
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
    "total_paid": "지급 총액(세전) (원)",
}
ITEM_FIELDS = {"name": "항목명", "amount": "금액 (원)", "category": "분류"}
TERM_FIELDS = {"text": "특약 내용", "type": "특약 유형"}
ITEM_CHOICES = {"category": {
    **{key: label for key, label in PAYSLIP_CATEGORIES.items() if key != "total"},
    "unclassified": "분류 불가",
}}
TERM_CHOICES = {"type": {"penalty": "위약금 예정", "wage_deduction": "임금 공제", "other": "기타"}}
LABELS = {**CORE_FIELDS, **OTHER_FIELDS, **PAYSLIP_FIELDS, **ITEM_FIELDS, **TERM_FIELDS,
          "pay_period_rule": "임금 정산 기간", "workplace_size": "사업장 규모"}


def _priority_labels(source, labels):
    return {name: label for name, label in labels.items() if source[name]["confidence"] == "low" and source[name]["value"] is not None}


def _has_low_rows(field):
    return any(cell["confidence"] == "low" for row in field["value"] or [] for cell in row.values())


def _core_inputs(contract, extra):
    values = {}
    for name, column in zip(("hourly_wage", "weekly_hours"), st.columns(2, gap="small")):
        with column:
            values[name] = text_field(contract[name], CORE_FIELDS[name], f"review-contract-{name}")
    day, period = st.columns(2, gap="small")
    with day:
        values["pay_day"] = text_field(contract["pay_day"], CORE_FIELDS["pay_day"], "review-contract-pay_day")
    with period:
        pay_period = text_field(extra["pay_period_rule"], "임금 정산 기간", "review-pay-period")
    return values, pay_period


def _additional_fields(contract, payslip):
    count = len(OTHER_FIELDS) + 1
    if payslip is not None:
        count += len(PAYSLIP_FIELDS) + 1
    with st.expander(f"나머지 항목 {count}개 보기"):
        return _detail_inputs(contract, payslip)


def _detail_inputs(contract, payslip):
    contract_priority = _priority_labels(contract, OTHER_FIELDS)
    payslip_labels = {name: label for name, label in PAYSLIP_FIELDS.items() if name != "total_paid"}
    payslip_priority = _priority_labels(payslip, payslip_labels) if payslip is not None else {}
    terms = contract["special_terms"]
    items = payslip["items"] if payslip is not None else None
    term_fixes, item_fixes = {}, {}
    contract_result, payslip_result = {}, {}
    if payslip is not None:
        payslip_result["total_paid"] = text_field(
            payslip["total_paid"], PAYSLIP_FIELDS["total_paid"], "review-payslip-total_paid",
        )
    if contract_priority or payslip_priority or _has_low_rows(terms) or (items and _has_low_rows(items)):
        st.markdown("**신뢰도가 낮은 값도 먼저 확인해요**")
        contract_result.update(text_fields(contract, contract_priority, "review-contract"))
        term_fixes = low_rows(terms, TERM_FIELDS, "특약", TERM_CHOICES)
        if payslip is not None:
            payslip_result.update(text_fields(payslip, payslip_priority, "review-payslip"))
            item_fixes = low_rows(items, ITEM_FIELDS, "급여 항목", ITEM_CHOICES)

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
            payslip, {name: label for name, label in payslip_labels.items() if name not in payslip_priority},
            "review-payslip",
        ))
        st.markdown("**급여 항목**")
        payslip_result["items"] = editable_rows(
            items, ITEM_FIELDS, "review-items", choices=ITEM_CHOICES, corrected=item_fixes,
        )
    return contract_result, payslip_result


def _workplace_size(extra):
    size_field = extra["workplace_size"]
    sizes = list(WORKPLACE_LABELS)
    size = st.selectbox(
        confidence_label("사업장 규모", size_field), sizes,
        index=sizes.index(size_field["value"] or "unknown"),
        format_func=WORKPLACE_LABELS.__getitem__, key="review-workplace-size",
    )
    if hint := workplace_size_hint(size_field.get("source_text") or ""):
        st.caption(f"계약서에 상시근로자 {hint[0]:,}명으로 적혀 있어요.")
    st.caption("사업장 규모에 따라 가산수당 적용이 달라질 수 있어요. 모르면 두 경우를 모두 보여 드려요.")
    return edited_field(size_field, size)


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


def _preview_markup(sample: dict, schedule: Contract, core: dict, *, valid_schedule: bool) -> str:
    amounts = []
    if valid_schedule:
        try:
            temporary = ExtractionResult.model_validate({
                **sample,
                "contract": {
                    **schedule.model_dump(mode="json"),
                    "hourly_wage": core["hourly_wage"],
                    "weekly_hours": core["weekly_hours"],
                },
                "user_confirmed": True,
            })
            groups = pay_by_workplace(run_engine(temporary).expected_pay)
        except (ValidationError, ArithmeticError):
            groups = []
        contract = [total for group in groups if (total := monthly_total(group.contract)) is not None]
        minimum = [total for group in groups if group.minimum_wage and (total := monthly_total(group.minimum_wage)) is not None]
        for label, totals in (("계약 기준", contract), ("최저임금 기준", minimum)):
            if totals:
                amounts.append(f'<span>{label} 월 <strong class="amount">{escape(amount_range(totals))}</strong></span>')
    content = "".join(amounts) or "<span>값을 확인하면 계산돼요</span>"
    return (
        f'<div class="review-live-row" aria-live="polite"><b>지금 값으로 계산하면</b>{content}'
        '<small>시급·주 소정근로시간·근무표를 고치면 바로 다시 계산돼요.</small></div>'
    )


def review_step(sample_name: str | None, sample: dict, *, texts: DocumentTexts | None = None) -> ExtractionResult | None:
    provided = DocumentsProvided.model_validate(sample.get("documents_provided", {}))
    with st.container(key="review-live"):
        preview = st.empty()
    st.title("AI가 읽은 내용을 확인해 주세요")
    st.caption("확인한 값으로만 계산해요. 주황색은 원문 근거가 불확실한 값이에요.")
    if not provided.payslip:
        st.caption("급여명세서 없이 진행 중")
    with st.container(key="review-split"):
        document, editor = st.columns(2, gap="medium")
        with document, st.container(border=True, key="documents-card"):
            document_preview(sample_name, sample, texts, payslip_provided=provided.payslip)
        with editor, st.container(border=True, key="review-editor"):
            st.subheader("핵심 값")
            core, pay_period = _core_inputs(sample["contract"], sample["extra_answers"])
            schedule, valid_schedule = edit_schedule(
                Contract.model_validate(sample["contract"]), core["weekly_hours"]["value"],
            )
            workplace_size = _workplace_size(sample["extra_answers"])

    preview.html(_preview_markup(sample, schedule, core, valid_schedule=valid_schedule))
    with st.form("review-form", border=False):
        if provided.payslip and sample["payslip"]["total_paid"]["value"] is None:
            st.warning("명세서의 지급 총액(세전)을 확인해 주세요")
        remaining, payslip = _additional_fields(sample["contract"], sample["payslip"] if provided.payslip else None)
        with st.container(border=True, key="review-actions"):
            back, note, next_step = st.columns([1, 4, 1], gap="small", vertical_alignment="center")
            back_clicked = back.form_submit_button("이전으로", width="stretch")
            note.caption("모든 값을 확인했다면 결과를 볼 수 있어요")
            submitted = next_step.form_submit_button(
                "결과 보기", type="primary", width="stretch", disabled=not valid_schedule,
            )
    if back_clicked:
        st.session_state.pop("draft", None)
        st.session_state.update(step=1, scroll_on_show=True)
        st.rerun()
    if submitted:
        try:
            return ExtractionResult.model_validate({
                "contract": {**schedule.model_dump(mode="json"), **core, **remaining},
                "payslip": payslip if provided.payslip else sample["payslip"],
                "extra_answers": {"workplace_size": workplace_size, "pay_period_rule": pay_period},
                "user_confirmed": True,
                "documents_provided": provided,
            })
        except ValidationError as error:
            _invalid_input(error)
    return None

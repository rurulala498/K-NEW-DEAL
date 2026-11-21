from collections.abc import Mapping

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from engine.comparison import PAYSLIP_CATEGORIES
from schemas import ExtractionResult

WORKPLACE_LABELS = {
    "5_or_more": "5인 이상",
    "under_5": "5인 미만",
    "unknown": "모름",
}
CONTRACT_LABELS = {
    "hourly_wage": "시급 (원)",
    "daily_hours": "하루 소정근로시간",
    "weekly_hours": "주 소정근로시간",
    "work_days": "근무 요일 (쉼표로 구분)",
    "work_start": "근무 시작 (HH:MM)",
    "work_end": "근무 종료 (HH:MM)",
    "break_minutes": "휴게시간 (분)",
    "contract_start": "계약 시작일 (YYYY-MM-DD)",
    "contract_end": "계약 종료일 (YYYY-MM-DD)",
    "pay_day": "지급일",
    "pay_method": "지급 방법",
    "holidays": "휴일",
    "annual_leave": "연차유급휴가",
    "workplace": "근무 장소",
    "job_description": "업무 내용",
}
PAYSLIP_LABELS = {
    "pay_period_start": "정산 시작일 (YYYY-MM-DD)",
    "pay_period_end": "정산 종료일 (YYYY-MM-DD)",
    "total_paid": "총 지급액 (원)",
}
ITEM_LABELS = {"name": "항목명", "amount": "금액 (원)", "category": "분류"}
ITEM_CATEGORIES = {**PAYSLIP_CATEGORIES, "unclassified": "분류 불가"}
TERM_TYPES = {"penalty": "위약금 예정", "wage_deduction": "임금 공제", "other": "기타"}
FIELD_LABELS = {
    **CONTRACT_LABELS, **PAYSLIP_LABELS, **ITEM_LABELS,
    "pay_period_rule": "임금 정산 기간", "text": "특약 내용", "type": "특약 유형",
}


def _confidence_label(label, field):
    return f"🟠 {label} · 신뢰도 낮음" if field["confidence"] == "low" else label


def _edited_field(original, value):
    if str(value) == str(original["value"]):
        confidence = original["confidence"]
    else:
        confidence = "low" if value is None else "high"
    return {"value": value, "confidence": confidence}


def _text_field(field, label, key, *, is_work_days=False):
    preset = field["value"]
    text = ", ".join(preset or []) if is_work_days else "" if preset is None else str(preset)
    entered = st.text_input(_confidence_label(label, field), value=text, key=key).strip()
    value = [day.strip() for day in entered.split(",") if day.strip()] if is_work_days else entered
    return _edited_field(field, value or None)


def _text_fields(source, labels, namespace):
    columns = st.columns(2)
    edited = {}
    for index, (name, label) in enumerate(labels.items()):
        with columns[index % len(columns)]:
            edited[name] = _text_field(
                source[name], label, f"{namespace}-{name}", is_work_days=name == "work_days",
            )
    return edited


def _editable_rows(field, labels, key, *, choices: Mapping[str, dict[str, str]]):
    original = field["value"] or []
    blank = {"value": None, "confidence": "low"}
    review_labels = [
        f"{index}행 {labels[name]}"
        for index, row in enumerate(original, start=1)
        for name, cell in row.items()
        if cell["confidence"] == "low"
    ]
    if field["confidence"] == "low" or review_labels:
        st.warning(f"신뢰도 낮음 · {', '.join(review_labels) or '목록 전체'}")

    def display(name, value):
        return choices.get(name, {}).get(value, value)

    frame = pd.DataFrame(
        [{name: display(name, row[name]["value"]) for name in labels} for row in original],
        columns=list(labels), dtype="string",
    )
    columns = {
        name: st.column_config.SelectboxColumn(label, options=list(choices[name].values()))
        if name in choices else st.column_config.TextColumn(label)
        for name, label in labels.items()
    }
    edited = st.data_editor(
        frame, num_rows="dynamic", column_config=columns, hide_index=True, key=key,
    )
    codes = {name: {label: code for code, label in options.items()} for name, options in choices.items()}
    records = []
    for index, row in edited.iterrows():
        values = {name: None if pd.isna(cell) else str(cell).strip() or None for name, cell in row.items()}
        if not any(value is not None for value in values.values()):
            continue
        source = original[index] if 0 <= index < len(original) else {}
        records.append({
            name: _edited_field(source.get(name, blank), codes.get(name, {}).get(value, value))
            for name, value in values.items()
        })
    return _edited_field(field, None if field["value"] is None and not records else records)


def review_form(sample: dict, revision: int) -> ExtractionResult | None:
    st.header("추출값 확인")
    st.caption("샘플 추출값을 검토하고 빈 값이나 신뢰도가 낮은 항목을 필요에 따라 수정하세요.")
    prefix = f"sample-{revision}"
    with st.form(f"{prefix}-review"):
        st.subheader("근로계약서")
        contract = _text_fields(sample["contract"], CONTRACT_LABELS, f"{prefix}-contract")
        st.markdown("**특약**")
        contract["special_terms"] = _editable_rows(
            sample["contract"]["special_terms"], {"text": "특약 내용", "type": "특약 유형"},
            f"{prefix}-terms", choices={"type": TERM_TYPES},
        )

        st.subheader("급여명세서")
        payslip = _text_fields(sample["payslip"], PAYSLIP_LABELS, f"{prefix}-payslip")
        st.markdown("**급여 항목**")
        payslip["items"] = _editable_rows(
            sample["payslip"]["items"], ITEM_LABELS, f"{prefix}-items",
            choices={"category": ITEM_CATEGORIES},
        )

        st.subheader("추가 질문")
        extra = sample["extra_answers"]
        size_field = extra["workplace_size"]
        sizes = list(WORKPLACE_LABELS)
        size = st.selectbox(
            _confidence_label("사업장 규모", size_field), sizes,
            index=sizes.index(size_field["value"] or "unknown"),
            format_func=WORKPLACE_LABELS.__getitem__, key=f"{prefix}-workplace-size",
        )
        rule = _text_field(extra["pay_period_rule"], "임금 정산 기간", f"{prefix}-pay-period")
        submitted = st.form_submit_button("확인 완료", type="primary")

    if not submitted:
        return None
    st.session_state.pop("result", None)
    try:
        return ExtractionResult.model_validate({
            "contract": contract,
            "payslip": payslip,
            "extra_answers": {
                "workplace_size": _edited_field(size_field, size),
                "pay_period_rule": rule,
            },
            "user_confirmed": True,
        })
    except ValidationError as error:
        labels = dict.fromkeys(
            FIELD_LABELS.get(str(issue["loc"][-2]), str(issue["loc"][-2]))
            for issue in error.errors()
        )
        st.error(f"형식을 확인해 주세요. {', '.join(labels)}")
        return None

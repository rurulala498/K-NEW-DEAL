from decimal import Decimal

import streamlit as st

from app.review import WORKPLACE_LABELS
from schemas import EngineResult

STATUS_LABELS = {"ok": "적절", "needs_review": "확인 필요"}


def _won(amount: Decimal | None) -> str:
    return "—" if amount is None else f"{amount.normalize():,f}원"


def _section_table(rows, empty_message):
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.caption(empty_message)


def show_results(result: EngineResult):
    st.header("급여 점검 결과")
    st.subheader("계약서 진단표")
    _section_table([
        {
            "항목": row.item,
            "상태": STATUS_LABELS[row.status],
            "설명": row.description,
            "근거 키": ", ".join(row.evidence_ids),
        }
        for row in result.checks
    ], "표시할 진단 항목이 없습니다.")

    st.subheader("예상 급여 명세")
    st.caption("세전 금액입니다. 주휴수당이 적용된 경우 소정근로일 개근을 가정합니다.")
    for scenario in result.expected_pay:
        with st.container(border=True):
            st.markdown(f"**사업장 규모 · {WORKPLACE_LABELS[scenario.workplace_size]}**")
            monthly_total = next(
                (row.amount for row in scenario.items if row.period == "monthly" and row.item == "합계"),
                None,
            )
            if monthly_total is not None:
                st.metric("월 예상 급여", _won(monthly_total))
            _section_table([
                {
                    "기간": "주" if row.period == "weekly" else "월",
                    "항목": row.item,
                    "금액": _won(row.amount),
                    "계산식": row.formula,
                    "적용 조건": row.applied_condition,
                    "기준 연도": str(row.standards_year),
                }
                for row in scenario.items
            ], "계산할 수 있는 급여 항목이 없습니다.")

    st.subheader("지급액 대조표")
    st.caption("차액은 월 예상 지급액에서 실제 지급액을 뺀 값입니다. —는 확인할 수 없는 금액입니다.")
    _section_table([
        {
            "사업장 규모": WORKPLACE_LABELS.get(row.workplace_size, "—"),
            "항목": row.item,
            "예상": _won(row.expected),
            "실제": _won(row.actual),
            "차액": _won(row.difference),
            "상태": STATUS_LABELS[row.status],
        }
        for row in result.comparison
    ], "대조할 수 있는 지급 항목이 없습니다.")

    if result.limitations:
        with st.expander("계산 한계와 확인할 기준"):
            for row in result.limitations:
                st.text(f"{row.item} · {row.reason}")

    st.subheader("용어 설명 · 샘플")
    st.markdown("**기본급**은 소정근로시간과 시급을 곱한 금액입니다.")
    st.markdown("**주휴수당**은 적용 조건에 맞을 때 유급으로 쉬는 날에 대한 지급 항목입니다.")

    needs_review = any(row.status == "needs_review" for row in result.checks + result.comparison)
    if needs_review:
        st.subheader("대응 자료 · 샘플")
        st.markdown("**사업주 확인 질문**")
        st.write("안녕하세요. 계약서와 급여명세서에서 확인이 필요한 항목의 산정 근거를 알려주실 수 있을까요?")
        st.markdown("**수정 요청 문구**")
        st.write("함께 확인한 내용과 차이가 있다면 계약서나 명세서에 반영해 주실 수 있을까요?")
    else:
        st.success("계약서 진단과 지급액 대조에서 추가로 확인할 항목이 없습니다.")

import streamlit as st

from app.components.help_panel import help_panel
from app.components.report import (
    pay_comparison, required_term_grid, review_cards, scenario_comparison, summary_band,
)
from app.format import PREMIUMS, WORKPLACE_LABELS, conversational, review_items
from schemas import EngineResult, ExtractionResult


def _comparison_section(result: EngineResult):
    st.subheader("받을 금액과 받은 금액을 비교해요")
    st.caption("세전 기준이에요. 차이는 받을 금액에서 받은 금액을 뺀 값이에요.")
    scenarios = result.expected_pay
    if not scenarios:
        pay_comparison(result.comparison, False)
        return
    tabs = st.tabs([WORKPLACE_LABELS[scenario.workplace_size] for scenario in scenarios]) if len(scenarios) > 1 else [st.container()]
    for tab, scenario in zip(tabs, scenarios):
        rows = [row for row in result.comparison if row.workplace_size in (None, scenario.workplace_size)]
        premiums = [line for line in scenario.items if line.period == "monthly" and line.item in PREMIUMS]
        premium_is_zero = len(premiums) == len(PREMIUMS) and all(line.amount == 0 for line in premiums)
        with tab:
            pay_comparison(rows, premium_is_zero)


def _standards(result: EngineResult):
    years = sorted({row.standards_year for scenario in result.expected_pay for row in scenario.items})
    year_text = ", ".join(f"{year}년" for year in years) or "기준 연도 정보 없음"
    unverified = list(dict.fromkeys(row.item for row in result.limitations if row.reason == "기준 확인 필요"))
    notes = [row for row in result.limitations if row.reason != "기준 확인 필요"]
    st.caption(f"{year_text} 기준 · 소정근로일 개근 가정 · 미검증 기준 {len(unverified)}개")
    with st.expander("계산 기준 안내"):
        st.markdown("주휴수당이 적용되는 경우 소정근로일에 모두 일했다고 가정해요.")
        if unverified:
            st.markdown(f"**기준 확인 필요** · {', '.join(unverified)}")
        for note in notes:
            st.markdown(f"**{note.item}** · {conversational(note.reason)}")
        st.caption("실제 지급액과 비교할 수 있도록 급여 항목마다 적용 조건을 표시해요.")
        for scenario in result.expected_pay:
            st.markdown(f"**{WORKPLACE_LABELS[scenario.workplace_size]}**")
            for row in scenario.items:
                if row.period == "weekly":
                    st.markdown(f"**{row.item}** · {row.applied_condition}")


def show_result(result: EngineResult, extraction: ExtractionResult):
    st.title("내 급여 영수증")
    items = review_items(result, extraction)
    actual = extraction.payslip.total_paid.value
    if actual is None:
        actual = next((row.actual for row in result.comparison if row.item == "합계" and row.actual is not None), None)
    summary_band(result, items, actual)

    with st.container(key="report-split"):
        report, support = st.columns([8, 4], gap="large", vertical_alignment="top")
        with report:
            with st.container(border=True, key="pay-card"):
                _comparison_section(result)
            review_cards(items)
            with st.container(border=True, key="terms-card"):
                st.subheader("계약서 필수 기재사항")
                required_term_grid(extraction)

            ok_checks = [row for row in result.checks if row.status == "ok" and row.item != "필수 기재사항"]
            if ok_checks:
                with st.expander("기준에 맞는 진단 보기"):
                    for row in ok_checks:
                        st.markdown(f"**{row.item}** · {conversational(row.description)}")
            scenario_comparison(result)
            _standards(result)
        with support:
            help_panel(result, items)

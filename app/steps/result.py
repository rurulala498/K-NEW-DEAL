import streamlit as st

from app.components.evidence import evidence_chips, explanation_terms
from app.components.help_panel import help_panel
from app.components.report import (
    expected_pay_receipt, pay_comparison, required_term_grid, review_cards,
    scenario_comparison, status_badge, summary_band,
)
from app.format import (
    PAY_BASIS_LABELS, PREMIUMS, WORKPLACE_LABELS, WorkplacePay, conversational, pay_by_workplace,
)
from llm import explain
from llm.client import ApiSettings, configuration_message
from rag.basis_map import check_basis_keys, pay_basis_keys
from rag.store import RagStore
from schemas import EngineResult, ExtractionResult


def _comparison_section(result: EngineResult, scenarios: list[WorkplacePay], store: RagStore,
                        explanation: explain.Explained):
    st.subheader("받을 금액과 받은 금액을 비교해요")
    if any(group.minimum_wage is not None for group in scenarios):
        st.caption("세전 금액이에요. 최저임금 기준으로 계산하면 실제 받은 금액과 아래처럼 차이가 날 수 있어요.")
    else:
        st.caption("세전 기준이에요. 차이는 받을 금액에서 받은 금액을 뺀 값이에요.")
    if not scenarios:
        pay_comparison(result.comparison, False, store, explanation)
        return
    labels = [WORKPLACE_LABELS[group.contract.workplace_size] for group in scenarios]
    tabs = st.tabs(labels) if len(scenarios) > 1 else [st.container()]
    for tab, group in zip(tabs, scenarios):
        scenario = group.for_comparison
        rows = [row for row in result.comparison if row.workplace_size in (None, scenario.workplace_size)]
        premiums = [line for line in scenario.items if line.period == "monthly" and line.item in PREMIUMS]
        premium_is_zero = len(premiums) == len(PREMIUMS) and all(line.amount == 0 for line in premiums)
        with tab:
            pay_comparison(rows, premium_is_zero, store, explanation, minimum_wage=group.minimum_wage is not None)


def _expected_section(scenarios: list[WorkplacePay], store: RagStore, explanation: explain.Explained) -> bool:
    st.subheader("받을 금액 명세")
    status_badge("비교 안 함", "missing")
    st.caption("세전 기준이에요.")
    if len(scenarios) > 1:
        tabs = st.tabs([WORKPLACE_LABELS[group.contract.workplace_size] for group in scenarios])
        for tab, group in zip(tabs, scenarios):
            with tab:
                expected_pay_receipt(group, store, explanation)
    elif scenarios:
        expected_pay_receipt(scenarios[0], store, explanation)
    st.caption("급여명세서를 올리면 실제 받은 금액과 비교할 수 있어요")
    return st.button("급여명세서 추가하기", key="add-payslip")


def _standards(result: EngineResult, store: RagStore, explanation: explain.Explained):
    years = sorted({row.standards_year for scenario in result.expected_pay for row in scenario.items})
    year_text = ", ".join(f"{year}년" for year in years) or "기준 연도 정보 없음"
    unverified = list(dict.fromkeys(row.item for row in result.limitations if row.reason == "기준 확인 필요"))
    notes = [row for row in result.limitations if row.reason != "기준 확인 필요"]
    st.caption(f"{year_text} 기준 · 소정근로일 개근 가정 · 미검증 기준 {len(unverified)}개")
    with st.expander("계산 기준 안내"):
        st.markdown("주휴수당이 적용되는 경우 소정근로일에 모두 일했다고 가정해요.")
        if unverified:
            st.markdown(f"**기준 확인 필요** · {', '.join(unverified)}")
            if "단시간 근로자 연장 가산" in unverified:
                evidence_chips(store, ("part_time_overtime",), "단시간 근로자 연장 가산", explanation)
        for note in notes:
            st.markdown(f"**{note.item}** · {conversational(note.reason)}")
        st.caption("급여 항목마다 적용 조건과 연결된 자료를 확인할 수 있어요.")
        multiple_bases = any(scenario.basis == "minimum_wage" for scenario in result.expected_pay)
        for scenario in result.expected_pay:
            label = WORKPLACE_LABELS[scenario.workplace_size]
            if multiple_bases:
                label += f" · {PAY_BASIS_LABELS[scenario.basis]}"
            st.markdown(f"**{label}**")
            for row in scenario.items:
                if row.period == "weekly":
                    st.markdown(f"**{row.item}** · {row.applied_condition}")
                    evidence_chips(store, pay_basis_keys(row.item), row.item, explanation)


def show_result(
    result: EngineResult, extraction: ExtractionResult, store: RagStore, *, sample_name: str | None = None,
) -> bool:
    context = explain.build_context(result, extraction, store)
    key = context.cache_key
    cache = st.session_state.setdefault("explanation_cache", {})
    if sample_name:
        settings = ApiSettings("", "")
        explanation = explain.saved_sample(sample_name, context)
        pending = False
    else:
        settings = explain.read_settings()
        explanation = cache.get(key, explain.Explained(context.basic))
        pending = key not in cache and configuration_message(settings) is None

    st.title("내 급여 영수증")
    if pending:
        st.info("기본 설명을 먼저 보여 드려요. AI 설명을 준비하고 있어요…")
    items = context.items
    payslip_provided = extraction.documents_provided.payslip
    actual = extraction.payslip.total_paid.value if payslip_provided else None
    if payslip_provided and actual is None:
        actual = next((row.actual for row in result.comparison if row.item == "합계" and row.actual is not None), None)
    scenarios = pay_by_workplace(result.expected_pay)
    summary_band(result, scenarios, items, actual, explanation, payslip_provided=payslip_provided)
    add_payslip = False

    with st.container(key="report-split"):
        report, support = st.columns([8, 4], gap="large", vertical_alignment="top")
        with report:
            with st.container(border=True, key="pay-card"):
                if payslip_provided:
                    _comparison_section(result, scenarios, store, explanation)
                else:
                    add_payslip = _expected_section(scenarios, store, explanation)
            review_cards(items, explanation, store)
            explanation_terms(explanation, store)
            with st.container(border=True, key="terms-card"):
                st.subheader("계약서 필수 기재사항")
                required_term_grid(extraction)
                terms_check = next((row for row in result.checks if row.item == "필수 기재사항"), None)
                if terms_check:
                    evidence_chips(store, check_basis_keys(terms_check, extraction.contract), terms_check.item, explanation)

            ok_checks = [row for row in result.checks if row.status == "ok" and row.item != "필수 기재사항"]
            if ok_checks:
                with st.expander("기준에 맞는 진단 보기"):
                    for row in ok_checks:
                        st.markdown(f"**{row.item}** · {conversational(row.description)}")
                        evidence_chips(store, check_basis_keys(row, extraction.contract), row.item, explanation)
            scenario_comparison(scenarios)
            _standards(result, store, explanation)
        with support:
            help_panel(context, explanation, settings, store, pending=pending)
    if pending and not add_payslip:
        cache[key] = explain.generate(context, settings)
        if len(cache) > 8:
            cache.pop(next(iter(cache)))
        st.rerun()
    return add_payslip

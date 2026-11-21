import streamlit as st

from app.components.evidence import evidence_chips, explanation_terms, help_channel_links
from app.components.help_panel import help_panel
from app.components.shell import begin_check, reset
from app.components.report import (
    expected_pay_receipt, pay_comparison, required_term_grid, review_cards,
    scenario_comparison, status_badge, summary_band,
)
from app.format import (
    PAY_BASIS_LABELS, PREMIUMS, WORKPLACE_LABELS, WorkplacePay, conversational, pay_by_workplace,
)
from config import load_standards
from llm import chat, explain
from llm.client import configuration_message
from rag.basis_map import check_basis_keys, pay_basis_keys
from rag.store import RagStore
from schemas import EngineResult, ExtractionResult


def _comparison_section(result: EngineResult, scenarios: list[WorkplacePay], store: RagStore,
                        explanation: explain.Explained):
    st.subheader("급여 영수증")
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
            st.caption(f"월 기준 · 사업장 {WORKPLACE_LABELS[scenario.workplace_size]}")
            pay_comparison(rows, premium_is_zero, store, explanation, group=group)


def _expected_section(scenarios: list[WorkplacePay], store: RagStore, explanation: explain.Explained):
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


def _standards(result: EngineResult, store: RagStore, explanation: explain.Explained):
    years = sorted({row.standards_year for scenario in result.expected_pay for row in scenario.items})
    year_text = ", ".join(f"{year}년" for year in years) or "기준 연도 정보 없음"
    unverified = list(dict.fromkeys(row.item for row in result.limitations if row.reason == "기준 확인 필요"))
    notes = [row for row in result.limitations if row.reason != "기준 확인 필요"]
    st.html('<div id="report-standards"></div>')
    label = f"계산 기준 · {year_text} 기준값, 개근 가정"
    if len(years) == 1:
        standards = load_standards(years[0])
        if isinstance(standards, dict) and (weeks := standards.get("weeks_per_month", {}).get("value")) is not None:
            label += f", 한 달 {weeks}주"
    with st.expander(label):
        st.caption(f"미검증 기준 {len(unverified)}개")
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


def _contract_checks(result: EngineResult, extraction: ExtractionResult, store: RagStore, explanation: explain.Explained):
    required = next((check for check in result.checks if check.item == "필수 기재사항"), None)
    if required is None or required.status != "ok":
        with st.container(border=True, key="terms-card"):
            st.subheader("계약서 필수 기재사항")
            required_term_grid(extraction)
            if required:
                evidence_chips(store, check_basis_keys(required, extraction.contract), required.item, explanation)

    passed = [check for check in result.checks if check.status == "ok"]
    if passed:
        titles = " · ".join(check.item for check in passed)
        with st.expander(f"기준 충족 {len(passed)} · {titles}"):
            for check in passed:
                st.markdown(f"**{check.item}** · {conversational(check.description)}")
                if check is required:
                    required_term_grid(extraction)
                evidence_chips(store, check_basis_keys(check, extraction.contract), check.item, explanation)


def _another_check():
    reset()
    begin_check()


def _more_help(store: RagStore):
    with st.container(border=True, key="report-help"):
        title, links, action = st.columns([2, 5, 2], gap="small", vertical_alignment="center")
        title.subheader("더 확인하고 싶다면")
        with links:
            if store.help_channels:
                help_channel_links(store)
            else:
                st.caption(chat.OFFICIAL_HELP)
        action.button("다른 서류 점검하기", key="another-check", on_click=_another_check)


def _paid_period(extraction: ExtractionResult) -> str:
    start = extraction.payslip.pay_period_start.value
    end = extraction.payslip.pay_period_end.value
    if start and end and (start.year, start.month) == (end.year, end.month):
        return f"{start.month}월 급여명세서"
    if start and end:
        return f"{start.isoformat()} ~ {end.isoformat()}"
    return ""


def show_result(
    result: EngineResult, extraction: ExtractionResult, store: RagStore, *, sample_name: str | None = None,
) -> bool:
    context = explain.build_context(result, extraction, store)
    key = context.cache_key
    cache = st.session_state.setdefault("explanation_cache", {})
    settings = explain.read_settings()
    if sample_name:
        explanation = explain.saved_sample(sample_name, context)
        pending = False
    else:
        explanation = cache.get(key, explain.Explained(context.basic))
        pending = key not in cache and configuration_message(settings) is None

    if pending:
        st.info("기본 설명을 먼저 보여 드려요. AI 설명을 준비하고 있어요…")
    items = context.items
    payslip_provided = extraction.documents_provided.payslip
    actual = extraction.payslip.total_paid.value if payslip_provided else None
    if payslip_provided and actual is None:
        actual = next((row.actual for row in result.comparison if row.item == "합계" and row.actual is not None), None)
    scenarios = pay_by_workplace(result.expected_pay)
    add_payslip = summary_band(
        result, scenarios, items, actual, explanation, payslip_provided=payslip_provided,
        paid_period=_paid_period(extraction) if payslip_provided else "",
    )

    with st.container(key="report-split"):
        report, support = st.columns([2, 1], gap="medium", vertical_alignment="top")
        with report:
            with st.container(border=True, key="pay-card"):
                if payslip_provided:
                    _comparison_section(result, scenarios, store, explanation)
                else:
                    _expected_section(scenarios, store, explanation)
            review_cards(items, explanation, store)
            _contract_checks(result, extraction, store, explanation)
            _standards(result, store, explanation)
            scenario_comparison(scenarios)
            if explanation.content.terms:
                with st.expander("급여 용어 풀이"):
                    explanation_terms(explanation, store)
        with support, st.container(key="report-support"):
            help_panel(context, explanation, settings, store, pending=pending, sample=bool(sample_name))
    _more_help(store)
    if pending and not add_payslip:
        cache[key] = explain.generate(context, settings)
        if len(cache) > 8:
            cache.pop(next(iter(cache)))
        st.rerun()
    return add_payslip

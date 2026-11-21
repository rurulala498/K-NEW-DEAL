from decimal import Decimal
from html import escape

import streamlit as st

from app.components.evidence import evidence_chips, explanation_label
from app.format import (
    PAY_BASIS_LABELS, PREMIUMS, REQUIRED_TERMS, WORKPLACE_LABELS, ReviewItem, WorkplacePay,
    amount_range, monthly_total, won,
)
from llm.explain import Explained
from rag.basis_map import pay_basis_keys
from rag.store import RagStore
from schemas import EngineResult, ExtractionResult
from schemas.engine import ComparisonItem


def status_badge(text: str, status: str):
    st.html(f'<span class="status status-{status}">{escape(text)}</span>')


def summary_band(
    result: EngineResult, scenarios: list[WorkplacePay], items: list[ReviewItem],
    actual: Decimal | None, explanation: Explained, *, payslip_provided: bool,
):
    totals = [total for group in scenarios if (total := monthly_total(group.for_comparison)) is not None]
    has_minimum = any(group.minimum_wage is not None for group in scenarios)
    with st.container(key="summary-band"):
        narrative, metrics = st.columns([6, 6], vertical_alignment="center", gap="medium")
        with narrative:
            if items:
                status_badge(f"확인 필요 {len(items)}건", "review")
            elif (not payslip_provided and result.checks) or (totals and actual is not None and result.comparison):
                status_badge("기준에 맞아요", "ok")
            elif payslip_provided:
                status_badge("정보 없음", "missing")
            st.header(explanation.content.summary.headline)
            st.markdown(explanation.content.summary.reason)
            st.caption(explanation_label(explanation.ai_fields, "summary.", 2, mixed=explanation.mixed_fields))
        with metrics:
            expected_box, actual_box, terms_box = st.columns(3, gap="small")
            expected_box.metric("받을 금액", amount_range(totals))
            if has_minimum:
                contract_totals = [total for group in scenarios if (total := monthly_total(group.contract)) is not None]
                expected_box.caption(f"계약대로 {amount_range(contract_totals)} · 최저임금 기준 {amount_range(totals)}")
            actual_box.metric("받은 금액", won(actual) if payslip_provided else "비교 안 함")
            passed = sum(check.status == "ok" for check in result.checks)
            terms_box.metric("계약서 점검", f"{passed} / {len(result.checks)} 충족")


def _zero_premium(row: ComparisonItem) -> bool:
    return row.item in PREMIUMS and row.expected == 0 and row.actual in (None, 0)


def _bar(amount, ceiling, css_class: str) -> str:
    size = max(Decimal(0), amount or Decimal(0)) / ceiling * 100
    return (
        f'<div class="pay-bar-line"><span class="pay-track" aria-hidden="true">'
        f'<span class="pay-bar {css_class}" style="width:{size:.2f}%"></span></span>'
        f'<b>{won(amount)}</b></div>'
    )


def _bars(rows: list[ComparisonItem], columns):
    if not rows:
        st.caption("비교할 수 있는 급여 항목이 없어요.")
        return
    amounts = (getattr(row, field) for row in rows for _, field, _ in columns)
    ceiling = max([Decimal(1), *(amount for amount in amounts if amount is not None)])
    legend = "".join(f'<span class="{color}-label">{label}</span>' for label, _, color in columns)
    parts = [f'<div class="pay-legend">{legend}</div>']
    for row in rows:
        bars = "".join(_bar(getattr(row, field), ceiling, color) for _, field, color in columns)
        parts.append(f'<div class="pay-bar-group"><strong>{escape(row.item)}</strong>{bars}</div>')
    st.html(f'<div class="pay-bars" role="group" aria-label="항목별 받을 금액과 받은 금액">{"".join(parts)}</div>')


def _table(rows: list[ComparisonItem], columns):
    if not rows:
        st.caption("표에 표시할 금액이 없어요.")
        return
    headers = "".join(f'<th scope="col">{label}</th>' for label, _, _ in columns)
    entries = []
    for row in rows:
        amounts = "".join(f"<td>{won(getattr(row, field))}</td>" for _, field, _ in columns)
        difference = ' class="pay-diff"' if row.difference is not None and row.difference != 0 else ""
        entries.append(
            f'<tr><th scope="row">{escape(row.item)}</th>{amounts}<td{difference}>{won(row.difference)}</td></tr>'
        )
    st.html(
        '<table class="pay-table"><thead><tr><th scope="col">항목</th>'
        f'{headers}<th scope="col">차이</th></tr></thead><tbody>{"".join(entries)}</tbody></table>'
    )


def pay_comparison(rows: list[ComparisonItem], premium_is_zero: bool, store: RagStore, explanation: Explained, *,
                   minimum_wage: bool = False):
    if minimum_wage:
        columns = [(PAY_BASIS_LABELS["contract"], "contract_expected", "contract"),
                   (PAY_BASIS_LABELS["minimum_wage"], "expected", "expected")]
    else:
        columns = [("받을 금액", "expected", "expected")]
    columns.append(("받은 금액", "actual", "actual"))
    visible = [row for row in rows if not _zero_premium(row)]
    _bars([row for row in visible if row.item != "합계"], columns)
    if premium_is_zero and not any(row.item in PREMIUMS for row in visible):
        st.caption("연장·야간 가산은 해당 없음이에요.")
    with st.expander("상세 영수증 보기"):
        _table(visible, columns)
        for row in visible:
            st.markdown(f"**{row.item}**")
            if minimum_wage and row.expected is not None:
                st.caption(f"{row.item}은 최저임금 기준으로 계산하면 {won(row.expected)}이에요.")
            evidence_chips(store, pay_basis_keys(row.item), row.item, explanation)
    if minimum_wage:
        st.caption("요약의 금액은 원인별, 표의 금액은 항목별로 나눈 값이에요")


def expected_pay_receipt(group: WorkplacePay, store: RagStore, explanation: Explained):
    rows = [row for row in group.contract.items if row.period == "monthly"]
    if not rows:
        st.caption("표에 표시할 받을 금액이 없어요.")
        return

    minimum = group.minimum_wage
    headers = '<th scope="col">받을 금액</th>'
    css_class = "pay-table"
    minimum_rows = {}
    if minimum:
        st.caption("최저임금 기준으로 계산하면 아래 금액을 예상할 수 있어요.")
        headers = '<th scope="col">계약 시급 기준</th><th scope="col">최저임금 기준</th>'
        css_class += " has-wage-bases"
        minimum_rows = {row.item: row for row in minimum.items if row.period == "monthly"}

    entries = []
    for row in rows:
        formula = escape(row.formula)
        amounts = f"<td>{won(row.amount)}</td>"
        if minimum:
            alternate = minimum_rows.get(row.item)
            amounts += f"<td>{won(alternate.amount if alternate else None)}</td>"
            alternate_formula = escape(alternate.formula) if alternate else "정보 없음"
            formula = f"계약 {formula}<br>최저임금 {alternate_formula}"
        entries.append(
            f'<tr><th scope="row">{escape(row.item)}</th>{amounts}<td class="receipt-formula">{formula}</td></tr>'
        )
    st.html(
        f'<table class="{css_class}"><thead><tr><th scope="col">항목</th>{headers}'
        f'<th scope="col">계산식</th></tr></thead><tbody>{"".join(entries)}</tbody></table>'
    )
    with st.expander("급여 항목별 근거 보기"):
        for row in rows:
            st.markdown(f"**{row.item}**")
            evidence_chips(store, pay_basis_keys(row.item), row.item, explanation)


def review_cards(items: list[ReviewItem], explanation: Explained, store: RagStore):
    if not items:
        return
    st.subheader("확인할 항목")
    with st.container(key="review-card-grid"):
        for start in range(0, len(items), 2):
            for index, (item, column) in enumerate(zip(items[start:start + 2], st.columns(2)), start=start):
                with column:
                    with st.container(border=True, key=f"review-item-{index}"):
                        status_badge("확인 필요", "review")
                        st.markdown(f"**{item.title}**" + (f" · {item.scenario_name}" if item.scenario_name else ""))
                        detail = explanation.content.items[index]
                        st.markdown(detail.easy_explanation)
                        st.markdown(detail.next_action)
                        st.caption(explanation_label(
                            explanation.ai_fields, f"items.{detail.item_id}.", 2, mixed=explanation.mixed_fields,
                        ))
                        if item.formula:
                            st.html(f'<p class="pay-formula">{escape(item.formula)}</p>')
                        keys = tuple(dict.fromkeys((*detail.basis_keys, *item.basis_keys)))
                        evidence_chips(store, keys, item.title, explanation)


def required_term_grid(extraction: ExtractionResult):
    parts = []
    for label, field in REQUIRED_TERMS.items():
        has_value = getattr(extraction.contract, field).value is not None
        state = "ok" if has_value else "missing"
        icon, text = ("check", "기재돼 있어요") if has_value else ("remove", "정보 없음")
        parts.append(
            f'<div class="term-cell"><span aria-hidden="true" class="icon-line status-{state}">{icon}</span>'
            f'<div><strong>{escape(label)}</strong><small class="status-{state}">{text}</small></div></div>'
        )
    st.html(f'<div class="term-grid">{"".join(parts)}</div>')


def scenario_comparison(scenarios: list[WorkplacePay]):
    if len(scenarios) < 2:
        return
    st.subheader("사업장 규모에 따라 비교해요")
    st.caption("사업장 규모를 모르면 가산수당 적용 여부에 따라 받을 금액이 달라질 수 있어요.")
    with st.container(key="scenario-pair"):
        for group, column in zip(scenarios, st.columns(2)):
            size = group.contract.workplace_size
            with column:
                with st.container(border=True, key=f"scenario-card-{size}"):
                    st.markdown(f"**{WORKPLACE_LABELS[size]}**")
                    expected = won(monthly_total(group.for_comparison))
                    st.metric("월 받을 금액", expected)
                    if group.minimum_wage:
                        st.caption(f"계약대로 {won(monthly_total(group.contract))} · 최저임금 기준 {expected}")

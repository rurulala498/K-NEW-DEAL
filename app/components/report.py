from decimal import Decimal
from html import escape
import re

import streamlit as st

from app.components.evidence import evidence_chips, explanation_label
from app.components.shell import DISCLAIMER
from app.format import (
    PAY_BASIS_LABELS, PREMIUMS, REQUIRED_TERMS, WORKPLACE_LABELS, ReviewItem, WorkplacePay,
    amount_range, monthly_total, won,
)
from llm.explain import Explained
from rag.basis_map import pay_basis_keys
from rag.store import RagStore
from schemas import EngineResult, ExtractionResult
from schemas.engine import ComparisonItem

WON_TEXT = re.compile(r"[-−]?\d[\d,]*(?:\.\d+)?\s*원")


def _money_markup(text: str, *, highlight: bool = False) -> str:
    style = "amount headline-amount" if highlight else "amount"
    parts = WON_TEXT.split(text)
    amounts = WON_TEXT.findall(text)
    return "".join(
        f'{escape(part)}<span class="{style}">{escape(amount)}</span>' for part, amount in zip(parts, amounts)
    ) + escape(parts[-1])


def _badge(text: str, status: str) -> str:
    return f'<span class="status status-{status}">{escape(text)}</span>'


def status_badge(text: str, status: str):
    st.html(_badge(text, status))


def summary_band(
    result: EngineResult, scenarios: list[WorkplacePay], items: list[ReviewItem],
    actual: Decimal | None, explanation: Explained, *, payslip_provided: bool, paid_period: str = "",
) -> bool:
    totals = [total for group in scenarios if (total := monthly_total(group.for_comparison)) is not None]
    has_minimum = any(group.minimum_wage is not None for group in scenarios)
    with st.container(key="summary-band"):
        if items:
            status_badge(f"확인 필요 {len(items)}건", "review")
        elif (not payslip_provided and result.checks) or (totals and actual is not None and result.comparison):
            status_badge("기준 충족", "ok")
        else:
            status_badge("정보 없음", "missing")
        summary = explanation.content.summary
        has_shortage = any(row.item == "합계" and row.difference is not None and row.difference > 0
                           for row in result.comparison)
        title = _money_markup(summary.headline, highlight=payslip_provided and bool(items) and has_shortage)
        st.html(f'<h1 class="report-summary-title">{title}</h1>')
        with st.container(key="summary-context"):
            narrative, note = st.columns([3, 2], gap="medium", vertical_alignment="center")
            with narrative:
                st.html(f'<p class="report-summary-reason">{_money_markup(summary.reason)}</p>')
                st.caption(explanation_label(explanation.ai_fields, "summary.", 2, mixed=explanation.mixed_fields))
            note.html(
                '<p class="report-summary-note">'
                f'{escape(DISCLAIMER)} <a href="#report-standards">계산 기준</a></p>'
            )

        with st.container(key="summary-tiles"):
            expected_box, actual_box, terms_box = st.columns(3, gap="small")
            with expected_box, st.container(border=True, key="summary-expected"):
                st.metric("받아야 할 금액", amount_range(totals))
                if has_minimum:
                    contract_totals = [total for group in scenarios if (total := monthly_total(group.contract)) is not None]
                    st.caption(f"계약대로 {amount_range(contract_totals)} · 최저임금 기준 {amount_range(totals)}")
            with actual_box, st.container(border=True, key="summary-actual"):
                st.metric("실제로 받은 금액", won(actual) if payslip_provided else "비교 안 함")
                if paid_period:
                    st.caption(paid_period)
            with terms_box, st.container(border=True, key="summary-checks"):
                passed = sum(check.status == "ok" for check in result.checks)
                st.metric("계약서 점검", f"{passed} / {len(result.checks)} 충족")
                flagged = [check.item for check in result.checks if check.status == "needs_review"]
                if flagged:
                    st.html(f'<p class="tile-checks">{escape(" · ".join(flagged))} 확인 필요</p>')
        if not payslip_provided:
            st.caption("급여명세서를 올리면 실제 받은 금액과 비교할 수 있어요")
            return st.button("급여명세서 추가하기", key="add-payslip", type="primary")
    return False


def _zero_premium(row: ComparisonItem) -> bool:
    return row.item in PREMIUMS and row.expected == 0 and row.actual in (None, 0)


def _bar(amount, ceiling, css_class: str) -> str:
    size = max(Decimal(0), amount or Decimal(0)) / ceiling * 100
    return (
        f'<div class="pay-bar-line"><span class="pay-track" aria-hidden="true">'
        f'<span class="pay-bar {css_class}" style="width:{size:.2f}%"></span></span>'
        f'<b class="amount">{escape(won(amount))}</b></div>'
    )


def _bars(rows: list[ComparisonItem], columns):
    if not rows:
        st.caption("비교할 수 있는 급여 항목이 없어요.")
        return
    amounts = (getattr(row, field) for row in rows for _, field, _ in columns)
    ceiling = max([Decimal(1), *(amount for amount in amounts if amount is not None)])
    legend = "".join(f'<span class="{color}-label">{escape(label)}</span>' for label, _, color in columns)
    parts = [f'<div class="pay-legend">{legend}</div>']
    for row in rows:
        bars = "".join(_bar(getattr(row, field), ceiling, color) for _, field, color in columns)
        parts.append(f'<div class="pay-bar-group"><strong>{escape(row.item)}</strong>{bars}</div>')
    st.html(f'<div class="pay-bars" role="group" aria-label="항목별 받을 금액과 받은 금액">{"".join(parts)}</div>')


def _formula_details(item: str, group: WorkplacePay | None) -> str:
    if group is None:
        return ""
    lines = []
    for scenario in (group.contract, group.minimum_wage):
        if scenario is None:
            continue
        row = next((line for line in scenario.items if line.item == item and line.period == "monthly"), None)
        if row:
            lines.append(
                f'<p><b>{escape(PAY_BASIS_LABELS[scenario.basis])}</b><br>'
                f'{_money_markup(row.formula)} = <span class="amount">{escape(won(row.amount))}</span></p>'
            )
    if not lines:
        return ""
    return (
        '<details class="receipt-calculation"><summary>계산식</summary>'
        f'<div class="receipt-calculation-body">{"".join(lines)}</div></details>'
    )


def _amount_cell(amount, *, different: bool = False) -> str:
    css = ' class="pay-diff"' if different else ""
    return f'<td{css}><span class="amount">{escape(won(amount))}</span></td>'


def _table(rows: list[ComparisonItem], columns, group: WorkplacePay | None):
    if not rows:
        st.caption("표에 표시할 금액이 없어요.")
        return
    headers = "".join(f'<th scope="col">{escape(label)}</th>' for label, _, _ in columns)
    entries = []
    for row in rows:
        amounts = "".join(_amount_cell(getattr(row, field)) for _, field, _ in columns)
        different = row.difference is not None and row.difference != 0
        classes = " ".join(name for name, active in (("pay-row-diff", different), ("pay-row-total", row.item == "합계")) if active)
        entries.append(
            f'<tr class="{classes}"><th scope="row">{escape(row.item)}{_formula_details(row.item, group)}</th>'
            f'{amounts}{_amount_cell(row.difference, different=different)}</tr>'
        )
    st.html(
        '<div class="table-scroll" role="region" aria-label="급여 영수증" tabindex="0">'
        '<table class="pay-table"><thead><tr><th scope="col">항목</th>'
        f'{headers}<th scope="col">차이</th></tr></thead><tbody>{"".join(entries)}</tbody></table></div>'
    )


def pay_comparison(rows: list[ComparisonItem], premium_is_zero: bool, store: RagStore, explanation: Explained, *,
                   group: WorkplacePay | None = None):
    minimum_wage = group is not None and group.minimum_wage is not None
    if minimum_wage:
        columns = [(PAY_BASIS_LABELS["contract"], "contract_expected", "contract"),
                   (PAY_BASIS_LABELS["minimum_wage"], "expected", "expected")]
    else:
        columns = [("받을 금액", "expected", "expected")]
    columns.append(("받은 금액", "actual", "actual"))
    visible = [row for row in rows if not _zero_premium(row)]
    _table(visible, columns, group)
    if minimum_wage:
        st.caption("요약의 금액은 원인별, 표의 금액은 항목별로 나눈 값이에요")
    if premium_is_zero and not any(row.item in PREMIUMS for row in visible):
        st.caption("연장·야간 가산은 해당 없음이에요.")
    if visible:
        with st.expander("항목별 근거·금액 그래프 보기"):
            _bars([row for row in visible if row.item != "합계"], columns)
            for row in visible:
                st.markdown(f"**{row.item}**")
                if minimum_wage and row.expected is not None:
                    st.caption(f"{row.item}은 최저임금 기준으로 계산하면 {won(row.expected)}이에요.")
                evidence_chips(store, pay_basis_keys(row.item), row.item, explanation)


def expected_pay_receipt(group: WorkplacePay, store: RagStore, explanation: Explained):
    rows = [row for row in group.contract.items if row.period == "monthly"]
    if not rows:
        st.caption("표에 표시할 받을 금액이 없어요.")
        return

    minimum = group.minimum_wage
    headers = '<th scope="col">받을 금액</th>'
    css_class = "pay-table"
    alternate = {}
    if minimum:
        st.caption("최저임금 기준으로 계산하면 아래 금액을 예상할 수 있어요.")
        headers = '<th scope="col">계약 시급 기준</th><th scope="col">최저임금 기준</th>'
        css_class += " has-wage-bases"
        alternate = {row.item: row.amount for row in minimum.items if row.period == "monthly"}

    entries = []
    for row in rows:
        amounts = _amount_cell(row.amount)
        if minimum:
            amounts += _amount_cell(alternate.get(row.item))
        row_class = ' class="pay-row-total"' if row.item == "합계" else ""
        entries.append(
            f'<tr{row_class}><th scope="row">{escape(row.item)}{_formula_details(row.item, group)}</th>{amounts}</tr>'
        )
    st.html(
        '<div class="table-scroll" role="region" aria-label="받을 금액 명세" tabindex="0">'
        f'<table class="{css_class}"><thead><tr><th scope="col">항목</th>{headers}'
        f'</tr></thead><tbody>{"".join(entries)}</tbody></table></div>'
    )
    with st.expander("급여 항목별 근거 보기"):
        for row in rows:
            st.markdown(f"**{row.item}**")
            evidence_chips(store, pay_basis_keys(row.item), row.item, explanation)


def review_cards(items: list[ReviewItem], explanation: Explained, store: RagStore):
    if not items:
        return
    st.subheader("확인이 필요한 항목")
    with st.container(key="review-card-grid"):
        for start in range(0, len(items), 2):
            batch = items[start:start + 2]
            for index, (item, column) in enumerate(zip(batch, st.columns(2, gap="medium")), start=start):
                with column, st.container(border=True, key=f"review-item-{index}"):
                    label = f"{item.title} · {item.scenario_name}" if item.scenario_name else item.title
                    st.html(f'<div class="review-card-heading"><h3>{escape(label)}</h3>{_badge("확인 필요", "review")}</div>')
                    detail = explanation.content.items[index]
                    st.html(f'<p class="review-card-copy">{_money_markup(detail.easy_explanation)}</p>')
                    st.markdown(detail.next_action)
                    if item.formula:
                        st.html(f'<p class="pay-formula">{_money_markup(item.formula)}</p>')
                    keys = tuple(dict.fromkeys((*detail.basis_keys, *item.basis_keys)))
                    evidence_chips(store, keys, item.title, explanation)
                    attribution = explanation_label(
                        explanation.ai_fields, f"items.{detail.item_id}.", 2, mixed=explanation.mixed_fields,
                    )
                    link = ('<a href="#employer-message">사장님께 확인하기 →</a>'
                            if explanation.content.messages is not None else "")
                    st.html(f'<div class="review-card-footer"><small>{escape(attribution)}</small>{link}</div>')


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

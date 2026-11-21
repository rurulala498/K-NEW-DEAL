from decimal import Decimal
from html import escape

import streamlit as st

from app.components.evidence import evidence_chips
from app.format import (
    PREMIUMS, REQUIRED_TERMS, WORKPLACE_LABELS, ReviewItem,
    amount_range, monthly_total, won,
)
from rag.basis_map import pay_basis_keys
from rag.store import RagStore
from schemas import EngineResult, ExtractionResult
from schemas.engine import ComparisonItem, ExpectedPayScenario


def status_badge(text: str, status: str):
    st.html(f'<span class="status status-{status}">{escape(text)}</span>')


def summary_band(result: EngineResult, items: list[ReviewItem], actual, *, payslip_provided: bool):
    totals = [total for scenario in result.expected_pay if (total := monthly_total(scenario)) is not None]
    shortage = [
        row.difference for row in result.comparison
        if row.item == "합계" and row.status == "needs_review" and row.difference is not None and row.difference > 0
    ]
    reasons = [
        row for row in result.comparison
        if row.item != "합계" and row.status == "needs_review" and row.difference is not None and row.difference > 0
    ]
    with st.container(key="summary-band"):
        narrative, metrics = st.columns([6, 6], vertical_alignment="center", gap="medium")
        with narrative:
            if not payslip_provided:
                if items:
                    status_badge(f"확인 필요 {len(items)}건", "review")
                elif result.checks:
                    status_badge("기준에 맞아요", "ok")
                if totals:
                    st.header(f"계약대로라면 매달 약 {amount_range(totals)}을 받아요")
                else:
                    st.header("예상 급여를 계산하려면 계약 내용을 더 확인해 주세요")
            elif items:
                status_badge(f"확인 필요 {len(items)}건", "review")
                lead_in = "가산수당이 적용되면 " if len(result.expected_pay) > 1 else ""
                st.header(f"{lead_in}{amount_range(shortage)} 덜 받았을 수 있어요" if shortage
                          else "다시 살펴볼 항목이 있어요")
                if reasons:
                    cause = max(reasons, key=lambda row: row.difference)
                    st.markdown(f"명세서에 **{cause.item}이 {won(cause.actual)}**이에요.")
                else:
                    st.markdown(items[0].description)
            elif totals and actual is not None and result.comparison:
                status_badge("기준에 맞아요", "ok")
                st.header("계약 내용과 지급액이 기준에 맞습니다")
                st.markdown("계약서와 명세서에서 확인한 금액이 맞아요.")
            else:
                status_badge("정보 없음", "missing")
                st.header("급여를 비교하려면 정보가 더 필요해요")
        with metrics:
            expected_box, actual_box, terms_box = st.columns(3, gap="small")
            expected_box.metric("받을 금액", amount_range(totals))
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


def _bars(rows: list[ComparisonItem]):
    if not rows:
        st.caption("비교할 수 있는 급여 항목이 없어요.")
        return
    ceiling = max([Decimal(1), *(amount for row in rows for amount in (row.expected, row.actual)
                                  if amount is not None)])
    parts = [
        '<div class="pay-legend"><span class="expected-label">받을 금액</span>'
        '<span class="actual-label">받은 금액</span></div>'
    ]
    for row in rows:
        parts.append(
            f'<div class="pay-bar-group"><strong>{escape(row.item)}</strong>'
            f'{_bar(row.expected, ceiling, "expected")}{_bar(row.actual, ceiling, "actual")}</div>'
        )
    st.html(f'<div class="pay-bars" role="group" aria-label="항목별 받을 금액과 받은 금액">{"".join(parts)}</div>')


def _table(rows: list[ComparisonItem]):
    if not rows:
        st.caption("표에 표시할 금액이 없어요.")
        return
    entries = []
    for row in rows:
        difference = ' class="pay-diff"' if row.difference is not None and row.difference != 0 else ""
        entries.append(
            f'<tr><th scope="row">{escape(row.item)}</th><td>{won(row.expected)}</td>'
            f'<td>{won(row.actual)}</td><td{difference}>{won(row.difference)}</td></tr>'
        )
    st.html(
        '<table class="pay-table"><thead><tr><th scope="col">항목</th>'
        '<th scope="col">받을 금액</th><th scope="col">받은 금액</th><th scope="col">차이</th></tr></thead>'
        f'<tbody>{"".join(entries)}</tbody></table>'
    )


def pay_comparison(rows: list[ComparisonItem], premium_is_zero: bool, store: RagStore):
    visible = [row for row in rows if not _zero_premium(row)]
    _bars([row for row in visible if row.item != "합계"])
    if premium_is_zero and not any(row.item in PREMIUMS for row in visible):
        st.caption("연장·야간 가산은 해당 없음이에요.")
    with st.expander("상세 영수증 보기"):
        _table(visible)
        for item in dict.fromkeys(row.item for row in visible):
            st.markdown(f"**{item}**")
            evidence_chips(store, pay_basis_keys(item), item)


def expected_pay_receipt(scenario: ExpectedPayScenario, store: RagStore):
    rows = [row for row in scenario.items if row.period == "monthly"]
    if not rows:
        st.caption("표에 표시할 받을 금액이 없어요.")
        return

    entries = [
        f'<tr><th scope="row">{escape(row.item)}</th><td>{won(row.amount)}</td>'
        f'<td>{escape(row.formula)}</td></tr>'
        for row in rows
    ]
    st.html(
        '<table class="pay-table"><thead><tr><th scope="col">항목</th>'
        '<th scope="col">받을 금액</th><th scope="col">계산식</th></tr></thead>'
        f'<tbody>{"".join(entries)}</tbody></table>'
    )
    with st.expander("급여 항목별 근거 보기"):
        for row in rows:
            st.markdown(f"**{row.item}**")
            evidence_chips(store, pay_basis_keys(row.item), row.item)


def review_cards(items: list[ReviewItem], store: RagStore):
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
                        st.markdown(item.description)
                        if item.formula:
                            st.html(f'<p class="pay-formula">{escape(item.formula)}</p>')
                        evidence_chips(store, item.basis_keys, item.title)


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


def scenario_comparison(result: EngineResult):
    if len(result.expected_pay) < 2:
        return
    st.subheader("사업장 규모에 따라 비교해요")
    st.caption("사업장 규모를 모르면 가산수당 적용 여부에 따라 받을 금액이 달라질 수 있어요.")
    with st.container(key="scenario-pair"):
        for scenario, column in zip(result.expected_pay, st.columns(2)):
            with column:
                with st.container(border=True, key=f"scenario-card-{scenario.workplace_size}"):
                    st.markdown(f"**{WORKPLACE_LABELS[scenario.workplace_size]}**")
                    st.metric("월 받을 금액", won(monthly_total(scenario)))

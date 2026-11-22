from html import escape

import streamlit as st

from app.components.shell import begin_check, svg_uri
from app.format import conversational, won
from engine import run_engine
from llm import explain
from llm.samples import load_sample
from rag.store import RagStore
from schemas import ExtractionResult

WHY = (
    ("“주휴수당, 나도 받는 거야?”", "조건이 여러 개라 내 경우에 해당하는지 판단하기 어려워요."),
    ("“소정근로시간이 뭐지?”", "계약서와 명세서의 용어가 어려워서 숫자를 맞춰 보기 힘들어요."),
    ("“물어보기 눈치 보여요”", "틀린 것 같아도 근거 없이 사장님께 말을 꺼내기 어려워요."),
)
HOW = (
    ("서류 올리기", "근로계약서는 필수, 급여명세서는 선택이에요. AI가 서류를 읽어요."),
    ("읽은 값 확인하기", "원문과 대조하고 틀린 값은 고쳐요. 시급과 근무 시간을 고치면 예상 급여가 바로 바뀌어요."),
    ("결과 받기", "급여 영수증, 확인할 항목, 근거 조문, 사장님께 보낼 문구를 한 화면에서 봐요."),
)
PRIVACY = (
    ("금액은 누가 계산하나요?", "AI는 서류를 읽고 설명만 해요. 금액과 판단은 연도별 기준값으로 만든 계산 규칙이 맡아요."),
    ("근거는 어디서 오나요?", "국가법령정보센터의 조문과 고용노동부·생활법령정보의 설명을 함께 보여 드려요."),
    ("서류는 저장되나요?", "저장하지 않아요. 읽을 때 텍스트만 AI로 보내고, 창을 닫으면 사라져요."),
    ("결과를 그대로 믿어도 되나요?", "법률 자문이 아니라 “확인 필요”를 알려 드리는 서비스예요. 최종 판단은 관계 기관에서 이루어져요."),
)


def _actions(prefix: str):
    with st.container(key=f"landing-{prefix}-actions"):
        check, examples = st.columns(2, gap="small")
        check.button("내 급여 점검하기", key=f"{prefix}-check", type="primary", on_click=begin_check)
        examples.button("예시로 먼저 보기", key=f"{prefix}-examples", on_click=begin_check, args=(True,))


def _cards(entries, *, numbered: bool = False) -> str:
    cards = []
    for index, (title, body) in enumerate(entries, 1):
        badge = f'<span class="landing-pill">{index}단계</span>' if numbered else ""
        cards.append(f'<article class="landing-card">{badge}<h3>{escape(title)}</h3><p>{escape(body)}</p></article>')
    return "".join(cards)


def _amount(value) -> str:
    return f'<span class="amount">{escape(won(value))}</span>'


def _preview(context: explain.ExplanationContext, extraction: ExtractionResult) -> str:
    result = context.result
    comparison = next((row for row in result.comparison if row.item == "합계"), None)
    month = extraction.payslip.pay_period_start.value
    period = f"{month.month}월 " if month else ""
    explanation = context.basic
    holiday = next((detail.easy_explanation for item, detail in zip(context.items, explanation.items)
                    if item.title == "주휴수당"), "")
    rest = next((check for check in result.checks if check.item == "휴게시간"), None)
    question = explanation.messages.employer_question if explanation.messages else ""
    warning = f'<span class="status status-review">확인 필요 {len(context.items)}건</span>' if context.items else (
        '<span class="status status-ok">기준 충족</span>'
    )
    shortage = _amount(comparison.difference if comparison else None)
    rest_status = '<span class="status status-ok">기준 충족</span>' if rest and rest.status == "ok" else (
        '<span class="status status-review">확인 필요</span>'
    )
    return f"""
        <figure class="landing-preview">
          <div class="landing-preview-grid">
            <article class="landing-card landing-receipt">
              <div class="landing-card-heading"><h3>{period}급여 영수증</h3>{warning}</div>
              <dl>
                <div><dt>받아야 할 금액</dt><dd>{_amount(comparison.expected if comparison else None)}</dd></div>
                <div><dt>실제로 받은 금액</dt><dd>{_amount(comparison.actual if comparison else None)}</dd></div>
                <div class="landing-receipt-gap"><dt>덜 받았을 수 있는 금액</dt><dd>{shortage}</dd></div>
              </dl>
              <p>{escape(explanation.summary.reason)}</p>
              <img class="landing-stamp" src="{svg_uri('seal-textured.svg')}" alt="" aria-hidden="true">
            </article>
            <div class="landing-preview-asides">
              <article class="landing-card"><div class="landing-card-heading"><h3>주휴수당</h3>
                <span class="status status-review">확인 필요</span></div><p>{escape(holiday)}</p>
              </article>
              <article class="landing-card"><div class="landing-card-heading"><h3>휴게시간</h3>{rest_status}</div>
                <p>{escape(conversational(rest.description)) if rest else ""}</p>
              </article>
              <article class="landing-card"><h3>사장님께 보낼 문구</h3><p>“{escape(question)}”</p></article>
            </div>
          </div>
          <figcaption>가상 서류로 만든 예시 화면이에요.</figcaption>
        </figure>
    """


def _extraction_preview(extraction: ExtractionResult) -> str:
    contract = extraction.contract
    entry = next(iter(contract.schedule_entries()), None)
    days = "·".join(entry.days.value or []) if entry else ""
    start, end = (entry.start.value, entry.end.value) if entry else (None, None)
    schedule = f"{days} {start:%H:%M}~{end:%H:%M}".strip() if start and end else (days or "정보 없음")
    wage = won(contract.hourly_wage.value)
    weekly = f"{contract.weekly_hours.value:g}시간" if contract.weekly_hours.value is not None else "정보 없음"
    pay_day = contract.pay_day.value or "정보 없음"
    break_minutes = entry.break_minutes.value if entry else None
    rest = f"{break_minutes}분" if break_minutes is not None else "정보 없음"
    return f"""
      <div class="landing-card landing-mini">
        <p>근로일 및 근로시간: <mark>{escape(schedule)}</mark> · 휴게 <mark>{escape(rest)}</mark><br>
        임금: 시간급 <mark>{escape(wage)}</mark> · 지급일 <mark class="mark-low">{escape(pay_day)}</mark></p>
        <div class="landing-mini-values">
          <div><small>시급</small><strong class="amount">{escape(wage)}</strong></div>
          <div><small>주 소정근로시간</small><strong>{escape(weekly)}</strong></div>
          <div class="is-review"><small>지급일 · 확인 필요</small><strong>{escape(pay_day)}</strong></div>
        </div>
      </div>
    """


def _pay_preview(context: explain.ExplanationContext) -> str:
    rows = [row for row in context.result.comparison if row.item in ("기본급", "주휴수당", "합계")]
    entries = "".join(
        f'<tr><th scope="row">{escape(row.item)}</th><td>{_amount(row.expected)}</td>'
        f'<td>{_amount(row.actual)}</td><td>{_amount(row.difference)}</td></tr>' for row in rows
    )
    return (
        '<div class="landing-card landing-mini table-scroll"><table class="pay-table">'
        '<thead><tr><th scope="col">항목</th><th scope="col">받을 금액</th>'
        '<th scope="col">받은 금액</th><th scope="col">차이</th></tr></thead>'
        f'<tbody>{entries}</tbody></table></div>'
    )


def _chat_preview(context: explain.ExplanationContext, store: RagStore) -> str:
    row = next((row for row in context.result.comparison if row.item == "주휴수당"), None)
    question = f"주휴수당은 왜 {won(row.actual)}이에요?" if row and row.actual is not None else "주휴수당은 어떻게 계산됐어요?"
    answer = next((detail.easy_explanation for item, detail in zip(context.items, context.basic.items)
                   if item.title == "주휴수당"), "")
    law = store.get_law("weekly_holiday_pay")
    term = store.get_term("주휴수당")
    labels = [label for label in (f"{law.law_name} {law.article_no}" if law else "", f"용어 · {term.term}" if term else "") if label]
    chips = "".join(f'<span>{escape(label)}</span>' for label in labels)
    return (
        f'<div class="landing-card landing-mini"><p class="landing-chat-question">{escape(question)}</p>'
        f'<div class="landing-chat-answer"><div>{chips}</div><p>{escape(answer)}</p></div></div>'
    )


def _feature(label: str, title: str, body: str, preview: str, *, reverse: bool = False) -> str:
    modifier = " landing-feature-reverse" if reverse else ""
    return (
        f'<section class="landing-feature{modifier}"><div class="landing-feature-copy">'
        f'<p class="landing-eyebrow">{escape(label)}</p><h2>{escape(title)}</h2>'
        f'<p>{escape(body)}</p></div>{preview}</section>'
    )


@st.cache_resource(show_spinner=False, hash_funcs={RagStore: lambda store: (store.laws, store.terms)})
def _landing_example(store: RagStore) -> tuple[ExtractionResult, explain.ExplanationContext]:
    extraction = ExtractionResult.model_validate(load_sample("review_case"))
    context = explain.build_context(run_engine(extraction), extraction, store)
    return extraction, context


def landing_step(store: RagStore):
    extraction, context = _landing_example(store)
    with st.container(key="landing-content"):
        st.html("""
          <section class="landing-hero" id="service-intro">
            <span class="landing-pill">시급제 알바·단기근로 급여 점검</span>
            <h1>일한 만큼,<br>제대로 받았수당?</h1>
            <p>근로계약서와 급여명세서만 올리세요.<br>
            받아야 할 돈과 실제로 받은 돈을 영수증처럼 맞춰 보고, 차이가 나는 이유까지 알려 드려요.</p>
          </section>
        """)
        _actions("hero")
        st.html('<p class="landing-quiet">회원가입 없이 무료 · 서류는 저장하지 않아요</p>')
        st.html(_preview(context, extraction))
        st.html(f"""
          <section class="landing-section landing-band">
            <p class="landing-eyebrow">01 · 왜 필요할까요</p>
            <h2>알바 급여, 맞게 받고 있는지<br>혼자 확인하기 어려워요</h2>
            <div class="landing-card-grid">{_cards(WHY)}</div>
          </section>
        """)
        st.html(
            '<div class="landing-features">'
            + _feature("02 · 서류 읽기", "서류를 올리면\nAI가 대신 읽어요",
                       "시급, 요일별 근무 시간, 지급액을 찾아 원문에 형광펜으로 표시해요. "
                       "불확실한 값은 주황으로 알려 드리고, 확인한 값으로만 계산해요.", _extraction_preview(extraction))
            + _feature("03 · 급여 대조", "받을 돈과 받은 돈,\n영수증처럼 맞춰 봐요",
                       "계약서대로라면 얼마를 받아야 하는지 항목별로 계산하고, 명세서와 한 줄씩 비교해요. "
                       "최저임금보다 낮으면 최저임금 기준 금액도 함께 보여 드려요.", _pay_preview(context), reverse=True)
            + _feature("04 · 설명과 대응", "왜 다른지,\n뭐라고 말할지까지",
                       "어려운 용어는 내 숫자로 풀어 설명하고, 근거 조문 원문도 함께 보여 드려요. "
                       "사장님께 보낼 문구를 만들어 주고, 남는 궁금증은 도움 챗봇에 물어볼 수 있어요.",
                       _chat_preview(context, store))
            + "</div>"
        )
        st.html(f"""
          <section id="how-it-works" class="landing-section landing-band landing-how">
            <p class="landing-eyebrow">이용 방법</p><h2>3단계면 끝나요</h2>
            <div class="landing-card-grid">{_cards(HOW, numbered=True)}</div>
          </section>
        """)
        st.html(f"""
          <section id="privacy-by-design" class="landing-section">
            <p class="landing-eyebrow">안심하고 쓰는 이유</p>
            <h2>AI가 틀릴 수 있다는 걸<br>알고 설계했어요</h2>
            <div class="landing-card-grid two">{_cards(PRIVACY)}</div>
          </section>
        """)
        with st.container(key="landing-closing"):
            st.html('<div class="landing-closing-copy"><h2>이번 달 급여,<br>지금 확인해 보세요</h2>'
                    '<p>서류 한 장이면 충분해요.</p></div>')
            _actions("closing")

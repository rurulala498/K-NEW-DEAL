import streamlit as st

from app.components.evidence import glossary, help_channels
from app.format import ReviewItem, review_rows, won
from rag.store import RagStore
from schemas import EngineResult

QUESTIONS = ("주휴수당이 뭐예요?", "차이는 어떻게 봐요?", "다음에 뭘 확인해요?")


def _chat(store: RagStore):
    with st.container(border=True, key="chat-panel"):
        st.subheader("도움 챗봇")
        st.caption("질문을 골라 보거나 직접 써 볼 수 있어요.")
        for index, question in enumerate(QUESTIONS):
            if st.button(question, key=f"chat-prompt-{index}", width="stretch"):
                st.session_state["receipt_question"] = question
        with st.form("receipt-chat-form", border=False, clear_on_submit=True):
            question = st.text_input("도움 챗봇에 질문하기", placeholder="질문을 직접 적어요",
                                     label_visibility="collapsed")
            if st.form_submit_button("질문 보내기", width="stretch") and question.strip():
                st.session_state["receipt_question"] = question.strip()
        if st.session_state.get("receipt_question"):
            st.text(st.session_state["receipt_question"])
            st.markdown("**준비 중** · 지금은 샘플 답변이에요. 상세 영수증에서 항목별 금액을 비교해 보세요.")
        with st.expander("급여 용어 찾아보기"):
            glossary(store)


def _messages(result: EngineResult, items: list[ReviewItem]):
    rows = review_rows(result.comparison)
    numeric = next((row for row in rows if row.actual is not None and row.difference is not None), None)
    if numeric is not None:
        detail = (
            f"{numeric.item}의 받을 금액이 {won(numeric.expected)}, "
            f"명세서 금액이 {won(numeric.actual)}으로 나왔어요."
        )
    else:
        detail = items[0].description
    with st.container(border=True, key="employer-panel"):
        st.subheader("사장님께 보낼 문구")
        st.caption("문구 오른쪽 복사 아이콘을 누르고, 보내기 전에 내용을 확인해 주세요.")
        st.markdown("**사업주 확인 질문**")
        st.code(f"안녕하세요. 급여 내역을 확인하고 싶어요. {detail} 산정 근거를 알려주실 수 있을까요?",
                language=None, wrap_lines=True)
        st.markdown("**수정 요청 문구**")
        st.code(f"안녕하세요. {detail} 함께 확인한 내용과 차이가 있다면 명세서나 계약서에 반영해 주실 수 있을까요?",
                language=None, wrap_lines=True)


def help_panel(result: EngineResult, items: list[ReviewItem], store: RagStore):
    _chat(store)
    if items:
        _messages(result, items)
    help_channels(store)

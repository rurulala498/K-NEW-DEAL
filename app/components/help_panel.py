import streamlit as st

from app.components.evidence import explanation_label, help_channels
from llm import explain
from llm.client import ApiSettings
from rag.store import RagStore

QUESTIONS = ("주휴수당이 뭐예요?", "차이는 어떻게 봐요?", "다음에 뭘 확인해요?")


def _chat(payslip_provided: bool):
    questions = QUESTIONS if payslip_provided else (QUESTIONS[0], "예상 급여는 어떻게 봐요?", QUESTIONS[2])
    with st.container(border=True, key="chat-panel"):
        st.subheader("도움 챗봇")
        st.caption("질문을 골라 보거나 직접 써 볼 수 있어요.")
        for index, question in enumerate(questions):
            if st.button(question, key=f"chat-prompt-{index}", width="stretch"):
                st.session_state["receipt_question"] = question
        with st.form("receipt-chat-form", border=False, clear_on_submit=True):
            question = st.text_input("도움 챗봇에 질문하기", placeholder="질문을 직접 적어요",
                                     label_visibility="collapsed")
            if st.form_submit_button("질문 보내기", width="stretch") and question.strip():
                st.session_state["receipt_question"] = question.strip()
        if st.session_state.get("receipt_question"):
            st.text(st.session_state["receipt_question"])
            action = ("상세 영수증에서 항목별 금액을 비교해 보세요." if payslip_provided
                      else "받을 금액 명세에서 예상 급여를 확인해 보세요.")
            st.markdown(f"**준비 중** · 지금은 샘플 답변이에요. {action}")


def _messages(context: explain.ExplanationContext, explanation: explain.Explained, settings: ApiSettings, *, pending: bool):
    messages = explanation.content.messages
    if messages is None:
        return

    # Pending editors and final editors have separate keys so initial text can be replaced once.
    key = f"{context.cache_key}-{'pending' if pending else 'ready'}"
    rewrite_key = f"rewritten-{key}"
    fields = (
        ("사업주 확인 질문", "employer_question", f"employer-question-{key}"),
        ("수정 요청 문구", "revision_request", f"revision-request-{key}"),
    )
    with st.container(border=True, key="employer-panel"):
        st.subheader("사장님께 보낼 문구")
        st.caption("문구를 직접 고친 뒤 복사 아이콘으로 복사할 수 있어요.")
        tone = st.selectbox("말투", ("정중하게", "간단하게"), key=f"employer-tone-{key}")
        request = st.text_input("다시 쓰기 요청", placeholder="카톡으로 보낼 거라 짧게", max_chars=100,
                                key=f"employer-request-{key}")
        if st.button("다시 쓰기", key="rewrite-employer-messages", disabled=pending):
            with st.spinner("대응 문구를 다시 쓰고 있어요…"):
                rewritten = explain.rewrite(context, settings, tone, request)
            if rewritten.messages is not None:
                st.session_state[rewrite_key] = rewritten
                for _, name, widget_key in fields:
                    st.session_state[widget_key] = getattr(rewritten.messages, name)

        rewritten = st.session_state.get(rewrite_key)
        source = rewritten.ai_fields if rewritten is not None else explanation.ai_fields
        baseline = rewritten.messages if rewritten is not None else messages
        st.caption(explanation_label(source, "messages.", 2))
        for label, name, widget_key in fields:
            if widget_key not in st.session_state:
                st.session_state[widget_key] = getattr(baseline, name)
            value = st.text_area(label, key=widget_key, height=120, disabled=pending)
            st.code(value, language=None, wrap_lines=True)


def help_panel(context: explain.ExplanationContext, explanation: explain.Explained, settings: ApiSettings,
               store: RagStore, *, pending: bool):
    _chat(context.payload["confirmed"]["documents_provided"]["payslip"])
    _messages(context, explanation, settings, pending=pending)
    help_channels(store)

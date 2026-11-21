from html import escape

import streamlit as st

from app.components.evidence import chat_evidence, explanation_label, help_channel_links, help_channels
from app.format import PAY_BASIS_LABELS, WORKPLACE_LABELS, won
from llm import chat, explain
from llm.client import ApiSettings
from rag.store import RagStore

CHAT_EDIT_REQUEST = "receipt_chat_edit_requested"

def _chat_reply(reply: chat.ChatReply, store: RagStore):
    st.text(reply.answer)
    if reply.validation == "passed":
        st.caption("AI가 작성한 답변이에요")
    chat_evidence(tuple(source.record for source in reply.sources))
    if reply.calculations:
        with st.expander("엔진 계산식 보기"):
            for entry in reply.calculations:
                row = entry.row
                period = "주" if row.period == "weekly" else "월"
                st.caption(f"{WORKPLACE_LABELS[entry.workplace_size]} · {PAY_BASIS_LABELS[entry.basis]} · {row.standards_year}년 기준")
                st.text(f"{row.item} · {period} {won(row.amount)}")
                st.text(row.formula)
    if reply.web:
        with st.container(border=True):
            st.markdown("**참고 · 추가 확인 필요**")
            st.text(reply.web.summary)
            for source in reply.web.sources:
                st.html(
                    f'<p class="rag-source"><a href="{escape(source.url)}" target="_blank" '
                    f'rel="noopener noreferrer">{escape(source.title)}</a></p>'
                )
    if reply.needs_official_check:
        if store.help_channels:
            st.caption("공식 상담 경로")
            help_channel_links(store)
        else:
            st.caption(chat.OFFICIAL_HELP)


def _chat(context: explain.ExplanationContext, settings: ApiSettings, store: RagStore):
    session = st.session_state.setdefault(chat.SESSION_KEY, chat.ChatSession())
    session.use_context(context.cache_key)
    with st.container(border=True, key="chat-panel"):
        st.subheader("도움 챗봇")
        st.caption("질문을 골라 보거나 직접 적어요. 개인정보는 빼 주세요.")
        selected = None
        for index, question in enumerate(chat.suggestions(context, store)):
            if st.button(question, key=f"chat-prompt-{index}", width="stretch"):
                selected = question
        history = st.container(key="chat-history")
        with st.form("receipt-chat-form", border=False, clear_on_submit=True):
            question = st.text_input(
                "도움 챗봇에 질문하기", placeholder="질문을 직접 적어요", key="receipt-chat-input",
                label_visibility="collapsed", max_chars=chat.QUESTION_LENGTH,
            )
            if st.form_submit_button("질문 보내기", width="stretch"):
                selected = question
        if selected is not None:
            with st.spinner("답변에 쓸 자료를 확인하고 있어요…"):
                reply = chat.ask(selected, context, store, settings, session)
            if reply.validation == "blocked":
                st.info(reply.answer)
        with history:
            for asked, reply in session.history.items():
                with st.chat_message("user"):
                    st.text(asked)
                with st.chat_message("assistant"):
                    _chat_reply(reply, store)
        if session.history and next(reversed(session.history.values())).answer_type == "hypothetical":
            if st.button("내용 확인으로 돌아가기", key="chat-edit-confirmed", width="stretch"):
                st.session_state[CHAT_EDIT_REQUEST] = True


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
               store: RagStore, *, pending: bool, sample: bool):
    _chat(context, settings, store)
    _messages(context, explanation, ApiSettings("", "") if sample else settings, pending=pending)
    help_channels(store)

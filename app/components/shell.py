from base64 import b64encode
from functools import lru_cache
from pathlib import Path

import streamlit as st

from llm.chat import SESSION_KEY

ASSETS = Path(__file__).resolve().parents[1] / "assets"
STEPS = ("서류 올리기", "내용 확인", "점검 결과")
DISCLAIMER = "법률 자문이 아니며 최종 판단은 관계 기관에서 이루어집니다."
README = "https://github.com/rurulala498/K-NEW-DEAL"
FOOTER_LINKS = (
    f'<a href="{README}#privacy" target="_blank" rel="noopener noreferrer">개인정보 처리 안내</a>'
    f'<a href="{README}#sources" target="_blank" rel="noopener noreferrer">출처·라이선스</a>'
)


@lru_cache
def svg_uri(name: str) -> str:
    payload = b64encode((ASSETS / name).read_bytes()).decode("ascii")
    return f"data:image/svg+xml;base64,{payload}"


def begin_check(examples: bool = False):
    st.session_state.update(step=1, scroll_on_show=True)
    if examples:
        st.session_state.scroll_to_examples = True


def _progress(step: int) -> str:
    links = []
    for index, name in enumerate(STEPS, 1):
        state = "current" if index == step else "done" if index < step else ""
        current = ' aria-current="step"' if state == "current" else ""
        mark = "✓" if state == "done" else str(index)
        links.append(
            f'<li class="{state}"{current}><span class="step-number">{mark}</span><span>{name}</span></li>'
        )
    return f'<nav class="receipt-nav" aria-label="급여 점검 진행 단계"><ol>{"".join(links)}</ol></nav>'


def top_bar(step: int):
    logo = svg_uri("logo-horizontal.svg")
    st.html(f'<style>:root{{--brand-logo:url("{logo}");}}</style>')
    with st.container(key="app-bar"):
        brand, middle, action = st.columns([1, 3, 1], vertical_alignment="center")
        brand.button("받았수당", key="brand-home", type="tertiary", on_click=reset)
        if step == 0:
            middle.html(
                '<nav class="receipt-nav landing-nav" aria-label="서비스 둘러보기">'
                '<a href="#service-intro">서비스 소개</a><a href="#how-it-works">이용 방법</a>'
                '<a href="#privacy-by-design">안심하고 쓰는 이유</a></nav>'
            )
            action.button("점검 시작하기", key="begin-check", type="primary", on_click=begin_check)
        else:
            middle.html(_progress(step))
            action.button("처음으로", key="restart", on_click=reset)


def _footer_row():
    disclaimer, links = st.columns([3, 2], vertical_alignment="center")
    disclaimer.caption(DISCLAIMER)
    links.html(f'<nav class="footer-links" aria-label="서비스 안내">{FOOTER_LINKS}</nav>')


def footer(step: int):
    if step == 0:
        with st.container(key="landing-footer"):
            st.html(
                f'<div class="footer-brand"><img src="{svg_uri("logo-horizontal.svg")}" alt="받았수당">'
                '<span>2026 대한민국 청년 CAREER FAIR AI 해커톤 출품작 · [팀명]</span>'
                '<a href="https://www.law.go.kr/" target="_blank" rel="noopener noreferrer">'
                '법령 출처: 국가법령정보센터</a></div>'
            )
            _footer_row()
    else:
        with st.bottom:
            with st.container(key="legal-footer"):
                _footer_row()


def reset():
    chat_session = st.session_state.get(SESSION_KEY)
    st.session_state.clear()
    if chat_session is not None:
        chat_session.clear_conversation()
        st.session_state[SESSION_KEY] = chat_session
    st.session_state.scroll_on_show = True


def reset_scroll():
    # This static script never receives sample or user input.
    st.html(
        """<script>
        requestAnimationFrame(() => document.querySelector('[data-testid="stMain"]')?.scrollTo(0, 0));
        </script>""",
        unsafe_allow_javascript=True,
    )

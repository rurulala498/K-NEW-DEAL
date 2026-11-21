import streamlit as st

STEPS = ("서류 올리기", "내용 확인", "점검 결과")
DISCLAIMER = "법률 자문이 아니며 최종 판단은 관계 기관에서 이루어집니다."


def top_bar(step: int):
    with st.container(key="app-bar"):
        navigation, reset_column = st.columns([8, 2], vertical_alignment="center")
        marks = ("①", "②", "③")
        links = []
        for number, (mark, title) in enumerate(zip(marks, STEPS), start=1):
            active = ' class="active" aria-current="step"' if number == step else ""
            links.append(f"<li{active}><span>{mark}</span> {title}</li>")
        navigation.html(
            '<nav class="receipt-nav" aria-label="급여 점검 진행 단계">'
            '<strong class="receipt-logo">받았수당<span class="receipt-dot"></span></strong>'
            f'<ol>{"".join(links)}</ol></nav>'
        )
        reset_column.button("다시 해보기", key="restart", icon=":material/restart_alt:",
                     on_click=reset, width="stretch")


def footer():
    with st.bottom:
        with st.container(key="legal-footer"):
            st.caption(DISCLAIMER)


def reset():
    st.session_state.clear()
    st.session_state.scroll_on_show = True


def reset_scroll():
    # This static script never receives sample or user input.
    st.html(
        """<script>
        requestAnimationFrame(() => document.querySelector('[data-testid="stMain"]')?.scrollTo(0, 0));
        </script>""",
        unsafe_allow_javascript=True,
    )

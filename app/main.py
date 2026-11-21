import sys
from pathlib import Path

import streamlit as st

# Streamlit Cloud initially resolves modules relative to this script.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.components.shell import footer, reset_scroll, top_bar
from app.steps.result import show_result
from app.steps.review import review_step
from app.steps.upload import upload_step
from engine import run_engine

STYLE = Path(__file__).with_name("style.css")


def main():
    st.set_page_config(page_title="받았수당 · 내 급여 영수증", layout="wide")
    st.html(STYLE)
    step = st.session_state.get("step", 1)
    top_bar(step)
    if st.session_state.pop("scroll_on_show", False):
        reset_scroll()

    if step == 1:
        draft = upload_step()
        if draft:
            st.session_state.pop("extraction_error", None)
            st.session_state.update(step=2, draft=draft, scroll_on_show=True)
            st.rerun()
    elif step == 2:
        draft = st.session_state.draft
        if draft.notice:
            st.warning(draft.notice)
            if st.button("서류 다시 선택하기", key="reselect-documents"):
                st.session_state.pop("draft", None)
                st.session_state.update(step=1, scroll_on_show=True)
                st.rerun()
        confirmed = review_step(draft.sample_name, draft.extraction, texts=draft.texts)
        if confirmed is not None:
            st.session_state.pop("draft", None)
            st.session_state.update(step=3, confirmed=confirmed, result=run_engine(confirmed), scroll_on_show=True)
            st.rerun()
    else:
        show_result(st.session_state.result, st.session_state.confirmed)

    footer()


if __name__ == "__main__":
    main()

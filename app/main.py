import sys
from dataclasses import replace
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
from llm.documents import DocumentTexts
from rag.store import default_store

STYLE = Path(__file__).with_name("style.css")


def main():
    st.set_page_config(page_title="받았수당 · 내 급여 영수증", layout="wide")
    st.html(STYLE)
    store = default_store()
    step = st.session_state.get("step", 1)
    top_bar(step)
    if st.session_state.pop("scroll_on_show", False):
        reset_scroll()

    if step == 1:
        adding_payslip = st.session_state.get("adding_payslip", False)
        draft = upload_step(adding_payslip=adding_payslip)
        if draft:
            if adding_payslip:
                saved = st.session_state.confirmed.model_dump(mode="json", include={"contract", "extra_answers"})
                draft = replace(
                    draft,
                    extraction={**draft.extraction, **saved, "documents_provided": {"contract": True, "payslip": True}},
                    texts=DocumentTexts(st.session_state.get("contract_text"), draft.texts.payslip),
                )
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
            st.session_state.pop("adding_payslip", None)
            if not confirmed.documents_provided.payslip and draft.texts is not None:
                st.session_state.contract_text = draft.texts.contract
            else:
                st.session_state.pop("contract_text", None)
            st.session_state.update(
                step=3, confirmed=confirmed, result=run_engine(confirmed),
                result_sample=draft.sample_name, scroll_on_show=True,
            )
            st.rerun()
    else:
        if show_result(st.session_state.result, st.session_state.confirmed, store,
                       sample_name=st.session_state.get("result_sample")):
            st.session_state.update(step=1, adding_payslip=True, scroll_on_show=True)
            st.rerun()

    footer()


if __name__ == "__main__":
    main()

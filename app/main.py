import json
from pathlib import Path

import streamlit as st

from app.results import show_results
from app.review import review_form
from engine import run_engine

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
SAMPLES = {"정상 지급": "normal_case.json", "주휴수당 미지급": "review_case.json"}
DISCLAIMER = "법률 자문이 아니며 최종 판단은 관계 기관에서 이루어집니다."


def _choose_sample():
    st.header("자료 업로드")
    st.caption("업로드한 파일은 세션 동안 메모리에만 보관됩니다. 추출값 확인은 가상 샘플로 체험할 수 있습니다.")
    with st.container(border=True):
        contract, payslip = st.columns(2)
        contract.file_uploader("근로계약서", key="contract-upload")
        payslip.file_uploader("급여명세서", key="payslip-upload")
        sample = st.radio("체험할 샘플", list(SAMPLES), horizontal=True, key="sample-choice")
        if st.button("샘플로 해보기", key="load-sample", type="primary"):
            path = FIXTURES / SAMPLES[sample]
            extracted = json.loads(path.read_text(encoding="utf-8"))["extraction"]
            extracted["user_confirmed"] = False
            st.session_state["sample"] = extracted
            st.session_state["sample_name"] = sample
            st.session_state["revision"] = st.session_state.get("revision", 0) + 1
            st.session_state.pop("result", None)
            st.session_state.pop("chat_question", None)


def _footer(show_chat):
    with st.bottom:
        question = st.chat_input("도움 챗봇에 질문하기", key="help-chat") if show_chat else None
        st.caption(DISCLAIMER)
    if question:
        st.session_state["chat_question"] = question
    if show_chat and st.session_state.get("chat_question"):
        with st.chat_message("user"):
            st.text(st.session_state["chat_question"])
        with st.chat_message("assistant"):
            st.write("샘플 답변입니다. 예상 급여 명세에서 계산식을, 지급액 대조표에서 항목별 차액을 확인할 수 있어요.")


def main():
    st.set_page_config(page_title="받았수당", layout="wide")
    st.title("받았수당")
    st.caption("일한 만큼, 제대로 받았수당?")
    with st.container(border=True):
        st.write("시급제 근로자의 근로계약서와 급여명세서를 비교해 확인할 항목을 찾습니다.")
        st.write("주민등록번호, 계좌번호 등 개인정보를 가린 뒤 업로드하세요.")
        st.caption(DISCLAIMER)
        consented = st.checkbox("서비스 범위와 개인정보 안내를 확인하고 체험에 동의합니다.", key="consent")

    has_sample = False
    if consented:
        _choose_sample()
        has_sample = "sample" in st.session_state
        if has_sample:
            st.caption(f"선택한 샘플 · {st.session_state['sample_name']}")
            confirmed = review_form(st.session_state["sample"], st.session_state["revision"])
            if confirmed is not None:
                st.session_state["result"] = run_engine(confirmed)
            if "result" in st.session_state:
                show_results(st.session_state["result"])
    else:
        for key in ("sample", "sample_name", "result", "chat_question"):
            st.session_state.pop(key, None)

    _footer(has_sample)


if __name__ == "__main__":
    main()

from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from app.components.documents import receipt_preview
from llm.client import ExtractionRequestError, configuration_message, read_settings
from llm.documents import DocumentReadError, DocumentTexts, SUPPORTED_SUFFIXES, TEXT_REQUIRED, read_document
from llm.extract import ExtractionOutcome, blank_result, extract_documents, extract_sample
from llm.samples import load_sample, sample_texts

SAMPLES = {
    "normal_case": ("정상 지급", "예상 급여와 명세서 금액이 같은 사례예요."),
    "review_case": ("확인 필요", "시급과 주휴수당을 한 번 더 확인할 사례예요."),
}


@dataclass(frozen=True)
class ReviewDraft:
    extraction: dict
    sample_name: str | None = None
    texts: DocumentTexts | None = None
    notice: str | None = None


def _file_text(upload) -> str:
    return read_document(upload.name, upload.getvalue())


def _manual_text(upload) -> str:
    if upload is None:
        return ""
    try:
        return _file_text(upload)
    except DocumentReadError:
        return ""


def _draft(outcome: ExtractionOutcome, texts: DocumentTexts) -> ReviewDraft:
    notice = None
    if outcome.validation_failed:
        notice = "AI가 추출 결과의 형식을 맞추지 못했어요. 원문을 보며 빈칸에 직접 입력하거나 서류를 다시 읽을 수 있어요."
    return ReviewDraft(outcome.result.model_dump(mode="json"), texts=texts, notice=notice)


def _intro():
    with st.container(key="intro"):
        st.title("서류 올리기")
        st.markdown("근로계약서와 급여명세서를 올리면 받을 금액과 받은 금액을 비교할 수 있어요.")
        st.caption(
            "주민등록번호와 계좌번호 같은 개인정보는 가려 주세요. "
            "AI로 읽을 때는 추출할 텍스트를 OpenAI API로 보내고, 앱은 서류를 디스크에 저장하지 않아요."
        )
        if not st.session_state.get("consented"):
            st.button("동의하고 시작하기", key="consent", type="primary",
                      on_click=lambda: st.session_state.update(consented=True))


def _toggle_examples():
    st.session_state.examples_open = not st.session_state.get("examples_open", False)


def _sample_cards() -> ReviewDraft | None:
    st.caption("가상 근로계약서와 급여명세서로 체험할 수 있어요.")
    with st.container(key="sample-pair"):
        for (key, (title, description)), column in zip(SAMPLES.items(), st.columns(2)):
            with column:
                with st.container(border=True, key=f"sample-card-{key}"):
                    st.markdown(f"**{title}**")
                    st.caption(description)
                    if st.button(f"{title} 샘플 열기", key=f"sample-{key}", icon=":material/arrow_forward:",
                                 width="stretch"):
                        return ReviewDraft({**load_sample(key), "user_confirmed": False}, sample_name=key)
    return None


def _upload_pair():
    with st.container(key="upload-pair"):
        files = []
        for label, column in zip(("근로계약서", "급여명세서"), st.columns(2)):
            with column:
                with st.container(border=True, key=f"upload-card-{label}"):
                    files.append(st.file_uploader(label, key=f"upload-{label}"))
                    st.caption("텍스트 PDF · DOCX · TXT")
    return files


def upload_step() -> ReviewDraft | None:
    _intro()
    if not st.session_state.get("consented"):
        return None

    settings = read_settings()
    config_notice = configuration_message(settings)
    if config_notice:
        st.info(config_notice)

    contract, payslip = _upload_pair()
    attachments = (contract, payslip)
    unsupported = any(upload is not None and Path(upload.name).suffix.lower() not in SUPPORTED_SUFFIXES
                      for upload in attachments)
    if unsupported:
        st.warning(TEXT_REQUIRED)
    error = st.session_state.get("extraction_error")
    upload_ready = all(upload is not None for upload in attachments) and not unsupported and not config_notice
    if st.button("다시 시도" if error and error[0] == "upload" else "서류 읽기",
                 key="read-uploads", disabled=not upload_ready, type="primary"):
        try:
            with st.spinner("근로계약서와 급여명세서를 읽고 있어요…"):
                texts = DocumentTexts(_file_text(contract), _file_text(payslip))
                return _draft(extract_documents(texts, settings), texts)
        except (DocumentReadError, ExtractionRequestError) as problem:
            st.session_state.extraction_error = ("upload", str(problem))
            st.rerun()

    error = st.session_state.get("extraction_error")
    if error:
        st.warning(f"{error[1]} 다시 시도하거나, 직접 입력하거나, 아래 예시로 체험해 보세요.")
    if error or unsupported or (config_notice and (contract is not None or payslip is not None)):
        if st.button("직접 입력하기", key="manual-entry"):
            texts = (sample_texts(st.session_state.get("ai-sample-choice", "normal_case")) if error and error[0] == "sample"
                     else DocumentTexts(_manual_text(contract), _manual_text(payslip)))
            return ReviewDraft(
                blank_result().model_dump(mode="json"), texts=texts,
                notice="원문을 보며 빈칸에 직접 입력해 주세요.",
            )
    with st.container(key="sample-demo"):
        is_open = st.session_state.get("examples_open", False)
        st.button("예시 접기" if is_open else "예시로 체험하기", key="show-sample-demo",
                  type="tertiary", on_click=_toggle_examples)
        if is_open:
            chosen = _sample_cards()
            if chosen:
                return chosen
            with st.expander("예시 영수증 보기"):
                receipt_preview()


    if settings.show_dev_tools:
        with st.expander("샘플 서류를 AI로 직접 읽어 보기"):
            ai_choice = st.selectbox("AI로 읽을 가상 서류", list(SAMPLES),
                                     format_func=lambda name: SAMPLES[name][0], key="ai-sample-choice")
            if st.button("다시 시도" if error and error[0] == "sample" else "샘플 서류 읽기",
                         key="read-ai-sample", disabled=bool(config_notice)):
                try:
                    with st.spinner("가상 샘플 서류를 읽고 있어요…"):
                        return _draft(extract_sample(ai_choice, settings), sample_texts(ai_choice))
                except ExtractionRequestError as problem:
                    st.session_state.extraction_error = ("sample", str(problem))
                    st.rerun()
    return None

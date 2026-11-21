from dataclasses import dataclass
from html import escape
from pathlib import Path

import streamlit as st

from app.components.shell import scroll_to_example_cards, svg_uri
from llm.client import ExtractionRequestError, configuration_message, read_settings
from llm.documents import DocumentReadError, DocumentTexts, SUPPORTED_SUFFIXES, TEXT_REQUIRED, read_document
from llm.extract import ExtractionOutcome, blank_result, extract_documents, extract_sample
from llm.samples import load_sample, sample_texts
from schemas.extraction import DocumentsProvided

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


def _manual_text(upload) -> str | None:
    if upload is None:
        return None
    try:
        return _file_text(upload)
    except DocumentReadError:
        return ""


def _draft(outcome: ExtractionOutcome, texts: DocumentTexts, *, sample_name: str | None = None) -> ReviewDraft:
    notice = None
    if outcome.validation_failed:
        notice = "AI가 추출 결과의 형식을 맞추지 못했어요. 원문을 보며 빈칸에 직접 입력하거나 서류를 다시 읽을 수 있어요."
    return ReviewDraft(outcome.result.model_dump(mode="json"), sample_name=sample_name, texts=texts, notice=notice)


PRIVACY_NOTICE = (
    "주민등록번호와 계좌번호 같은 개인정보는 가려 주세요. "
    "서류를 읽을 때는 추출할 텍스트를, 결과를 설명할 때는 필요한 숫자와 확인값을 OpenAI API로 보내요. "
    "앱은 서류를 디스크에 저장하지 않아요."
)


def _intro():
    with st.container(key="intro"):
        st.title("서류를 올려 주세요")
        st.caption("근로계약서만 있어도 점검할 수 있어요. 급여명세서를 함께 올리면 실제 받은 금액과 비교해요.")
        if not st.session_state.get("consented"):
            with st.container(border=True, key="upload-consent"):
                st.subheader("개인정보 처리 안내")
                st.caption(PRIVACY_NOTICE)
                st.button("동의하고 시작하기", key="consent", type="primary",
                          on_click=lambda: st.session_state.update(consented=True))


def _sample_cards() -> ReviewDraft | None:
    with st.container(key="sample-demo"):
        st.subheader("서류가 지금 없다면")
        with st.container(key="sample-pair"):
            for (key, (title, description)), column in zip(SAMPLES.items(), st.columns(2)):
                with column, st.container(border=True, key=f"sample-card-{key}"):
                    st.caption(description)
                    if st.button(f"{title} 예시", key=f"sample-{key}", width="stretch"):
                        return ReviewDraft({**load_sample(key), "user_confirmed": False}, sample_name=key)
    return None


def _remove_upload(version_key: str):
    st.session_state[version_key] = st.session_state.get(version_key, 0) + 1
    error = st.session_state.get("extraction_error")
    if error and error[0] == "upload":
        st.session_state.pop("extraction_error")


def _upload_heading(label: str, requirement: str, ready: bool) -> str:
    state = " ready" if ready else ""
    badge = "올림 완료" if ready else requirement
    color = "ok" if ready else "required" if requirement == "필수" else "missing"
    return (
        f'<div class="upload-card-heading{state}"><span class="status status-{color}">{badge}</span>'
        f'<h3>{escape(label)}</h3></div>'
    )


def _upload_file(label: str, requirement: str, *, saved: bool = False):
    with st.container(border=True, key=f"upload-card-{label}"):
        if saved:
            st.html(_upload_heading(label, requirement, ready=True))
            st.caption("확인한 계약서 내용을 그대로 사용해요.")
            return None

        version_key = f"upload-version-{label}"
        version = st.session_state.get(version_key, 0)
        widget_key = f"upload-{label}" + (f"-{version}" if version else "")
        heading = st.empty()
        upload = st.file_uploader(label, key=widget_key, label_visibility="collapsed")
        heading.html(_upload_heading(label, requirement, ready=upload is not None))
        if upload is not None:
            st.html(f'<p class="upload-file-name">{escape(upload.name)} · {upload.size / 1024:,.1f} KB</p>')
            st.button("삭제", key=f"remove-upload-{label}", type="tertiary", on_click=_remove_upload, args=(version_key,))
        return upload


def _upload_pair(adding_payslip: bool):
    with st.container(key="upload-pair"):
        contract_column, payslip_column = st.columns(2, gap="medium")
        with contract_column:
            contract = _upload_file("근로계약서", "필수", saved=adding_payslip)
        with payslip_column:
            payslip = _upload_file("급여명세서", "선택")
    return contract, payslip


def _processing_notes():
    with st.container(key="upload-helpers"):
        guide, privacy = st.columns(2, gap="medium")
        with guide, st.container(border=True, key="upload-guide-card"):
            st.subheader("이렇게 처리돼요")
            st.html("""
              <ol class="upload-guide">
                <li>AI가 서류에서 시급·근무 시간·지급액을 찾아요.</li>
                <li>읽은 값을 원문과 함께 보여 드려요. 직접 확인하고 고칠 수 있어요.</li>
                <li>확인한 값으로 계산 규칙이 금액을 계산해요.</li>
              </ol>
            """)
        with privacy, st.container(border=True, key="upload-privacy-card"):
            st.html(
                f'<div class="upload-privacy-heading"><img src="{svg_uri("seal.svg")}" alt="">'
                '<h3>개인정보를 가려 주세요</h3></div>'
            )
            st.caption(PRIVACY_NOTICE)


def _read_workspace(settings, *, adding_payslip: bool) -> ReviewDraft | None:
    config_notice = configuration_message(settings)
    if config_notice:
        st.info(config_notice)

    contract, payslip = _upload_pair(adding_payslip)
    st.caption("텍스트가 있는 PDF · Word(DOCX) · TXT 파일을 올릴 수 있어요. 사진이나 스캔본은 아직 읽지 못해요.")
    unsupported = any(upload is not None and Path(upload.name).suffix.lower() not in SUPPORTED_SUFFIXES
                      for upload in (contract, payslip))
    if unsupported:
        st.warning(TEXT_REQUIRED)
    elif adding_payslip:
        if payslip is None:
            st.caption("확인한 계약서는 그대로 두고 급여명세서를 올려 주세요.")
    elif contract is None and payslip is None:
        st.caption("근로계약서를 올려 주세요. 급여명세서도 함께 올리면 실제 받은 금액과 비교할 수 있어요.")
    elif contract is None:
        st.caption("근로계약서를 함께 올려 주세요. 계약서를 서면으로 받지 못했다면, 그 자체도 확인할 사항이에요.")
    elif payslip is None:
        st.caption("급여명세서 없이 계약서만 점검해요. 명세서를 함께 올리면 받은 금액과 비교할 수 있어요.")

    error = st.session_state.get("extraction_error")
    required_file = payslip if adding_payslip else contract
    upload_ready = required_file is not None and not unsupported and not config_notice
    if st.button("다시 시도" if error and error[0] == "upload" else "서류 읽고 점검하기",
                 key="read-uploads", disabled=not upload_ready, type="primary", width="stretch"):
        try:
            with st.spinner("올린 서류를 읽고 있어요…"):
                texts = DocumentTexts(
                    _file_text(contract) if contract is not None else None,
                    _file_text(payslip) if payslip is not None else None,
                )
                return _draft(extract_documents(texts, settings), texts)
        except (DocumentReadError, ExtractionRequestError) as problem:
            st.session_state.extraction_error = ("upload", str(problem))
            st.rerun()

    if error:
        next_steps = "다시 시도하거나 직접 입력해 보세요." if adding_payslip else (
            "다시 시도하거나, 직접 입력하거나, 아래 예시로 체험해 보세요."
        )
        st.warning(f"{error[1]} {next_steps}")
    failed_sample = not adding_payslip and contract is None and payslip is None and error and error[0] == "sample"
    if failed_sample or (required_file is not None and (error or unsupported or config_notice)):
        if st.button("직접 입력하기", key="manual-entry"):
            texts = (sample_texts(st.session_state.get("ai-sample-choice", "normal_case")) if failed_sample
                     else DocumentTexts(_manual_text(contract), _manual_text(payslip)))
            provided = DocumentsProvided(contract=texts.contract is not None, payslip=texts.payslip is not None)
            return ReviewDraft(
                blank_result(provided).model_dump(mode="json"), texts=texts,
                sample_name=st.session_state.get("ai-sample-choice", "normal_case") if failed_sample else None,
                notice="원문을 보며 빈칸에 직접 입력해 주세요.",
            )

    return None


def upload_step(*, adding_payslip: bool = False) -> ReviewDraft | None:
    _intro()
    if not st.session_state.get("consented"):
        return None

    settings = read_settings()
    with st.container(border=True, key="upload-workspace"):
        draft = _read_workspace(settings, adding_payslip=adding_payslip)
        if draft is not None:
            return draft

    if adding_payslip:
        if st.button("계약서 점검 결과로 돌아가기", key="cancel-add-payslip"):
            st.session_state.pop("adding_payslip", None)
            st.session_state.pop("extraction_error", None)
            st.session_state.update(step=3, scroll_on_show=True)
            st.rerun()
        return None

    _processing_notes()
    if st.session_state.pop("scroll_to_examples", False):
        scroll_to_example_cards()
    if chosen := _sample_cards():
        return chosen

    config_notice = configuration_message(settings)
    error = st.session_state.get("extraction_error")
    if settings.show_dev_tools:
        with st.expander("샘플 서류를 AI로 직접 읽어 보기"):
            ai_choice = st.selectbox("AI로 읽을 가상 서류", list(SAMPLES),
                                     format_func=lambda name: SAMPLES[name][0], key="ai-sample-choice")
            if st.button("다시 시도" if error and error[0] == "sample" else "샘플 서류 읽기",
                         key="read-ai-sample", disabled=bool(config_notice)):
                try:
                    with st.spinner("가상 샘플 서류를 읽고 있어요…"):
                        return _draft(extract_sample(ai_choice, settings), sample_texts(ai_choice), sample_name=ai_choice)
                except ExtractionRequestError as problem:
                    st.session_state.extraction_error = ("sample", str(problem))
                    st.rerun()
    return None

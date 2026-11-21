from collections import defaultdict
from html import escape
import re

import streamlit as st

from llm.documents import DocumentTexts
from llm.samples import render_sample_line, sample_document_lines, sample_value


def _marked(text: str, confidence: str) -> str:
    low = " mark-low" if confidence == "low" else ""
    return f'<mark class="sample-mark{low}">{escape(text)}</mark>'


def _sources(node):
    if isinstance(node, dict):
        if "confidence" in node:
            if node["value"] is not None and node.get("source_text"):
                yield node["source_text"], node["confidence"]
            yield from _sources(node["value"])
        else:
            if "days" in node and node.get("source_text"):
                cells = [cell for cell in node.values() if isinstance(cell, dict) and cell.get("value") is not None]
                yield node["source_text"], "low" if any(cell["confidence"] == "low" for cell in cells) else "high"
            for child in node.values():
                yield from _sources(child)
    elif isinstance(node, list):
        for child in node:
            yield from _sources(child)


def highlight_document(original: str, extraction: dict) -> str:
    changes = defaultdict(list)
    for quote, confidence in set(_sources(extraction)):
        # Highlight the exact quotations. A low-confidence citation overrides overlapping high ones.
        for match in re.finditer(re.escape(quote), original):
            changes[match.start()].append((confidence, 1))
            changes[match.end()].append((confidence, -1))

    fragments = []
    active = {"high": 0, "low": 0}
    start = 0
    for boundary in sorted(changes):
        if boundary > start:
            text = original[start:boundary]
            if active["low"] or active["high"]:
                fragments.append(_marked(text, "low" if active["low"] else "high"))
            else:
                fragments.append(escape(text))
        for confidence, delta in changes[boundary]:
            active[confidence] += delta
        start = boundary
    fragments.append(escape(original[start:]))
    return "".join(fragments)


def document_preview(
    sample: str | None, extraction: dict, texts: DocumentTexts | None = None, *, payslip_provided: bool = True,
):
    st.subheader("원본 서류 미리보기")
    if texts is None:
        st.caption("시연용 가상 서류예요. 주황색으로 표시한 값은 한 번 더 확인해 주세요.")
    else:
        st.caption("원문에서 찾은 근거를 표시했어요. 주황색 구절은 한 번 더 확인해 주세요.")

    tabs = st.tabs(["근로계약서", "급여명세서"]) if payslip_provided else [st.container()]
    if texts is not None:
        for tab, (section, original) in zip(tabs, texts._asdict().items()):
            with tab:
                if original:
                    annotated = highlight_document(original, extraction[section])
                    st.html(f'<pre class="sample-document uploaded-document">{annotated}</pre>')
                else:
                    st.caption("미리 볼 원문이 없어요. 확인한 내용을 오른쪽에 직접 적어 주세요.")
        return

    for tab, (heading, lines) in zip(tabs, sample_document_lines(sample).items()):
        with tab:
            paragraphs = "".join(
                f"<p>{render_sample_line(line, extraction, escape, _sample_mark)}</p>" for line in lines
            )
            st.html(f'<div class="sample-document"><strong>{escape(heading)}</strong>{paragraphs}</div>')


def _sample_mark(path: str, field: dict) -> str:
    return _marked(sample_value(path, field["value"]), field["confidence"])

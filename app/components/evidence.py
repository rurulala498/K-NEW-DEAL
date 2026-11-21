from html import escape

import streamlit as st

from rag.store import RagStore
from schemas.rag import LawRecord, TermRecord


def _source(url: str, checked_date) -> str:
    return (
        f'<p class="rag-source"><a href="{escape(url)}" target="_blank" rel="noopener noreferrer">'
        f'출처 보기</a> · 확인 날짜 {checked_date.isoformat()}</p>'
    )


def _law_content(law: LawRecord) -> str:
    return (
        f'<article class="rag-article"><h4>{escape(law.law_name)} {escape(law.article_no)}</h4>'
        f'<p>{escape(law.title)}</p>'
        f'<pre class="rag-original"><span>{escape(law.text)}</span></pre>'
        f'{_source(law.source_url, law.checked_date)}</article>'
    )


def _term_content(term: TermRecord) -> str:
    return (
        f'<details class="term-chip"><summary>{escape(term.term)} 뜻 보기</summary>'
        f'<div class="rag-detail"><p>{escape(term.easy_explanation)}</p>'
        f'{_source(term.source_url, term.checked_date)}</div></details>'
    )


def evidence_markup(store: RagStore, keys: tuple[str, ...], title: str) -> str:
    laws = tuple(law for key in keys if (law := store.get_law(key)) is not None)
    if laws:
        pending = '<p class="rag-pending">일부 근거 조문 준비 중</p>' if len(laws) < len(keys) else ""
        basis = (
            '<details class="term-chip evidence-law"><summary>근거 조문</summary>'
            f'<div class="rag-detail">{"".join(map(_law_content, laws))}{pending}</div></details>'
        )
    else:
        basis = '<span class="basis-chip">근거 조문 준비 중</span>'

    terms = {term.term: term for key in keys for term in store.get_terms(key)}
    if direct := store.get_term(title):
        terms = {direct.term: direct, **terms}
    term_chips = "".join(map(_term_content, terms.values()))
    if not term_chips:
        term_chips = '<span class="basis-chip">용어 설명 준비 중</span>'
    return f'<div class="review-chips evidence-chips">{basis}{term_chips}</div>'


def evidence_chips(store: RagStore, keys: tuple[str, ...], title: str):
    st.html(evidence_markup(store, keys, title))


def glossary(store: RagStore):
    if not store.terms:
        st.caption("용어 설명 준비 중")
        return
    for term in store.terms:
        st.html(_term_content(term))


def help_channels(store: RagStore):
    if not store.help_channels:
        return
    with st.container(border=True, key="official-help-panel"):
        st.subheader("더 확인하고 싶다면")
        for channel in store.help_channels:
            st.html(
                f'<article class="rag-help"><h4>{escape(channel.name)}</h4>'
                f'<p>{escape(channel.what_for)}</p><p>{escape(channel.contact)}</p>'
                f'{_source(channel.url, channel.checked_date)}</article>'
            )

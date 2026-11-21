from html import escape

import streamlit as st

from llm.explain import Explained
from rag.basis_map import CARD_TERM_ORDER, TERM_BASIS
from rag.store import RagStore
from schemas.explanation import TermExplanation
from schemas.rag import LawRecord


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


def _term_markup(term: TermExplanation, store: RagStore, explanation: Explained | None) -> str:
    official = store.get_term(term.term)
    prefix = f"terms.{term.term}."
    ai_fields = explanation.ai_fields if explanation else frozenset()
    lines, labels = [], []
    meaning = official.easy_explanation if official else term.general_meaning
    if meaning:
        lines.append(f"<p>{escape(meaning)}</p>")
        origin = "용어 사전" if official else (
            "AI 설명" if f"{prefix}general_meaning" in ai_fields else "기본 설명"
        )
        labels.append(f"뜻: {origin}")
    if situation := term.user_context.strip():
        lines.append(f"<p>{escape(situation)}</p>")
        origin = "AI 설명" if f"{prefix}user_context" in ai_fields else "기본 설명"
        labels.append(f"내 상황: {origin}")
    if official:
        lines.append(_source(official.source_url, official.checked_date))
    if not labels:
        return ""

    name = official.term if official else term.term
    footer = f'<p class="rag-source"><small>{" · ".join(labels)}</small></p>'
    return (
        f'<details class="term-chip"><summary>{escape(name)} 뜻 보기</summary>'
        f'<div class="rag-detail">{"".join(lines)}{footer}</div></details>'
    )


def _card_terms(store: RagStore, keys: tuple[str, ...], title: str,
                explanation: Explained | None) -> list[TermExplanation]:
    direct = store.get_term(title)
    direct_name = direct.term if direct else title
    available = set(keys)
    matches = {}
    for term in explanation.content.terms if explanation else ():
        official = store.get_term(term.term)
        name = official.term if official else term.term
        related = official.related_basis_keys if official else TERM_BASIS.get(name, ())
        if (name == direct_name or available.intersection(related)) and (
            official or term.general_meaning or term.user_context.strip()
        ):
            matches.setdefault(name, term)

    for key in keys:
        for official in store.get_terms(key):
            matches.setdefault(official.term, TermExplanation(
                term=official.term, general_meaning=official.easy_explanation, user_context="",
            ))
    if direct and direct_name not in matches:
        matches[direct_name] = TermExplanation(term=direct_name, general_meaning=direct.easy_explanation, user_context="")
    if title not in CARD_TERM_ORDER:
        ordered = sorted(matches, key=lambda name: name != direct_name)
    else:
        primary, preferred = CARD_TERM_ORDER[title]
        for name in preferred:
            if (official := store.get_term(name)) and official.term not in matches:
                matches[official.term] = TermExplanation(
                    term=official.term, general_meaning=official.easy_explanation, user_context="",
                )

        def rank(name: str) -> tuple[int, int]:
            official = store.get_term(name)
            if name in preferred:
                return 0, preferred.index(name)
            return (1, 0) if official and primary in official.related_basis_keys else (2, 0)

        ordered = sorted(matches, key=rank)
    return [matches[name] for name in ordered[:2]]


def evidence_markup(store: RagStore, keys: tuple[str, ...], title: str, explanation: Explained | None = None) -> str:
    laws = tuple(law for key in keys if (law := store.get_law(key)) is not None)
    if laws:
        pending = '<p class="rag-pending">일부 근거 조문 준비 중</p>' if len(laws) < len(keys) else ""
        basis = (
            '<details class="term-chip evidence-law"><summary>근거 조문</summary>'
            f'<div class="rag-detail">{"".join(map(_law_content, laws))}{pending}</div></details>'
        )
    else:
        basis = '<span class="basis-chip">근거 조문 준비 중</span>'

    terms = _card_terms(store, keys, title, explanation)
    term_chips = "".join(_term_markup(term, store, explanation) for term in terms)
    return f'<div class="review-chips evidence-chips">{basis}{term_chips}</div>'


def evidence_chips(store: RagStore, keys: tuple[str, ...], title: str, explanation: Explained):
    st.html(evidence_markup(store, keys, title, explanation))


def explanation_label(fields: frozenset[str], prefix: str, total: int, *, mixed: frozenset[str] = frozenset()) -> str:
    count = sum(name.startswith(prefix) for name in fields)
    if not count:
        return "기본 설명"
    if count == total and not any(name.startswith(prefix) for name in mixed):
        return "AI가 작성한 설명이에요"
    return "AI가 작성한 설명이에요 · 기본 설명 포함"


def explanation_terms(explanation: Explained, store: RagStore):
    if not explanation.content.terms:
        return
    with st.container(border=True, key="result-terms"):
        st.subheader("급여 용어 풀이")
        for term in explanation.content.terms:
            if markup := _term_markup(term, store, explanation):
                st.html(markup)


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

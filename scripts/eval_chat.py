import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import run_engine
from llm import chat, explain
from llm.chat_web import ChatClient
from llm.client import ExtractionRequestError, configuration_message, read_settings
from rag.store import default_store
from scripts.preview_explanations import confirmed_sample

QUESTIONS = (
    "주휴수당 받을 수 있어요?",
    "야간수당은 얼마나 더 받아요?",
    "5인 미만 사업장도 주휴수당 받아요?",
    "수습이라 시급이 깎였어요",
    "계약서를 안 썼어요",
    "명세서에 공제가 있어요",
    "휴게시간 안 줘도 돼요?",
    "소정근로시간이 뭐예요?",
    "퇴직금은 어떻게 계산해요?",
    "해고당했어요",
    "오늘 날씨 어때?",
    "내 주휴수당은 왜 0원이에요?",
)
MAX_CALLS = 15
TYPE_LABELS = {
    "term": "용어", "law": "법·기준", "personal_amount": "내 금액",
    "out_of_scope": "범위 밖", "hypothetical": "가정 계산",
}


class CallBudget:
    def __init__(self, client):
        self.client = client
        self.calls = 0

    def _request(self, call, *args):
        if self.calls >= MAX_CALLS:
            raise ExtractionRequestError("평가 API 요청 한도에 도달했어요.")
        self.calls += 1
        return call(*args)

    def read(self, *args):
        return self._request(self.client.read, *args)

    def search_official(self, question: str):
        return self._request(self.client.search_official, question)


def main() -> int:
    store = default_store()
    plans = [chat.plan_question(question, store) for question in QUESTIONS]
    print(f"RAG 검색 최소 점수 {chat.MIN_SEARCH_SCORE}점 · 연결은 용어의 근거 키로 찾은 자료")
    print("| 질문 | 유형 | 검색된 근거와 점수 |")
    print("| --- | --- | --- |")
    for question, plan in zip(QUESTIONS, plans):
        evidence = " · ".join(
            f"{source.label} ({source.score if source.score is not None else '연결'})" for source in plan.sources
        )
        print(f"| {question} | {TYPE_LABELS[plan.answer_type]} | {evidence or '자료 없음'} |")

    if os.getenv("RUN_LIVE") != "1":
        return 0
    settings = read_settings()
    if notice := configuration_message(settings):
        print(notice)
        return 1

    client = CallBudget(ChatClient(settings))
    extraction = confirmed_sample("review_case")
    context = explain.build_context(run_engine(extraction), extraction, store)
    session = chat.ChatSession()
    input_total = output_total = 0
    for question in QUESTIONS:
        reply = chat.ask(question, context, store, settings, session, client=client)
        print(f"\n질문 · {question}")
        print(reply.answer)
        for source in reply.sources:
            print(f"  근거 {source.id} · {source.label} · {source.record.source_url}")
        for entry in reply.calculations:
            print(f"  엔진 계산식 {entry.basis} · {entry.workplace_size} · {entry.row.period} · {entry.row.formula}")
        if reply.web:
            print(f"  참고 · 추가 확인 필요 · {reply.web.summary}")
            for source in reply.web.sources:
                print(f"    {source.title} · {source.url}")
        if reply.needs_official_check:
            print(f"  {chat.OFFICIAL_HELP}")
        verification = ", ".join(reply.issues) or "추가 문제 없음"
        print(f"  검증 {reply.validation} · {verification}")
        for index, usage in enumerate(reply.usage, 1):
            input_total += usage.input_tokens
            output_total += usage.output_tokens
            print(f"  응답 {index} · 입력 {usage.input_tokens:,} · 출력 {usage.output_tokens:,} 토큰")
    print(f"\n전체 API 요청 {client.calls}/{MAX_CALLS}회 · 입력 {input_total:,} · 출력 {output_total:,} 토큰")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

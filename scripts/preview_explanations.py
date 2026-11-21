import argparse
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import run_engine
from llm import explain
from llm.client import configuration_message
from llm.extract import blank_result
from llm.samples import load_sample
from rag.store import default_store
from schemas import ExtractionResult
from scripts.eval_extraction import expected_fields

CASES = ("normal_case", "review_case", "tricky", "contract_only")


def confirmed_sample(case: str) -> ExtractionResult:
    if case not in CASES:
        raise ValueError("등록된 가상 사례만 사용할 수 있어요.")
    data = load_sample(case if case in explain.SAMPLE_NAMES else "review_case")
    if case == "tricky":
        data.update(expected_fields("tricky").model_dump(mode="json"))
    elif case == "contract_only":
        data["payslip"] = blank_result().payslip.model_dump(mode="json")
        data["documents_provided"] = {"contract": True, "payslip": False}
    data["user_confirmed"] = True
    return ExtractionResult.model_validate(data)


def save_sample(case: str, context: explain.ExplanationContext, result: explain.Explained):
    if case not in explain.SAMPLE_NAMES:
        raise ValueError("화면에 있는 두 가상 샘플만 저장할 수 있어요.")
    record = {"cache_key": context.cache_key, "ai_fields": sorted(result.ai_fields),
              "mixed_fields": sorted(result.mixed_fields), "explanation": result.content.model_dump(mode="json")}
    path = explain.SAMPLE_REPORTS / f"{case}_explanation.json"
    with NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as pending:
        temporary = Path(pending.name)
        pending.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="가상 사례의 결과 설명과 대응 문구를 OpenAI로 생성합니다.")
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--save-samples", action="store_true", help="화면의 정상·확인 필요 샘플 설명 파일을 갱신합니다.")
    args = parser.parse_args()
    if os.getenv("RUN_LIVE") != "1":
        print("실제 호출은 RUN_LIVE=1일 때만 실행해요.")
        return 1
    settings = explain.read_settings()
    if notice := configuration_message(settings):
        print(notice)
        return 1

    failed = False
    for case in (args.case,) if args.case else CASES:
        extraction = confirmed_sample(case)
        context = explain.build_context(run_engine(extraction), extraction, default_store())
        result = explain.generate(context, settings)
        print(f"\n{case} · 검증 제외 문장 {result.rejected_sentences}개 · 형식 실패 {result.format_failures}회")
        for index, tokens in enumerate(result.usage, 1):
            print(f"응답 {index} · 입력 {tokens.input_tokens:,} · 출력 {tokens.output_tokens:,} 토큰")
        if not result.ai_fields:
            failed = True
            print("AI가 작성한 유효한 문장이 없어 기본 설명을 출력해요.")
        print(result.content.model_dump_json(indent=2))
        if args.save_samples and case in explain.SAMPLE_NAMES and result.ai_fields:
            save_sample(case, context, result)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())

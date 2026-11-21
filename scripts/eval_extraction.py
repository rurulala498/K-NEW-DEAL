import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm.client import ExtractionRequestError, configuration_message, read_settings
from llm.documents import DocumentTexts, read_document
from llm.extract import DocumentExtraction, extract_documents

FIXTURES = ROOT / "tests" / "fixtures"
DOCS = FIXTURES / "docs"
CASES = ("normal", "review", "tricky")
EVAL_CASES = (*CASES, "schedule")
MISSING = object()


def read_fixture_pair(case: str) -> DocumentTexts:
    if case == "schedule":
        path = DOCS / "schedule_contract.pdf"
        return DocumentTexts(read_document(path.name, path.read_bytes()))
    return DocumentTexts(*[
        read_document(path.name, path.read_bytes())
        for role in ("contract", "payslip")
        for path in (DOCS / f"{case}_{role}.pdf",)
    ])


def expected_fields(case: str) -> DocumentExtraction:
    if case in ("tricky", "schedule"):
        path = DOCS / f"{case}_expected.json"
        expected = json.loads(path.read_text(encoding="utf-8"))
    else:
        path = FIXTURES / f"{case}_case.json"
        expected = json.loads(path.read_text(encoding="utf-8"))["extraction"]
    model = DocumentExtraction.model_validate(expected)
    if not model.contract.work_schedule.value and (entries := model.contract.schedule_entries()):
        model.contract.work_schedule.value = entries
    return model


def field_values(node, path=""):
    """Compare field values, including nulls and missing or extra list rows."""
    if not isinstance(node, dict):
        yield path, node
    elif "confidence" in node:
        value = node["value"]
        if path in ("contract.special_terms", "payslip.items", "contract.work_schedule"):
            yield f"{path}.개수", None if value is None else len(value)
            for index, row in enumerate(value or [], start=1):
                record = {key: cell for key, cell in row.items() if key != "source_text"}
                yield from field_values(record, f"{path}.{index}")
        else:
            yield path, value
    else:
        for name, child in node.items():
            yield from field_values(child, f"{path}.{name}" if path else name)


def main() -> int:
    parser = argparse.ArgumentParser(description="가상 서류의 필드 일치율과 OpenAI 토큰 사용량을 측정합니다.")
    parser.add_argument("--case", choices=EVAL_CASES, help="지정한 가상 사례만 측정합니다.")
    args = parser.parse_args()
    settings = read_settings()
    notice = configuration_message(settings)
    if notice:
        print(notice)
        return 1

    scores = defaultdict(lambda: [0, 0])
    failed = False
    for case in (args.case,) if args.case else EVAL_CASES:
        truth = dict(field_values(expected_fields(case).model_dump()))
        try:
            outcome = extract_documents(read_fixture_pair(case), settings)
        except ExtractionRequestError as error:
            print(f"{case} 요청 실패 · {error}")
            failed = True
            for field in truth:
                scores[field][1] += 1
            continue

        actual = dict(field_values(outcome.result.model_dump(include={"contract", "payslip"})))
        fields = truth.keys() | actual.keys()
        matches = 0
        for field in fields:
            correct = not outcome.validation_failed and truth.get(field, MISSING) == actual.get(field, MISSING)
            scores[field][0] += int(correct)
            scores[field][1] += 1
            matches += int(correct)
        status = "형식 오류로 입력값을 읽지 못했어요" if outcome.validation_failed else "응답 검증 완료"
        failed = failed or outcome.validation_failed
        print(f"{case} · {matches}/{len(fields)} 일치 · {status}")
        for index, usage in enumerate(outcome.usage, start=1):
            print(f"  응답 {index} · 입력 {usage.input_tokens:,} · 출력 {usage.output_tokens:,} 토큰")

    print("\n항목별 일치율")
    for field, (correct, total) in sorted(scores.items()):
        print(f"{field:<34} {correct}/{total}  {correct / total:.1%}")
    matches = sum(correct for correct, _ in scores.values())
    count = sum(total for _, total in scores.values())
    if count:
        print(f"전체 {matches}/{count}  {matches / count:.1%}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())

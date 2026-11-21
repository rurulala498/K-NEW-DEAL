import json
from collections.abc import Callable
from pathlib import Path
import re

from llm.documents import DocumentTexts

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_IDS = ("normal_case", "review_case")
TOKEN = re.compile(r"{{([\w.]+)}}")


def load_sample(sample_name: str) -> dict:
    if sample_name not in SAMPLE_IDS:
        raise ValueError("Unknown sample document")
    path = ROOT / "tests" / "fixtures" / f"{sample_name}.json"
    return json.loads(path.read_text(encoding="utf-8"))["extraction"]


def sample_document_lines(sample_name: str) -> dict[str, list[str]]:
    if sample_name not in SAMPLE_IDS:
        raise ValueError("Unknown sample document")
    path = ROOT / "data" / "samples" / "documents.json"
    return json.loads(path.read_text(encoding="utf-8"))[sample_name]


def _field(path: str, extraction: dict) -> dict:
    node = extraction
    for part in path.split("."):
        if isinstance(node, dict) and "confidence" in node:
            node = node["value"]
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def sample_value(path: str, value) -> str:
    if value is None:
        return "정보 없음"
    if path.endswith((".hourly_wage", ".amount", ".total_paid")):
        return f"{value:,}원"
    if isinstance(value, list):
        return ", ".join(value)
    return str(value)


def render_sample_line(
    line: str, extraction: dict, literal: Callable[[str], str], value: Callable[[str, dict], str],
) -> str:
    return "".join(
        literal(part) if index % 2 == 0 else value(part, _field(part, extraction))
        for index, part in enumerate(TOKEN.split(line))
    )


def sample_texts(sample_name: str) -> DocumentTexts:
    extraction = load_sample(sample_name)
    documents = sample_document_lines(sample_name)
    return DocumentTexts(*[
        "\n".join([title, *(
            render_sample_line(line, extraction, str, lambda path, field: sample_value(path, field["value"]))
            for line in lines
        )])
        for title, lines in documents.items()
    ])

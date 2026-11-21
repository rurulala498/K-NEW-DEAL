import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.basis_map import BASIS_KEYS
from rag.store import DATA_DIR, DataIssue, RagStore, load_store


def check_rows(store: RagStore) -> list[DataIssue]:
    missing = [
        DataIssue("누락 키", "laws.json", None, "basis_key", key)
        for key in BASIS_KEYS
        if store.get_law(key) is None
    ]
    return missing + list(store.issues)


def _table_cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def format_table(rows: list[DataIssue]) -> str:
    lines = ["| 점검 | 파일 | 항목 번호 | 필드 | 상세 |", "| --- | --- | ---: | --- | --- |"]
    if not rows:
        lines.append("| 통과 | - | - | - | 점검 대상에 문제가 없어요. |")
    for row in rows:
        values = (row.kind, row.file, row.row or "-", row.field or "-", row.detail)
        lines.append("| " + " | ".join(map(_table_cell, values)) + " |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 자료의 형식과 필수 근거 키를 점검합니다.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="점검할 JSON 파일이 있는 폴더")
    args = parser.parse_args()
    store = load_store(args.data_dir)
    rows = check_rows(store)
    print(f"사용 가능 · 조문 {len(store.laws)}건 · 용어 {len(store.terms)}건 · 상담 경로 {len(store.help_channels)}건")
    print(format_table(rows))
    return int(bool(rows))


if __name__ == "__main__":
    raise SystemExit(main())

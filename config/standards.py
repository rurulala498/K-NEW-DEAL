import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal


def load_standards(year: int) -> dict[str, Any] | Literal["등록되지 않은 기준 연도"]:
    """연도별 파일의 기준값과 검증 상태를 읽습니다."""
    path = Path(__file__).with_name(f"standards_{year}.json")
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source, parse_float=Decimal)
    except FileNotFoundError:
        return "등록되지 않은 기준 연도"

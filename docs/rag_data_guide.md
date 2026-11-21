# RAG 데이터 작성 안내

앱에서는 `data/laws.json`, `data/terms.json`, `data/help_channels.json`을 UTF-8 JSON 목록으로 읽습니다. 출처 URL에는 공식 자료의 HTTP 또는 HTTPS 링크를 적고, 날짜는 `YYYY-MM-DD`로 기록합니다.

## 조문 및 공식 기준

| 필드 | 작성 방법 |
| --- | --- |
| `basis_key` | `rag/basis_map.py`에 등록된 연결 키를 적습니다. 같은 조문을 서로 다른 키로 연결할 수 있습니다. |
| `law_name`, `article_no`, `title` | 자료의 이름, 조항 또는 고시 식별자, 제목을 적습니다. |
| `text` | 확인한 원문을 그대로 적습니다. JSON 문자열의 줄바꿈은 `\n`으로 기록합니다. 팀 요약 자료는 이름이나 제목에 요약임을 표시합니다. |
| `source_url`, `checked_date` | 출처 주소와 출처를 확인한 날짜를 적습니다. |

아래는 형식만 보여 주는 예시이며, 법령이나 공식 기준을 인용한 내용이 아닙니다.

```json
{
  "basis_key": "minimum_wage",
  "law_name": "예시용 문서",
  "article_no": "예시 항목",
  "title": "형식 예시",
  "text": "예시 첫 줄\n예시 둘째 줄",
  "source_url": "https://example.com/law",
  "checked_date": "2026-01-01"
}
```

`laws_upcoming.json`은 시행 예정 자료를 보관하는 파일입니다. 조문 형식에 시행일을 나타내는 `effective_date`를 추가합니다. 아래는 보관 형식 예시입니다.

```json
{
  "basis_key": "break_time",
  "law_name": "예시용 시행 예정 문서",
  "article_no": "예시 항목",
  "title": "시행일 작성 예시",
  "text": "시행 예정 자료를 적는 형식 예시",
  "source_url": "https://example.com/upcoming",
  "checked_date": "2026-01-01",
  "effective_date": "2027-01-01"
}
```

## 용어

| 필드 | 작성 방법 |
| --- | --- |
| `term`, `aliases` | 대표 이름과 대체 표현의 목록을 적습니다. 다른 표현이 없으면 빈 목록을 사용합니다. |
| `easy_explanation` | 확인한 출처를 바탕으로 쉬운 설명을 적습니다. |
| `related_basis_keys` | 연결할 근거 키 목록을 적습니다. |
| `source_url`, `checked_date` | 출처 주소와 확인 날짜를 적습니다. |

아래는 설명이 들어갈 자리를 보여 주는 예시입니다.

```json
{
  "term": "예시용 용어",
  "aliases": ["예시용 다른 표현"],
  "easy_explanation": "확인한 출처에 따라 설명을 적는 예시입니다.",
  "related_basis_keys": ["minimum_wage"],
  "source_url": "https://example.com/term",
  "checked_date": "2026-01-01"
}
```

## 상담 경로

| 필드 | 작성 방법 |
| --- | --- |
| `name`, `contact` | 기관 이름과 확인한 연락 방법을 적습니다. |
| `url`, `what_for`, `checked_date` | 공식 안내 링크, 상담 목적, 확인 날짜를 적습니다. |

아래는 연락 방법의 작성 위치를 보여 주는 예시입니다.

```json
{
  "name": "예시용 상담 창구",
  "contact": "확인한 연락 방법을 적는 예시",
  "url": "https://example.com/help",
  "what_for": "상담 목적을 적는 예시",
  "checked_date": "2026-01-01"
}
```

저장소 최상위에서 아래 명령으로 목록 형식, 비어 있는 필드, 중복, 누락된 연결 키를 점검합니다.

```powershell
python scripts/check_rag_data.py
```

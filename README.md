# <img src="app/assets/app-icon.svg" width="36" height="36" alt=""> 받았수당

**일한 만큼, 제대로 받았수당?**

시급제 청년 근로자가 근로계약서의 조건을 확인하고, 받을 것으로 예상되는 급여와 실제 지급액을 비교하는 해커톤 프로젝트입니다.

2026 대한민국 청년 CAREER FAIR AI 해커톤 출품작입니다.

## 웹에서 체험하기

[받았수당 앱 바로 열기](https://k-new-deal.streamlit.app/)

1. 서비스 소개 화면에서 **예시로 먼저 보기**를 누릅니다.
2. 개인정보 안내를 읽고 **동의하고 시작하기**를 누른 뒤 **정상 지급** 또는 **확인 필요** 샘플을 엽니다.
3. 읽은 값을 확인한 뒤 **결과 보기**를 눌러 급여 영수증을 확인합니다.

저장된 두 샘플은 API 키 없이 체험할 수 있습니다.

| 예시 | 확인할 결과 |
| --- | --- |
| 정상 지급 | 월 예상 급여 1,094,940원 |
| 확인 필요 | 계약 시급 기준 1,042,800원, 최저임금 기준으로 계산하면 1,076,170원, 실제 지급액과의 차이 207,170원 |

## 내 서류로 점검하기

1. 서비스 소개 화면에서 **내 급여 점검하기**를 눌러 동의하고, 텍스트가 포함된 PDF, DOCX 또는 TXT 근로계약서를 올린 뒤 **서류 읽고 점검하기**를 누릅니다. 급여명세서를 함께 올리면 실제 지급액도 비교합니다.
2. 원문에서 색으로 표시된 근거와 추출값을 확인합니다. **근무 시간** 표에서 요일·시작·종료·휴게를 편집할 수 있습니다. 자정을 넘기는 종료는 **익일**로 표시합니다. 근무표 합계와 계약서의 주 소정근로시간이 다르면 확인 안내가 나옵니다.
3. 상시근로자 수가 기록된 계약서는 사업장 규모 선택에 추천값이 표시됩니다. 값을 확인한 뒤 **결과 보기**를 누릅니다.
4. 급여 영수증에서 예상 금액, 지급액 차이와 원인을 확인합니다. 계약서만 올린 경우 지급액은 **비교 안 함**으로 표시됩니다. **급여명세서 추가하기**를 누르면 확인한 계약서 내용을 유지한 채 명세서를 추가할 수 있습니다.

결과 카드에서는 근거 조문과 용어의 뜻, 내 상황에 맞춘 설명을 펼쳐 볼 수 있습니다. **확인 필요** 항목이 있으면 사장님께 보낼 확인 질문과 수정 요청 문구를 고치거나 말투를 선택해 다시 쓸 수 있습니다. 도움 챗봇은 확인한 값과 공식 근거를 바탕으로 답변합니다. 가정한 값으로 다시 계산하려면 내용 확인 화면으로 돌아갑니다.

요일별 근무를 확인하려면 [가상 근로계약서 PDF](tests/fixtures/docs/schedule_contract.pdf)만 올려 보세요. 5인 이상 기준으로 금요일의 야간 휴게 30분을 제외하면 야간근로는 2시간, 예상 월 급여는 1,249,188원입니다.

## 로컬에서 실행하기

Python 3.11 이상을 사용합니다. 저장소 최상위에서 실행한 뒤 터미널에 표시된 로컬 주소를 엽니다.

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app/main.py --server.address 127.0.0.1
```

AI로 서류를 읽거나 결과 설명을 생성하려면 [환경 변수 예시](.env.example)를 참고해 로컬 `.env`에 `OPENAI_API_KEY`와 Structured Outputs를 지원하는 `OPENAI_MODEL`을 설정합니다. `ENABLE_WEB_SEARCH=true`를 설정하면 도움 챗봇이 공식 사이트 검색을 함께 사용합니다. `SHOW_DEV_TOOLS=true`를 설정하면 가상 샘플을 AI로 직접 읽는 개발용 도구가 표시됩니다.

Streamlit Community Cloud에서는 앱 설정의 **Secrets**에 같은 환경 변수를 설정합니다. 배포 대상은 이 저장소의 `master` 브랜치와 실행 파일 `app/main.py`입니다. [Streamlit Secrets 설정 안내](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)

## 계산과 데이터 처리

- 규칙 엔진이 계약과 급여를 점검하고 숫자를 계산합니다. 기본급과 주휴수당은 확인한 주 소정근로시간을 사용합니다. 근무표가 있으면 요일별 휴게와 연장·야간 시간, 야간과 겹치는 휴게 구간을 반영합니다. 최저임금보다 낮은 시급은 계약 시급과 최저임금 기준을 함께 계산합니다.
- AI는 서류에서 항목을 추출하고 엔진 결과를 설명하며 대응 문구를 작성합니다. 결과 화면에 기본 설명을 먼저 표시하고, AI 설명이 준비되면 출처를 구분해 표시합니다. 조문과 용어 사전은 팀이 정리한 데이터를 사용합니다. [RAG 데이터 작성 안내](docs/rag_data_guide.md)
- 도움 챗봇은 확인한 값, 엔진 결과, 팀이 정리한 조문·용어를 근거로 답합니다. 자료에 없는 질문은 웹 검색을 켠 경우 국가법령정보센터·고용노동부·찾기쉬운 생활법령정보·최저임금위원회 사이트만 검색해 "참고 · 추가 확인 필요"로 표시하며, 검색 결과는 금액 계산에 쓰지 않습니다. 질문은 세션당 20회, 웹 검색은 3회로 제한합니다.
- 추가 확인이 필요하면 공식 상담 경로 6곳(고용노동부 고객상담센터 1350, 청소년근로권익센터 1644-3119, AI 노동법 상담, 임금체불 진정, 대한법률구조공단 132, 근로복지공단 대지급금 안내)을 결과 화면과 챗봇에서 안내합니다.
- 서류는 세션 메모리에서 처리합니다. 서류를 읽을 때는 추출할 텍스트가 OpenAI API로 전송됩니다. 결과 설명에는 필요한 엔진 값과 확인한 일부 항목을 전달합니다. 주민등록번호·계좌번호 등 개인을 식별할 수 있는 정보는 업로드 전에 가려 주세요.

받았수당은 법률 자문 서비스가 아닙니다. 최종 판단은 관계 기관에서 이루어집니다.

<a name="privacy"></a>
## 개인정보 처리 안내

서류는 앱의 세션 메모리에서 처리하며 디스크에 저장하지 않습니다. AI 추출에는 서류의 텍스트가 전송되고, 설명과 챗봇에는 필요한 확인값과 엔진 결과가 OpenAI API로 전달됩니다. 업로드 전 주민등록번호와 계좌번호를 가리고 질문에는 개인정보를 적지 마세요.

<a name="sources"></a>
## 출처·라이선스

법령과 용어의 출처 및 확인일은 결과의 각 근거에서 확인할 수 있습니다. [RAG 데이터 작성 안내](docs/rag_data_guide.md)

### 데이터

| 자료 | 출처 | 사용 위치 |
| --- | --- | --- |
| 근로기준법·최저임금법 조문 | [국가법령정보센터](https://www.law.go.kr/) | 점검 근거, 조문 원문 표시 |
| 2026년 적용 최저임금 고시 | 고용노동부 고시, [최저임금위원회](https://www.minimumwage.go.kr/) | 최저임금 기준값 |
| 노동 용어 28개 | 고용노동부·찾기쉬운 생활법령정보·국가법령정보센터 자료를 팀이 정리 | 용어 풀이, 챗봇 |
| 공공 상담 경로 6곳 | 각 기관 공식 안내 (`data/help_channels.json`에 링크·확인일 기록) | 결과 화면, 챗봇 |
| 가상 근로계약서·급여명세서 | 팀 직접 제작 (실제 개인정보 없음) | 예시 체험, 테스트, 추출 평가 |

### 오픈소스

| 이름 | 라이선스 | 용도 |
| --- | --- | --- |
| [Streamlit](https://github.com/streamlit/streamlit) | Apache-2.0 | 웹 화면 |
| [Pydantic](https://github.com/pydantic/pydantic) | MIT | 데이터 형식 검증 |
| [OpenAI Python SDK](https://github.com/openai/openai-python) | Apache-2.0 | API 연동 |
| [pypdf](https://github.com/py-pdf/pypdf) | BSD-3-Clause | PDF 텍스트 추출 |
| [python-docx](https://github.com/python-openxml/python-docx) | MIT | DOCX 텍스트 추출 |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | BSD-3-Clause | 환경 변수 관리 |
| [pytest](https://github.com/pytest-dev/pytest) | MIT | 자동 테스트 |
| [Pretendard](https://github.com/orioncactus/pretendard) | [SIL OFL 1.1](https://github.com/orioncactus/pretendard/blob/v1.3.9/LICENSE) | 화면 글꼴 |

템플릿·보일러플레이트는 사용하지 않았습니다. 로고와 화면 디자인은 팀이 제작했습니다.

### AI 모델·개발 도구

| 이름 | 용도 |
| --- | --- |
| OpenAI API `gpt-6.1-terra` | 서비스 내 서류 추출, 결과 설명, 대응 문구, 챗봇 답변 ([OpenAI 이용약관](https://openai.com/policies/)) |
| Codex (GPT-6.2 sol) | 기능 구현, 테스트 코드 작성 |
| Claude | 개발 요구사항 정리, 코드 검토, 화면·로고 시안, 문서 초안 |

## 테스트와 평가

```powershell
python -m pytest -q
python scripts/check_rag_data.py
```

실제 API를 사용하는 pytest는 기본적으로 건너뜁니다. 아래 평가는 실제 OpenAI API를 호출하며 API 키와 모델 설정이 필요합니다.

```powershell
python scripts/eval_extraction.py --case schedule
$env:RUN_LIVE = "1"
python scripts/preview_explanations.py
```

- **추출 평가**는 가상 서류의 예상값과 실제 추출값을 비교합니다. `--case schedule`은 요일별 근무표 계약서만 평가합니다. 옵션을 빼면 정상·확인 필요·까다로운 사례도 함께 평가합니다.
- **설명 미리보기**는 가상 사례의 설명과 대응 문구, 사용한 토큰 수, 검증에서 제외한 문장 수를 출력합니다. 화면의 두 예시 설명을 갱신하려면 `--save-samples`를 붙입니다.

## 코드 구성

| 경로 | 내용 |
| --- | --- |
| `app/` | Streamlit 화면과 근무표 편집기 |
| `engine/`, `schemas/`, `config/` | 급여 계산, 데이터 형식, 연도별 기준값 |
| `llm/`, `rag/` | 서류 추출·결과 설명, 공식 근거·용어 검색 |
| `data/` | 조문, 용어, 상담 경로, 저장된 예시 |
| `scripts/`, `tests/` | 평가 스크립트, 테스트와 가상 서류 |
| `docs/` | 기획·데이터 작성 안내 |

작업 기준은 [AGENTS.md](AGENTS.md)에 있습니다.

from collections import defaultdict
from decimal import Decimal
import re
from unicodedata import normalize

FORBIDDEN = re.compile(
    r"위반|불법|위법|적법|합법|체불|고소|처벌|고발|신고하겠|소송|확정|무효|반드시|법적으로|받아야\s*(?:합니다|해요)"
    r"|기준에\s*맞(?:습니다|아요|지\s*않)"
    r"|기준을\s*충족하지|지급\s*의무|제\s*\d+\s*조|https?://|www\.|\[[^\]]*\]\(|<[^>]+>"
)
PAYSLIP_LANGUAGE = re.compile(r"명세서|실제\s*(?:지급|받)|받은\s*금액|덜\s*받|미지급|지급액\s*대조")
PRIVATE_TEXT = re.compile(
    r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|https?://|www\.|"
    r"주민(?:등록)?번호|계좌|연락처|전화번호|주소|상호|사업장명|"
    r"(?:성명|이름|대표자|근로자|사업주|사용자|예금주|담당자)\s*[:：=]\s*\S|"
    r"(?:근로자|사업주|사용자|대표자|담당자)\s+[가-힣]{2,4}(?=\s|[.,]|$)|"
    r"(?:\+82[ .-]?)?(?:0?1[016789]|0[2-6]\d?)[ .-]+\d{3,4}[ .-]+\d{4}|"
    r"주식회사|유한회사|[（(]주[)）]|"
    r"\d{2,}-\d{2,}-(?!\d{1,2}(?:\D|$))\d+|\d{6}\s*-?\s*[1-4]\d{6}|\d{7,}"
)
DATE = r"\d{4}[-./]\s*\d{1,2}[-./]\s*\d{1,2}|\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일"
DATE_OR_NUMBER = re.compile(
    rf"(?P<date>{DATE})|(?P<clock>\d{{1,2}}:\d{{2}}(?::\d{{2}})?)|"
    r"(?P<number>[+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?:\s*(?P<unit>만\s*원|원|시간|분|주|일|월|년|시|명|인|건|개|%))?"
)


def private_text(text: str, workplace: str | None) -> bool:
    normalized = normalize("NFKC", text)
    compact = "".join(normalized.casefold().split())
    private_place = "".join(normalize("NFKC", workplace or "").casefold().split())
    return bool(PRIVATE_TEXT.search(normalized) or (private_place and private_place in compact))


def numeric_tokens(text: str):
    for match in DATE_OR_NUMBER.finditer(normalize("NFKC", text)):
        if date := match["date"]:
            yield "date", tuple(map(int, re.findall(r"\d+", date)))
        elif clock := match["clock"]:
            clock_parts = tuple(map(int, clock.split(":")))
            yield "clock", clock_parts[:2] if len(clock_parts) == 2 or clock_parts[2] == 0 else clock_parts
        else:
            value = Decimal(match["number"].replace(",", "").replace("−", "-"))
            unit = (match["unit"] or "").replace(" ", "")
            if unit == "만원":
                unit, value = "원", value * 10000
            if unit == "시":
                yield "clock", (value, 0)
            else:
                yield {"명": "인"}.get(unit, unit), value


class NumericFacts:
    def __init__(self):
        self.values = defaultdict(set)

    def add(self, value, unit: str = ""):
        if value is not None:
            self.values[unit].add(Decimal(value))

    def read(self, text: str):
        for unit, value in numeric_tokens(text):
            self.values[unit].add(value)

    def permits(self, text: str) -> bool:
        return all(value in self.values.get(unit, ()) for unit, value in numeric_tokens(text))


def sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?。])\s+|[\r\n]+", text.strip()) if part.strip()]


def safety_issue(text: str, facts: NumericFacts, *, payslip: bool, workplace: str | None) -> str | None:
    if len(text) > 1400:
        return "문장 길이 초과"
    if FORBIDDEN.search(normalize("NFKC", text)):
        return "금지 표현"
    # The salutation is public wording, never a confirmed person's name.
    without_money = re.sub(r"\d[\d,.]*\s*원", "", text)
    if private_text(without_money.replace("사장님", ""), workplace):
        return "개인정보"
    if not payslip and PAYSLIP_LANGUAGE.search(text):
        return "명세서 없이 지급액 언급"
    return None if facts.permits(text) else "입력에 없는 숫자"


def is_safe(text: str, facts: NumericFacts, *, payslip: bool, workplace: str | None) -> bool:
    return safety_issue(text, facts, payslip=payslip, workplace=workplace) is None


def safe_style_request(text: str) -> list[str]:
    """Pass known style choices only. The original request stays in the UI."""
    return [style for options, style in (
        (("카톡", "문자", "메시지"), "메신저로 보낼 수 있게"),
        (("짧", "간결", "간단"), "짧고 간결하게"),
        (("부드럽", "완곡", "조심"), "부드럽게"),
        (("정중", "공손"), "정중하게"),
        (("인사",), "짧은 인사를 포함해"),
    ) if any(option in text for option in options)]

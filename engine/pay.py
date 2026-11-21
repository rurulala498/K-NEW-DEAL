from datetime import time
from decimal import ROUND_HALF_UP, Decimal

from schemas.engine import ExpectedPayItem, ExpectedPayScenario, Limitation, PayBasis
from schemas.extraction import Contract, KnownWorkplaceSize

ZERO = Decimal(0)
SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 24 * SECONDS_PER_HOUR

BASE_PAY = "기본급"
HOLIDAY_PAY = "주휴수당"
OVERTIME_PAY = "연장 가산"
NIGHT_PAY = "야간 가산"
TOTAL_PAY = "합계"


def _weekly(item, amount, formula, condition, year):
    return ExpectedPayItem(
        item=item,
        period="weekly",
        amount=amount,
        formula=formula,
        applied_condition=condition,
        standards_year=year,
    )


def _base_pay(wage, hours, year, limitations):
    if wage is None or hours is None:
        limitations.append(Limitation(item=BASE_PAY, reason="시급 또는 주 소정근로시간이 없습니다."))
        return None
    return _weekly(BASE_PAY, wage * hours, f"{wage} × {hours}", "주 소정근로시간 적용", year)


def _holiday_pay(wage, hours, standards, year, limitations):
    minimum = standards["weekly_holiday_min_hours"]["value"]
    if hours is not None and hours < minimum:
        return _weekly(HOLIDAY_PAY, ZERO, "0", f"주 소정근로시간 {minimum}시간 미만", year)
    if wage is None or hours is None:
        limitations.append(Limitation(item=HOLIDAY_PAY, reason="시급 또는 주 소정근로시간이 없습니다."))
        return None
    rule = standards["weekly_holiday_hours"]
    full_time, cap = rule["value"]["full_time_weekly"], rule["value"]["max_hours"]
    holiday_hours = min(hours / full_time * cap, Decimal(cap))
    if not rule["verified"]:
        limitations.append(Limitation(item=HOLIDAY_PAY, reason="기준 확인 필요"))
    return _weekly(
        HOLIDAY_PAY, holiday_hours * wage,
        f"min({hours} ÷ {full_time} × {cap}, {cap}) × {wage}",
        f"주 {minimum}시간 이상, 소정근로일 개근 가정, 최대 {cap}시간", year,
    )


def _overtime_pay(contract, wage, rate, threshold, standards, year, limitations):
    daily = contract.daily_hours.value
    weekly = contract.weekly_hours.value
    days = contract.work_days.value
    rule = standards["overtime_hours"]
    daily_limit, weekly_limit = rule["value"]["daily"], rule["value"]["weekly"]
    if wage is None or daily is None or weekly is None or (daily > daily_limit and days is None):
        limitations.append(Limitation(
            item=OVERTIME_PAY, reason="시급, 근로시간 또는 주 근무일수가 없습니다.",
        ))
        return None

    daily_excess = max(ZERO, daily - daily_limit) * len(days or [])
    weekly_excess = max(ZERO, weekly - weekly_limit)
    hours = max(daily_excess, weekly_excess)
    if not rule["verified"] or not standards["premium_rate"]["verified"]:
        limitations.append(Limitation(item=OVERTIME_PAY, reason="기준 확인 필요"))
    return _weekly(
        OVERTIME_PAY, hours * wage * rate,
        f"max({daily_excess}, {weekly_excess}) × {wage} × {rate}",
        f"{threshold}인 이상, 일 {daily_limit}시간과 주 {weekly_limit}시간 초과분 중 큰 값", year,
    )


def _seconds(clock: time):
    return (
        Decimal(clock.hour * SECONDS_PER_HOUR + clock.minute * 60 + clock.second)
        + Decimal(clock.microsecond) / 1_000_000
    )


def _night_overlap(start, end, night_start, night_end):
    shift_start, shift_end = _seconds(start), _seconds(end)
    if shift_end < shift_start:
        shift_end += SECONDS_PER_DAY
    window_start, window_end = _seconds(night_start), _seconds(night_end)
    if window_end < window_start:
        window_end += SECONDS_PER_DAY
    overlap = sum(
        (
            max(ZERO, min(shift_end, window_end + offset) - max(shift_start, window_start + offset))
            for offset in (-SECONDS_PER_DAY, 0, SECONDS_PER_DAY)
        ),
        ZERO,
    )
    return overlap, shift_end - shift_start


def _night_pay(contract, wage, rate, threshold, standards, year, limitations):
    start, end = contract.work_start.value, contract.work_end.value
    days = contract.work_days.value
    if wage is None or start is None or end is None or days is None or start == end:
        limitations.append(Limitation(item=NIGHT_PAY, reason="시급, 근무 시각과 주 근무일수를 확인해 주세요."))
        return None

    rule = standards["night_work_hours"]
    window = rule["value"]
    overlap, duration = _night_overlap(
        start, end, time.fromisoformat(window["start"]), time.fromisoformat(window["end"]),
    )
    condition = f"{threshold}인 이상, {window['start']}~{window['end']} 근로"
    break_minutes = contract.break_minutes.value
    if overlap > 0 and days:
        if break_minutes is None or (break_minutes > 0 and overlap < duration):
            condition += ", 휴게시간대 미확인에 따른 상한"
            limitations.append(Limitation(
                item=NIGHT_PAY, reason="야간 구간의 휴게시간을 확인해 주세요.",
            ))
        if break_minutes is not None:
            overlap = min(overlap, max(ZERO, duration - break_minutes * 60))
    if not rule["verified"] or not standards["premium_rate"]["verified"]:
        limitations.append(Limitation(item=NIGHT_PAY, reason="기준 확인 필요"))
    hours = overlap / SECONDS_PER_HOUR
    return _weekly(
        NIGHT_PAY, hours * wage * rate * len(days),
        f"{hours.normalize():f} × {wage} × {rate} × {len(days)}",
        condition, year,
    )


def calculate_pay(
    contract: Contract, workplace_size: KnownWorkplaceSize, standards: dict,
    year: int, limitations: list[Limitation], *, basis: PayBasis = "contract",
) -> ExpectedPayScenario:
    wage = standards["minimum_hourly_wage"]["value"] if basis == "minimum_wage" else contract.hourly_wage.value
    hours = contract.weekly_hours.value
    threshold = standards["premium_min_workplace_size"]["value"]
    weekly_rows = [
        _base_pay(wage, hours, year, limitations),
        _holiday_pay(wage, hours, standards, year, limitations),
    ]
    if workplace_size == "5_or_more":
        rate = standards["premium_rate"]["value"]
        weekly_rows.extend([
            _overtime_pay(contract, wage, rate, threshold, standards, year, limitations),
            _night_pay(contract, wage, rate, threshold, standards, year, limitations),
        ])
        if not standards["part_time_overtime"]["verified"]:
            limitations.append(Limitation(item="단시간 근로자 연장 가산", reason="기준 확인 필요"))
    else:
        weekly_rows.extend(
            _weekly(item, ZERO, "0", f"{threshold}인 미만 사업장", year)
            for item in (OVERTIME_PAY, NIGHT_PAY)
        )

    weekly = [row for row in weekly_rows if row is not None]
    if len(weekly) == len(weekly_rows):
        weekly.append(_weekly(
            TOTAL_PAY, sum((row.amount for row in weekly), ZERO),
            " + ".join(f"{row.amount.normalize():f}" for row in weekly),
            "주 기본급, 주휴수당, 가산수당 합산", year,
        ))
    else:
        limitations.append(Limitation(item=TOTAL_PAY, reason="계산에서 제외된 급여 항목이 있습니다."))

    month_rule = standards["weeks_per_month"]
    factor = month_rule["value"]
    monthly = []
    for row in weekly:
        if basis == "minimum_wage":
            row.applied_condition = f"최저임금 시급 {wage:,}원 적용, {row.applied_condition}"
        monthly.append(ExpectedPayItem(
            item=row.item,
            period="monthly",
            amount=(row.amount * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP),
            formula=f"{row.amount.normalize():f} × {factor} (원 단위 반올림)",
            applied_condition=f"{row.applied_condition}, 월 환산 {factor}주",
            standards_year=year,
        ))
        if not month_rule["verified"]:
            limitations.append(Limitation(item=f"{row.item}(월)", reason="기준 확인 필요"))
    return ExpectedPayScenario(workplace_size=workplace_size, items=weekly + monthly, basis=basis)

from dataclasses import dataclass
from datetime import time
from decimal import Decimal

from schemas.extraction import ScheduleEntry

WEEKDAYS = "월화수목금토일"
HOUR = Decimal(3600)
DAY = 24 * HOUR
ZERO = Decimal(0)


def seconds(clock: time) -> Decimal:
    return Decimal(clock.hour * 3600 + clock.minute * 60 + clock.second) + Decimal(clock.microsecond) / 1_000_000


def weekday(day: str) -> str:
    return day.removesuffix("요일").strip()


def hours_text(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')).normalize():f}"


def overlap(start: Decimal, end: Decimal, other_start: Decimal, other_end: Decimal) -> Decimal:
    return max(ZERO, min(end, other_end) - max(start, other_start))


def night_overlap(start: Decimal, end: Decimal, night_start: time, night_end: time) -> Decimal:
    window_start, window_end = seconds(night_start), seconds(night_end)
    if window_end < window_start:
        window_end += DAY
    return sum((overlap(start, end, window_start + offset, window_end + offset)
                for offset in (-DAY, ZERO, DAY)), ZERO)


@dataclass(frozen=True)
class Shift:
    days: tuple[str, ...]
    start: Decimal
    end: Decimal
    break_seconds: Decimal | None
    break_interval: tuple[Decimal, Decimal] | None

    @property
    def duration(self) -> Decimal:
        return self.end - self.start

    @property
    def net_hours(self) -> Decimal | None:
        if self.break_seconds is None:
            return None
        return (self.duration - self.break_seconds) / HOUR

    @property
    def label(self) -> str:
        return "·".join(self.days) or "요일 미확인"

    def night(self, start: time, end: time) -> tuple[Decimal, bool]:
        gross = night_overlap(self.start, self.end, start, end)
        if self.break_interval is not None:
            return (gross - night_overlap(*self.break_interval, start, end)) / HOUR, False
        limit = gross > 0 and (
            self.break_seconds is None or (self.break_seconds > 0 and gross < self.duration)
        )
        net = min(gross, self.duration - (self.break_seconds or ZERO))
        return net / HOUR, limit


def measure_shift(entry: ScheduleEntry) -> Shift | None:
    start, end = entry.start.value, entry.end.value
    if start is None or end is None or start == end:
        return None
    opens, closes = seconds(start), seconds(end)
    if closes < opens:
        closes += DAY
    minutes = entry.break_minutes.value
    pause_start, pause_end = entry.break_start.value, entry.break_end.value
    interval = None
    pause = None if minutes is None else Decimal(minutes * 60)
    if pause_start is not None or pause_end is not None:
        if pause_start is None or pause_end is None:
            return None
        left, right = seconds(pause_start), seconds(pause_end)
        if left < opens:
            left += DAY
        if right < opens:
            right += DAY
        if not opens <= left <= right <= closes:
            return None
        interval = left, right
        pause = right - left
    if pause is not None and pause > closes - opens:
        return None
    days = tuple(weekday(day) for day in entry.days.value or [])
    return Shift(days, opens, closes, pause, interval)


def valid_schedule_days(shifts: list[Shift | None]) -> bool:
    if not shifts or any(shift is None or not shift.days for shift in shifts):
        return False
    days = [day for shift in shifts for day in shift.days]
    return len(days) == len(set(days)) and all(day in WEEKDAYS for day in days)


def schedule_hours(shifts: list[Shift | None]) -> Decimal | None:
    if not valid_schedule_days(shifts) or any(shift.net_hours is None for shift in shifts):
        return None
    return sum((shift.net_hours * len(shift.days) for shift in shifts), ZERO)

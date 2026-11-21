from datetime import time
from decimal import Decimal, InvalidOperation
import re

import pandas as pd
from pydantic import ValidationError
import streamlit as st

from app.components.fields import MISSING_FIELD, edited_field
from engine.schedule import DAY, WEEKDAYS, hours_text, measure_shift, schedule_hours, weekday
from schemas.extraction import Contract, ExtractedField, ScheduleEntry, normalize_clock

EDITOR_KEY = "review-work-schedule"
LABELS = {
    "days": "요일", "start": "시작", "end": "종료", "break_start": "휴게 시작",
    "break_end": "휴게 종료", "break_minutes": "휴게 분",
}


def _clock_text(clock: time | None, start: time | None = None) -> str:
    if clock is None:
        return ""
    clock = normalize_clock(clock)
    prefix = "익일 " if start is not None and clock < normalize_clock(start) else ""
    return prefix + clock.isoformat(timespec="minutes")


def _display(entry: ScheduleEntry, index: int) -> dict:
    start = entry.start.value
    return {
        "_source_row": str(index),
        "days": "·".join(entry.days.value or []),
        "start": _clock_text(start),
        "end": _clock_text(entry.end.value, start),
        "break_start": _clock_text(entry.break_start.value, start),
        "break_end": _clock_text(entry.break_end.value, start),
        "break_minutes": entry.break_minutes.value,
    }


def _parse_entry(row: dict, previous: ScheduleEntry | None) -> ScheduleEntry:
    values = {}
    days = [weekday(day) for day in re.split(r"[,·/\s]+", row["days"]) if day]
    if any(day not in WEEKDAYS for day in days):
        raise ValueError("요일은 월, 화, 수, 목, 금, 토, 일로 적어 주세요.")
    values["days"] = days or None
    for field in ("start", "end", "break_start", "break_end"):
        entered = row[field].removeprefix("익일").strip()
        try:
            values[field] = normalize_clock(entered or None)
        except ValueError:
            raise ValueError(f"{LABELS[field]} 시각을 HH:MM 형식으로 적어 주세요.") from None
    if row["break_minutes"] and not re.fullmatch(r"\d+", row["break_minutes"]):
        raise ValueError("휴게 분은 0 이상의 정수로 적어 주세요.")
    values["break_minutes"] = int(row["break_minutes"]) if row["break_minutes"] else None
    if values["break_start"] is not None and values["break_end"] is not None:
        values["break_minutes"] = None
    fields = previous.model_dump() if previous else {}
    updates = {name: edited_field(fields.get(name, MISSING_FIELD), value) for name, value in values.items()}
    if previous and all(getattr(previous, name).value == value for name, value in values.items()):
        updates["source_text"] = previous.source_text
    entry = ScheduleEntry.model_validate(updates)
    if (entry.start.value is not None and entry.end.value is not None and measure_shift(entry) is None) or (
        (entry.break_start.value is None) != (entry.break_end.value is None)
    ):
        raise ValueError("시작·종료 시각과 근무 구간 안의 휴게 시작·종료를 확인해 주세요.")
    return entry


def edit_schedule(original: Contract, contractual_weekly: str | None) -> tuple[Contract, bool]:
    defaults = original.schedule_entries()
    frame = pd.DataFrame([_display(entry, index) for index, entry in enumerate(defaults)],
                         columns=("_source_row", *LABELS), dtype="string")
    st.markdown("**요일별 근무 시간**")
    st.caption("같은 시간에 일하는 요일은 한 행에 적어요. 표의 +로 행을 추가하고, 휴게 시각이나 휴게 분을 적어 주세요.")
    table = st.data_editor(
        frame, num_rows="dynamic", hide_index=True, key=EDITOR_KEY,
        column_config={"_source_row": None, **{name: st.column_config.TextColumn(label) for name, label in LABELS.items()}},
    )
    entries, errors, seen = [], [], set()
    for index, row in enumerate(table.to_dict(orient="records"), 1):
        cells = {name: "" if pd.isna(row[name]) else str(row[name]).strip() for name in LABELS}
        if not any(cells.values()):
            continue
        source = row["_source_row"]
        previous = defaults[int(source)] if pd.notna(source) else None
        try:
            entry = _parse_entry(cells, previous)
        except (ValueError, ValidationError) as error:
            errors.append(f"{index}행 · {error}")
            continue
        days = entry.days.value or []
        if seen.intersection(days) or len(days) != len(set(days)):
            errors.append(f"{index}행 · 같은 요일을 두 번 적었어요.")
        seen.update(days)
        entries.append(entry)

    if errors:
        st.warning(" ".join(errors))
        return original, False

    shifts = [measure_shift(entry) for entry in entries]
    for shift in shifts:
        if shift and shift.end >= DAY:
            st.caption(f"{shift.label} 근무 종료는 익일이에요.")
    total = schedule_hours(shifts)
    if total is not None:
        text = f"요일별 시간을 더하면 주 {hours_text(total)}시간이에요."
        try:
            weekly = Decimal(contractual_weekly) if contractual_weekly is not None else None
        except InvalidOperation:
            weekly = None
        if weekly is not None and weekly.is_finite() and total != weekly:
            st.warning(f"{text} 계약서의 주 소정근로시간 {hours_text(weekly)}시간과 달라요.")
        else:
            st.caption(text)
    if entries == defaults:
        return original, True
    updated = original.model_copy(deep=True)
    updated.work_schedule = ExtractedField(value=entries or None, confidence="high" if entries else "low")
    updated.sync_legacy_schedule()
    return updated, True

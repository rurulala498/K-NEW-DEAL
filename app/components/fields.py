from collections.abc import Mapping

import pandas as pd
import streamlit as st

MISSING_FIELD = {"value": None, "confidence": "low"}


def confidence_label(label, field):
    return f"{label} · 신뢰도가 낮아요" if field["confidence"] == "low" and field["value"] is not None else label


def edited_field(original, value):
    unchanged = str(value) == str(original["value"])
    confidence = original["confidence"] if unchanged else "low" if value is None else "high"
    return {
        "value": value, "confidence": confidence,
        "source_text": original.get("source_text") if unchanged else None,
    }


def text_field(field, label, key, *, is_work_days=False):
    preset = field["value"]
    display = "" if preset is None else str(preset)
    if is_work_days:
        display = ", ".join(preset or [])
    entered = st.text_input(confidence_label(label, field), value=display, key=key).strip()
    if is_work_days:
        return edited_field(field, [day.strip() for day in entered.split(",") if day.strip()] or None)
    return edited_field(field, entered or None)


def text_fields(source, labels, prefix):
    return {
        name: text_field(source[name], label, f"{prefix}-{name}", is_work_days=name == "work_days")
        for name, label in labels.items()
    }


def low_rows(field, labels, key, choices):
    corrected = {}
    for index, row in enumerate(field["value"] or []):
        if all(cell["confidence"] != "low" for cell in row.values()):
            continue
        st.markdown(f"**{key} {index + 1}행**")
        corrected[index] = {}
        for name, label in labels.items():
            widget_key = f"review-low-{key}-{index}-{name}"
            if name in choices:
                options = choices[name]
                current = row[name]["value"]
                keys = [None, *options]
                if current not in keys:
                    keys.append(current)
                selected = st.selectbox(
                    confidence_label(label, row[name]), keys, index=keys.index(current),
                    format_func=lambda option: options.get(option, option or "정보 없음"), key=widget_key,
                )
                corrected[index][name] = edited_field(row[name], selected)
            else:
                corrected[index][name] = text_field(row[name], label, widget_key)
    return corrected


def editable_rows(field, labels, key, *, choices: Mapping[str, dict[str, str]], corrected):
    original = field["value"] or []
    editable = [(index, row) for index, row in enumerate(original) if index not in corrected]
    if corrected:
        st.caption("신뢰도가 낮은 행은 위에서 수정할 수 있어요.")

    frame = pd.DataFrame(
        [{name: choices.get(name, {}).get(row[name]["value"], row[name]["value"])
          for name in labels} for _, row in editable],
        columns=list(labels), dtype="string",
    )
    config = {
        name: st.column_config.SelectboxColumn(label, options=list(choices[name].values()))
        if name in choices else st.column_config.TextColumn(label)
        for name, label in labels.items()
    }
    table = st.data_editor(frame, num_rows="dynamic", column_config=config, hide_index=True, key=key)
    codes = {name: {label: code for code, label in options.items()} for name, options in choices.items()}
    records = dict(corrected)
    for position, row in table.iterrows():
        values = {name: None if pd.isna(cell) else str(cell).strip() or None for name, cell in row.items()}
        if not any(value is not None for value in values.values()):
            continue
        if position < len(editable):
            index, source = editable[position]
        else:
            index, source = len(original) + position - len(editable), {}
        records[index] = {
            name: edited_field(source.get(name, MISSING_FIELD), codes.get(name, {}).get(value, value))
            for name, value in values.items()
        }
    rows = [records[index] for index in sorted(records)]
    return edited_field(field, None if field["value"] is None and not rows else rows)

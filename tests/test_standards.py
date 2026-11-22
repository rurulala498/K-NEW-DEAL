from decimal import Decimal

from config import load_standards


def test_load_2026_standards():
    standards = load_standards(2026)

    assert standards["year"] == 2026
    assert standards["minimum_hourly_wage"] == {"value": 10320, "verified": True}
    assert standards["weekly_holiday_min_hours"] == {"value": 15, "verified": True}
    assert standards["break_time"] == {
        "value": [
            {"work_hours": 4, "break_minutes": 30},
            {"work_hours": 8, "break_minutes": 60},
        ],
        "verified": True,
    }
    assert standards["premium_min_workplace_size"] == {"value": 5, "verified": True}
    assert standards["night_work_hours"] == {
        "value": {"start": "22:00", "end": "06:00"},
        "verified": True,
    }
    assert standards["premium_rate"] == {
        "value": Decimal("0.5"),
        "verified": True,
    }
    assert standards["weeks_per_month"] == {
        "value": Decimal("4.345"),
        "verified": False,
    }
    assert standards["weekly_holiday_hours"] == {
        "value": {"full_time_weekly": 40, "max_hours": 8},
        "verified": False,
    }
    assert standards["overtime_hours"] == {
        "value": {"daily": 8, "weekly": 40},
        "verified": True,
    }
    assert standards["part_time_overtime"]["value"] is None
    assert standards["part_time_overtime"]["verified"] is False
    assert standards["part_time_overtime"]["todo"]


def test_load_unregistered_year():
    assert load_standards(9999) == "등록되지 않은 기준 연도"

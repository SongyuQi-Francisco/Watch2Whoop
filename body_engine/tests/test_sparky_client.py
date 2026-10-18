from datetime import date

import pytest

from body_engine import sparky_client as S
from body_engine.assistant import sparky_tools
from body_engine.assistant.tools import TOOLS_BY_NAME


class FakeClient:
    """Return prepared responses by path, and record the (path, params) calls made."""

    def __init__(self, responses: dict):
        self.responses, self.calls = responses, []

    def get(self, path, **params):
        self.calls.append((path, params))
        r = self.responses[path]
        return r(params) if callable(r) else r


def _set(n, reps, weight, rir=None):
    return {"set_number": n, "set_type": "Working Set", "reps": reps, "weight": weight, "rir": rir}


def _preset(d, name, exercises):
    return {"type": "preset", "entry_date": d, "name": name, "notes": None, "source": "manual",
            "total_duration_minutes": 0, "exercises": exercises}


def _entry(name, sets):
    return {"exercise_id": "x", "exercise_snapshot": {"name": name, "category": "strength"}, "sets": sets}


def test_training_log_filters_range_and_stops_paging_before_start():
    pages = {
        1: [_preset("2026-10-15", "胸 + 腿", [_entry("上斜卧推", [_set(2, 12, 23), _set(1, 12, 23, rir=2)])]),
            _preset("2026-10-12", "肩", [])],
        2: [_preset("2026-09-30", "背", [])],  # already earlier than start, page 3 must not be fetched
        3: [_preset("2026-09-01", "不该读到", [])],
    }
    c = FakeClient({"/v2/exercise-entries/history": lambda p: {
        "sessions": pages[p["page"]], "pagination": {"hasMore": p["page"] < 3}}})
    out = S.training_log(c, date(2026, 10, 1), date(2026, 10, 15))
    assert [s["date"] for s in out] == ["2026-10-12", "2026-10-15"]  # ascending
    assert [s["set"] for s in out[1]["exercises"][0]["sets"]] == [1, 2]  # sets sorted by index
    assert out[1]["exercises"][0]["sets"][0]["rir"] == 2
    assert [p["page"] for _, p in c.calls] == [1, 2]


def test_individual_session_becomes_one_exercise():
    s = S._session({"type": "individual", "entry_date": "2026-09-16", "name": None, "duration_minutes": 40,
                    "exercise_id": "w", "exercise_snapshot": {"name": "爬坡（跑步机）"},
                    "sets": [{"set_number": 1, "duration": 2400}]})
    assert s["name"] == "爬坡（跑步机）" and s["exercises"][0]["sets"][0]["duration_s"] == 2400


def test_nutrition_scales_entries_and_marks_unlogged_days():
    c = FakeClient({
        # The goal in the report is the default goal (does not follow the weekly goal plan), must not be used
        "/reports/nutrition-trends-with-goals": [
            {"date": "2026-10-14", "calories": 0, "protein": 0, "carbs": 0, "fat": 0, "calorieGoal": 2000},
            {"date": "2026-10-15", "calories": 700, "protein": 40, "carbs": 45, "fat": 40, "calorieGoal": 2000}],
        "/goals/by-date/2026-10-14": {"preset_name": "休息日", "calories": 2200, "protein": 160},
        "/goals/by-date/2026-10-15": {"preset_name": "训练日", "calories": 2400, "protein": 160},
        "/food-entries/range/2026-10-14/2026-10-15": [
            {"entry_date": "2026-10-15", "entry_time": "14:30:00", "food_name": "酸奶", "quantity": 300,
             "serving_size": 100, "unit": "g", "calories": 60, "protein": 10, "carbs": None, "fat": 0}],
    })
    out = S.nutrition(c, date(2026, 10, 14), date(2026, 10, 15))
    assert [d["logged"] for d in out["days"]] == [False, True]
    assert [(d["goal"]["name"], d["goal"]["calories"]) for d in out["days"]] == [("休息日", 2200), ("训练日", 2400)]
    e = out["entries"][0]
    assert (e["calories"], e["protein_g"], e["carbs_g"], e["time"]) == (180, 30, None, "14:30")


def test_weight_skips_empty_rows_and_weekly_average():
    c = FakeClient({"/measurements/check-in-measurements-range/2026-09-01/2026-10-16": [
        {"entry_date": "2026-09-25", "weight": 87.8}, {"entry_date": "2026-09-23", "weight": 86},
        {"entry_date": "2026-10-01", "weight": None, "steps": 9000}]})
    rows = S.weight(c, date(2026, 9, 1), date(2026, 10, 16))
    assert rows == [{"date": "2026-09-23", "weight_kg": 86}, {"date": "2026-09-25", "weight_kg": 87.8}]
    assert S.weekly_weight_average(rows) == [{"week_start": "2026-09-21", "avg_kg": 86.9, "n": 2}]


def test_whoami_rejects_non_readonly_key():
    def client(perms):
        return FakeClient({"/auth/get-session": {"session": {"id": "k1"}, "user": {"id": "u1"}},
                           "/identity/user-api-keys": [{"id": "k1", "permissions": {"sparky": perms}}]})
    assert S.SparkyClient.whoami(client(["read"]))["id"] == "u1"
    with pytest.raises(S.SparkyError):
        S.SparkyClient.whoami(client(["read", "write"]))


def test_sparky_tools_registered_read_only():
    for name in ("get_training_log", "get_nutrition", "get_weight"):
        assert name in TOOLS_BY_NAME and not TOOLS_BY_NAME[name].writes


def test_tool_range_is_validated():
    with pytest.raises(ValueError):
        sparky_tools._range({"start": "2026-10-16", "end": "2026-10-01"})
    with pytest.raises(ValueError):
        sparky_tools._range({"start": "2025-01-01", "end": "2026-10-01"})

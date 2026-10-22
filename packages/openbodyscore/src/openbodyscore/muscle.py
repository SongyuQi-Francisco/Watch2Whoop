"""Muscle load (daily_scores.muscle_load): heart rate underestimates strength training, so it is computed separately from SparkyFitness workout records (system design §7).

- muscle_load = Σ reps × weight (kg, training volume) over the working sets of all strength exercises that day. Weight is as recorded: a dumbbell counts as a single one, unilateral exercises per side,
  so it is a relative measure "comparable for the same person over time", not the real total weight; warm-up sets are excluded; bodyweight sets (no weight) count toward set count but not volume.
- When RPE and duration exist, session RPE load = average RPE × minutes (Foster) is computed separately, placed in details.muscle.srpe (a different unit from volume, not added together).
- No workout record that day -> left empty (no record ≠ no training).
"""
from collections import defaultdict
from datetime import date

WARMUP = {"warm-up", "warmup", "warm up", "热身"}


def daily_muscle_load(sessions: list[dict]) -> dict[date, dict]:
    """sessions = output of sparky_client.training_log. Returns date -> {volume_kg, working_sets, ...}."""
    days: dict[date, dict] = {}
    for s in sessions:
        d = date.fromisoformat(s["date"][:10])
        day = days.setdefault(d, {"volume_kg": 0.0, "working_sets": 0, "bodyweight_sets": 0, "srpe": None,
                                  "sessions": [], "sets_by_muscle": defaultdict(int)})
        day["sessions"].append(s.get("name"))
        rpes, minutes = [], s.get("total_duration_minutes") or 0
        ex_minutes = 0
        for e in s["exercises"]:
            ex_minutes += e.get("duration_minutes") or 0
            sets = [x for x in e["sets"] if (x.get("type") or "").lower() not in WARMUP]
            rpes += [x["rpe"] for x in sets if x.get("rpe") is not None]
            if (e.get("category") or "strength") != "strength":
                continue
            for x in sets:
                if x.get("reps") is None and x.get("duration_s") is None:
                    continue
                day["working_sets"] += 1
                for m in e.get("primary_muscles") or []:
                    day["sets_by_muscle"][m] += 1
                if x.get("weight_kg") and x.get("reps"):
                    day["volume_kg"] += x["reps"] * x["weight_kg"]
                else:
                    day["bodyweight_sets"] += 1
        minutes = minutes or ex_minutes
        if rpes and minutes:
            day["srpe"] = round((day["srpe"] or 0) + sum(rpes) / len(rpes) * minutes, 1)
    for day in days.values():
        day["volume_kg"] = round(day["volume_kg"], 1)
        day["sets_by_muscle"] = dict(day["sets_by_muscle"])
    return days

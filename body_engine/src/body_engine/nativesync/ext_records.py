"""The person's training / weight / food log as raw rows, per day (pull only; docs/ios-native-2.0.md section 2.2, decision 2).

GET /v1/users/me/ext-records?from=&to=  -> {"from", "to", "available", "source", "error", "days": [
    {"date", "fingerprint", "strength_sets", "weights", "nutrition_totals"}, ...]}
GET ...&fingerprints=1                   -> the same without "days", with "fingerprints": {date: fingerprint}

The rows come from the food / training log the user's other read paths use (sparky_client.reader_for_user): the
SparkyFitness read-only key, or Body Engine's own tables when the user's reads are own (flags own_food_read /
own_food_only). Nothing is computed from them except the day's nutrition sums; the phone computes muscle load, body-age
strength minutes and reports from them (M1c, M4, M5).
- strength_sets: the day's logged sessions, each with its exercises and their sets (reps, weight_kg, duration_s …),
  in the shape of sparky_client.training_log;
- weights: the day's weigh-ins (sparky_client.weight: weight_kg, waist_cm, body_fat_pct …);
- nutrition_totals: the sums of the day's food entries (calories, protein_g …, `entries` = how many).
Only days with any of them are listed. `fingerprint` = sha256 of the day's canonical JSON (sorted keys, no spaces) over
those three fields, so the phone refetches only days whose fingerprint changed and drops days that are no longer listed.
`available` false (error not_linked / unreadable): the log cannot be read now, the phone keeps what it has.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date

import psycopg

log = logging.getLogger(__name__)
NUTRIENTS = ("calories", "protein_g", "carbs_g", "fat_g", "saturated_fat_g", "fiber_g", "sugar_g", "sodium_mg")


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def fingerprint(day: dict) -> str:
    """sha256 of the day's canonical JSON (strength_sets, weights, nutrition_totals)."""
    body = {k: day.get(k) for k in ("strength_sets", "weights", "nutrition_totals")}
    return hashlib.sha256(canonical(body).encode()).hexdigest()


def _totals(entries: list[dict]) -> dict:
    out: dict = {"entries": len(entries)}
    for k in NUTRIENTS:
        vals = [e[k] for e in entries if isinstance(e.get(k), (int, float))]
        out[k] = round(sum(vals), 1) if vals else None
    return out


def reader(conn: psycopg.Connection, uid: int):
    """The user's log reader (tests replace it with a fake)."""
    from .. import sparky_client
    return sparky_client.reader_for_user(conn, uid)


def days(conn: psycopg.Connection, uid: int, start: date, end: date) -> dict:
    """The answer (with days); see the module doc."""
    from .. import sparky_client as S
    out = {"from": start.isoformat(), "to": end.isoformat(), "available": True, "source": None, "error": None, "days": []}
    try:
        r = reader(conn, uid)
        out["source"] = "own" if getattr(r, "own", False) else "sparky"
        sessions = S.training_log(r, start, end)
        weights = S.weight(r, start, end)
        foods = S.food_entries(r, start, end)
    except S.SparkyError as e:
        linked = conn.execute("SELECT sparky_key_name, sparky_user_id FROM users WHERE id = %s", (uid,)).fetchone()
        out.update(available=False, error="unreadable" if linked and (linked["sparky_key_name"] or linked["sparky_user_id"])
                   else "not_linked")
        log.info("ext_records of user %s not readable: %s", uid, e)
        return out
    by: dict[str, dict] = {}

    def day(d) -> dict:
        k = str(d)[:10]
        return by.setdefault(k, {"date": k, "strength_sets": [], "weights": [], "nutrition_totals": None})

    for s in sessions:
        day(s["date"])["strength_sets"].append(s)
    for w in weights:
        day(w["date"])["weights"].append(w)
    food_by: dict[str, list] = {}
    for f in foods:
        food_by.setdefault(str(f["date"])[:10], []).append(f)
    for k, entries in food_by.items():
        day(k)["nutrition_totals"] = _totals(entries)
    for k in sorted(by):
        if not start.isoformat() <= k <= end.isoformat():
            continue
        d = by[k]
        for f in ("strength_sets", "weights"):   # a stable order, whatever order the log answered in
            d[f].sort(key=canonical)
        out["days"].append({**d, "fingerprint": fingerprint(d)})
    return out


def fingerprints(conn: psycopg.Connection, uid: int, start: date, end: date) -> dict:
    full = days(conn, uid, start, end)
    got = full.pop("days")
    return {**full, "fingerprints": {d["date"]: d["fingerprint"] for d in got}}

"""Kind `sleep` of package M1 (derive; docs/ios-native-2.0.md sections 3.2 and 4): the nights the phone derived.

Rows of one day = the sessions waking that day (common.sessionize: end − 4 h), in start order, each
{start, end (UTC seconds), asleep_min, core_min, deep_min, rem_min, awake_min, unspecified_min, is_main, device,
 segments: [[start, end, stage], ...]}: rawdata/derive.session_rows of derive.nights_of (Open Wearables' session rule per
HealthKit source, its stage clean-up, importers/ow.sleep_segments' source priority, sessionize), as the server derives the
app's sleep in the sync mode `app`.

The spot check recomputes the day from the stage samples the server has (derive.nights). Stored (primary only): the app's
sessions of the day replace the server's (source 'app', origin 'app' + bundle_version, the segments), the lower-priority
sources' sessions of a day with app sleep go (common.replace_sleep's rule), is_main is chosen again over what is left.
"""
from __future__ import annotations

import math
from datetime import date
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from ...i18n import L
from ...importers import common
from ...rawdata import derive

KIND = "sleep"
PACKAGE = "derive"
ALGO_KEY = "derive"
# docs/ios-native-2.0.md section 3.4: session start / end ±1 s (HealthKit times through two clocks), stage minutes ±0.5
# (whole minutes, rounded on both sides); is_main, device and the segments exact.
TOLERANCE = {"start": 1.0, "end": 1.0, "asleep_min": 0.5, "core_min": 0.5, "deep_min": 0.5, "rem_min": 0.5,
             "awake_min": 0.5, "unspecified_min": 0.5}
MINUTES = ("asleep_min", "core_min", "deep_min", "rem_min", "awake_min", "unspecified_min")
STAGES = ("core", "deep", "rem", "awake", "unspecified")


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate(day: date, rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if not (_finite(r.get("start")) and _finite(r.get("end")) and r["start"] <= r["end"]):
            raise ValueError(L("每一段睡眠要有开始和结束时间", "Every session needs a start and an end"))
        if not all(_finite(r.get(k)) and r[k] >= 0 for k in MINUTES):
            raise ValueError(L("睡眠分钟数应该是非负数字", "Sleep minutes must be non-negative numbers"))
        if not isinstance(r.get("is_main"), bool) or (r.get("device") is not None and not isinstance(r["device"], str)):
            raise ValueError(L("is_main / device 格式不对", "is_main / device have the wrong type"))
        segs = r.get("segments") or []
        if not isinstance(segs, list) or any(
                not isinstance(s, list) or len(s) != 3 or not _finite(s[0]) or not _finite(s[1]) or s[2] not in STAGES
                for s in segs):
            raise ValueError(L("睡眠分段格式不对", "Bad sleep segments"))
        out.append({"start": float(r["start"]), "end": float(r["end"]), **{k: int(round(r[k])) for k in MINUTES},
                    "is_main": r["is_main"], "device": r.get("device"),
                    "segments": [[float(a), float(b), st] for a, b, st in segs]})
    if sum(r["is_main"] for r in out) > 1:
        raise ValueError(L("一天只能有一段主睡眠", "Only one main sleep per day"))
    return sorted(out, key=lambda r: r["start"])


def recompute(conn: psycopg.Connection, uid: int, day: date, uploaded: dict[date, list[dict]]) -> list[dict] | None:
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (uid,)).fetchone()["timezone"])
    lo, hi = derive.night_window({day}, tz)
    if not conn.execute("""SELECT 1 FROM hk_samples WHERE user_id = %s AND type = %s AND start_at >= %s AND start_at < %s
                           LIMIT 1""", (uid, derive.SLEEP, lo, hi)).fetchone():
        return None   # none of the phone's stage samples around that night are here (yet): nothing to compare with
    return derive.session_rows(derive.nights(conn, uid, {day}, tz))


def write(conn: psycopg.Connection, uid: int, days: dict[date, list[dict]], bundle_version: str | None) -> int:
    app = common.APP_SOURCE
    lower = [s for s, p in common.SOURCE_PRIORITY.items() if p < common.SOURCE_PRIORITY[app]]
    n = 0
    for d, rows in days.items():
        conn.execute("DELETE FROM sleep_sessions WHERE user_id = %s AND wake_date = %s AND source = %s", (uid, d, app))
        if rows:
            conn.execute("DELETE FROM sleep_sessions WHERE user_id = %s AND wake_date = %s AND source = ANY(%s)",
                         (uid, d, lower))
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, core_min, deep_min, rem_min,
                                               awake_min, unspecified_min, is_main, source, device, origin, bundle_version,
                                               segments)
                   VALUES (%s, %s, to_timestamp(%s), to_timestamp(%s), %s, %s, %s, %s, %s, %s, FALSE, %s, %s, 'app', %s, %s)""",
                [(uid, d, r["start"], r["end"], r["asleep_min"], r["core_min"], r["deep_min"], r["rem_min"], r["awake_min"],
                  r["unspecified_min"], app, r["device"], bundle_version, Jsonb(r["segments"])) for r in rows])
        conn.execute(
            """UPDATE sleep_sessions s SET is_main = (s.id = m.id)
               FROM (SELECT DISTINCT ON (wake_date) wake_date, id FROM sleep_sessions
                     WHERE user_id = %s AND wake_date = %s ORDER BY wake_date, asleep_min DESC, id) m
               WHERE s.user_id = %s AND s.wake_date = m.wake_date""", (uid, d, uid))
        n += len(rows)
    return n

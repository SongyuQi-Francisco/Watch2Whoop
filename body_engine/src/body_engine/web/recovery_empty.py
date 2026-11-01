"""Why the recovery ring is empty (Today page): a code, a short label and the next step the person can tap.

`scores_today` (views.py) adds `recovery_empty` when the viewed day has no recovery score, so the ring never stays blank
without saying why. The old per-field reasons (`today_reasons`) are long Chinese sentences for the AI; this block is for the
page: `code` (the page has a branch for each one, tests/test_recovery_empty.py checks it), `label` (short, in the request's
language, L()), `cta` (the button text) and `action` (what the button does; the page maps it: consent = open the consent form,
sync = the app's sync page (a browser: the data status list), sleep_help = the data status of sleep with the steps to switch
on sleep tracking on the watch). `action` is None when there is nothing for the person to do (wait).

Order matters, the first match wins:
  consent         never agreed to the consent form: the server does not process this person's data (profile/consent.py)
  awaiting_sync   nothing received from this person yet (just signed up, first sync not done)
  no_watch        something arrived (steps ...) but no resting heart rate: no watch data
  no_day          this day is before the watch data began
  no_sleep        resting heart rate arrives but no watch sleep in the last 7 days: sleep tracking is off / the watch is not worn in bed
  sleep_pending   no sleep for this night yet but there was within the last 7 days: the night has not synced yet
  no_sleep_night  a past day without a watch sleep
  no_hrv          slept, but no HRV reading in the sleep window
  baseline        HRV readings, but fewer nights than the baseline needs
  pending         has data, the scores of the day are not computed yet (the job runs every hour)
"""
from __future__ import annotations

from datetime import date, timedelta

import psycopg

from ..i18n import L
from ..profile import consent
from ..scores import engine as scores_engine
from ..scores.inputs import is_watch_sleep

SLEEP_LOOKBACK_DAYS = 7      # "no sleep tracking" = no watch sleep within this many days before the day

CODES = ("consent", "awaiting_sync", "no_watch", "no_day", "no_sleep", "sleep_pending", "no_sleep_night", "no_hrv",
         "baseline", "pending")


ACTIONS = ("consent", "sync", "sleep_help")   # what the button can do; app.js recoveryNext has a branch for each


def _entry(code: str, label: str, cta: str | None = None, action: str | None = None) -> dict:
    return {"code": code, "label": label, "cta": cta, "action": action}


def _has_any_data(conn: psycopg.Connection, user_id: int) -> bool:
    """Anything at all received from this person (the app's sync, the Shortcut, the Apple Health history)?"""
    return bool(conn.execute(
        """SELECT EXISTS (SELECT 1 FROM daily_metrics WHERE user_id = %(u)s)
               OR EXISTS (SELECT 1 FROM sleep_sessions WHERE user_id = %(u)s)
               OR EXISTS (SELECT 1 FROM workouts WHERE user_id = %(u)s)
               OR EXISTS (SELECT 1 FROM shortcut_uploads WHERE user_id = %(u)s)
               OR EXISTS (SELECT 1 FROM rawdata_uploads WHERE user_id = %(u)s) AS any""",
        {"u": user_id}).fetchone()["any"])


def _recent_watch_sleep(conn: psycopg.Connection, user_id: int, day: date) -> bool:
    for r in conn.execute(
            """SELECT core_min, deep_min, rem_min, device FROM sleep_sessions
               WHERE user_id = %s AND wake_date BETWEEN %s AND %s""",
            (user_id, day - timedelta(days=SLEEP_LOOKBACK_DAYS), day)):
        if is_watch_sleep(r):
            return True
    return False


def explain(conn: psycopg.Connection, user_id: int, day: date, today: dict | None, local_today: date) -> dict | None:
    """The empty-ring block for `day` (today = its daily_scores row or None); None when the day has a recovery score."""
    if today is not None and today.get("recovery") is not None:
        return None
    return explain_from(day, today, local_today, consent.needs_consent(conn, user_id),
                        lambda: scores_engine.scoring_origin(conn, user_id), lambda: _has_any_data(conn, user_id),
                        lambda: _recent_watch_sleep(conn, user_id, day))


def _v(x):
    return x() if callable(x) else x


def explain_from(day: date, today: dict | None, local_today: date, needs_consent: bool, origin, any_data,
                 recent_watch_sleep) -> dict | None:
    """The pure part of explain. origin (scoring origin or None), any_data and recent_watch_sleep (bools) may be given as
    callables, asked only when the order above needs them (the server's queries); the phone passes the values
    (docs/ios-native-2.0.md M4: JS OBS.coach.today.recovery_empty)."""
    if today is not None and today.get("recovery") is not None:
        return None
    if needs_consent:
        return _entry("consent", L("需要先确认同意书", "Review the consent form first"),
                      L("去确认", "Review"), "consent")
    origin = _v(origin)
    if origin is None:
        if _v(any_data):
            return _entry("no_watch", L("还没有手表数据", "No watch data yet"), L("手机同步", "Phone sync"), "sync")
        return _entry("awaiting_sync", L("等待第一次同步", "Waiting for the first sync"), L("手机同步", "Phone sync"), "sync")
    if day < origin:
        return _entry("no_day", L("这天还没有手表数据", "No watch data for this day"))
    flags = (today or {}).get("flags") or {}
    is_today = day >= local_today
    if "no_sleep" in flags or today is None:
        if not _v(recent_watch_sleep):
            return _entry("no_sleep", L("没有睡眠数据", "No sleep data"), L("开启睡眠追踪", "Turn on sleep tracking"), "sleep_help")
        if today is not None:
            if is_today:
                return _entry("sleep_pending", L("等待昨晚的睡眠同步", "Waiting for last night's sleep"),
                              L("手机同步", "Phone sync"), "sync")
            return _entry("no_sleep_night", L("这天没有睡眠数据", "No sleep data for this night"))
    if "hrv_missing" in flags:
        return _entry("no_hrv", L("夜里没有 HRV 读数", "No overnight HRV reading"))
    if "baseline_insufficient" in flags:
        return _entry("baseline", L("还在建立你的基线", "Still building your baseline"))
    return _entry("pending", L("还在计算", "Still being calculated"))

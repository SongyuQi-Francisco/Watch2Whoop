"""What the phone needs from the server to score a user itself (GET /v1/sync/scoring, #340 N2b; docs/ios-native-app.md
section 9.1). The phone computes with the JS bundle from its local database; these are the inputs it cannot work out on
its own (packages/openbodyscore-js/README.md, "Inputs only the server can provide"):

- hrv_kind (scores/hrv_kind.choose for the days the phone writes; never mixed within one person), birth date, time zone,
  scoring origin, the server's algorithm version;
- sync_mode (rawdata/engine.mode): the phone scores only users in the app mode, whose scoring inputs are the app's own
  uploads (other users' come from Open Wearables / the Shortcut, which the phone does not have);
- app_scoring: whether the server currently leaves this user's scores to the app (nativesync/appscores.active) and the
  last spot check;
- muscle (from, to): the daily muscle load of those days (SparkyFitness and imported strength logs, server-only);
- hr_hist (hist_from, hist_to): the heart-rate histogram of each day, {bpm: count} of the rounded raw heart rate the
  scores use (scores/inputs.load: every source merged) — the phone keeps raw samples for APP_RAW_DAYS only and needs the
  365-day HRmax window before them. Loads that much history: asked for once after an install, not on every scoring run.
Everything else (stored rows for the baselines and CTL, daily resting heart rate, sleep sessions and stage segments)
comes through POST /v1/sync/pull.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from ..scores import engine as E, hrv_kind as HK, params as SP
from . import appscores, params as P


def _user(conn: psycopg.Connection, uid: int) -> dict:
    return conn.execute("SELECT timezone, birth_date FROM users WHERE id = %s", (uid,)).fetchone()


def hr_histograms(conn: psycopg.Connection, uid: int, tz: ZoneInfo, start: date, end: date, hrv_kind: str) -> dict:
    """local date -> {bpm: count}: the rounded raw heart rate of each day, as scores/engine.score_days counts it."""
    from ..importers import shortcut
    from ..ow_client import OWClient, OWError
    from ..scores import inputs as SI
    try:
        client = OWClient()
    except OWError:
        client = None
    inp = SI.load(conn, client, uid, start, end, shortcut.user_archive_dir(uid), hrv_kind=hrv_kind, ow_cache="use")
    days: dict[date, Counter] = defaultdict(Counter)
    for t, v in inp.hr:
        d = t.astimezone(tz).date()
        if start <= d <= end:
            days[d][round(v)] += 1
    return {d.isoformat(): {str(b): n for b, n in sorted(c.items())} for d, c in sorted(days.items())}


def muscle(conn: psycopg.Connection, uid: int, start: date, end: date) -> dict:
    from ..nativesync.bodies import plain
    from ..scores.muscle import daily_muscle_load
    sessions, error, _ = E.muscle_sessions(conn, uid, start, end)
    if error:
        return {"from": start.isoformat(), "to": end.isoformat(), "days": {}, "error": error}
    loads = daily_muscle_load(sessions)
    return {"from": start.isoformat(), "to": end.isoformat(), "error": None,
            "days": {d.isoformat(): plain(v) for d, v in sorted(loads.items()) if start <= d <= end}}


def context(conn: psycopg.Connection, uid: int, start: date | None = None, end: date | None = None,
            hist_from: date | None = None, hist_to: date | None = None) -> dict:
    """The answer of GET /v1/sync/scoring (see the module doc). Raises ValueError for a bad range."""
    from ..rawdata import engine as EN
    u = _user(conn, uid)
    tz = ZoneInfo(u["timezone"])
    today = datetime.now(tz).date()
    kind, reason = HK.choose(conn, uid, tz, today - timedelta(days=P.APP_RAW_DAYS), today)
    origin = E.scoring_origin(conn, uid)
    st = appscores.state(conn, uid)
    last = conn.execute("""SELECT date, ok, diffs, bundle_version, checked_at FROM app_score_checks WHERE user_id = %s
                           ORDER BY checked_at DESC LIMIT 1""", (uid,)).fetchone()
    out = {
        "tz": u["timezone"], "birth_date": u["birth_date"].isoformat() if u["birth_date"] else None,
        "today": today.isoformat(), "hrv_kind": kind, "hrv_kind_reason": reason,
        "scoring_origin": origin.isoformat() if origin else None, "algo_version": SP.ALGO_VERSION,
        "sync_mode": EN.mode(conn, uid), "raw_days": P.APP_RAW_DAYS, "upload_max_days": P.UPLOAD_MAX_DAYS,
        "app_scoring": {
            "active": appscores.active(conn, uid),
            "trusted": bool(st["trusted"]) if st else None,
            "server_forced": bool(st["server_forced"]) if st else False,
            "bundle_version": st["bundle_version"] if st else None,
            "last_upload_at": st["last_upload_at"].isoformat() if st and st["last_upload_at"] else None,
            "last_check": {"date": last["date"].isoformat(), "ok": last["ok"], "diffs": last["diffs"],
                           "bundle_version": last["bundle_version"], "checked_at": last["checked_at"].isoformat()}
            if last else None,
        },
    }
    if start is not None or end is not None:
        start, end = start or end, end or start
        if end < start or (end - start).days >= P.MUSCLE_MAX_DAYS:
            raise ValueError(f"from / to: at most {P.MUSCLE_MAX_DAYS} days")
        out["muscle"] = muscle(conn, uid, start, end)
    if hist_from is not None or hist_to is not None:
        hist_from, hist_to = hist_from or hist_to, hist_to or hist_from
        if hist_to < hist_from or (hist_to - hist_from).days >= P.HIST_MAX_DAYS:
            raise ValueError(f"hist_from / hist_to: at most {P.HIST_MAX_DAYS} days")
        out["hr_hist"] = {"from": hist_from.isoformat(), "to": hist_to.isoformat(),
                          "days": hr_histograms(conn, uid, tz, hist_from, hist_to, kind)}
    return out

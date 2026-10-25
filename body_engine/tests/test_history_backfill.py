"""History top-up (sync/backfill.py, sync/api.py, assistant/briefing.py, web/static/sync-status.js): the coverage count over every
source, what a short history costs the scores, the reminder rhythm (1 / 3 / 7 days after the first sync, then weekly; mute;
complete; paused after a whole-history top-up), the top-up job the card starts (the re-sync job, the flag-free window for the
app, a stalled ask), the endpoints (own data only, write scope) and the page script's pure helpers (run in node).

Separate test database BODY_TEST_DB_NAME (default body_test_history_backfill), recreated each run; skipped without Postgres.
"""
import json
import os
import shutil
import subprocess
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.sync import backfill, overview, params as P

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_history_backfill")
UTC = timezone.utc
TZ = ZoneInfo("Australia/Sydney")
NOW = datetime.now(UTC).replace(microsecond=0)   # the endpoints use the real clock, so the data is placed relative to it
TODAY = NOW.astimezone(TZ).date()
STATIC = Path(__file__).resolve().parents[1] / "src/body_engine/web/static"
D = timedelta(days=1)


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


@pytest.fixture
def conn(dsn):
    c = psycopg.connect(dsn, row_factory=dict_row)
    yield c
    c.close()


@pytest.fixture
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()
    api.pool = None


def user(conn, *, consent=True) -> int:
    uid = conn.execute("""INSERT INTO users (display_name, role, timezone, ow_user_id, consent_at, consent_version)
                          VALUES ('u', 'member', 'Australia/Sydney', %s, %s, %s) RETURNING id""",
                       (f"ow-{uuid.uuid4().hex[:8]}", NOW if consent else None, "1" if consent else None)).fetchone()["id"]
    conn.commit()
    return uid


def put(conn, uid, metric, days, upto=0, source="ow"):
    """One daily value of `metric` for `days` days ending `upto` days before today."""
    for i in range(upto, upto + days):
        conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, agg, n, source)
                        VALUES (%s, %s, %s, 50, 'avg', 10, %s) ON CONFLICT DO NOTHING""", (uid, TODAY - i * D, metric, source))
    conn.commit()


def sleep(conn, uid, days, upto=0):
    for i in range(upto, upto + days):
        day = TODAY - i * D
        end = datetime(day.year, day.month, day.day, 7, tzinfo=TZ)
        conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, source)
                        VALUES (%s, %s, %s, %s, 420, 'ow')""", (uid, day, end - timedelta(hours=7), end))
    conn.commit()


def watch(conn, uid, days, upto=0):
    """Heart rate, HRV and sleep for `days` days."""
    put(conn, uid, "heart_rate", days, upto)
    put(conn, uid, "heart_rate_variability_sdnn", days, upto)
    sleep(conn, uid, days, upto)


def anchor(conn, uid, days_ago):
    conn.execute("""INSERT INTO history_backfill (user_id, anchor_at) VALUES (%s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET anchor_at = EXCLUDED.anchor_at""", (uid, NOW - days_ago * D))
    conn.commit()


def ev(conn, uid, now=NOW):
    return backfill.evaluate(conn, uid, now)


# ---------------------------------------------------------------- coverage and impact
def test_coverage_is_the_union_of_heart_hrv_and_sleep_inside_the_window(conn):
    uid = user(conn)
    put(conn, uid, "heart_rate", 10)                       # days 0-9
    put(conn, uid, "resting_heart_rate", 3, upto=12)       # days 12-14: resting heart rate counts as heart
    put(conn, uid, "heart_rate_variability_rmssd", 4, upto=20)   # RMSSD counts as HRV
    sleep(conn, uid, 3, upto=30)                           # days 30-32
    put(conn, uid, "step_count", 50)                       # steps are not watch history
    put(conn, uid, "heart_rate", 5, upto=P.BACKFILL_TARGET_DAYS)   # older than the window
    c = backfill.coverage(conn, uid, TODAY)
    assert (c["heart_days"], c["hrv_nights"], c["sleep_nights"]) == (13, 4, 3)
    assert c["days"] == 20 and c["baseline_days"] == 20 and c["baseline_hrv_nights"] == 4


def test_other_sources_count_too(conn):
    uid = user(conn)
    put(conn, uid, "heart_rate", 5, source="shortcut")
    put(conn, uid, "heart_rate", 5, upto=5, source="apple_export")
    put(conn, uid, "heart_rate", 5, upto=10, source="app")
    assert backfill.coverage(conn, uid, TODAY)["days"] == 15


def test_impact_by_baseline_window(conn):
    short, thin, full = user(conn), user(conn), user(conn)
    watch(conn, short, 10)                                   # 10 HRV nights < 14: no recovery score yet
    watch(conn, thin, 30)                                    # 14+ nights but only 30 of the 60 baseline days
    watch(conn, full, 60)
    assert backfill.impact(backfill.coverage(conn, short, TODAY)) == "baseline"
    assert backfill.impact(backfill.coverage(conn, thin, TODAY)) == "thin"
    assert backfill.impact(backfill.coverage(conn, full, TODAY)) is None
    assert backfill.impact_label("baseline") and backfill.impact_label("thin") and backfill.impact_label(None) is None
    # the HRV nights must be inside the baseline window, not just inside the 90 days
    far = user(conn)
    watch(conn, far, 20, upto=65)
    assert backfill.impact(backfill.coverage(conn, far, TODAY)) == "baseline"


# ---------------------------------------------------------------- the rhythm (pure)
def d(day):
    return date(2026, 10, day)


@pytest.mark.parametrize("last, expected", [
    (None, 2), (d(2), 4), (d(4), 8), (d(8), 15), (d(15), 22),     # 1, 3, 7 days after Oct 1, then weekly
    (d(6), 8), (d(9), 16), (d(1), 2), (d(3), 4),                   # a late first look skips the days already gone
])
def test_next_due(last, expected):
    assert backfill.next_due(d(1), last) == d(expected)


def decide(**kw):
    base = dict(consent_ok=True, days=14, target=90, anchor=d(1), last_shown=None, today=d(2), muted=False, completed=False,
                exhausted=False, active=False, failed=False)
    return backfill.decide(**{**base, **kw})


def test_decide_order_and_stops():
    assert decide()[0] == "due"
    assert decide(today=d(1))[0] == "later" and decide(today=d(1))[1] == d(2)
    assert decide(last_shown=d(2))[0] == "due"                                    # the reminder day stays up all day
    assert decide(last_shown=d(2), today=d(3)) == ("later", d(4))
    assert decide(last_shown=d(2), today=d(4))[0] == "due"
    assert decide(muted=True)[0] == "muted"
    assert decide(days=90)[0] == "complete" and decide(completed=True, days=40)[0] == "complete"
    assert decide(days=0)[0] == "none" and decide(anchor=None)[0] == "none"
    assert decide(exhausted=True)[0] == "exhausted"
    assert decide(failed=True)[0] == "error" and decide(failed=True, muted=True)[0] == "error"
    assert decide(consent_ok=False)[0] == "consent"
    # a running top-up beats everything but consent, and is not a reminder
    assert decide(active=True, muted=True, exhausted=True, days=90)[0] == "progress"


# ---------------------------------------------------------------- state
def test_first_sight_creates_the_row_from_the_first_data_time(conn):
    uid = user(conn)
    assert ev(conn, uid)["remind"]["state"] == "none"                           # no watch data yet
    assert conn.execute("SELECT count(*) AS n FROM history_backfill WHERE user_id = %s", (uid,)).fetchone()["n"] == 0
    watch(conn, uid, 14)
    conn.execute("UPDATE daily_metrics SET updated_at = %s WHERE user_id = %s", (NOW - 3 * D, uid))
    conn.commit()
    out = ev(conn, uid)
    row = conn.execute("SELECT * FROM history_backfill WHERE user_id = %s", (uid,)).fetchone()
    assert row["anchor_at"] == NOW - 3 * D
    assert out["days"] == 14 and out["target_days"] == 90 and out["impact"] == "thin"   # 14 nights is just enough for a recovery score
    assert out["remind"]["state"] == "due" and out["remind"]["show"] is True      # day 3 of the rhythm has passed: first look shows it
    assert out["status"] == "building" and out["action"] == {"kind": "backfill", "days": 90}


def test_reminder_days_each_shown_once(conn):
    uid = user(conn)
    watch(conn, uid, 14)
    anchor(conn, uid, 0)                                                         # first sync today
    assert ev(conn, uid)["remind"]["state"] == "later" and ev(conn, uid)["remind"]["next_on"] == (TODAY + D).isoformat()
    day1 = NOW + D
    assert ev(conn, uid, day1)["remind"]["show"] is True
    s1 = backfill.mark_seen(conn, uid, day1)
    assert s1["remind"]["state"] == "due" and s1["remind"]["shown_count"] == 1
    assert backfill.mark_seen(conn, uid, day1 + timedelta(hours=1))["remind"]["shown_count"] == 1   # same local day: one reminder
    assert ev(conn, uid, day1 + D)["remind"]["state"] == "later"                  # day 2: quiet
    assert ev(conn, uid, day1 + 2 * D)["remind"]["show"] is True                  # day 3
    backfill.mark_seen(conn, uid, day1 + 2 * D)
    assert ev(conn, uid, day1 + 5 * D)["remind"]["show"] is False
    assert ev(conn, uid, day1 + 6 * D)["remind"]["show"] is True                  # day 7
    backfill.mark_seen(conn, uid, day1 + 6 * D)
    assert ev(conn, uid, day1 + 12 * D)["remind"]["show"] is False
    assert ev(conn, uid, day1 + 13 * D)["remind"]["show"] is True                 # weekly from here
    assert backfill.mark_seen(conn, uid, day1 + 13 * D)["remind"]["shown_count"] == 4


def test_seen_does_nothing_unless_a_reminder_is_up(conn):
    uid = user(conn)
    watch(conn, uid, 14)
    anchor(conn, uid, 0)
    assert backfill.mark_seen(conn, uid, NOW)["remind"]["shown_count"] == 0


def test_mute_and_unmute(conn):
    uid = user(conn)
    watch(conn, uid, 14)
    anchor(conn, uid, 5)
    assert ev(conn, uid)["remind"]["show"] is True
    m = backfill.set_muted(conn, uid, True, NOW)
    assert m["remind"] == {**m["remind"], "state": "muted", "show": False, "muted": True}
    assert backfill.mark_seen(conn, uid, NOW)["remind"]["shown_count"] == 0
    assert backfill.briefing_hint(conn, uid) is None                              # the AI is told to leave it alone, too
    assert backfill.set_muted(conn, uid, False, NOW)["remind"]["show"] is True
    # muting before any watch data exists makes the row too
    fresh = user(conn)
    assert backfill.set_muted(conn, fresh, True, NOW)["remind"]["muted"] is True


def test_reaching_the_target_stops_for_good(conn):
    uid = user(conn)
    watch(conn, uid, 90)
    anchor(conn, uid, 20)
    out = ev(conn, uid)
    assert out["days"] == 90 and out["remind"]["state"] == "complete" and out["status"] == "complete"
    assert conn.execute("SELECT completed_at FROM history_backfill WHERE user_id = %s", (uid,)).fetchone()["completed_at"] == NOW
    conn.execute("DELETE FROM daily_metrics WHERE user_id = %s AND date < %s", (uid, TODAY - 30 * D))   # a break from wearing it
    conn.execute("DELETE FROM sleep_sessions WHERE user_id = %s AND wake_date < %s", (uid, TODAY - 30 * D))
    conn.commit()
    later = ev(conn, uid, NOW + 10 * D)
    assert later["days"] < 90 and later["remind"]["state"] == "complete"
    assert backfill.briefing_hint(conn, uid) is None


def test_no_consent_no_card(conn):
    uid = user(conn, consent=False)
    watch(conn, uid, 14)
    anchor(conn, uid, 5)
    out = ev(conn, uid)
    assert out["remind"]["state"] == "consent" and out["remind"]["show"] is False
    assert backfill.briefing_hint(conn, uid) is None


def test_a_finished_whole_history_top_up_pauses_the_reminders(conn):
    uid = user(conn)
    watch(conn, uid, 14)
    anchor(conn, uid, 5)
    conn.execute("""INSERT INTO sync_resync (user_id, status, days, finished_at, requested_at) VALUES (%s, 'done', NULL, %s, %s)""",
                 (uid, NOW - D, NOW - 2 * D))
    conn.commit()
    out = ev(conn, uid)
    assert out["remind"]["state"] == "exhausted" and out["remind"]["show"] is False
    assert ev(conn, uid, NOW + 29 * D)["remind"]["state"] == "exhausted"
    assert ev(conn, uid, NOW + 31 * D)["remind"]["show"] is True                  # the pause ends; a new look is allowed
    # a done job for fewer days than the target says nothing about the rest of the history
    other = user(conn)
    watch(conn, other, 14)
    anchor(conn, other, 5)
    conn.execute("INSERT INTO sync_resync (user_id, status, days, finished_at) VALUES (%s, 'done', 30, %s)", (other, NOW - D))
    conn.commit()
    assert ev(conn, other)["remind"]["state"] == "due"
    # the read-range backfill (health_history, scope all) counts as a whole-history top-up
    third = user(conn)
    watch(conn, third, 14)
    anchor(conn, third, 5)
    conn.execute("INSERT INTO health_history (user_id, scope, status, finished_at) VALUES (%s, 'all', 'done', %s)", (third, NOW - D))
    conn.commit()
    assert ev(conn, third)["remind"]["state"] == "exhausted"


# ---------------------------------------------------------------- progress
def test_progress_from_every_way_a_top_up_runs(conn):
    uid = user(conn)
    watch(conn, uid, 14)
    anchor(conn, uid, 5)
    conn.execute("INSERT INTO sync_resync (user_id, status, days, phone_percent) VALUES (%s, 'phone', 90, 50)", (uid,))
    conn.commit()
    out = ev(conn, uid, datetime.now(UTC))
    assert out["progress"]["active"] and out["progress"]["via"] == "resync" and out["progress"]["percent"] == 30
    assert out["remind"]["state"] == "progress" and out["remind"]["show"] is True
    assert backfill.mark_seen(conn, uid, datetime.now(UTC))["remind"]["shown_count"] == 0      # progress is not a reminder
    conn.execute("UPDATE sync_resync SET status = 'importing', import_from = %s, import_until = %s, import_cursor = %s WHERE user_id = %s",
                 (TODAY - 90 * D, TODAY, TODAY - 45 * D, uid))
    conn.commit()
    assert 60 < ev(conn, uid, datetime.now(UTC))["progress"]["percent"] < 95
    conn.execute("UPDATE sync_resync SET status = 'done', finished_at = now(), days = 30 WHERE user_id = %s", (uid,))
    conn.execute("INSERT INTO health_history (user_id, scope, status, upload_percent) VALUES (%s, 'all', 'uploading', 50)", (uid,))
    conn.commit()
    p = ev(conn, uid, datetime.now(UTC))["progress"]
    assert p["active"] and p["via"] == "range"
    conn.execute("DELETE FROM health_history WHERE user_id = %s", (uid,))
    conn.execute("""INSERT INTO history_imports (user_id, status, filename, size_bytes, received_bytes)
                    VALUES (%s, 'uploading', 'export.zip', 1000, 500)""", (uid,))
    conn.commit()
    p = ev(conn, uid, datetime.now(UTC))["progress"]
    assert p["active"] and p["via"] == "import" and p["percent"] == 20


# ---------------------------------------------------------------- the card's job
def test_request_starts_the_resync_job_and_opens_the_app_window(conn):
    uid = user(conn)
    watch(conn, uid, 14)
    anchor(conn, uid, 5)
    assert backfill.allows_resync(conn, uid) is False
    res = backfill.request(conn, uid)
    assert res["accepted"] is True and res["status"] == "phone" and res["days"] == 90
    assert res["coverage"]["remind"]["state"] == "progress"
    assert backfill.allows_resync(conn, uid) is True
    assert backfill.allows_resync(conn, uid, datetime.now(UTC) + 3 * D) is False        # the window closes after 48 hours
    again = backfill.request(conn, uid)
    assert again["accepted"] is False and again["reason"] == "busy"
    assert backfill.request(conn, user(conn, consent=False))["reason"] == "consent"


def test_a_top_up_the_app_never_took_up_is_an_error_and_can_be_asked_again(conn):
    uid = user(conn)
    watch(conn, uid, 14)
    anchor(conn, uid, 5)
    backfill.request(conn, uid)
    conn.execute("UPDATE sync_resync SET updated_at = now() - interval '20 minutes' WHERE user_id = %s", (uid,))
    conn.commit()
    out = ev(conn, uid, datetime.now(UTC))
    assert out["progress"]["active"] is False and out["remind"]["state"] == "error" and out["remind"]["show"] is True
    # ... and the retry button works at once (the old job is voided), unless the app did report something
    conn.execute("UPDATE sync_resync SET requested_at = now() - interval '11 minutes' WHERE user_id = %s", (uid,))
    conn.commit()
    assert backfill.request(conn, uid)["accepted"] is True
    conn.execute("UPDATE sync_resync SET updated_at = now() - interval '20 minutes', phone_percent = 7 WHERE user_id = %s", (uid,))
    conn.commit()
    assert backfill.request(conn, uid)["reason"] == "busy"


def test_a_failed_job_shows_a_retry_for_a_day(conn):
    uid = user(conn)
    watch(conn, uid, 14)
    anchor(conn, uid, 5)
    backfill.request(conn, uid)
    conn.execute("UPDATE sync_resync SET status = 'error', error = 'x' WHERE user_id = %s", (uid,))
    conn.commit()
    assert ev(conn, uid, datetime.now(UTC))["remind"]["state"] == "error"
    assert ev(conn, uid, datetime.now(UTC) + 2 * D)["remind"]["state"] != "error"


def test_the_overview_carries_the_answer(conn):
    uid = user(conn)
    watch(conn, uid, 14)
    anchor(conn, uid, 5)
    out = overview.collect(conn, uid, NOW)
    assert out["coverage"]["days"] == 14 and out["coverage"]["remind"]["state"] == "due"


# ---------------------------------------------------------------- the AI briefing
def test_briefing_hint(conn):
    uid = user(conn)
    assert backfill.briefing_hint(conn, uid) is None                                 # nothing to say before any data
    watch(conn, uid, 14)
    anchor(conn, uid, 5)
    h = backfill.briefing_hint(conn, uid)
    assert h["days"] == 14 and h["of"] == 90 and h["effect"] and "补全历史数据" in h["how"]
    backfill.request(conn, uid)
    h = backfill.briefing_hint(conn, uid)
    assert "how" not in h and h["topping_up"] == 0                                    # running: no need to bring it up
    from body_engine.assistant import briefing
    uid2 = user(conn)
    watch(conn, uid2, 14)
    anchor(conn, uid2, 5)
    assert briefing.get(conn, uid2, uid2)["history_gap"]["of"] == 90
    other = user(conn)
    viewer = user(conn)
    watch(conn, other, 14)
    assert "history_gap" not in briefing.get(conn, viewer, other)                       # only one's own briefing


# ---------------------------------------------------------------- endpoints
def test_endpoints(conn, client):
    uid = user(conn)
    other = user(conn)
    watch(conn, uid, 14)
    anchor(conn, uid, 5)
    tok = auth.create_token(conn, uid, "app", ["read", "write"])
    ro = auth.create_token(conn, uid, "ro", ["read"])
    conn.commit()
    h = {"Authorization": f"Bearer {tok}"}
    r = client.get("/v1/users/me/sync/history", headers=h)
    assert r.status_code == 200 and r.json()["days"] == 14 and r.json()["remind"]["state"] == "due"
    assert client.get(f"/v1/users/{other}/sync/history", headers=h).status_code == 403
    assert client.get("/v1/users/me/sync/history").status_code == 401
    assert client.get("/v1/users/me/sync/overview", headers=h).json()["coverage"]["days"] == 14

    ro_h = {"Authorization": f"Bearer {ro}"}
    assert client.get("/v1/users/me/sync/history", headers=ro_h).status_code == 200
    for method, path, body in (("post", "/v1/users/me/sync/history/seen", {}), ("put", "/v1/users/me/sync/history/mute", {"muted": True}),
                               ("post", "/v1/users/me/sync/backfill", {"days": 90})):
        assert getattr(client, method)(path, headers=ro_h, json=body).status_code == 403, path
    assert client.post(f"/v1/users/{other}/sync/backfill", headers=h, json={}).status_code == 403

    r = client.post("/v1/users/me/sync/history/seen", headers=h, json={})
    assert r.status_code == 200 and r.json()["remind"]["shown_count"] == 1
    r = client.put("/v1/users/me/sync/history/mute", headers=h, json={"muted": True})
    assert r.json()["remind"]["state"] == "muted"
    assert client.put("/v1/users/me/sync/history/mute", headers=h, json={"muted": False, "x": 1}).status_code == 422
    assert client.put("/v1/users/me/sync/history/mute", headers=h, json={"muted": False}).json()["remind"]["muted"] is False

    assert client.post("/v1/users/me/sync/backfill", headers=h, json={"days": 0}).status_code == 422
    # without the sync_center flag the app cannot use the re-sync endpoint ...
    assert client.get("/v1/ingest/resync", headers=h).status_code == 404
    r = client.post("/v1/users/me/sync/backfill", headers=h, json={})
    assert r.status_code == 202 and r.json()["accepted"] is True and r.json()["days"] == 90
    # ... until the card has started a top-up: then it reads the job, reports progress and finishes the phone part
    r = client.get("/v1/ingest/resync", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "phone" and r.json()["days"] == 90
    r = client.put("/v1/ingest/resync", headers=h, json={"phone_percent": 40})
    assert r.status_code == 200 and r.json()["phone_percent"] == 40
    r = client.post("/v1/users/me/sync/backfill", headers=h, json={"days": 90})
    assert r.status_code == 200 and r.json()["accepted"] is False and r.json()["reason"] == "busy"
    assert client.get("/v1/users/me/sync/history", headers=h).json()["progress"]["percent"] == 24
    # all history
    conn.execute("UPDATE sync_resync SET status = 'cancelled' WHERE user_id = %s", (uid,))
    conn.execute("UPDATE sync_resync SET requested_at = now() - interval '7 hours' WHERE user_id = %s", (uid,))
    conn.commit()
    r = client.post("/v1/users/me/sync/backfill", headers=h, json={"days": None})
    assert r.status_code == 202 and r.json()["days"] is None


# ---------------------------------------------------------------- page script (sync-status.js, run in node)
def node_json(expr: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    probe = ("globalThis.window = undefined;\n"
             "globalThis.I18N = { t: (k, p) => k.replace(/\\{(\\w+)\\}/g, (_, n) => (p || {})[n]) };\n"
             f"const src = require('fs').readFileSync({json.dumps(str(STATIC / 'sync-status.js'))}, 'utf8')"
             ".replace('window.I18N.t(k, p)', 'I18N.t(k, p)');\n"
             "const module = { exports: {} }; new Function('module', src)(module); const P = module.exports;\n"
             f"console.log(JSON.stringify({expr}));")
    return json.loads(subprocess.run([node, "-e", probe], capture_output=True, text=True, check=True).stdout)


def test_page_helpers():
    out = node_json("""(() => {
      const cov = (state, extra) => ({ remind: { show: state === "due" || state === "progress" || state === "error", state },
                                       progress: { active: state === "progress", percent: 40 }, days: 14, target_days: 90, ...extra });
      const ok = (c, extra) => ({ state: "ok", coverage: c, ...extra });
      return [
        [P.backfillLink(90), P.backfillLink(null), P.backfillLink(30)],
        [P.wantsBackfill(ok(cov("due")), "today", true, true), P.wantsBackfill(ok(cov("due")), "trends", true, true),
         P.wantsBackfill(ok(cov("due")), "today", false, true), P.wantsBackfill(ok(cov("due")), "today", true, false),
         P.wantsBackfill(ok(cov("later")), "today", true, true), P.wantsBackfill(ok(cov("muted")), "today", true, true),
         P.wantsBackfill({ state: "issue", coverage: cov("due") }, "today", true, true),
         P.wantsBackfill({ state: "issue", coverage: cov("progress") }, "today", true, true),
         P.wantsBackfill({ state: "ok" }, "today", true, true), P.wantsBackfill(null, "today", true, true)],
        [P.backfillAction({ backfill: true }, false), P.backfillAction({ backfill: true, syncCenter: true }, true),
         P.backfillAction({ syncCenter: true }, true), P.backfillAction({ syncCenter: true }, false), P.backfillAction({}, true), P.backfillAction(null, false)],
        [P.backfillSub(cov("due")), P.backfillSub(cov("due", { impact_label: "Baseline" })), P.backfillSub(cov("progress")),
         P.backfillSub(cov("error")), P.backfillSub({ ...cov("progress"), progress: { active: true, percent: null } })],
        [P.backfillPercent(cov("due")), P.backfillPercent(cov("progress")), P.backfillPercent(cov("due", { days: 90 })), P.backfillPercent(cov("due", { days: 0 }))],
      ];
    })()""")
    assert out[0] == ["watch2whoop://backfill?days=90", "watch2whoop://backfill?days=all", "watch2whoop://backfill?days=30"]
    assert out[1] == [True, False, False, False, False, False, False, True, False, False]   # only in the app, own Today tab, a reminder day or a running top-up
    assert out[2] == [{"kind": "bridge"}, {"kind": "bridge"},
                      {"kind": "link", "href": "watch2whoop://sync-center"}, {"kind": "link", "href": "watch2whoop://sync"},
                      {"kind": "link", "href": "watch2whoop://sync"}, {"kind": "link", "href": "watch2whoop://sync"}]
    assert out[3] == ["14 / 90 天", "14 / 90 天 · Baseline", "补全中 40%", "没能补全", "补全中 0%"]
    assert out[4] == [16, 40, 100, 0]


def test_page_wiring():
    js = (STATIC / "sync-status.js").read_text(encoding="utf-8")
    for url in ("/v1/users/me/sync/backfill", "/v1/users/me/sync/history/seen", "/v1/users/me/sync/history/mute"):
        assert url in js, url
    assert "watch2whoop://backfill?days=" in js and "Watch2WhoopFeatures" in js
    assert 'ev.kind === "backfill"' in js                                  # the app's progress for a top-up never reaches the connect flow
    assert js.count("localStorage.setItem(") == 1                          # the reminder state lives on the server, not in the page
    assert "ov.coverage" in js and 'data-ss="bf_mute"' in js
    assert "v2.140" in (STATIC.parent / "api.py").read_text(encoding="utf-8") and "be-shell-v143" in (STATIC / "sw.js").read_text(encoding="utf-8")
    doc = (Path(__file__).resolve().parents[2] / "docs/sync-onboarding-app-requirements.md").read_text(encoding="utf-8")
    assert "watch2whoop://backfill?days=" in doc and "Watch2WhoopFeatures.backfill" in doc

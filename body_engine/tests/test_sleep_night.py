"""Sleep detail page (sleep_night/, rollout flag sleep_detail): the night's numbers, stage composition and timeline, score parts,
own averages, vitals, notes; degrading without stages; shares (sleep for the page, scores for score / vitals); the flag off = 404.

Pure parts (notes) need no database. The rest uses its own test database BODY_TEST_DB_NAME_SLEEP (default body_test_sleep_night),
recreated each run; skipped without Postgres. OW is never contacted (views.ow_segments is patched).
"""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.i18n import use_lang
from body_engine.importers.common import Segment
from body_engine.rollout import flags
from body_engine.sleep_night import params as P, view
from body_engine.web import views

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_SLEEP", "body_test_sleep_night")
TZ = ZoneInfo("Australia/Sydney")
TODAY = datetime.now(TZ).date()
NIGHT = TODAY - timedelta(days=1)          # the night we look at (wake date)
FLAG = "sleep_detail"
_ids: dict = {}                            # user name -> id, filled by the world fixture


# ---------------------------------------------------------------- pure: notes
def _night(**kw):
    base = {"start": "23:30", "end": "07:00", "deep_pct": 18, "rem_pct": 22, "efficiency_pct": 92.0, "latency_min": 10,
            "waso_min": 10, "awakenings": 1}
    return {**base, **kw}


def _score(need=480, actual=470, parts=None):
    parts = parts or {"duration": 90, "stages": 85, "consistency": 88, "interruptions": 95}
    return {"value": 85, "need_min": need, "actual_min": actual,
            "parts": [{"key": k, "score": v, "weight": 0.25} for k, v in parts.items()]}


def test_notes_all_good():
    n = view.notes(_night(), _score(), {"avg30": {"bedtime": "23:40"}}, [], None)
    assert [x["key"] for x in n] == ["all_good"] or {x["tone"] for x in n} == {"good"}


def test_notes_flag_problems_worst_first(monkeypatch):
    night = _night(start="02:30", deep_pct=7, rem_pct=12, efficiency_pct=70.0, latency_min=50, waso_min=60, awakenings=4)
    vitals = [{"key": "hrv", "value": 30, "avg30": 50, "delta": -20}, {"key": "rhr", "value": 62, "avg30": 55, "delta": 7},
              {"key": "spo2", "value": 92.5, "avg30": 96, "delta": -3.5}]
    n = view.notes(night, _score(need=540, actual=400, parts={"duration": 40, "stages": 60, "consistency": 30, "interruptions": 70}),
                   {"avg30": {"bedtime": "23:30"}}, vitals, {"illness_warning": "x"})
    keys = [x["key"] for x in n]
    assert len(n) == P.MAX_NOTES
    assert [x["tone"] for x in n] == sorted((x["tone"] for x in n), key=view.TONE_ORDER.get)   # bad first
    assert keys[0] == "short_of_need" and "weak_consistency" in keys
    assert "all_good" not in keys and "need_met" not in keys
    monkeypatch.setattr(P, "MAX_NOTES", 99)
    every = {x["key"] for x in view.notes(night, None, {"avg30": {"bedtime": "23:30"}}, vitals, {"illness_warning": "x"})}
    assert {"deep_low", "rem_low", "efficiency_low", "latency_long", "waso_long", "bedtime_late", "hrv_low", "rhr_high",
            "spo2_low", "illness"} <= every


def test_notes_bedtime_across_midnight_and_language():
    # 00:30 vs an average of 23:45 is 45 min later (not 23 h earlier): no note; 01:00 is 75 min later -> note
    assert "bedtime_late" not in {x["key"] for x in view.notes(_night(start="00:30"), None, {"avg30": {"bedtime": "23:45"}}, [], None)}
    with use_lang("en"):
        n = view.notes(_night(start="01:00"), None, {"avg30": {"bedtime": "23:45"}}, [], None)
    late = next(x for x in n if x["key"] == "bedtime_late")
    assert late["text"] == "Asleep 1h 15m later than your 30-night average"
    assert view._circ_mean([23 * 60 + 30, 30, 0]) == 0 and view._circ_mean([0, 30]) is None   # around midnight; < 3 nights: none


# ---------------------------------------------------------------- database
@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # no password file, or the database is not running
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


def _sleep(conn, uid, wake, bed_h, bed_m, hours, deep, core, rem, awake, unspec=None):
    start = datetime.combine(wake - timedelta(days=1), datetime.min.time(), TZ) + timedelta(hours=bed_h, minutes=bed_m)
    asleep = (deep or 0) + (core or 0) + (rem or 0) + (unspec or 0)
    conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, deep_min, core_min, rem_min,
                      awake_min, unspecified_min, is_main, source, device)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true, 'ow', 'Apple Watch')""",
                 (uid, wake, start, start + timedelta(hours=hours), asleep, deep, core, rem, awake, unspec))
    return start


@pytest.fixture(scope="module")
def world(dsn):
    """A: 20 nights (every night but 3 days ago), scores with details; the last night has its stage timeline.
    B: shared sleep only; D: shared sleep + scores; C: shared scores only; E: no stage data at all (Shortcut-only nights)."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name) VALUES (%s) RETURNING id", (n,)).fetchone()["id"]
               for n in "ABCDE"}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        for viewer, scopes in (("B", ["sleep"]), ("D", ["sleep", "scores"]), ("C", ["scores"])):
            conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["A"], ids[viewer], scopes))
        a = ids["A"]
        for k in range(1, 21):
            wake = TODAY - timedelta(days=k)
            if k == 3:
                continue   # a gap: prev of 2 days ago jumps to 4 days ago
            if k == 1:     # NIGHT: 23:30 -> 06:30 (7 h), 60 deep / 240 core / 80 rem / 40 awake
                _sleep(conn, a, wake, 23, 30, 7, 60, 240, 80, 40)
            else:          # usual nights: 23:00 -> 07:00, 90 / 270 / 100 / 20
                _sleep(conn, a, wake, 23, 0, 8, 90, 270, 100, 20)
            ss = 72 if k == 1 else 85
            details = {"sleep": {"duration": 70 if k == 1 else 90, "stages": 60, "consistency": 80, "interruptions": 75,
                                 "efficiency": 0.905 if k == 1 else 0.96, "latency_min": 12.0, "waso_min": 25.0,
                                 "awakenings": 3, "need_from_debt": 30, "need_from_strain": 10, "nap_min": 0},
                       "recovery": {"spo2_night": 95.5 if k == 1 else 97.0, "wrist_temp": 35.9 if k == 1 else 35.2}}
            conn.execute("""INSERT INTO daily_scores (user_id, date, sleep_score, sleep_performance, sleep_actual_min,
                              sleep_need_min, sleep_debt_min, hrv_night_ln, rhr_night, resp_rate_night, details, flags, algo_version)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, ln(%s), %s, %s, %s, '{}', 'test')""",
                         (a, wake, ss, 0.73 if k == 1 else 0.95, 380 if k == 1 else 460, 520, 40,
                          40.0 if k == 1 else 60.0, 58 if k == 1 else 52, 15.0, Jsonb(details)))
        # a nap the afternoon of NIGHT's wake date
        nap = datetime.combine(NIGHT, datetime.min.time(), TZ) + timedelta(hours=14)
        conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, is_main, source)
                        VALUES (%s, %s, %s, %s, 20, false, 'ow')""", (a, NIGHT, nap, nap + timedelta(minutes=20)))
        # E: one night from a source without stages (only asleep / in bed)
        e = ids["E"]
        start = datetime.combine(NIGHT - timedelta(days=1), datetime.min.time(), TZ) + timedelta(hours=23)
        conn.execute("""INSERT INTO sleep_sessions (user_id, wake_date, start_at, end_at, asleep_min, unspecified_min, is_main, source)
                        VALUES (%s, %s, %s, %s, 420, 420, true, 'shortcut')""", (e, NIGHT, start, start + timedelta(hours=7.5)))
        flags.set_flag(conn, FLAG, "all", actor="test")
    flags.invalidate()
    _ids.update(ids)
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


@pytest.fixture(autouse=True)
def no_ow(monkeypatch):
    """Stage timeline: OW is off, the Shortcut samples give NIGHT's stages for user A only."""
    def ow_off(*a):
        raise RuntimeError("OW off")

    def shortcut(conn, owner_id, start, end):
        if start.astimezone(TZ).date() != NIGHT - timedelta(days=1) or owner_id == _ids.get("E"):
            return []
        t, out = start, []
        for stage, m in (("core", 30), ("deep", 60), ("core", 120), ("rem", 40), ("awake", 40), ("core", 90), ("rem", 40)):
            out.append(Segment(t, t + timedelta(minutes=m), stage, "Apple Watch"))
            t += timedelta(minutes=m)
        return out
    monkeypatch.setattr(views, "ow_segments", ow_off)
    monkeypatch.setattr(views, "shortcut_segments", shortcut)
    monkeypatch.setattr(views, "export_segments", lambda *a: [])



def get(client, tok, path, **kw):
    return client.get(path, headers={"Authorization": f"Bearer {tok}", **kw.pop("headers", {})}, **kw)


def test_night_numbers(client, world):
    ids, tok = world
    d = get(client, tok["A"], f"/v1/users/me/sleep/night?date={NIGHT}").json()
    m = d["main"]
    assert d["date"] == NIGHT.isoformat() and d["next"] is None and d["is_latest"] is True
    assert d["prev"] == (TODAY - timedelta(days=2)).isoformat()
    assert (m["start"], m["end"], m["in_bed_min"], m["asleep_min"]) == ("23:30", "06:30", 420, 380)
    assert m["efficiency_pct"] == 90.5 and m["latency_min"] == 12 and m["waso_min"] == 25 and m["awakenings"] == 3
    assert m["deep_pct"] == 16 and m["rem_pct"] == 21     # of time asleep
    comp = {c["stage"]: (c["min"], c["pct"]) for c in m["composition"]}
    assert comp == {"deep": (60, 14), "core": (240, 57), "rem": (80, 19), "awake": (40, 10)}   # of 420 min, no "unspecified"
    assert m["stages_source"] == "shortcut" and m["stages"][0] == {"stage": "core", "start": "23:30", "end": "00:00",
                                                                    "offset_min": 0.0, "minutes": 30.0}
    assert sum(s["minutes"] for s in m["stages"]) == 420
    assert d["naps"] == [{"start": "14:00", "end": "14:20", "asleep_min": 20}]
    s = d["score"]
    assert s["value"] == 72 and s["performance_pct"] == 73 and (s["need_min"], s["actual_min"], s["debt_min"]) == (520, 380, 40)
    assert [(p["key"], p["score"], p["weight"]) for p in s["parts"]] == [
        ("duration", 70, 0.4), ("stages", 60, 0.2), ("consistency", 80, 0.2), ("interruptions", 75, 0.2)]
    b = d["baseline"]
    assert b["avg7"]["nights"] == 6 and b["avg30"]["nights"] == 18          # the night itself is not in its own average
    assert b["avg7"]["asleep_min"] == 460 and b["avg7"]["bedtime"] == "23:00" and b["avg7"]["wake"] == "07:00"
    assert b["avg7"]["sleep_score"] == 85 and b["avg7"]["efficiency_pct"] == 95.8 and b["need_min"] == 520
    assert b["regularity_pct_30d"] is not None
    v = {x["key"]: x for x in d["vitals"]}
    assert v["hrv"] == {"key": "hrv", "value": 40, "unit": "ms", "avg30": 60, "delta": -20}
    assert v["rhr"]["delta"] == 6 and v["spo2"]["value"] == 95.5 and v["wrist_temp"]["delta"] == 0.7
    # 1 bad + 6 mid findings, capped at MAX_NOTES, worst first, then in the order they are checked
    assert [n["key"] for n in d["notes"]] == ["short_of_need", "weak_stages", "waso_long", "hrv_low", "rhr_high", "temp_off"]
    assert d["notes"][0]["tone"] == "bad" and d["notes"][0]["text"] == "比睡眠需求少 2 小时 20 分"
    assert f"{NIGHT.day} 日" in d["ai_prompt"] and "23:30" in d["ai_prompt"] and d["hidden"] == []


def test_default_is_latest_and_navigation(client, world):
    _, tok = world
    d = get(client, tok["A"], "/v1/users/me/sleep/night").json()
    assert d["date"] == NIGHT.isoformat()
    two = get(client, tok["A"], f"/v1/users/me/sleep/night?date={TODAY - timedelta(days=2)}").json()
    assert two["prev"] == (TODAY - timedelta(days=4)).isoformat() and two["next"] == NIGHT.isoformat()
    gap = get(client, tok["A"], f"/v1/users/me/sleep/night?date={TODAY - timedelta(days=3)}").json()
    assert gap["main"] is None and gap["notes"] == [] and gap["reason"] and gap["prev"] and gap["next"]


def test_degrades_without_stages(client, world):
    ids, tok = world
    d = get(client, tok["E"], f"/v1/users/me/sleep/night?date={NIGHT}").json()
    m = d["main"]
    assert m["staged"] is False and m["stages"] == [] and m["stages_reason"]
    assert m["deep_pct"] is None and m["rem_pct"] is None
    assert [c["stage"] for c in m["composition"]] == ["unspecified"]
    assert m["efficiency_pct"] == round(100 * 420 / 450, 1)            # no scores row: asleep / in bed
    assert d["score"] is None and d["vitals"] == [] and d["notes"][0]["key"] == "all_good"
    empty = get(client, tok["E"], f"/v1/users/me/sleep/night?date={TODAY}").json()
    assert empty["main"] is None and empty["prev"] == NIGHT.isoformat()


def test_shares(client, world):
    ids, tok = world
    path = f"/v1/users/{ids['A']}/sleep/night?date={NIGHT}"
    full = get(client, tok["D"], path).json()                  # sleep + scores: everything
    assert full["score"]["value"] == 72 and full["vitals"] and full["hidden"] == []
    only_sleep = get(client, tok["B"], path).json()             # sleep only: no score, need, vitals, latency
    assert only_sleep["main"]["asleep_min"] == 380 and only_sleep["main"]["stages"]
    assert only_sleep["score"] is None and only_sleep["vitals"] == [] and only_sleep["hidden"] == ["scores"]
    assert "sleep_score" not in only_sleep["baseline"]["avg7"] and "need_min" not in only_sleep["baseline"]
    assert only_sleep["main"]["latency_min"] is None and only_sleep["main"]["awakenings"] is None
    assert not {n["key"] for n in only_sleep["notes"]} & {"short_of_need", "hrv_low", "rhr_high"}
    assert get(client, tok["C"], path).status_code == 403      # scores but no sleep share
    assert get(client, tok["E"], path).status_code == 403      # no share at all


def test_flag_off_is_404_and_old_endpoints_unchanged(client, world, dsn):
    _, tok = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        flags.set_flag(conn, FLAG, "off", actor="test")
    flags.invalidate()
    try:
        assert get(client, tok["A"], f"/v1/users/me/sleep/night?date={NIGHT}").status_code == 404
        nights = get(client, tok["A"], "/v1/users/me/sleep/nights?days=2").json()
        assert nights["nights"][0]["main"]["start"] == "23:30"
        today = get(client, tok["A"], "/v1/users/me/today").json()["sleep"]
        assert today["main"]["start"] == "23:30" and today["stages_source"] == "shortcut"
        assert FLAG not in get(client, tok["A"], "/v1/users/me/flags").json().get("flags", [])
    finally:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            flags.set_flag(conn, FLAG, "all", actor="test")
        flags.invalidate()


def test_english(client, world):
    _, tok = world
    d = get(client, tok["A"], f"/v1/users/me/sleep/night?date={NIGHT}", headers={"X-Body-Lang": "en"}).json()
    assert d["ai_prompt"].startswith("Analyse my sleep") and not any("\u4e00" <= ch <= "\u9fff" for n in d["notes"] for ch in n["text"])


def test_jarvis_sleep_card(world, dsn):
    """show_card type sleep (jarvis_rich): built from the same night view; the model gets a one-line summary without numbers to repeat."""
    from body_engine.ai import cards
    ids, _ = world
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        card = cards.build(conn, ids["A"], {"type": "sleep", "date": NIGHT.isoformat()})
        latest = cards.build(conn, ids["A"], {"type": "sleep", "date": None})
        empty = cards.build(conn, ids["A"], {"type": "sleep", "date": (TODAY - timedelta(days=3)).isoformat()})
    assert card["v"] == 1 and card["type"] == "sleep" and card["date"] == NIGHT.isoformat() and latest["date"] == card["date"]
    assert card["main"]["start"] == "23:30" and card["main"]["stages"] and card["score"] == 72 and card["need_min"] == 520
    assert card["notes"][0] == {"key": "short_of_need", "tone": "bad"} and card["actions"] == [{"kind": "open", "page": "sleep"}]
    s = cards.summary(card)
    assert "23:30→06:30" in s and "short_of_need" in s and "不要用文字复述" in s
    assert empty["main"] is None and empty["actions"] == [] and "没有手表睡眠数据" in cards.summary(empty)

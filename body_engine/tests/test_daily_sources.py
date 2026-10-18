"""Choosing each day's value among sources (importers/common.py upsert_daily / resolve_daily, docs/shortcut-sync-spec.md §5):
cumulative metrics = largest complete de-duplicated source, degraded ones (hour-summed / partly synced energy) only as a fallback;
non-cumulative metrics keep the source priority ow > apple_export > shortcut.
The database part uses a separate test database body_test_daily_sources (BODY_TEST_DB_NAME_DAILY), recreated on every run; skipped without Postgres."""
import os
from datetime import date, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.importers import common

D = date(2026, 10, 15)
TODAY = date(2026, 10, 18)
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_DAILY", "body_test_daily_sources")


def cand(source, metric, value, d=D, agg="sum", flags=None):
    return {"date": d, "metric": metric, "source": source, "value": value, "agg": agg, "flags": flags or {}}


# ---------- pure rules ----------

def test_cumulative_takes_largest_clean_source_not_priority():
    win, flags = common.choose([cand("ow", "step_count", 5200), cand("apple_export", "step_count", 5600),
                                cand("shortcut", "step_count", 5400)], {})
    assert (win["source"], flags) == ("apple_export", {})
    # equal values: priority breaks the tie
    win, _ = common.choose([cand("shortcut", "step_count", 5000), cand("ow", "step_count", 5000)], {})
    assert win["source"] == "ow"


def test_hour_summed_only_used_without_clean_source():
    hourly = {"hourly_sum": "x"}
    win, flags = common.choose([cand("ow", "step_count", 804), cand("shortcut", "step_count", 1014, flags=hourly)], {})
    assert win["source"] == "ow" and flags == {}
    win, flags = common.choose([cand("shortcut", "step_count", 10327, flags=hourly)], {})
    assert win["value"] == 10327 and "hourly_sum" in flags


def test_incomplete_energy_never_beats_complete_source():
    cands = [cand("ow", "basal_energy_burned", 185), cand("ow", "active_energy_burned", 133),
             cand("ow", "step_count", 6818),
             cand("shortcut", "basal_energy_burned", 2089, flags={"hourly_sum": "x"}),
             cand("shortcut", "active_energy_burned", 844, flags={"hourly_sum": "x"}),
             cand("shortcut", "step_count", 14039, flags={"hourly_sum": "x"})]
    inc = common.incomplete_days(cands, {D: 2000.0}, {}, TODAY)
    assert set(inc) == {(D, "ow")}
    by = {m: common.choose([c for c in cands if c["metric"] == m], inc) for m in
          ("basal_energy_burned", "active_energy_burned", "step_count")}
    # partial OW energy (both energy metrics) loses even to the hour-summed Shortcut, which is degraded too but larger
    assert by["basal_energy_burned"][0]["value"] == 2089 and by["active_energy_burned"][0]["value"] == 844
    # steps have no completeness reference: the de-duplicated OW value wins over the hour-summed (iPhone + Watch) one
    assert by["step_count"][0]["value"] == 6818 and by["step_count"][1] == {}
    # a complete clean source beats the partial one (with only degraded ones, the largest, flagged)
    win, flags = common.choose([cand("ow", "basal_energy_burned", 185), cand("apple_export", "basal_energy_burned", 150)],
                               {(D, "ow"): "partial", (D, "apple_export"): "partial"})
    assert win["source"] == "ow" and flags["incomplete"] == "partial"


def test_incomplete_rules():
    ref = {D: 2000.0}
    # basal below half of the usual day; the same day as "today" is never judged
    assert (D, "ow") in common.incomplete_days([cand("ow", "basal_energy_burned", 900)], ref, {}, TODAY)
    assert common.incomplete_days([cand("ow", "basal_energy_burned", 1100)], ref, {}, TODAY) == {}
    assert common.incomplete_days([cand("ow", "basal_energy_burned", 100)], ref, {}, D) == {}
    assert common.incomplete_days([cand("ow", "basal_energy_burned", 100)], {}, {}, TODAY) == {}   # no reference yet
    # whole-day active energy below that day's workouts (the training calendar's daily_incomplete)
    assert (D, "ow") in common.incomplete_days([cand("ow", "active_energy_burned", 133)], ref, {D: 393.0}, TODAY)
    assert common.incomplete_days([cand("ow", "active_energy_burned", 380)], ref, {D: 393.0}, TODAY) == {}


def test_non_cumulative_keeps_source_priority():
    win, _ = common.choose([cand("shortcut", "heart_rate", 80, agg="avg"), cand("ow", "heart_rate", 60, agg="avg"),
                            cand("apple_export", "heart_rate", 70, agg="avg")], {(D, "ow"): "partial"})
    assert win["source"] == "ow"


# ---------- database ----------

@pytest.fixture(scope="module")
def dsn():
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:
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
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as c:
        c.uid = c.execute("INSERT INTO users (display_name, timezone) VALUES ('测试', 'Australia/Sydney') RETURNING id"
                          ).fetchone()["id"]
        yield c


def row(metric, value, d=D, agg="sum", **kw):
    return {"date": d, "metric": metric, "value": value, "unit": "kcal", "agg": agg, "n": 1,
            "min_value": None, "max_value": None, **kw}


def daily(conn, d=D):
    return {r["metric"]: r for r in conn.execute(
        "SELECT metric, value, source, flags FROM daily_metrics WHERE user_id = %s AND date = %s", (conn.uid, d))}


def test_sources_resolved_in_any_order_and_idempotent(conn):
    # 30 normal days of OW basal (the usual day), then a partly synced OW day
    hist = [row("basal_energy_burned", 2000 + i, D - timedelta(days=i)) for i in range(1, 31)]
    common.upsert_daily(conn, conn.uid, "ow", hist, today=TODAY)
    common.upsert_daily(conn, conn.uid, "ow", [row("basal_energy_burned", 185), row("active_energy_burned", 133),
                                               row("step_count", 6818), row("heart_rate", 60, agg="avg")], today=TODAY)
    hourly = {"hourly_sum": "x"}
    common.upsert_daily(conn, conn.uid, "shortcut", [
        row("basal_energy_burned", 2089, flags=hourly), row("active_energy_burned", 844, flags=hourly),
        row("step_count", 14039, flags=hourly), row("heart_rate", 70, agg="avg")], today=TODAY)
    d = daily(conn)
    assert (d["basal_energy_burned"]["value"], d["basal_energy_burned"]["source"]) == (2089, "shortcut")
    assert d["active_energy_burned"]["value"] == 844 and "hourly_sum" in d["active_energy_burned"]["flags"]
    assert (d["step_count"]["value"], d["step_count"]["source"]) == (6818, "ow")
    assert d["heart_rate"]["source"] == "ow"
    # the lower-priority source arriving first or again changes nothing; re-resolving is idempotent
    before = {m: (r["value"], r["source"], r["flags"]) for m, r in d.items()}
    common.upsert_daily(conn, conn.uid, "ow", [row("step_count", 6818)], today=TODAY)
    common.resolve_daily(conn, conn.uid, [D], today=TODAY)
    assert {m: (r["value"], r["source"], r["flags"]) for m, r in daily(conn).items()} == before
    n = conn.execute("SELECT count(*) n FROM daily_metric_sources WHERE user_id = %s AND date = %s",
                     (conn.uid, D)).fetchone()["n"]
    assert n == 8
    # a raw (clean) Shortcut day replaces the hour-summed one for the same source
    common.upsert_daily(conn, conn.uid, "shortcut", [row("step_count", 7300)], today=TODAY)
    assert daily(conn)["step_count"]["value"] == 7300 and daily(conn)["step_count"]["source"] == "shortcut"


def test_workout_kcal_marks_active_energy_incomplete(conn):
    conn.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at,
                    duration_min, active_kcal) VALUES (%s, 'ow', 'w1', 'strength_training', '力量训练', %s,
                    '2026-10-15T18:00+11:00', '2026-10-15T19:00+11:00', 60, 393)""", (conn.uid, D))
    common.upsert_daily(conn, conn.uid, "ow", [row("active_energy_burned", 133)], today=TODAY)
    assert daily(conn)["active_energy_burned"]["flags"]["incomplete"].startswith("ow ")   # the only source: used, flagged
    common.upsert_daily(conn, conn.uid, "apple_export", [row("active_energy_burned", 700)], today=TODAY)
    assert daily(conn)["active_energy_burned"]["source"] == "apple_export"

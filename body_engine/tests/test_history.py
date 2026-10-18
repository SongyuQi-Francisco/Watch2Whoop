"""History import: streaming export.zip parsing, idempotent DB writes, chunked upload API (authorization, resume, limits), background job.

Uses a separate test database BODY_TEST_DB_NAME (default body_test_history), dropped and recreated on every run, never touching the production DB; skipped if it cannot connect.
The test data is a small hand-written export.xml (on a Chinese-locale system the file is named 导出.xml); no real health data is used.
"""
import io
import os
import zipfile
from datetime import date

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.history import run as hrun, store
from body_engine.importers import apple_zip, common

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_history")
CSRF = {"X-Body-Web": "1"}


def export_xml(days: int = 3) -> bytes:
    """days days from 2026-09-01: per day 2 heart rate samples, steps (iPhone + watch, the watch has more), HRV, one night of sleep; one workout. Export date = the last day."""
    recs = []
    for i in range(days):
        d = date(2026, 9, 1 + i).isoformat()
        nxt = date(2026, 9, 2 + i).isoformat()
        for hh, bpm in (("08:00", 60), ("20:00", 80)):
            recs.append(f'<Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Apple Watch" unit="count/min" '
                        f'startDate="{d} {hh}:00 +1000" endDate="{d} {hh}:00 +1000" value="{bpm}">'
                        f'<MetadataEntry key="HKMetadataKeyHeartRateMotionContext" value="0"/></Record>')
        recs.append(f'<Record type="HKQuantityTypeIdentifierStepCount" sourceName="iPhone" unit="count" '
                    f'startDate="{d} 09:00:00 +1000" endDate="{d} 10:00:00 +1000" value="1000"/>')
        recs.append(f'<Record type="HKQuantityTypeIdentifierStepCount" sourceName="Apple Watch" unit="count" '
                    f'startDate="{d} 09:00:00 +1000" endDate="{d} 10:00:00 +1000" value="1500"/>')
        recs.append(f'<Record type="HKQuantityTypeIdentifierActiveEnergyBurned" sourceName="Apple Watch" unit="kJ" '
                    f'startDate="{d} 09:00:00 +1000" endDate="{d} 10:00:00 +1000" value="418.4"/>')
        recs.append(f'<Record type="HKQuantityTypeIdentifierRestingHeartRate" sourceName="Apple Watch" unit="count/min" '
                    f'startDate="{d} 07:00:00 +1000" endDate="{d} 07:00:00 +1000" value="55"/>')
        recs.append(f'<Record type="HKQuantityTypeIdentifierHeartRateVariabilitySDNN" sourceName="Apple Watch" unit="ms" '
                    f'startDate="{d} 03:00:00 +1000" endDate="{d} 03:01:00 +1000" value="45"/>')
        # Sleep: previous day 23:30 -> 03:30 core on the day, 03:30 -> 07:00 deep (wake date = the day)
        prev = date(2026, 8, 31 + i).isoformat() if i == 0 else date(2026, 9, i).isoformat()
        recs.append(f'<Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Apple Watch" '
                    f'startDate="{prev} 23:30:00 +1000" endDate="{d} 03:30:00 +1000" value="HKCategoryValueSleepAnalysisAsleepCore"/>')
        recs.append(f'<Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="Apple Watch" '
                    f'startDate="{d} 03:30:00 +1000" endDate="{d} 07:00:00 +1000" value="HKCategoryValueSleepAnalysisAsleepDeep"/>')
        recs.append(f'<Record type="HKCategoryTypeIdentifierSleepAnalysis" sourceName="iPhone" '
                    f'startDate="{prev} 23:00:00 +1000" endDate="{d} 07:00:00 +1000" value="HKCategoryValueSleepAnalysisInBed"/>')
        del nxt
    wk = ('<Workout workoutActivityType="HKWorkoutActivityTypeTraditionalStrengthTraining" duration="60" durationUnit="min" '
          'sourceName="Apple Watch" startDate="2026-09-01 18:00:00 +1000" endDate="2026-09-01 19:00:00 +1000">'
          '<WorkoutStatistics type="HKQuantityTypeIdentifierActiveEnergyBurned" sum="1255.2" unit="kJ"/>'
          '<WorkoutStatistics type="HKQuantityTypeIdentifierHeartRate" average="120" minimum="80" maximum="160" unit="count/min"/>'
          '</Workout>')
    last = date(2026, 9, days).isoformat()
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE HealthData [\n<!ELEMENT HealthData ANY>\n]>\n'
            f'<HealthData locale="zh_CN"><ExportDate value="{last} 12:00:00 +1000"/>'
            '<Me HKCharacteristicTypeIdentifierBiologicalSex="HKBiologicalSexNotSet"/>'
            + "".join(recs) + wk + "</HealthData>").encode()


def export_zip(xml: bytes, member: str = "apple_health_export/导出.xml") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, xml)
        z.writestr("apple_health_export/导出_cda.xml", b"<ClinicalDocument/>" * 50000)   # larger than the main file, must not be chosen
        z.writestr("apple_health_export/workout-routes/route_1.gpx", b"<gpx/>")
    return buf.getvalue()


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


@pytest.fixture(scope="module")
def users(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        # all have already consented in the app (rejection without consent: see test_profile.py)
        ids = {n: conn.execute("""INSERT INTO users (display_name, timezone, consent_at) VALUES (%s, 'Australia/Sydney', now())
                                  RETURNING id""", (n,)).fetchone()["id"] for n in ("A", "B", "C")}
        tok = {"A": auth.create_token(conn, ids["A"], "t", ["read", "write"]),
               "A_ro": auth.create_token(conn, ids["A"], "ro", ["read"]),
               "B": auth.create_token(conn, ids["B"], "t", ["read", "write"]),
               "C": auth.create_token(conn, ids["C"], "t", ["read", "write"])}
    return ids, tok


@pytest.fixture(autouse=True)
def import_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BODY_IMPORT_DIR", str(tmp_path / "imports"))
    monkeypatch.setenv("BODY_IMPORT_MIN_FREE_MB", "1")
    return tmp_path / "imports"


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def H(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------- parsing
def test_find_member_skips_cda_and_parses_chinese_name(tmp_path):
    p = tmp_path / "导出.zip"
    p.write_bytes(export_zip(export_xml()))
    raw, size, close = apple_zip.open_export(p)
    try:
        assert size == len(export_xml())
        assert raw.read(5) == b"<?xml"
    finally:
        close()


def test_not_an_export(tmp_path):
    p = tmp_path / "x.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "hi")
    p.write_bytes(buf.getvalue())
    with pytest.raises(apple_zip.ExportError, match="export.xml"):
        apple_zip.open_export(p)
    p.write_bytes(b"hello world")
    with pytest.raises(apple_zip.ExportError, match="格式不对"):
        apple_zip.open_export(p)
    q = tmp_path / "bad.xml"
    q.write_bytes(b"<?xml version='1.0'?><Other/>")
    raw, _, close = apple_zip.open_export(q)
    with pytest.raises(apple_zip.ExportError, match="HealthData"):
        list(apple_zip.iter_elements(raw))
    close()
    q.write_bytes(export_xml()[:-200])   # upload incomplete
    raw, _, close = apple_zip.open_export(q)
    with pytest.raises(apple_zip.ExportError, match="不完整"):
        list(apple_zip.iter_elements(raw))
    close()


def test_parse_daily_matches_parse_export_rules():
    from zoneinfo import ZoneInfo
    got = []
    p = apple_zip.parse(io.BytesIO(export_xml()), ZoneInfo("Australia/Sydney"), got.extend)
    rows = {(r["date"].isoformat(), r["metric"]): r for r in apple_zip.daily_rows(p, p.export_at.date())}
    assert ("2026-09-03", "heart_rate") not in rows                      # the export day itself is dropped
    assert rows[("2026-09-01", "step_count")]["value"] == 1500            # cumulative type: the most complete single source, not summed
    assert rows[("2026-09-01", "active_energy_burned")]["unit"] == "kcal"
    assert rows[("2026-09-01", "active_energy_burned")]["value"] == pytest.approx(100)   # 418.4 kJ
    hr = rows[("2026-09-01", "heart_rate")]
    assert (hr["value"], hr["min_value"], hr["max_value"], hr["agg"]) == (70, 60, 80, "avg")
    kinds = {k for k, *_ in got}
    assert kinds == {"hr", "hrv", "sleep"}
    assert sum(1 for k, *_ in got if k == "sleep") == 6                  # InBed does not count
    assert len(p.workouts) == 1
    w = p.workouts[0]
    assert (w["type"], w["source"], w["is_xml"], w["duration_min"], w["avg_hr"]) == ("strength_training", "apple_export", True, 60, 120)
    assert w["active_kcal"] == pytest.approx(300)


# ---------------------------------------------------------------- DB writes
def test_run_writes_and_is_idempotent(dsn, users, tmp_path):
    ids, _ = users
    uid = ids["C"]
    p = tmp_path / "export.zip"
    p.write_bytes(export_zip(export_xml(5)))
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        # a day that already has (larger, complete) OW data: must not be overwritten
        common.upsert_daily(conn, uid, "ow", [{"date": date(2026, 9, 2), "metric": "step_count", "value": 9999,
                                               "unit": "count", "agg": "sum", "n": 1, "min_value": None,
                                               "max_value": None}])
        seen = []
        res = apple_zip.run(conn, uid, p, None, seen.append)
        assert res["days"] == 4 and res["first_day"] == "2026-09-01" and res["last_day"] == "2026-09-04"
        assert res["nights"] == 5 and res["workouts"] == 1
        counts = lambda: {  # noqa: E731
            t: conn.execute(f"SELECT count(*) AS n FROM {t} WHERE user_id = %s", (uid,)).fetchone()["n"]
            for t in ("daily_metrics", "sleep_sessions", "workouts", "export_samples")}
        first = counts()
        assert first["export_samples"] == 5 * (2 + 1 + 2)
        assert conn.execute("""SELECT value, source FROM daily_metrics WHERE user_id = %s AND date = '2026-09-02'
                               AND metric = 'step_count'""", (uid,)).fetchone() == {"value": 9999, "source": "ow"}
        s = conn.execute("""SELECT asleep_min, deep_min, core_min, source FROM sleep_sessions WHERE user_id = %s
                            AND wake_date = '2026-09-03'""", (uid,)).fetchone()
        assert s == {"asleep_min": 450, "deep_min": 210, "core_min": 240, "source": "apple_export"}
        apple_zip.run(conn, uid, p)
        assert counts() == first


def test_scores_inputs_use_export_samples(dsn, users):
    from body_engine.scores import inputs
    ids, _ = users
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        inp = inputs.load(conn, None, ids["C"], date(2026, 9, 1), date(2026, 9, 5), None)
    assert len(inp.hr) == 10 and len(inp.hrv) == 5
    assert {s[2] for s in inp.segments} == {"core", "deep"}


# ---------------------------------------------------------------- upload API
def upload(client, token, data: bytes, name="export.zip", chunk=None):
    r = client.post("/v1/users/me/history-import", json={"filename": name, "size": len(data)}, headers=H(token))
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    step = chunk or r.json()["chunk_bytes"]
    off = job["received_bytes"]
    while off < len(data):
        r = client.put(f"/v1/users/me/history-import/{job['id']}/data?offset={off}", content=data[off:off + step],
                       headers=H(token))
        assert r.status_code == 200, r.text
        off = r.json()["received_bytes"]
    r = client.post(f"/v1/users/me/history-import/{job['id']}/complete", headers=H(token))
    assert r.status_code == 200, r.text
    return r.json()["job"]


def test_upload_auth_and_limits(client, users, import_dir, monkeypatch):
    ids, tok = users
    a = ids["A"]
    assert client.get("/v1/users/me/history-import").status_code == 401
    assert client.post("/v1/users/me/history-import", json={"filename": "export.zip", "size": 10},
                       headers=H(tok["A_ro"])).status_code == 403
    assert client.get(f"/v1/users/{a}/history-import", headers=H(tok["B"])).status_code == 403   # someone else's
    assert client.post("/v1/users/me/history-import", json={"filename": "photo.jpg", "size": 10},
                       headers=H(tok["A"])).status_code == 400
    monkeypatch.setenv("BODY_IMPORT_MAX_MB", "1")
    assert client.post("/v1/users/me/history-import", json={"filename": "export.zip", "size": 2 * 1048576},
                       headers=H(tok["A"])).status_code == 413
    monkeypatch.setenv("BODY_IMPORT_MAX_MB", "1024")
    monkeypatch.setattr(store, "free_bytes", lambda: 100 * 1048576)
    monkeypatch.setenv("BODY_IMPORT_MIN_FREE_MB", "200")
    r = client.post("/v1/users/me/history-import", json={"filename": "export.zip", "size": 10}, headers=H(tok["A"]))
    assert r.status_code == 507 and "空间" in r.json()["detail"]


def test_upload_resume_and_conflicts(client, users, import_dir):
    ids, tok = users
    data = export_zip(export_xml())
    r = client.post("/v1/users/me/history-import", json={"filename": "导出.zip", "size": len(data)}, headers=H(tok["B"]))
    job = r.json()["job"]
    jid = job["id"]
    half = len(data) // 2
    assert client.put(f"/v1/users/me/history-import/{jid}/data?offset=0", content=data[:half],
                      headers=H(tok["B"])).json()["received_bytes"] == half
    # Re-sending the same chunk (network dropped, unsure whether it landed): 409, tells where to resume
    r = client.put(f"/v1/users/me/history-import/{jid}/data?offset=0", content=data[:half], headers=H(tok["B"]))
    assert r.status_code == 409 and f"received_bytes={half}" in r.json()["detail"]
    # others cannot write into it
    assert client.put(f"/v1/users/{ids['B']}/history-import/{jid}/data?offset={half}", content=b"x",
                      headers=H(tok["A"])).status_code == 403
    assert client.put(f"/v1/users/me/history-import/{jid}/data?offset={half}", content=b"x",
                      headers=H(tok["A"])).status_code == 404
    # Cannot finish before it is fully uploaded; "starting" the same file again = resume
    assert client.post(f"/v1/users/me/history-import/{jid}/complete", headers=H(tok["B"])).status_code == 409
    again = client.post("/v1/users/me/history-import", json={"filename": "导出.zip", "size": len(data)}, headers=H(tok["B"]))
    assert again.json()["job"]["id"] == jid and again.json()["job"]["received_bytes"] == half
    assert client.post("/v1/users/me/history-import", json={"filename": "other.zip", "size": 5},
                       headers=H(tok["B"])).status_code == 409
    client.put(f"/v1/users/me/history-import/{jid}/data?offset={half}", content=data[half:], headers=H(tok["B"]))
    done = client.post(f"/v1/users/me/history-import/{jid}/complete", headers=H(tok["B"])).json()["job"]
    assert done["status"] == "queued"
    assert store.file_path(ids["B"], jid).read_bytes() == data
    # can be cancelled while queued (file deleted)
    c = client.delete(f"/v1/users/me/history-import/{jid}", headers=H(tok["B"])).json()["job"]
    assert c["status"] == "cancelled" and not store.file_path(ids["B"], jid).exists()


def test_cookie_write_needs_csrf_header(client, users):
    _, tok = users
    client.cookies.clear()
    assert client.post("/v1/web/login", json={"token": tok["A"]}).status_code == 200
    assert client.post("/v1/users/me/history-import", json={"filename": "export.zip", "size": 10}).status_code == 403
    r = client.post("/v1/users/me/history-import", json={"filename": "export.zip", "size": 10}, headers=CSRF)
    assert r.status_code == 200
    jid = r.json()["job"]["id"]
    assert client.delete(f"/v1/users/me/history-import/{jid}", headers=CSRF).status_code == 200
    client.cookies.clear()


def test_worker_end_to_end(client, users, dsn, import_dir, monkeypatch):
    ids, tok = users
    monkeypatch.setattr(hrun, "_connect", lambda: psycopg.connect(dsn, row_factory=dict_row, autocommit=True))
    job = upload(client, tok["A"], export_zip(export_xml(4)), chunk=4096)
    assert job["status"] == "queued"
    out = hrun.run_pending(None)
    assert [j["status"] for j in out["jobs"]] == ["done"]
    st = client.get("/v1/users/me/history-import", headers=H(tok["A"])).json()["job"]
    assert st["status"] == "done" and "2026-09-01 到 2026-09-03" in st["message"]
    assert st["result"]["nights"] == 4
    assert not store.file_path(ids["A"], job["id"]).exists()           # original file deleted
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        n = conn.execute("SELECT count(*) AS n FROM daily_scores WHERE user_id = %s", (ids["A"],)).fetchone()["n"]
    assert n > 0                                                         # scores computed
    # Bad file: fails, gives a Chinese explanation, file deleted too
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("apple_health_export/export.xml", export_xml()[:-300])
    bad = upload(client, tok["A"], buf.getvalue())
    hrun.run_pending(None)
    st = client.get("/v1/users/me/history-import", headers=H(tok["A"])).json()["job"]
    assert st["id"] == bad["id"] and st["status"] == "failed" and "重新导出" in st["message"]
    assert not store.file_path(ids["A"], bad["id"]).exists()


def test_housekeeping_requeues_stale(dsn, users, monkeypatch):
    ids, _ = users
    monkeypatch.setattr(hrun, "_connect", lambda: psycopg.connect(dsn, row_factory=dict_row, autocommit=True))
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        j = conn.execute("""INSERT INTO history_imports (user_id, status, filename, size_bytes, updated_at)
                            VALUES (%s, 'processing', 'export.zip', 10, now() - interval '2 hours') RETURNING id""",
                         (ids["C"],)).fetchone()["id"]
        u = conn.execute("""INSERT INTO history_imports (user_id, status, filename, size_bytes, updated_at)
                            VALUES (%s, 'uploading', 'export.zip', 10, now() - interval '3 days') RETURNING id""",
                         (ids["B"],)).fetchone()["id"]
        assert hrun.housekeeping(conn) == {"requeued": 1, "expired": 1}
        st = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM history_imports WHERE id = ANY(%s)",
                                                          ([j, u],))}
        assert st == {j: "queued", u: "cancelled"}
        out = hrun.run_pending(None)   # file is gone -> fails with an explanation
        assert out["jobs"][0]["status"] == "failed"


def test_archive_dir_is_per_user():
    """compute-scores / import-shortcut default to the user's own archive: user 1 -> archive/, others -> archive/u<id>/.
    (A shared default made scoring fall back to user 1's uploads for users without imported Shortcut samples.)"""
    from body_engine.config import settings
    from body_engine.importers.shortcut import user_archive_dir
    base = settings.health_data_dir / "archive"
    assert user_archive_dir(1) == base
    assert user_archive_dir(4) == base / "u4"


def test_today_sleep_chart_uses_imported_history(client, users):
    """Nights that only exist in an imported export.zip get their stage chart from export_samples."""
    ids, tok = users
    d = client.get("/v1/users/me/today?date=2026-09-03", headers=H(tok["C"])).json()
    sl = d["sleep"]
    assert sl["main"] is not None and sl["stages_source"] == "export"
    assert {s["stage"] for s in sl["stages"]} >= {"core", "deep"}

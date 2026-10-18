"""Raw Apple Watch data (rawdata/, cardio/): RMSSD from beat-to-beat series, ECG parsing and R peaks, HRR60, export import,
idempotent / per-user storage, ECG authorization (own share scope), /cardio, scoring with hrv_kind = rmssd.

Synthetic data only. DB tests use a separate database BODY_TEST_DB_NAME (default body_test_rawdata), recreated on every run; skipped without Postgres.
"""
import io
import math
import os
import zipfile
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.cardio import hrr as H
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.importers import apple_zip
from body_engine.rawdata import compare as C
from body_engine.rawdata import ecg as E
from body_engine.rawdata import export as X
from body_engine.rawdata import hrv
from body_engine.rawdata import store
from body_engine.scores import inputs as SI

TZ = ZoneInfo("Australia/Sydney")
TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_rawdata")


# ---------------------------------------------------------------- HRV from beats
def beats_from_rr(rr_ms):
    t, out = 0.0, [0.0]
    for x in rr_ms:
        t += x / 1000
        out.append(t)
    return out


def test_rmssd_matches_definition():
    rr = [1000 + (20 if i % 2 else -20) for i in range(40)]      # alternating ±20 ms -> successive differences of 40 ms
    r = hrv.analyze(beats_from_rr(rr))
    assert r.rmssd == pytest.approx(40.0, abs=0.01)
    assert r.sdnn == pytest.approx(20.0, abs=0.01)
    assert r.n_valid == 40 and r.artifact_ratio == 0


def test_artifacts_and_gaps_are_dropped():
    rr = [1000 + (20 if i % 2 else -20) for i in range(40)]
    rr[10] = 500                                                   # ectopic beat
    beats = beats_from_rr(rr)
    gaps = [False] * len(beats)
    gaps[30] = True                                                # interval ending at beat 30 follows a gap
    r = hrv.analyze(beats, gaps)
    assert r.n_valid == 38
    assert r.rmssd == pytest.approx(40.0, abs=0.01)              # no difference across a dropped interval


def test_too_few_beats_gives_no_rmssd():
    assert hrv.analyze(beats_from_rr([1000] * 10)).rmssd is None


def test_clock_times_unwrap_midnight():
    assert hrv.beats_from_clock(["23:59:59.50", "00:00:00.40", "00:00:01.30", "bad"]) == [0.0, 0.9, 1.8]


# ---------------------------------------------------------------- ECG
def synth_ecg(hz=512, seconds=30, rr_s=1.0, invert=False):
    """Narrow R spikes every rr_s, a broad T wave 0.3 s later, a slow baseline wander."""
    n = int(hz * seconds)
    x = [80 * math.sin(2 * math.pi * 0.3 * i / hz) for i in range(n)]
    peaks = []
    t = 0.5
    while t < seconds - 0.5:
        c = int(t * hz)
        peaks.append(c)
        for k in range(-15, 16):
            x[c + k] += 1000 * math.exp(-(k / 4) ** 2)
        for k in range(-40, 41):
            x[c + int(0.3 * hz) + k] += 250 * math.exp(-(k / 18) ** 2)
        t += rr_s
    return ([-v for v in x] if invert else x), peaks


@pytest.mark.parametrize("invert", [False, True])
def test_r_peaks_found_either_polarity(invert):
    x, truth = synth_ecg(invert=invert)
    peaks = E.r_peaks(x, 512)
    assert len(peaks) == len(truth)
    assert all(abs(p - t) <= 3 for p, t in zip(peaks, truth))
    d = E.derive(x, 512)
    assert d["hr"] == pytest.approx(60, abs=0.5) and d["quality"] == "ok"


def test_pack_roundtrip_and_csv():
    x, _ = synth_ecg(seconds=4)
    assert E.unpack(E.pack(x)) == [round(v) for v in x]
    csv = ("Name,Someone\nDate of Birth,\"1 Jan 2000\"\nRecorded Date,2026-09-01 08:00:00 +1000\nClassification,Sinus Rhythm\n"
           "Symptoms,\nSoftware Version,2.0\nDevice,\"Watch7,9\"\nSample Rate,512 hertz\n\n\nLead,Lead I\nUnit,µV\n\n"
           + "\n".join(f"{v:.3f}" for v in x) + "\n")
    e = E.parse_csv(csv)
    assert e.classification == "sinusRhythm" and e.sample_hz == 512 and len(e.voltage_uv) == len(x)
    assert e.start_at == datetime(2026, 9, 1, 8, tzinfo=ZoneInfo("Australia/Brisbane"))
    assert "Someone" not in repr(e)                     # name / birth date are not kept
    with pytest.raises(E.EcgError):
        E.parse_csv("hello\n1\n2\n")


def test_classify_labels():
    assert E.classify("Atrial Fibrillation") == "atrialFibrillation"
    assert E.classify("Heart Rate Over 150") == "inconclusiveHighHeartRate"
    assert E.classify("") == "notSet" and E.classify("something new") == "unrecognized"


# ---------------------------------------------------------------- HRR60
def hr_series(end, peak=170, after=140, step=5):
    out = [(end - timedelta(seconds=s), peak - s / 10) for s in range(300, -1, -step)]
    out += [(end + timedelta(seconds=s), after) for s in range(step, 181, step)]
    return sorted(out)


def test_hrr60_valid_and_interpolated():
    end = datetime(2026, 9, 1, 19, tzinfo=TZ)
    r = H.hrr60(hr_series(end), end, 190)
    assert r["valid"] and r["peak_hr"] == 170 and r["hr_60s"] == 140 and r["hrr60"] == 30


def test_hrr60_low_peak_or_no_samples():
    end = datetime(2026, 9, 1, 19, tzinfo=TZ)
    r = H.hrr60(hr_series(end, peak=110, after=100), end, 190)
    assert not r["valid"] and r["hrr60"] == 10 and "最大心率" in r["reason"]
    r = H.hrr60([(end - timedelta(seconds=30), 160)], end, 190)
    assert not r["valid"] and r["hr_60s"] is None and "60 秒" in r["reason"]
    assert H.hrr60([], end, 190)["reason"].startswith("训练最后")


# ---------------------------------------------------------------- DB
def hrv_record(day: date, hh: str, rr_ms: list[float], apple: float = 45.0, src: str = "Apple Watch") -> str:
    t0 = datetime.fromisoformat(f"{day.isoformat()}T{hh}:00+10:00")
    beats = [t0 + timedelta(seconds=1)]
    for x in rr_ms:
        beats.append(beats[-1] + timedelta(milliseconds=x))
    inner = "".join(f'<InstantaneousBeatsPerMinute bpm="60" time="{b.strftime("%H:%M:%S.")}{b.microsecond // 10000:02d}"/>'
                    for b in beats)
    end = beats[-1]
    return (f'<Record type="HKQuantityTypeIdentifierHeartRateVariabilitySDNN" sourceName="{src}" unit="ms" '
            f'startDate="{t0:%Y-%m-%d %H:%M:%S} +1000" endDate="{end:%Y-%m-%d %H:%M:%S} +1000" value="{apple}">'
            f'<HeartRateVariabilityMetadataList>{inner}</HeartRateVariabilityMetadataList></Record>')


def export_xml(records: list[str]) -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<HealthData locale="en_AU"><ExportDate value="2026-09-10 12:00:00 +1000"/>'
            + "".join(records) + "</HealthData>").encode()


def ecg_csv() -> str:
    x, _ = synth_ecg()
    return ("Recorded Date,2026-09-02 08:00:00 +1000\nClassification,Sinus Rhythm\nSymptoms,\nDevice,\"Watch7,9\"\n"
            "Sample Rate,512 hertz\n\nLead,Lead I\nUnit,µV\n\n" + "\n".join(f"{v:.3f}" for v in x) + "\n")


def export_zip(records: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("apple_health_export/export.xml", export_xml(records))
        z.writestr("apple_health_export/electrocardiograms/ecg_2026-09-02.csv", ecg_csv())
    return buf.getvalue()


ALT = [1000 + (20 if i % 2 else -20) for i in range(50)]          # RMSSD 40


@pytest.fixture(scope="module")
def dsn():
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
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        ids = {n: conn.execute("""INSERT INTO users (display_name, timezone, consent_at) VALUES (%s, 'Australia/Sydney', now())
                                  RETURNING id""", (n,)).fetchone()["id"] for n in ("A", "B", "C")}
        tok = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
        tok["A_ro"] = auth.create_token(conn, ids["A"], "ro", ["read"])
        # B may see A's scores but not ECG; C may see A's ECG
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, '{scores}'), (%s, %s, '{ecg}')",
                     (ids["A"], ids["B"], ids["A"], ids["C"]))
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()


def Hd(token):
    return {"Authorization": f"Bearer {token}"}


def test_import_raw_from_zip_idempotent(dsn, users, tmp_path):
    ids, _ = users
    p = tmp_path / "export.zip"
    recs = [hrv_record(date(2026, 9, d), "03:00", ALT, apple=30 + d) for d in range(1, 6)]
    p.write_bytes(export_zip(recs))
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        r1 = X.run(conn, ids["A"], p)
        r2 = X.run(conn, ids["A"], p)
        conn.commit()
        assert r1 == r2 == {"heartbeat_series": 5, "ecg": 1, "ecg_skipped": []}
        rows = conn.execute("SELECT * FROM heartbeat_series WHERE user_id = %s ORDER BY start_at", (ids["A"],)).fetchall()
        assert len(rows) == 5 and rows[0]["origin"] == "apple_export" and rows[0]["apple_sdnn"] == 31
        assert rows[0]["rmssd"] == pytest.approx(40, abs=0.5)          # 10 ms clock resolution
        assert rows[0]["n_beats"] == 51 and rows[0]["beats_ms"][1] == 980
        e = conn.execute("SELECT * FROM ecg_recordings WHERE user_id = %s", (ids["A"],)).fetchall()
        assert len(e) == 1 and e[0]["classification"] == "sinusRhythm" and e[0]["derived"]["quality"] == "ok"
        assert conn.execute("SELECT count(*) AS n FROM heartbeat_series WHERE user_id = %s", (ids["B"],)).fetchone()["n"] == 0


def test_app_copy_wins_over_export(dsn, users):
    ids, _ = users
    start = datetime(2026, 9, 1, 3, tzinfo=ZoneInfo("Australia/Brisbane"))
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        app_beats = beats_from_rr([1000 + (10 if i % 2 else -10) for i in range(50)])   # RMSSD 20
        store.save_heartbeats(conn, ids["A"], [{"start_at": start.replace(microsecond=400000), "end_at": start + timedelta(minutes=1),
                                                "source": "Apple Watch", "origin": "app", "beats_s": app_beats,
                                                "gaps": [False] * len(app_beats), "hk_uuid": "6f1c1d1e-0000-4000-8000-000000000001"}])
        it = X.heartbeat_from_record(__import__("xml.etree.ElementTree", fromlist=["x"]).fromstring(
            hrv_record(date(2026, 9, 1), "03:00", ALT)))
        store.save_heartbeats(conn, ids["A"], [it])                   # the export copy again: must not replace the app copy
        r = conn.execute("SELECT origin, rmssd, apple_sdnn FROM heartbeat_series WHERE user_id = %s AND start_at = %s",
                         (ids["A"], start)).fetchone()
        conn.commit()
    assert r["origin"] == "app" and r["rmssd"] == pytest.approx(20, abs=0.01) and r["apple_sdnn"] == 31


def test_history_import_includes_raw(dsn, users, tmp_path):
    ids, _ = users
    p = tmp_path / "export.zip"
    p.write_bytes(export_zip([hrv_record(date(2026, 9, 3), "04:00", ALT)]))
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        res = apple_zip.run(conn, ids["C"], p)
        conn.commit()
        assert res["heartbeat_series"] == 1 and res["ecg"] == 1
        # export_samples keeps Apple's SDNN for the sdnn scoring path
        assert conn.execute("SELECT count(*) AS n FROM export_samples WHERE user_id = %s AND kind = 'hrv'",
                            (ids["C"],)).fetchone()["n"] == 1


def test_ecg_api_authorization(client, users):
    ids, tok = users
    a = ids["A"]
    lst = client.get(f"/v1/users/{a}/ecg", headers=Hd(tok["A"]))
    assert lst.status_code == 200 and len(lst.json()["recordings"]) == 1
    rec = lst.json()["recordings"][0]
    assert rec["classification_zh"] == "窦性心律" and rec["duration_s"] == 30.0 and rec["hr"] == pytest.approx(60, abs=1)
    d = client.get(f"/v1/users/{a}/ecg/{rec['id']}", headers=Hd(tok["A"])).json()
    assert d["sample_hz"] == 512 and len(d["voltage"]) == 15360 and len(d["r_peaks"]) == 29
    # B has a scores share but not ecg; C has ecg
    assert client.get(f"/v1/users/{a}/ecg", headers=Hd(tok["B"])).status_code == 403
    assert client.get(f"/v1/users/{a}/ecg/{rec['id']}", headers=Hd(tok["B"])).status_code == 403
    assert client.get(f"/v1/users/{a}/ecg", headers=Hd(tok["C"])).status_code == 200
    assert client.get(f"/v1/users/{a}/heartbeats?start=2026-09-01&end=2026-09-07", headers=Hd(tok["B"])).status_code == 200
    assert client.get(f"/v1/users/{a}/heartbeats?start=2026-09-01&end=2026-09-07", headers=Hd(tok["C"])).status_code == 403
    # someone else's id: 404 not leaked as existing
    assert client.get(f"/v1/users/{ids['C']}/ecg/{rec['id']}", headers=Hd(tok["C"])).status_code == 404
    # delete: only yourself, with write
    assert client.delete(f"/v1/users/{a}/ecg/{rec['id']}", headers=Hd(tok["C"])).status_code == 403
    assert client.delete(f"/v1/users/{a}/ecg/{rec['id']}", headers=Hd(tok["A_ro"])).status_code == 403
    assert client.delete(f"/v1/users/{a}/ecg/{rec['id']}", headers=Hd(tok["A"])).status_code == 200
    assert client.get(f"/v1/users/{a}/ecg", headers=Hd(tok["A"])).json()["recordings"] == []


def test_heartbeats_endpoint(client, users):
    ids, tok = users
    r = client.get(f"/v1/users/{ids['A']}/heartbeats?start=2026-09-01&end=2026-09-07", headers=Hd(tok["A"])).json()
    assert len(r["readings"]) == 5 and r["readings"][0]["origin"] == "app"
    assert client.get(f"/v1/users/{ids['A']}/heartbeats?start=2026-01-01&end=2026-09-07",
                      headers=Hd(tok["A"])).status_code == 422


def test_hrr_compute_and_cardio_endpoint(dsn, client, users):
    ids, tok = users
    a = ids["A"]
    end = datetime(2026, 9, 5, 19, tzinfo=TZ)
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        for i, (e, peak) in enumerate(((end, 170), (end + timedelta(hours=2), 110))):
            conn.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at, duration_min)
                            VALUES (%s, 'ow', %s, 'basketball', '篮球', %s, %s, %s, 60)""",
                         (a, f"w{i}", e.date(), e - timedelta(hours=1), e))
        conn.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at, duration_min)
                        VALUES (%s, 'ow', 'other-user', 'walking', '步行', %s, %s, %s, 60)""",
                     (ids["B"], end.date(), end - timedelta(hours=1), end))
        hr = sorted(hr_series(end) + hr_series(end + timedelta(hours=2), peak=110, after=100))
        res = H.compute(conn, a, hr, {end.date(): 190}, date(2026, 9, 1), date(2026, 9, 7))
        assert res == {"workouts": 2, "valid": 1}
        assert H.compute(conn, a, hr, {end.date(): 190}, date(2026, 9, 1), date(2026, 9, 7)) == res   # idempotent
        conn.execute("""INSERT INTO daily_scores (user_id, date, algo_version, details) VALUES (%s, '2026-09-05', 'test',
                        '{"recovery": {"rhr_low_offset_min": 120}, "sleep": {"bedtime": "2026-09-04T23:00+10:00", "wake_time": "2026-09-05T07:00+10:00"}}')""",
                     (a,))
        conn.commit()
    r = client.get(f"/v1/users/{a}/cardio?start=2026-09-01&end=2026-09-07", headers=Hd(tok["A"])).json()
    assert [w["valid"] for w in r["hrr"]["workouts"]] == [True, False]
    assert r["hrr"]["last30_mean"] == 30 and r["hrr"]["last30_n"] == 1
    assert r["recovery_index"]["nights"] == [{"date": "2026-09-05", "offset_min": 120, "pct": 25}]
    assert client.get(f"/v1/users/{a}/cardio", headers=Hd(tok["C"])).status_code == 403
    assert client.get(f"/v1/users/{a}/cardio", headers=Hd(tok["B"])).status_code == 200


def test_rmssd_scoring_inputs_and_compare(dsn, users):
    """hrv_kind = rmssd takes HRV only from heartbeat_series; sdnn ignores it; the comparison report renders."""
    ids, _ = users
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        r = SI.load(conn, None, ids["A"], date(2026, 8, 30), date(2026, 9, 7), None, "rmssd")
        assert r.hrv_kind == "rmssd" and len(r.hrv) == 5 and r.hrv[0][1] == pytest.approx(20, abs=0.01)
        s = SI.load(conn, None, ids["A"], date(2026, 8, 30), date(2026, 9, 7), None, "sdnn")
        assert s.hrv == []                                           # A has no SDNN source in this DB
        c = SI.load(conn, None, ids["C"], date(2026, 9, 1), date(2026, 9, 7), None, "sdnn")
        assert [v for _, v in c.hrv] == [45.0]                       # export_samples SDNN
        conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, unit, source, agg)
                        VALUES (%s, '2026-09-01', 'resting_heart_rate', 55, 'bpm', 'apple_export', 'avg')""", (ids["A"],))
        res = C.compare(conn, None, ids["A"], date(2026, 9, 1), date(2026, 9, 7))
        md = C.markdown(res)
        conn.rollback()
    assert res["check"]["readings"] == 5 and res["check"]["with_rmssd"] == 5
    assert "SDNN vs RMSSD" in md and "| 有 HRV 的晚数 |" in md


def test_slim_keeps_only_hrv_and_ecg_without_pii(dsn, users, tmp_path):
    ids, _ = users
    src = tmp_path / "export.zip"
    hr = ('<Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Apple Watch" unit="count/min" '
          'startDate="2026-09-04 08:00:00 +1000" endDate="2026-09-04 08:00:00 +1000" value="60"/>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("apple_health_export/export.xml", export_xml([hr, hrv_record(date(2026, 9, 4), "02:00", ALT)]))
        z.writestr("apple_health_export/electrocardiograms/ecg_2026-09-04.csv",
                   "Name,Someone Private\nDate of Birth,\"1 Jan 2000\"\n" + ecg_csv())
    src.write_bytes(buf.getvalue())
    out = tmp_path / "raw.zip"
    assert X.slim(src, out)["hrv_records"] == 1
    with zipfile.ZipFile(out) as z:
        xml = z.read("apple_health_export/export.xml").decode()
        ecg = z.read("apple_health_export/electrocardiograms/ecg_2026-09-04.csv").decode()
    assert "HeartRate\"" not in xml and "HeartRateVariabilitySDNN" in xml
    assert "Someone" not in ecg and "Date of Birth" not in ecg and "Sinus Rhythm" in ecg
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        assert X.run(conn, ids["B"], out) == {"heartbeat_series": 1, "ecg": 1, "ecg_skipped": []}
        conn.rollback()

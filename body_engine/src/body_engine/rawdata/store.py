"""heartbeat_series / ecg_recordings read-write (migration 150). Everything is per user; callers check authorization."""
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from ..i18n import is_en
from . import ecg as E
from . import hrv as H


def _second(t: datetime) -> datetime:
    return t.replace(microsecond=0)


def save_heartbeats(conn: psycopg.Connection, user_id: int, items: list[dict], import_id: int | None = None) -> int:
    """items: {start_at, end_at, source, origin, beats_s, gaps?, hk_uuid?, apple_sdnn?}. Idempotent;
    an export.xml copy never replaces the app copy of the same reading. Returns the number of rows written."""
    rows = []
    for it in items:
        beats = it["beats_s"]
        if len(beats) < 2:
            continue
        r = H.analyze(beats, it.get("gaps"))
        rows.append((user_id, _second(it["start_at"]), it["end_at"], it.get("source") or "", it["origin"], it.get("hk_uuid"),
                     [round(b * 1000) for b in beats], it.get("gaps"), it.get("apple_sdnn"), r.rmssd, r.sdnn, r.mean_rr,
                     r.n_beats, r.n_valid, H.ALGO, import_id))
    if not rows:
        return 0
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO heartbeat_series (user_id, start_at, end_at, source, origin, hk_uuid, beats_ms, gaps, apple_sdnn,
                                             rmssd, sdnn, mean_rr, n_beats, n_valid, algo, import_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (user_id, start_at, source) DO UPDATE SET
                 end_at = EXCLUDED.end_at, origin = EXCLUDED.origin, hk_uuid = COALESCE(EXCLUDED.hk_uuid, heartbeat_series.hk_uuid),
                 beats_ms = EXCLUDED.beats_ms, gaps = EXCLUDED.gaps,
                 apple_sdnn = COALESCE(EXCLUDED.apple_sdnn, heartbeat_series.apple_sdnn),
                 rmssd = EXCLUDED.rmssd, sdnn = EXCLUDED.sdnn, mean_rr = EXCLUDED.mean_rr, n_beats = EXCLUDED.n_beats,
                 n_valid = EXCLUDED.n_valid, algo = EXCLUDED.algo, import_id = EXCLUDED.import_id, updated_at = now()
               WHERE heartbeat_series.origin = 'apple_export' OR EXCLUDED.origin = 'app'""", rows)
    return len(rows)


def save_ecg(conn: psycopg.Connection, user_id: int, e: E.Ecg, origin: str, import_id: int | None = None,
             hk_uuid: str | None = None) -> int:
    """One recording, idempotent on (user, start time); the app copy replaces an export copy. Returns its id."""
    derived = E.derive(e.voltage_uv, e.sample_hz)
    row = conn.execute(
        """INSERT INTO ecg_recordings (user_id, start_at, origin, hk_uuid, sample_hz, n_samples, voltage, classification,
                                       classification_label, symptoms, apple_avg_hr, device, software_version, derived, import_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (user_id, start_at) DO UPDATE SET
             origin = EXCLUDED.origin, hk_uuid = COALESCE(EXCLUDED.hk_uuid, ecg_recordings.hk_uuid),
             sample_hz = EXCLUDED.sample_hz, n_samples = EXCLUDED.n_samples, voltage = EXCLUDED.voltage,
             classification = EXCLUDED.classification, classification_label = EXCLUDED.classification_label,
             symptoms = EXCLUDED.symptoms, apple_avg_hr = COALESCE(EXCLUDED.apple_avg_hr, ecg_recordings.apple_avg_hr),
             device = EXCLUDED.device, software_version = EXCLUDED.software_version, derived = EXCLUDED.derived,
             import_id = EXCLUDED.import_id
           WHERE ecg_recordings.origin = 'apple_export' OR EXCLUDED.origin = 'app'
           RETURNING id""",
        (user_id, _second(e.start_at), origin, hk_uuid, e.sample_hz, len(e.voltage_uv), E.pack(e.voltage_uv),
         e.classification, e.classification_label, e.symptoms, e.avg_hr, e.device, e.software_version,
         Jsonb(derived), import_id)).fetchone()
    if row is None:   # an export copy of a recording the app already sent
        row = conn.execute("SELECT id FROM ecg_recordings WHERE user_id = %s AND start_at = %s",
                           (user_id, _second(e.start_at))).fetchone()
    return row["id"]


def _ecg_summary(r: dict, tz=None) -> dict:
    d = r["derived"] or {}
    start = r["start_at"].astimezone(tz) if tz else r["start_at"]
    return {"id": r["id"], "start_at": start.isoformat(timespec="seconds"), "classification": r["classification"],
            "classification_zh": E.CLASS_ZH.get(r["classification"], r["classification"]),
            "classification_text": (E.CLASS_EN if is_en() else E.CLASS_ZH).get(r["classification"], r["classification"]),
            "classification_label": r["classification_label"], "symptoms": r["symptoms"],
            "hr": r["apple_avg_hr"] or d.get("hr"), "rmssd": d.get("rmssd"), "quality": d.get("quality"),
            "duration_s": round(r["n_samples"] / r["sample_hz"], 1), "device": r["device"], "origin": r["origin"]}


def list_ecg(conn: psycopg.Connection, user_id: int, limit: int = 100, tz=None) -> list[dict]:
    rows = conn.execute("""SELECT id, start_at, classification, classification_label, symptoms, apple_avg_hr, derived,
                                  n_samples, sample_hz, device, origin FROM ecg_recordings
                           WHERE user_id = %s ORDER BY start_at DESC LIMIT %s""", (user_id, limit)).fetchall()
    return [_ecg_summary(r, tz) for r in rows]


def get_ecg(conn: psycopg.Connection, user_id: int, ecg_id: int, tz=None) -> dict | None:
    r = conn.execute("""SELECT * FROM ecg_recordings WHERE user_id = %s AND id = %s""", (user_id, ecg_id)).fetchone()
    if r is None:
        return None
    d = r["derived"] or {}
    return {**_ecg_summary(r, tz), "sample_hz": r["sample_hz"], "unit": "µV", "voltage": E.unpack(r["voltage"]),
            "r_peaks": d.get("r_peaks", []), "rr_ms": d.get("rr_ms", []), "software_version": r["software_version"]}


def delete_ecg(conn: psycopg.Connection, user_id: int, ecg_id: int) -> bool:
    return conn.execute("DELETE FROM ecg_recordings WHERE user_id = %s AND id = %s RETURNING id",
                        (user_id, ecg_id)).fetchone() is not None


def heartbeat_readings(conn: psycopg.Connection, user_id: int, start: datetime, end: datetime) -> list[dict]:
    """Per-reading summary (no beat arrays) in [start, end)."""
    return conn.execute("""SELECT start_at, end_at, source, origin, apple_sdnn, rmssd, sdnn, mean_rr, n_beats, n_valid
                           FROM heartbeat_series WHERE user_id = %s AND start_at >= %s AND start_at < %s
                           ORDER BY start_at""", (user_id, start, end)).fetchall()


def rmssd_samples(conn: psycopg.Connection, user_id: int, start: datetime, end: datetime) -> list[tuple[datetime, float, bool]]:
    """(start, RMSSD, is_export) for scoring (scores/inputs.py when HRV_KIND = 'rmssd')."""
    return [(r["start_at"], r["rmssd"], r["origin"] == "apple_export") for r in conn.execute(
        """SELECT start_at, rmssd, origin FROM heartbeat_series
           WHERE user_id = %s AND start_at >= %s AND start_at < %s AND rmssd IS NOT NULL ORDER BY start_at""",
        (user_id, start, end))]

"""Uploads from the Watch2Whoop app: beat-to-beat HRV series and ECG recordings read from HealthKit by the app itself
(apps/watch2whoop/ios/Runner/RawSync.swift, lib/src/raw_sync.dart), not through Open Wearables (OW SDK 0.15 has neither type).

One request = one batch {batch_id, heartbeats[], ecgs[], deleted[]}, written with the same code as import-raw (store.py: RMSSD /
SDNN for each reading and the ECG R peaks / heart rate / RMSSD are computed on insert), origin 'app'.
- Idempotent: samples whose HealthKit UUID is already stored for this user are skipped; a re-sent batch (same batch_id) is
  answered from rawdata_uploads without touching the data (migration 430). The app moves its HealthKit anchor only after a 200.
- deleted[] = UUIDs HealthKit reports as deleted (the person deleted the reading / ECG in Health): the app copy is removed.
- Consent: ECGs and beat-to-beat intervals were first named in consent version RAW_CONSENT_VERSION (clauses 1, 2, 4, 6).
  Anyone (but the owner) who has not agreed to that version or a later one, including people who agreed to an older one
  or on paper, gets 409 consent_required: nothing is stored, the app keeps its anchor and tries again after the person
  confirmed the new consent on the Today page. Other health data is not affected.
- water[]: Apple Health drinks (HKQuantityTypeIdentifierDietaryWater, issue #125; Open Wearables 0.9 does not carry it)
  -> water_intake (water/store.py, source apple_health), deduplicated by UUID. A batch with only water needs the ordinary
  consent; deleted[] also removes water samples. Skipped while the water rollout flag is off for the user; every answer
  (and GET status) carries water_enabled, so the app reads and sends water only while it is on and keeps its water anchor
  when a batch was skipped (nothing is lost while the flag is off; an older server without the field counts as off).
"""
import base64
import binascii
import struct
import psycopg
from psycopg.types.json import Jsonb

from ..profile import consent
from . import ecg as E
from . import store

RAW_CONSENT_VERSION = "2026-10-22.1"   # first consent version that names ECGs and beat-to-beat intervals
MAX_BODY_BYTES = 2 * 1024 * 1024    # the public nginx /v1/ location allows 2 MB; the app aims at ≤ 1 MB per batch
MAX_HEARTBEATS = 500                # readings per batch (a reading is ~1 minute, ~50 beats)
MAX_BEATS = 4000                    # beats per reading (Breathe sessions are longer than background readings)
MAX_ECGS = 20                       # recordings per batch (~30 KB each as base64 int16)
MAX_DELETED = 2000
MAX_WATER = 2000                    # Apple Health drinks per batch
SYMPTOMS_PRESENT = "symptoms present (see Health)"


class IngestError(ValueError):
    pass


def decode_voltage(b64: str) -> list[int]:
    """int16 little-endian µV, base64 (what the app sends) -> list of µV."""
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise IngestError("voltage_b64 is not base64") from e
    if len(raw) % 2:
        raise IngestError("voltage_b64 must hold int16 values")
    n = len(raw) // 2
    if not 0 < n <= E.MAX_SAMPLES:
        raise IngestError(f"an ECG has 1–{E.MAX_SAMPLES} samples")
    return list(struct.unpack(f"<{n}h", raw))


def _known(conn: psycopg.Connection, table: str, user_id: int, uuids: list[str]) -> set[str]:
    if not uuids:
        return set()
    rows = conn.execute(f"SELECT hk_uuid::text AS u FROM {table} WHERE user_id = %s AND hk_uuid = ANY(%s::uuid[])",
                        (user_id, uuids)).fetchall()
    return {r["u"] for r in rows}


def _ecg(it: dict) -> E.Ecg:
    cls = it.get("classification") or "notSet"
    if cls not in E.CLASS_ZH:
        cls = "unrecognized"
    return E.Ecg(start_at=it["start"], sample_hz=float(it["sample_hz"]), voltage_uv=decode_voltage(it["voltage_b64"]),
                 classification=cls, classification_label=None,
                 symptoms=SYMPTOMS_PRESENT if it.get("symptoms_status") == "present" else None,
                 device=it.get("device"), software_version=it.get("software_version"), avg_hr=it.get("avg_hr"))


def ingest(conn: psycopg.Connection, user_id: int, batch: dict, size: int | None = None) -> dict:
    """batch: validated payload (rawdata/api.py IngestIn as a dict, datetimes already parsed). Commits. Returns counts."""
    raw = bool(batch.get("heartbeats") or batch.get("ecgs"))
    if (raw and not consent.agreed_at_least(conn, user_id, RAW_CONSENT_VERSION)) or consent.needs_consent(conn, user_id):
        raise PermissionError("consent_required")
    done = conn.execute("SELECT counts FROM rawdata_uploads WHERE user_id = %s AND batch_id = %s",
                        (user_id, batch["batch_id"])).fetchone()
    from .. import water
    water_on = water.enabled(conn, user_id)
    if done is not None:
        return {**done["counts"], "duplicate_batch": True, "water_enabled": water_on}
    # Decode every ECG first: a bad recording rejects the whole batch before anything is written
    ecgs = [(it, _ecg(it)) for it in batch.get("ecgs") or []]
    hb = batch.get("heartbeats") or []
    known_hb = _known(conn, "heartbeat_series", user_id, [str(it["uuid"]) for it in hb])
    known_ecg = _known(conn, "ecg_recordings", user_id, [str(it["uuid"]) for it, _ in ecgs])
    items = [{"start_at": it["start"], "end_at": it["end"], "source": it.get("source") or "", "origin": "app",
              "beats_s": it["beats"], "gaps": it.get("gaps"), "hk_uuid": str(it["uuid"])}
             for it in hb if str(it["uuid"]) not in known_hb]
    n_hb = store.save_heartbeats(conn, user_id, items)
    n_ecg = 0
    for it, e in ecgs:
        if str(it["uuid"]) in known_ecg:
            continue
        store.save_ecg(conn, user_id, e, "app", hk_uuid=str(it["uuid"]))
        n_ecg += 1
    deleted = [str(u) for u in batch.get("deleted") or []]
    n_del = 0
    if deleted:
        for table in ("heartbeat_series", "ecg_recordings"):
            n_del += len(conn.execute(f"""DELETE FROM {table} WHERE user_id = %s AND origin = 'app'
                                          AND hk_uuid = ANY(%s::uuid[]) RETURNING 1""", (user_id, deleted)).fetchall())
    n_water = n_water_skip = 0
    if water_on:
        from ..water import store as water_store
        n_del += water_store.delete_hk(conn, user_id, deleted)
        for w in batch.get("water") or []:
            r = water_store.add(conn, user_id, w["ml"], "apple_health", w["start"], hk_uuid=str(w["uuid"]))
            n_water_skip += r["duplicate"]
            n_water += not r["duplicate"]
    counts = {"heartbeats": n_hb, "ecgs": n_ecg, "water": n_water, "deleted": n_del,
              "skipped": (len(hb) - len(items)) + (len(ecgs) - n_ecg) + n_water_skip}
    conn.execute("""INSERT INTO rawdata_uploads (user_id, batch_id, device, app_version, counts, bytes)
                    VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                 (user_id, batch["batch_id"], (batch.get("device") or "")[:80] or None,
                  (batch.get("app_version") or "")[:40] or None, Jsonb(counts), size))
    conn.commit()
    return {**counts, "water_enabled": water_on}


def status(conn: psycopg.Connection, user_id: int) -> dict:
    """What the app has uploaded so far (shown on its sync page and in the admin's checks)."""
    from .. import water
    r = conn.execute("""SELECT (SELECT count(*) FROM heartbeat_series WHERE user_id = %(u)s AND origin = 'app') AS heartbeats,
                               (SELECT count(*) FROM ecg_recordings WHERE user_id = %(u)s AND origin = 'app') AS ecgs,
                               (SELECT count(*) FROM water_intake WHERE user_id = %(u)s AND hk_uuid IS NOT NULL) AS water,
                               (SELECT max(received_at) FROM rawdata_uploads WHERE user_id = %(u)s) AS last_upload_at""",
                     {"u": user_id}).fetchone()
    return {"heartbeats": r["heartbeats"], "ecgs": r["ecgs"], "water": r["water"], "water_enabled": water.enabled(conn, user_id),
            "last_upload_at": r["last_upload_at"].isoformat(timespec="seconds") if r["last_upload_at"] else None}

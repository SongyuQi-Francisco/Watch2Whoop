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
- quantities[]: HealthKit quantity samples of the types Open Wearables cannot read (registry rawdata/hk_types.py; stand
  time, exercise minutes, sleeping wrist temperature ...), written per type by rawdata/quantities.py into hk_samples and
  daily values (source 'app'). Ordinary consent only. Stopped by the emergency flag app_types_pause: while it is on for the
  user nothing is stored and every answer says metrics_enabled false, so the app keeps its anchors and sends them later
  (an older server without the field counts as off, so the app sends none). A batch that changed days queues the score
  recompute from the earliest of them (sync/refresh.queue_scores, the queue sync-refresh-run works through).
- The sync engine's fields (sources, samples, categories, workouts, stats, range, range_done, runs, phase, tz; flag
  apple_sync_engine): rawdata/engine.py. While the flag is on, deleted[] also removes engine samples.
The batch fields are written one after the other by their own writer (WRITERS); a field never depends on another.
"""
import base64
import binascii
import struct
import psycopg
from psycopg.types.json import Jsonb

from ..profile import consent
from ..rollout import flags
from . import ecg as E
from . import engine as EN
from . import hk_types
from . import quantities as Q
from . import store

RAW_CONSENT_VERSION = "2026-10-22.1"   # first consent version that names ECGs and beat-to-beat intervals
MAX_BODY_BYTES = 2 * 1024 * 1024    # the public nginx /v1/ location allows 2 MB; the app aims at ≤ 1 MB per batch
MAX_HEARTBEATS = 500                # readings per batch (a reading is ~1 minute, ~50 beats)
MAX_BEATS = 4000                    # beats per reading (Breathe sessions are longer than background readings)
MAX_ECGS = 20                       # recordings per batch (~30 KB each as base64 int16)
MAX_DELETED = 2000
MAX_WATER = 2000                    # Apple Health drinks per batch
MAX_QUANTITY_GROUPS = 20            # quantity types per batch
MAX_QUANTITIES = 2000               # quantity samples per type in a batch (~150 bytes each as JSON)
PAUSE_FLAG = "app_types_pause"      # emergency stop for quantities[] (rollout/registry.py)
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


def metrics_enabled(conn: psycopg.Connection, user_id: int) -> bool:
    """quantities[] is taken unless the emergency flag app_types_pause is on for this user."""
    return not flags.enabled(conn, user_id, PAUSE_FLAG)


def _tz(conn: psycopg.Connection, user_id: int):
    from zoneinfo import ZoneInfo
    return ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])


# ---------------------------------------------------------------- one writer per batch field
def _write_heartbeats(conn, user_id: int, batch: dict, ctx: dict) -> dict:
    hb = batch.get("heartbeats") or []
    known = _known(conn, "heartbeat_series", user_id, [str(it["uuid"]) for it in hb])
    items = [{"start_at": it["start"], "end_at": it["end"], "source": it.get("source") or "", "origin": "app",
              "beats_s": it["beats"], "gaps": it.get("gaps"), "hk_uuid": str(it["uuid"])}
             for it in hb if str(it["uuid"]) not in known]
    n = store.save_heartbeats(conn, user_id, items)
    return {"heartbeats": n, "skipped": len(hb) - len(items)}


def _write_ecgs(conn, user_id: int, batch: dict, ctx: dict) -> dict:
    ecgs = ctx["ecgs"]
    known = _known(conn, "ecg_recordings", user_id, [str(it["uuid"]) for it, _ in ecgs])
    n = 0
    for it, e in ecgs:
        if str(it["uuid"]) in known:
            continue
        store.save_ecg(conn, user_id, e, "app", hk_uuid=str(it["uuid"]))
        n += 1
    return {"ecgs": n, "skipped": len(ecgs) - n}


def _write_water(conn, user_id: int, batch: dict, ctx: dict) -> dict:
    if not ctx["water_enabled"]:
        return {}
    from ..water import store as water_store
    n = skip = 0
    for w in batch.get("water") or []:
        r = water_store.add(conn, user_id, w["ml"], "apple_health", w["start"], hk_uuid=str(w["uuid"]))
        skip += r["duplicate"]
        n += not r["duplicate"]
    return {"water": n, "skipped": skip}


def _changed(ctx: dict, d) -> None:
    if d is not None and (ctx["changed_from"] is None or d < ctx["changed_from"]):
        ctx["changed_from"] = d


def _write_quantities(conn, user_id: int, batch: dict, ctx: dict) -> dict:
    groups = batch.get("quantities") or []
    if not ctx["metrics_enabled"] or not (groups or ctx["deleted"]):
        return {}
    r = Q.ingest(conn, user_id, groups, ctx["deleted"], _tz(conn, user_id))
    _changed(ctx, r["changed_from"])
    return {"metrics": r["metrics"], "skipped": r["skipped"], "dropped": r["dropped"], "deleted": r["deleted"]}


def _write_engine(conn, user_id: int, batch: dict, ctx: dict) -> dict:
    """The sync engine's fields (rawdata/engine.py); with the flag on also the engine's deletions."""
    if not ctx["engine"] or not (ctx["uses_engine"] or ctx["deleted"]):
        return {}
    r = EN.write(conn, user_id, batch, _tz(conn, user_id), ctx["deleted"])
    _changed(ctx, r.pop("changed_from"))
    dirty_from = r.pop("dirty_from")
    if ctx["mode"] == "app":   # the engine's data counts: derive + recompute from the earliest changed day (sync-refresh-run)
        _changed(ctx, dirty_from)
    return r


def _write_deleted(conn, user_id: int, batch: dict, ctx: dict) -> dict:
    """UUIDs HealthKit reports as deleted: the app copies of beat-to-beat readings, ECGs and (flag on) drinks."""
    deleted = ctx["deleted"]
    if not deleted:
        return {}
    n = 0
    for table in ("heartbeat_series", "ecg_recordings"):
        n += len(conn.execute(f"""DELETE FROM {table} WHERE user_id = %s AND origin = 'app'
                                  AND hk_uuid = ANY(%s::uuid[]) RETURNING 1""", (user_id, deleted)).fetchall())
    if ctx["water_enabled"]:
        from ..water import store as water_store
        n += water_store.delete_hk(conn, user_id, deleted)
    return {"deleted": n}


# the engine before quantities: it removes deleted samples of every type (and marks their days), quantities then finds none
WRITERS = (_write_heartbeats, _write_ecgs, _write_deleted, _write_water, _write_engine, _write_quantities)
COUNTS = ("heartbeats", "ecgs", "water", "metrics", "deleted", "skipped", "dropped",
          "samples", "workouts", "buckets", "removed", "runs")


def ingest(conn: psycopg.Connection, user_id: int, batch: dict, size: int | None = None) -> dict:
    """batch: validated payload (rawdata/api.py IngestIn as a dict, datetimes already parsed). Commits. Returns counts."""
    raw = bool(batch.get("heartbeats") or batch.get("ecgs"))
    if (raw and not consent.agreed_at_least(conn, user_id, RAW_CONSENT_VERSION)) or consent.needs_consent(conn, user_id):
        raise PermissionError("consent_required")
    from .. import water
    flags_now = {"water_enabled": water.enabled(conn, user_id), "metrics_enabled": metrics_enabled(conn, user_id)}
    done = conn.execute("SELECT counts FROM rawdata_uploads WHERE user_id = %s AND batch_id = %s",
                        (user_id, batch["batch_id"])).fetchone()
    if done is not None:
        return {**done["counts"], "duplicate_batch": True, "dirty_days": None, **flags_now}   # unknown: pull as usual
    engine, uses = EN.enabled(conn, user_id), EN.uses_engine(batch)
    if uses and not engine:
        raise EN.Refused("engine_disabled")
    try:
        Q.check_groups(batch.get("quantities") or [])
        if uses:
            EN.check(batch, set(EN.accepted_types(conn, user_id, flags_now["water_enabled"], flags_now["metrics_enabled"])))
    except ValueError as e:
        raise IngestError(str(e)) from e
    if uses:
        EN.rate_limit(conn, user_id, batch.get("phase"), size)
    # Decode every ECG first: a bad recording rejects the whole batch before anything is written
    ctx = {**flags_now, "ecgs": [(it, _ecg(it)) for it in batch.get("ecgs") or []],
           "deleted": [str(u) for u in batch.get("deleted") or []], "changed_from": None,
           "engine": engine, "uses_engine": uses, "mode": EN.mode(conn, user_id) if engine else "ow"}
    counts = dict.fromkeys(COUNTS, 0)
    try:
        for write in WRITERS:
            for k, v in write(conn, user_id, batch, ctx).items():
                counts[k] += v
    except EN.BadBatch as e:   # found while writing (e.g. an unknown range): nothing of the batch is kept
        conn.rollback()
        raise IngestError(str(e)) from e
    conn.execute("""INSERT INTO rawdata_uploads (user_id, batch_id, device, app_version, counts, bytes, phase, tz)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                 (user_id, batch["batch_id"], (batch.get("device") or "")[:80] or None,
                  (batch.get("app_version") or "")[:40] or None, Jsonb(counts), size, batch.get("phase"),
                  batch.get("tz") or None))
    # the local days this batch changed (any table the native app mirrors, nativesync/): the app pulls just these;
    # values derived later (daily values, sleep, scores) reach it through the change log and a silent push
    from ..nativesync import store as sync_store
    dirty_days = sync_store.days_changed_here(conn, user_id)
    conn.commit()
    if ctx["changed_from"] is not None:
        from ..sync import refresh
        refresh.queue_scores(conn, user_id, ctx["changed_from"])
    return {**counts, "dirty_days": dirty_days, **flags_now}


def status(conn: psycopg.Connection, user_id: int, light: bool = False) -> dict:
    """What the app has uploaded so far (shown on its sync page and in the admin's checks), and what the server takes:
    water_enabled, metrics_enabled + metric_types (the HealthKit types of quantities[]; the app reads only these),
    sync_center (the app shows its sync center), engine (the sync engine: enabled, mode, accepted fields, encodings,
    types and limits; rawdata/engine.describe). light (the answer to a sync-engine batch): without the four counts,
    which count every stored sample of the user and grow with the history (counts: false says they are left out)."""
    from .. import water
    if light:
        r = conn.execute("SELECT max(received_at) AS last_upload_at FROM rawdata_uploads WHERE user_id = %s",
                         (user_id,)).fetchone()
        counts = {"counts": False}
    else:
        r = conn.execute("""SELECT (SELECT count(*) FROM heartbeat_series WHERE user_id = %(u)s AND origin = 'app') AS heartbeats,
                                   (SELECT count(*) FROM ecg_recordings WHERE user_id = %(u)s AND origin = 'app') AS ecgs,
                                   (SELECT count(*) FROM water_intake WHERE user_id = %(u)s AND hk_uuid IS NOT NULL) AS water,
                                   (SELECT count(*) FROM hk_samples WHERE user_id = %(u)s) AS metrics,
                                   (SELECT max(received_at) FROM rawdata_uploads WHERE user_id = %(u)s) AS last_upload_at""",
                         {"u": user_id}).fetchone()
        counts = {k: r[k] for k in ("heartbeats", "ecgs", "water", "metrics")}
    on, water_on = metrics_enabled(conn, user_id), water.enabled(conn, user_id)
    return {**counts,
            "water_enabled": water_on, "metrics_enabled": on, "engine": EN.describe(conn, user_id, water_on, on),
            "metric_types": [t.hk for t in hk_types.HK_TYPES] if on else [],
            "sync_center": flags.enabled(conn, user_id, "sync_center"),
            "last_upload_at": r["last_upload_at"].isoformat(timespec="seconds") if r["last_upload_at"] else None}

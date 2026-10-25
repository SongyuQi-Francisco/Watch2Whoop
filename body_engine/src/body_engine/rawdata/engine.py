"""The app's own HealthKit sync engine, server side step S1 (docs/apple-sync-engine.md §3.2; flag apple_sync_engine): the
batch fields it adds to POST /v1/ingest/healthkit, how they are stored, and what the server tells the app it takes.

Registry: rawdata/hk_types.ENGINE_TYPES (the app ships the same table). Fields (all optional, columnar; rawdata/api.py):
- sources[]: the batch's HealthKit sources (name, bundle, product, device ...) -> hk_sources; samples refer by index `src`;
- samples[] (quantities, values in the registry's `unit`, stored × `factor`), categories[] (value = HealthKit's integer,
  e.g. the sleep stage), workouts[] -> hk_samples, deduplicated by (user, HealthKit UUID); HealthKit samples never change,
  a changed sample is a deletion + a new UUID, so a known UUID is skipped;
- stats[]: 15-minute statistics buckets of cumulative types -> hk_stats; the span a group covers is replaced;
- deleted[] (the existing field): with the engine on, removes those UUIDs from hk_samples too;
- range / range_done: re-sync of a date range (§3.1.5). Every page carries the range; at range_done, stored samples of the
  range's types inside it that were received before the range started and not sent again are removed;
- runs[]: the app's sync rounds (trigger, duration, counts only) -> sync_runs;
- phase (recent / backfill / incremental / range) and tz (the phone's time zone) -> rawdata_uploads.
Every (type, local day) that changed is queued in hk_dirty_days, from which step S2 derives daily values, sleep and
workouts. The types the app already uploads through quantities[] (rawdata/hk_types.HK_TYPES) keep that behaviour: their
daily values are remade right away (rawdata/quantities.rebuild_days) and the score recompute is queued, and while the
emergency flag app_types_pause is on they are not taken.

The engine fields are refused (409 engine_disabled, nothing stored) while the flag is off for the user, and a type the
server does not take right now is refused as a whole batch (409 type_paused): in both cases the app keeps its anchors and
reads GET /v1/ingest/healthkit again, whose `engine` block says what is taken (accepts, types, mode, limits). An older
app sends none of these fields and is not affected (no rate limits either).
Rate limits, per user, only for batches with engine fields: batches per minute and bytes per UTC day, larger for the
backfill and range phases; over the limit = 429 with Retry-After and nothing stored.
"""
from __future__ import annotations

import zlib
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from psycopg.types.json import Jsonb

from ..rollout import flags
from . import hk_types as T
from . import quantities as Q

FLAG = "apple_sync_engine"
FIELDS = ("sources", "samples", "categories", "workouts", "stats", "range", "range_done", "runs", "phase", "tz")
PHASES = ("recent", "backfill", "incremental", "range")
BIG_PHASES = ("backfill", "range")    # first sync and range re-sync: larger rate limits
ENCODINGS = ("gzip",)
WORKOUT = "HKWorkoutTypeIdentifier"
BUCKET_S = 900                        # stats bucket, UTC-aligned (covers the 30 / 45-minute time zones)

# Sizes (the public nginx allows 2 MB per request, rawdata/ingest.MAX_BODY_BYTES; the app aims at ≤ 1 MB compressed)
MAX_INFLATED_BYTES = 16 * 1024 * 1024  # a gzip body larger than this when decompressed is refused (413)
MAX_SOURCES = 100
MAX_GROUPS = 80                       # samples[] / categories[] / stats[] groups each
MAX_GROUP_ITEMS = 5000                # samples of one group (the app's foreground page is 2000)
MAX_BUCKETS = 10000                   # buckets of one stats group (~104 days)
MAX_ITEMS = 20000                     # samples + categories + stats buckets of one batch
MAX_WORKOUTS = 200
MAX_WORKOUT_EVENTS = 500
MAX_RUNS = 50
MAX_RANGE_TYPES = 100
MAX_RANGE_DAYS = 3660
MAX_SAMPLE_SPAN = timedelta(days=7)   # a longer single sample is dropped
FUTURE_SLACK = timedelta(days=1)      # samples starting later than now + this are dropped (bad phone clock)
EPOCH_MIN, EPOCH_MAX = 946684800.0, 4102444800.0   # 2000-01-01 .. 2100-01-01 (UTC seconds)

# Rate limits per user (engine batches only)
BATCHES_PER_MINUTE = 30
BIG_BATCHES_PER_MINUTE = 120
DAY_BYTES = 64 * 1024 * 1024          # wire bytes per UTC day
BIG_DAY_BYTES = 512 * 1024 * 1024


class BadBatch(ValueError):
    """The batch breaks the contract (unknown type, wrong unit or field, bad index ...): 422, nothing stored."""


class Refused(Exception):
    """Not taken right now (engine_disabled / type_paused): 409, nothing stored, the app re-reads the status."""


class TooLarge(Exception):
    pass


class RateLimited(Exception):
    def __init__(self, retry_after: int):
        super().__init__("rate_limited")
        self.retry_after = max(1, int(retry_after))


# ---------------------------------------------------------------- what the server takes
def enabled(conn: psycopg.Connection, user_id: int) -> bool:
    return flags.enabled(conn, user_id, FLAG)


MODES = ("ow", "shadow", "app")      # = importers/common.SYNC_MODES
PRIMARY_FLAG = "apple_sync_primary"


def mode(conn: psycopg.Connection, user_id: int) -> str:
    """ow = only Open Wearables feeds the scores (flag apple_sync_engine off); shadow = the engine uploads too, stored for
    the comparison (§3.2.3); app = the engine's values take part, above ow (D4): only after `body-engine sync-mode` switched
    this user (sync_mode_state.mode = app, rawdata/cutover.py) AND while the kill switch apple_sync_primary is still on for
    them and they are not pinned to ow. Turning the flag off or pinning takes effect on the next read (rollback); the
    cut-over then cleans up the derived data."""
    if not enabled(conn, user_id):
        return "ow"
    st = conn.execute("SELECT mode, pin FROM sync_mode_state WHERE user_id = %s", (user_id,)).fetchone()
    if st and st["mode"] == "app" and st["pin"] is None and flags.enabled(conn, user_id, PRIMARY_FLAG):
        return "app"
    return "shadow"


def uses_engine(batch: dict) -> bool:
    return any(batch.get(f) not in (None, [], "") for f in FIELDS)


def accepted_types(conn: psycopg.Connection, user_id: int, water_enabled: bool, metrics_enabled: bool) -> list[str]:
    """HealthKit types the engine may send now: the registry minus water while the water flag is off and minus the
    direct-upload types while app_types_pause is on."""
    def ok(t: T.EngineType) -> bool:
        if t.flag == "water" and not water_enabled:
            return False
        if t.flag and t.flag != "water" and not flags.enabled(conn, user_id, t.flag):
            return False
        return metrics_enabled or t.hk not in T.BY_HK
    return [t.hk for t in T.ENGINE_TYPES if ok(t)]


def describe(conn: psycopg.Connection, user_id: int, water_enabled: bool, metrics_enabled: bool) -> dict:
    """The `engine` block of GET /v1/ingest/healthkit (and every POST answer)."""
    from .ingest import MAX_BODY_BYTES
    accepts = ["heartbeats", "ecgs", "deleted"] + ["water"] * water_enabled + ["quantities"] * metrics_enabled
    on = enabled(conn, user_id)
    if not on:
        return {"enabled": False, "mode": mode(conn, user_id), "accepts": accepts, "encodings": [], "types": []}
    return {"enabled": True, "mode": mode(conn, user_id), "accepts": accepts + list(FIELDS),
            "encodings": list(ENCODINGS), "types": accepted_types(conn, user_id, water_enabled, metrics_enabled),
            "limits": {"body_bytes": MAX_BODY_BYTES, "inflated_bytes": MAX_INFLATED_BYTES, "items": MAX_ITEMS,
                       "group_items": MAX_GROUP_ITEMS, "buckets": MAX_BUCKETS, "workouts": MAX_WORKOUTS,
                       "batches_per_minute": BATCHES_PER_MINUTE, "big_batches_per_minute": BIG_BATCHES_PER_MINUTE}}


def gunzip(raw: bytes) -> bytes:
    """One gzip member, at most MAX_INFLATED_BYTES once decompressed (a compression bomb stops there)."""
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = d.decompress(raw, MAX_INFLATED_BYTES + 1)
    except zlib.error as e:
        raise BadBatch("the body is not valid gzip") from e
    if len(out) > MAX_INFLATED_BYTES or d.unconsumed_tail:
        raise TooLarge
    if not d.eof or d.unused_data:
        raise BadBatch("the body must be one complete gzip member")
    return out


# ---------------------------------------------------------------- checks before anything is written
def _type(hk: str, accepted: set[str], field: str, ok) -> T.EngineType:
    t = T.ENGINE_BY_HK.get(hk)
    if t is None:
        raise BadBatch(f"{field}: unknown HealthKit type {hk}")
    if not ok(t):
        raise BadBatch(f"{field}: {hk} does not belong in {field}")
    if hk not in accepted:
        raise Refused("type_paused")
    return t


def check(batch: dict, accepted: set[str]) -> None:
    """Contract checks of the engine fields (raises BadBatch / Refused). Pydantic already checked shapes and lengths."""
    n_src = len(batch.get("sources") or [])
    items = 0
    for field, ok in (("samples", lambda t: t.kind == "quantity" and t.raw),
                      ("categories", lambda t: t.kind == "category"),
                      ("stats", lambda t: t.stats)):
        for g in batch.get(field) or []:
            t = _type(g["type"], accepted, field, ok)
            if field != "categories" and g["unit"] != t.unit:
                raise BadBatch(f"{field}: {t.hk} must be sent in {t.unit}, not {g['unit']}")
            if field == "stats":
                items += len(g["values"])
                if g["bucket_s"] != BUCKET_S or g["start"] % BUCKET_S:
                    raise BadBatch(f"stats: {t.hk} buckets must be {BUCKET_S} s, aligned to UTC")
                continue
            items += len(g["uuid"])
            if any(s is not None and not 0 <= s < n_src for s in g.get("src") or []):
                raise BadBatch(f"{field}: {t.hk} refers to a source the batch does not have")
    if items > MAX_ITEMS:
        raise BadBatch(f"at most {MAX_ITEMS} samples and buckets per batch")
    if batch.get("workouts"):
        _type(WORKOUT, accepted, "workouts", lambda t: t.kind == "workout")
        if any(w.get("src") is not None and not 0 <= w["src"] < n_src for w in batch["workouts"]):
            raise BadBatch("workouts: refers to a source the batch does not have")
    r = batch.get("range")
    if r:
        for hk in r["types"]:
            _type(hk, accepted, "range", lambda t: t.kind in ("category", "workout") or (t.kind == "quantity" and t.raw))
        if not r["start"] < r["end"] or r["end"] - r["start"] > MAX_RANGE_DAYS * 86400:
            raise BadBatch(f"range: start must be before end, at most {MAX_RANGE_DAYS} days")
    if batch.get("tz"):
        try:
            ZoneInfo(batch["tz"])
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise BadBatch(f"unknown time zone {batch['tz']}") from e


def rate_limit(conn: psycopg.Connection, user_id: int, phase: str | None, size: int | None) -> None:
    """Raises RateLimited when this batch would go over the user's per-minute batches or per-day bytes."""
    big = phase in BIG_PHASES
    now = datetime.now(timezone.utc)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    minute = now - timedelta(seconds=60)
    r = conn.execute("""SELECT count(*) FILTER (WHERE received_at > %(m)s) AS n, min(received_at) FILTER (WHERE received_at > %(m)s) AS oldest,
                               coalesce(sum(bytes) FILTER (WHERE received_at >= %(d)s), 0) AS day_bytes
                        FROM rawdata_uploads WHERE user_id = %(u)s AND received_at >= least(%(m)s, %(d)s)""",
                     {"u": user_id, "m": minute, "d": day0}).fetchone()
    if r["n"] >= (BIG_BATCHES_PER_MINUTE if big else BATCHES_PER_MINUTE):
        raise RateLimited(60 - (now - (r["oldest"] or now)).total_seconds())
    if r["day_bytes"] + (size or 0) > (BIG_DAY_BYTES if big else DAY_BYTES):
        raise RateLimited((day0 + timedelta(days=1) - now).total_seconds())


# ---------------------------------------------------------------- writing (in the caller's transaction)
def _ts(x: float) -> datetime:
    return datetime.fromtimestamp(x, timezone.utc)


def _sources(conn, user_id: int, sources: list[dict]) -> list[int]:
    ids = []
    for s in sources:
        ids.append(conn.execute(
            """INSERT INTO hk_sources (user_id, name, bundle, product, device_type, device_name, device_model, manufacturer,
                                       os_version)
               VALUES (%(u)s, %(name)s, %(bundle)s, %(product)s, %(device_type)s, %(device_name)s, %(device_model)s,
                       %(manufacturer)s, %(os_version)s)
               ON CONFLICT (user_id, name, bundle, product, device_name, device_model) DO UPDATE
               SET last_seen = now(), device_type = EXCLUDED.device_type, manufacturer = EXCLUDED.manufacturer,
                   os_version = CASE WHEN EXCLUDED.os_version <> '' THEN EXCLUDED.os_version ELSE hk_sources.os_version END
               RETURNING id""", {"u": user_id, **{k: s.get(k) or "" for k in (
                "name", "bundle", "product", "device_type", "device_name", "device_model", "manufacturer", "os_version")}}
        ).fetchone()["id"])
    return ids


def _range(conn, user_id: int, r: dict) -> dict:
    return conn.execute("""INSERT INTO sync_ranges (user_id, client_id, types, start_at, end_at) VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (user_id, client_id) DO UPDATE SET types = sync_ranges.types
                           RETURNING id, types, start_at, end_at, created_at, done_at""",
                        (user_id, str(r["id"]), list(r["types"]), _ts(r["start"]), _ts(r["end"]))).fetchone()


def _rows(batch: dict, src_ids: list[int], src_names: list[str], tz: ZoneInfo, now: datetime) -> tuple[list[dict], int]:
    """hk_samples rows of samples[], categories[] and workouts[] (stored units), and how many were dropped."""
    rows, dropped = [], 0
    late = now + FUTURE_SLACK

    def add(t: T.EngineType, uuid, start: datetime, end: datetime, value: float, src, tzname, text=None, meta=None):
        nonlocal dropped
        if not (t.lo <= value <= t.hi) or end < start or end - start > MAX_SAMPLE_SPAN or start > late:
            dropped += 1
            return
        rows.append({"uuid": str(uuid), "type": t.hk, "start": start, "end": end, "day": start.astimezone(tz).date(),
                     "value": value, "text": text, "meta": meta, "tz": tzname,
                     "source": src_names[src] if src is not None else "", "source_id": src_ids[src] if src is not None else None})

    for field in ("samples", "categories"):
        for g in batch.get(field) or []:
            t = T.ENGINE_BY_HK[g["type"]]
            src, tzs = g.get("src") or [None] * len(g["uuid"]), g.get("tz") or [None] * len(g["uuid"])
            for i, u in enumerate(g["uuid"]):
                add(t, u, _ts(g["start"][i]), _ts(g["end"][i]), float(g["value"][i]) * t.factor, src[i], tzs[i])
    wt = T.ENGINE_BY_HK[WORKOUT]
    for w in batch.get("workouts") or []:
        stats = {k: v for k, v in (w.get("stats") or {}).items() if v is not None}
        meta = {"activity": w["activity"], "stats": stats, "events": w.get("events") or [], "meta": w.get("meta") or {}}
        tzname = (w.get("meta") or {}).get("tz")
        add(wt, w["uuid"], _ts(w["start"]), _ts(w["end"]), w["duration_s"] / 60, w.get("src"),
            tzname if isinstance(tzname, str) else None, w["activity_name"], meta)
    return rows, dropped


def _insert(conn, user_id: int, rows: list[dict], range_id: int | None, batch_id: str) -> tuple[list[dict], int]:
    """Store the rows whose UUID is new (first one wins inside the batch); known ones are skipped (and, in a range,
    marked as seen in it). Returns the new rows and the number skipped."""
    seen: dict[str, dict] = {}
    for r in rows:
        seen.setdefault(r["uuid"], r)
    uuids = list(seen)
    known = {r["u"] for r in conn.execute(
        "SELECT hk_uuid::text AS u FROM hk_samples WHERE user_id = %s AND hk_uuid = ANY(%s::uuid[])",
        (user_id, uuids)).fetchall()} if uuids else set()
    new = [r for u, r in seen.items() if u not in known]
    if new:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO hk_samples (user_id, hk_uuid, type, start_at, end_at, local_date, value, value_text, source,
                                           source_id, tz, meta, origin, range_id, batch_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'app', %s, %s)
                   ON CONFLICT (user_id, hk_uuid) DO NOTHING""",
                [(user_id, r["uuid"], r["type"], r["start"], r["end"], r["day"], r["value"], r["text"], r["source"][:120],
                  r["source_id"], (r["tz"] or None) and r["tz"][:64], Jsonb(r["meta"]) if r["meta"] is not None else None,
                  range_id, batch_id) for r in new])
    if range_id is not None and known:
        conn.execute("UPDATE hk_samples SET range_id = %s WHERE user_id = %s AND hk_uuid = ANY(%s::uuid[])",
                     (range_id, user_id, list(known)))
    return new, len(rows) - len(new)


def _stats(conn, user_id: int, groups: list[dict], tz: ZoneInfo, now: datetime) -> tuple[int, int, set]:
    """Replace each group's span in hk_stats; returns (buckets changed, buckets dropped, changed (type, day) pairs)."""
    changed_n = dropped = 0
    pairs: set[tuple[str, date]] = set()
    late = now + FUTURE_SLACK
    for g in groups:
        t = T.ENGINE_BY_HK[g["type"]]
        lo, hi = _ts(g["start"]), _ts(g["start"] + len(g["values"]) * BUCKET_S)
        new: dict[datetime, float] = {}
        for i, v in enumerate(g["values"]):
            at, v = _ts(g["start"] + i * BUCKET_S), float(v) * t.factor
            if v == 0:
                continue
            if not t.lo <= v <= t.hi or at > late:
                dropped += 1
                continue
            new[at] = v
        old = {r["bucket_start"]: r["value"] for r in conn.execute(
            """SELECT bucket_start, value FROM hk_stats WHERE user_id = %s AND type = %s
               AND bucket_start >= %s AND bucket_start < %s""", (user_id, t.hk, lo, hi))}
        changed = {k for k in old.keys() | new.keys() if abs(old.get(k, 0.0) - new.get(k, 0.0)) > 1e-9}
        if not changed:
            continue
        conn.execute("DELETE FROM hk_stats WHERE user_id = %s AND type = %s AND bucket_start >= %s AND bucket_start < %s",
                     (user_id, t.hk, lo, hi))
        if new:
            with conn.cursor() as cur:
                cur.executemany("INSERT INTO hk_stats (user_id, type, bucket_start, value) VALUES (%s, %s, %s, %s)",
                                [(user_id, t.hk, k, v) for k, v in new.items()])
        changed_n += len(changed)
        pairs |= {(t.hk, k.astimezone(tz).date()) for k in changed}
    return changed_n, dropped, pairs


def _range_done(conn, user_id: int, client_id: str) -> tuple[int, set]:
    r = conn.execute("SELECT * FROM sync_ranges WHERE user_id = %s AND client_id = %s", (user_id, client_id)).fetchone()
    if r is None:
        raise BadBatch("range_done: unknown range (send the range with its pages)")
    if r["done_at"] is not None:
        return 0, set()
    gone = conn.execute(
        """DELETE FROM hk_samples WHERE user_id = %s AND type = ANY(%s) AND start_at >= %s AND start_at < %s
           AND origin = 'app' AND range_id IS DISTINCT FROM %s AND received_at < %s
           RETURNING type, local_date""",
        (user_id, r["types"], r["start_at"], r["end_at"], r["id"], r["created_at"])).fetchall()
    conn.execute("UPDATE sync_ranges SET done_at = now(), removed = %s WHERE id = %s", (len(gone), r["id"]))
    return len(gone), {(g["type"], g["local_date"]) for g in gone}


def mark_dirty(conn, user_id: int, pairs: set[tuple[str, date]]) -> None:
    by_day: dict[date, set[str]] = {}
    for hk, d in pairs:
        by_day.setdefault(d, set()).add(hk)
    if not by_day:
        return
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO hk_dirty_days AS d (user_id, day, types) VALUES (%s, %s, %s)
                           ON CONFLICT (user_id, day) DO UPDATE
                           SET types = ARRAY(SELECT DISTINCT x FROM unnest(d.types || EXCLUDED.types) x ORDER BY 1),
                               queued_at = now()""",
                        [(user_id, d, sorted(ts)) for d, ts in sorted(by_day.items())])


def _runs(conn, user_id: int, runs: list[dict], app_version: str | None) -> int:
    if not runs:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO sync_runs (user_id, run_id, trigger, result, error, started_at, duration_ms, batches, bytes,
                                      samples, app_version)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (user_id, run_id) DO NOTHING""",
            [(user_id, str(r["id"]), r["trigger"], r["result"], r.get("error"), _ts(r["started"]), r["duration_ms"],
              r["batches"], r["bytes"], r["samples"], (app_version or "")[:40] or None) for r in runs])
    return len(runs)


def write(conn: psycopg.Connection, user_id: int, batch: dict, tz: ZoneInfo, deleted: list[str]) -> dict:
    """Store the engine fields of a checked batch (does not commit). Returns the counts, `changed_from`, the earliest
    day of a direct-upload type (HK_TYPES) whose daily value was remade, and `dirty_from`, the earliest day queued for
    derivation (the caller queues the score recompute: from changed_from, in the app mode from dirty_from)."""
    now = datetime.now(timezone.utc)
    sources = batch.get("sources") or []
    src_ids = _sources(conn, user_id, sources)
    rng = _range(conn, user_id, batch["range"]) if batch.get("range") else None
    rows, dropped = _rows(batch, src_ids, [s.get("name") or "" for s in sources], tz, now)
    new, skipped = _insert(conn, user_id, rows, rng["id"] if rng else None, str(batch["batch_id"]))
    pairs = {(r["type"], r["day"]) for r in new}
    buckets, b_dropped, stat_pairs = _stats(conn, user_id, batch.get("stats") or [], tz, now)
    n_del = 0
    if deleted:
        gone = conn.execute("DELETE FROM hk_samples WHERE user_id = %s AND hk_uuid = ANY(%s::uuid[]) RETURNING type, local_date",
                            (user_id, deleted)).fetchall()
        n_del = len(gone)
        pairs |= {(g["type"], g["local_date"]) for g in gone}
    removed = 0
    if batch.get("range_done"):
        removed, gone_pairs = _range_done(conn, user_id, str(batch["range_done"]))
        pairs |= gone_pairs
    dirty = pairs | stat_pairs
    mark_dirty(conn, user_id, dirty)
    direct = {(hk, d) for hk, d in pairs if hk in T.BY_HK}
    Q.rebuild_days(conn, user_id, direct, datetime.now(tz).date())
    return {"samples": sum(r["type"] != WORKOUT for r in new), "workouts": sum(r["type"] == WORKOUT for r in new),
            "buckets": buckets, "removed": removed, "deleted": n_del, "skipped": skipped, "dropped": dropped + b_dropped,
            "runs": _runs(conn, user_id, batch.get("runs") or [], batch.get("app_version")),
            "changed_from": min((d for _, d in direct), default=None),
            "dirty_from": min((d for _, d in dirty), default=None)}

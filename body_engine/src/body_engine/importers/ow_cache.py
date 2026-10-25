"""Local copy of the Open Wearables raw samples that scoring reads (tables ow_sample_cache / ow_sample_cache_range, migration 770).

Why: compute-scores loads every heart-rate, HRV, respiratory-rate, SpO2 and wrist-temperature sample from a year before
the user's scoring start (CTL, the 60-day baselines and the 365-day HRmax window need them), and the stress score 104 days
of heart rate, steps and HRV. Paged over OW's HTTP API (1000 samples per request) that was 356 requests and 80 of 94
seconds for a user with 14 months of watch data, measured on the production server on 2026-10-25 (225 ms per request),
on every recompute. With the copy a recompute reads the old part from Postgres and only the recent days from OW.

Rules (the samples handed out are the ones OW would return, so scores do not change):
- Per user and OW series type the copy holds every non-daily-total sample of one contiguous time range
  (ow_sample_cache_range.covered_from / covered_to), bucketed by UTC day and source.
- mode "use": a read of [lo, hi) takes the part inside the range and older than LIVE_TAIL from the copy (late uploads land
  in the last days, so those are always read again) and fetches the rest from OW, storing it back: the range grows forward
  with every read. No range yet (first read, re-linked OW user, new CACHE_VERSION) = everything is fetched and stored.
- mode "refresh" (full recomputes: the nightly job, `compute-scores` without --start): fetch all of [lo, hi) from OW and
  replace the copy, so changes deep in the history (an export.xml imported into OW later, deletions) arrive within a day.
- mode None (default): no copy at all, straight from OW (tools, tests, anything that did not opt in).
- import-ow stores what it pulled anyway (all types of its window, store()), so late samples a few days old reach the copy.
"""
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import psycopg

from . import ow as ow_imp

log = logging.getLogger(__name__)

CACHE_TYPES = frozenset({"heart_rate", "heart_rate_variability_sdnn", "respiratory_rate", "oxygen_saturation",
                         "body_temperature", "steps"})
CACHE_VERSION = 1
LIVE_TAIL = timedelta(days=2)        # always read again from OW (= importers/ow.LOOKBACK: how late data usually arrives)
FETCH_WINDOW = timedelta(days=60)    # one OW pull covers at most this much time (bounds the memory of one pull)
MODES = (None, "use", "refresh")
UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
US = timedelta(microseconds=1)


def _us(ts: datetime) -> int:
    return (ts - EPOCH) // US


def _ts(us: int) -> datetime:
    return EPOCH + timedelta(microseconds=us)


def _src_flags(src: str) -> tuple[int, bool]:
    """priority and xml of a Sample as importers/ow.parse_sample sets them, from its source key 'device_type:device:source'."""
    xml = src.endswith(":" + ow_imp.XML_SOURCE)
    return ow_imp.DEVICE_PRIORITY.get(src.split(":", 1)[0], 2) * 2 + xml, xml


def fetch(client, ow_user: str, types, lo: datetime, hi: datetime) -> dict[str, list[ow_imp.Sample]]:
    """Straight from OW: the non-daily-total samples of `types` in [lo, hi), by type."""
    types = set(types)
    out: dict[str, list[ow_imp.Sample]] = defaultdict(list)
    cur = lo
    while cur < hi:
        nxt = min(cur + FETCH_WINDOW, hi)
        for raw in client.paginate(f"/users/{ow_user}/timeseries", start_time=cur.isoformat(), end_time=nxt.isoformat(),
                                   types=sorted(types), resolution="raw", limit=1000):
            if raw.get("is_daily_total") or raw["type"] not in types:
                continue
            _, _, s = ow_imp.parse_sample(raw)
            if cur <= s.ts < nxt:
                out[raw["type"]].append(s)
        cur = nxt
    return out


def _ranges(conn: psycopg.Connection, user_id: int, ow_user: str) -> dict[str, tuple[datetime, datetime]]:
    """Valid covered range per type (same OW user and version)."""
    return {r["type"]: (r["covered_from"], r["covered_to"]) for r in conn.execute(
        """SELECT type, covered_from, covered_to FROM ow_sample_cache_range
           WHERE user_id = %s AND ow_user_id = %s AND version = %s""", (user_id, ow_user, CACHE_VERSION))}


def store(conn: psycopg.Connection, user_id: int, ow_user: str, by_type: dict[str, list], lo: datetime, hi: datetime,
          types=None, now: datetime | None = None) -> None:
    """The samples of `types` (default: all CACHE_TYPES) in [lo, hi) were just read from OW (by_type: type -> Samples):
    replace that part of the copy and extend each type's range. A window that does not touch a type's range (a gap) is
    not stored for it (the next read fetches the gap). Call inside the caller's transaction."""
    now = now or datetime.now(UTC)
    types = CACHE_TYPES if types is None else set(types) & CACHE_TYPES
    upto = min(hi, now)   # never claim the future: later samples may still arrive
    if upto <= lo:
        return
    ranges = _ranges(conn, user_id, ow_user)
    for code in sorted(types):
        old = ranges.get(code)
        if old and (lo > old[1] or upto < old[0]):
            continue   # disjoint from the range: keep the range as it is
        if not old:   # no valid copy of this type: start from this window (drop rows of another OW user / version)
            conn.execute("DELETE FROM ow_sample_cache WHERE user_id = %s AND type = %s", (user_id, code))
        d_lo, d_hi = lo.astimezone(UTC).date(), (hi - US).astimezone(UTC).date()
        lo_us, hi_us = _us(lo), _us(hi)
        buckets: dict[tuple[date, str], list[tuple[int, float]]] = defaultdict(list)
        # the edge days: keep their samples outside [lo, hi)
        for r in conn.execute("""SELECT day, src, ts, val FROM ow_sample_cache
                                 WHERE user_id = %s AND type = %s AND day IN (%s, %s)""", (user_id, code, d_lo, d_hi)):
            buckets[(r["day"], r["src"])] += [(t, v) for t, v in zip(r["ts"], r["val"]) if not lo_us <= t < hi_us]
        conn.execute("DELETE FROM ow_sample_cache WHERE user_id = %s AND type = %s AND day BETWEEN %s AND %s",
                     (user_id, code, d_lo, d_hi))
        for s in by_type.get(code, ()):
            t = _us(s.ts)
            if lo_us <= t < hi_us:
                buckets[(s.ts.astimezone(UTC).date(), s.source)].append((t, s.value))
        rows = []
        for (day, src), xs in buckets.items():
            if xs:
                xs.sort(key=lambda x: x[0])   # stable: samples with the same time stamp keep OW's order
                rows.append((user_id, code, day, src, [t for t, _ in xs], [v for _, v in xs]))
        if rows:
            with conn.cursor() as cur:
                # ON CONFLICT: two writers of the same user at once (both just read OW) -> the later one wins
                cur.executemany("""INSERT INTO ow_sample_cache (user_id, type, day, src, ts, val) VALUES (%s, %s, %s, %s, %s, %s)
                                   ON CONFLICT (user_id, type, day, src) DO UPDATE SET ts = EXCLUDED.ts, val = EXCLUDED.val""",
                                rows)
        new_from, new_to = (min(lo, old[0]), max(upto, old[1])) if old else (lo, upto)
        conn.execute("""INSERT INTO ow_sample_cache_range (user_id, type, ow_user_id, version, covered_from, covered_to)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (user_id, type) DO UPDATE SET ow_user_id = EXCLUDED.ow_user_id, version = EXCLUDED.version,
                          covered_from = EXCLUDED.covered_from, covered_to = EXCLUDED.covered_to, updated_at = now()""",
                     (user_id, code, ow_user, CACHE_VERSION, new_from, new_to))


def store_safe(conn: psycopg.Connection, user_id: int, ow_user: str, by_type: dict[str, list], lo: datetime,
               hi: datetime) -> None:
    """store() for import-ow: a problem with the copy (e.g. the migration not applied yet) never stops the import."""
    try:
        with conn.transaction():
            store(conn, user_id, ow_user, by_type, lo, hi)
    except Exception as e:  # noqa: BLE001
        log.warning("ow_cache: not stored for user %s: %s: %s", user_id, type(e).__name__, e)


def _read(conn: psycopg.Connection, user_id: int, types, lo: datetime, hi: datetime) -> dict[str, list[ow_imp.Sample]]:
    out: dict[str, list[ow_imp.Sample]] = defaultdict(list)
    if hi <= lo or not types:
        return out
    lo_us, hi_us = _us(lo), _us(hi)
    with conn.cursor(binary=True) as cur:
        cur.execute("""SELECT type, src, ts, val FROM ow_sample_cache WHERE user_id = %s AND type = ANY(%s)
                       AND day BETWEEN %s AND %s ORDER BY type, day, src""",
                    (user_id, sorted(types), lo.astimezone(UTC).date(), (hi - US).astimezone(UTC).date()))
        Sample = ow_imp.Sample
        for r in cur:
            src = r["src"]
            prio, xml = _src_flags(src)
            out[r["type"]] += [Sample(EPOCH + timedelta(microseconds=t), v, src, prio, xml)
                               for t, v in zip(r["ts"], r["val"]) if lo_us <= t < hi_us]
    for xs in out.values():
        xs.sort(key=lambda s: s.ts)
    return out


def samples(conn: psycopg.Connection | None, client, user_id: int | None, ow_user: str, types, lo: datetime, hi: datetime,
            mode: str | None = None, now: datetime | None = None) -> dict[str, list[ow_imp.Sample]]:
    """The non-daily-total samples of `types` in [lo, hi) by type, each list in time order. mode: see the module doc."""
    if mode not in MODES:
        raise ValueError(f"unknown ow_cache mode {mode!r}")
    types = set(types)
    if mode is None or conn is None or user_id is None:
        return fetch(client, ow_user, types, lo, hi)
    try:
        with conn.transaction():
            return _samples(conn, client, user_id, ow_user, types, lo, hi, mode, now or datetime.now(UTC))
    except psycopg.Error as e:   # a broken copy must not stop the scores: read everything from OW, as without it
        log.warning("ow_cache: read failed for user %s, reading from OW: %s", user_id, e)
        return fetch(client, ow_user, types, lo, hi)


def _samples(conn, client, user_id, ow_user, types, lo, hi, mode, now):
    cached = types & CACHE_TYPES
    ranges = {} if mode == "refresh" else _ranges(conn, user_id, ow_user)
    # per type: the copy serves [lo, split), OW the rest
    split: dict[str, datetime] = {}
    for code in cached:
        r = ranges.get(code)
        split[code] = max(lo, min(hi, r[1], now - LIVE_TAIL)) if r and r[0] <= lo else lo
    out = _read(conn, user_id, {c for c in cached if split[c] > lo}, lo, max((split[c] for c in cached), default=lo))
    # the copy was read over the widest split: cut each type back to its own split
    out = defaultdict(list, {c: [s for s in xs if s.ts < split[c]] for c, xs in out.items()})
    groups: dict[datetime, set[str]] = defaultdict(set)
    for code in cached:
        if split[code] < hi:
            groups[split[code]].add(code)
    if types - cached:
        groups[lo] |= types - cached
    for start, codes in sorted(groups.items()):
        got = fetch(client, ow_user, codes, start, hi)
        store(conn, user_id, ow_user, got, start, hi, codes & CACHE_TYPES, now)
        for code in codes:
            out[code] += got.get(code, [])
    return dict(out)   # the caller commits (the copy is written in its transaction)

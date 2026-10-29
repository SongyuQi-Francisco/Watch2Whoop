"""POST /v1/sync/pull: what changed since the app's cursors, or everything (full restore) when a cursor is empty.

Cursors are opaque to the app:
  ""                 start a full restore of the stream
  "r.<xmin>.<day>"   restore in progress: days older than <day> are still to come (newest first)
  "r.<xmin>."        restore, nothing sent yet (undated record kinds go out on its first page)
  "c.<xid>.<seq>"    incremental: changes after (xid, seq)
A restore remembers the snapshot xmin it started at; when it is done the stream continues incrementally from there, so
whatever changed during the restore is sent again (harmless) and nothing is lost. Incremental pages only contain
transactions older than the oldest one still running (xid < xmin): a late commit can never slip behind a cursor.

Streams: scores, daily, sleep, records (kind = water / journal / ...), docs (op "stale": the server view of that day
changed, fetch it again if it is shown), raw (HealthKit samples, statistics buckets and beat-to-beat RMSSD per day;
pulled on a full restore, after a cursor, or for explicit raw_days; `raw_since` bounds a raw restore, since the phone
keeps a few weeks of raw samples only, #340 N2b), raw_history (the merged scoring inputs of the days before
`history_before`, newest first: the phone's background download of its full history, history.py; asked for alone and
throttled, api.py). Each change: {stream, op, kind, key, date, version, body};
op "delete" = that day / kind has no data any more (body null).
"""
from __future__ import annotations

import re
from datetime import date
from zoneinfo import ZoneInfo

import psycopg

from . import bodies as B, history as HI, params as P, store

CURSOR_RE = re.compile(r"^(?:r\.(\d+)\.(\d{4}-\d{2}-\d{2})?|c\.(\d+)\.(\d+))$")
DATED_STREAMS = ("scores", "daily", "sleep")


class BadCursor(ValueError):
    pass


def _xmin(conn) -> int:
    return int(conn.execute("SELECT pg_snapshot_xmin(pg_current_snapshot())::text::bigint AS x").fetchone()["x"])


def parse(cursor: str | None) -> tuple:
    """("restore", xmin|None, before|None) or ("changes", xid, seq)."""
    if not cursor:
        return ("restore", None, None)
    m = CURSOR_RE.match(cursor)
    if not m:
        raise BadCursor(cursor)
    if m.group(1) is not None:
        return ("restore", int(m.group(1)), date.fromisoformat(m.group(2)) if m.group(2) else None)
    return ("changes", int(m.group(3)), int(m.group(4)))


def _change(stream: str, kind: str, day: date | None, body, version: int, op: str | None = None) -> dict:
    return {"stream": stream, "op": op or ("upsert" if body is not None else "delete"), "kind": kind,
            "key": day.isoformat() if day else "all", "date": day.isoformat() if day else None,
            "version": version, "body": body}


class Puller:
    def __init__(self, conn: psycopg.Connection, uid: int, raw_since: date | None = None,
                 history_before: date | None = None):
        self.conn, self.uid, self.raw_since, self.history_before = conn, uid, raw_since, history_before
        self.tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (uid,)).fetchone()["timezone"])
        self.xmin = _xmin(conn)

    # ------------------------------------------------------------ bodies of (stream, kind, days)
    def bodies(self, stream: str, kind: str, days: list) -> dict:
        if stream == "records":
            return B.record(self.conn, self.uid, kind, days)
        if stream == "raw":
            return B.raw(self.conn, self.uid, days, self.tz)
        if stream == P.HISTORY_STREAM:
            return HI.bodies(self.conn, self.uid, days, self.tz)
        if stream == "sleep":
            return B.sleep(self.conn, self.uid, days, self.tz)
        return getattr(B, stream)(self.conn, self.uid, days)

    def emit(self, stream: str, pairs: list[tuple[str, date | None]]) -> list[dict]:
        """Changes for (kind, day) pairs of one stream, with their bodies and current versions."""
        by_kind: dict[str, list] = {}
        for k, d in pairs:
            by_kind.setdefault(k, []).append(d)
        found = {k: self.bodies(stream, k, [d for d in ds if d is not None] if any(d is not None for d in ds) else ds)
                 for k, ds in by_kind.items()}
        vers = store.versions(self.conn, self.uid, stream, [(k, d.isoformat() if d else "all") for k, d in pairs])
        return [_change(stream, k, d, found[k].get(d), vers.get((k, d.isoformat() if d else "all"), 0)) for k, d in pairs]

    # ------------------------------------------------------------ incremental
    def changes(self, stream: str, xid: int, seq: int, budget: int) -> tuple[list[dict], str, bool]:
        if stream == P.HISTORY_STREAM:
            return self.history_changes(xid, seq, budget)
        if stream == "docs":
            rows = self.conn.execute(
                """SELECT DISTINCT ON (day) '' AS kind, day::text AS key, day, xid::text::bigint AS xid, seq FROM sync_changes
                   WHERE user_id = %(u)s AND stream <> 'raw' AND day IS NOT NULL AND (xid, seq) > (%(x)s::text::xid8, %(s)s)
                     AND xid < pg_snapshot_xmin(pg_current_snapshot()) ORDER BY day, xid DESC, seq DESC""",
                {"u": self.uid, "x": xid, "s": seq}).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT DISTINCT ON (kind, key) kind, key, day, xid::text::bigint AS xid, seq FROM sync_changes
                   WHERE user_id = %(u)s AND stream = %(st)s AND (xid, seq) > (%(x)s::text::xid8, %(s)s)
                     AND xid < pg_snapshot_xmin(pg_current_snapshot()) ORDER BY kind, key, xid DESC, seq DESC""",
                {"u": self.uid, "st": stream, "x": xid, "s": seq}).fetchall()
        rows.sort(key=lambda r: (r["xid"], r["seq"]))
        if stream == "raw":   # bounded by rows of data, at least one day
            out, size, taken = [], 0, []
            for r in rows:
                if taken and (len(taken) >= budget or size >= P.RAW_PAGE_ROWS):
                    break
                ch = self.emit("raw", [("", r["day"])])[0]
                size += B.raw_size(ch["body"])
                out.append(ch)
                taken.append(r)
        else:
            taken = rows[:budget]
            if stream == "docs":
                out = [_change("docs", "", r["day"], None, int(r["seq"]), op="stale") | {"key": f"today:{r['day'].isoformat()}"}
                       for r in taken]
            else:
                out = self.emit(stream, [(r["kind"], r["day"]) for r in taken])
        if not taken:
            return [], f"c.{xid}.{seq}", False
        last = taken[-1]
        return out, f"c.{last['xid']}.{last['seq']}", len(taken) < len(rows)

    # ------------------------------------------------------------ full restore
    def restore(self, stream: str, x0: int | None, before: date | None, budget: int) -> tuple[list[dict], str, bool]:
        x0 = self.xmin if x0 is None else x0
        done = f"c.{x0}.0"
        if stream == P.HISTORY_STREAM:
            return self.history_restore(x0, before, done)
        if stream == "docs":    # server views are fetched on demand; the stream only invalidates from here on
            return [], done, False
        out: list[dict] = []
        if stream == "records" and before is None:   # undated kinds once, on the first page
            undated = [k for k, (_, _, col) in B.RECORD_KINDS.items() if col is None]
            out += [c for c in self.emit("records", [(k, None) for k in undated]) if c["body"] is not None]
        kinds = [k for k, (_, _, col) in B.RECORD_KINDS.items() if col is not None] if stream == "records" else [""]
        limit = max(budget, 1)
        since = self.raw_since if stream == "raw" else None
        lists = {k: B.restore_days(self.conn, self.uid, stream, k, before, limit, self.tz, since) for k in kinds}
        # a day can only be sent once every kind that might still have it was read that far back
        cutoff = max((ds[-1] for ds in lists.values() if len(ds) >= limit), default=None)
        days = sorted({d for ds in lists.values() for d in ds if cutoff is None or d >= cutoff}, reverse=True)
        sent: list[date] = []
        size = 0
        for d in days:
            pairs = [(k, d) for k in kinds if d in lists[k]]
            if sent and (len(out) + len(pairs) > budget or (stream == "raw" and size >= P.RAW_PAGE_ROWS)):
                break
            chunk = self.emit(stream, pairs)
            size += sum(B.raw_size(c["body"]) for c in chunk) if stream == "raw" else 0
            out += [c for c in chunk if c["body"] is not None]
            sent.append(d)
        more = len(sent) < len(days) or cutoff is not None
        if not more:
            return out, done, False
        return out, f"r.{x0}.{sent[-1].isoformat()}" if sent else f"r.{x0}.", True


    # ------------------------------------------------------------ raw_history (history.py)
    def history_restore(self, x0: int, before: date | None, done: str) -> tuple[list[dict], str, bool]:
        """One page of the download: up to HISTORY_PAGE_DAYS days older than the cursor's day and history_before."""
        days = HI.restore_days(self.conn, self.uid, before, self.history_before, P.HISTORY_PAGE_DAYS + 1)
        more = len(days) > P.HISTORY_PAGE_DAYS
        days = days[:P.HISTORY_PAGE_DAYS]
        if not days:
            return [], done, False
        sent, got = HI.page(self.conn, self.uid, self.tz, days)
        more = more or len(sent) < len(days)
        vers = store.versions(self.conn, self.uid, "raw", [("", d.isoformat()) for d in sent])
        out = [_change(P.HISTORY_STREAM, "", d, got[d], vers.get(("", d.isoformat()), 0)) for d in sent if d in got]
        return out, (f"r.{x0}.{sent[-1].isoformat()}" if more else done), more

    def history_changes(self, xid: int, seq: int, budget: int) -> tuple[list[dict], str, bool]:
        """After the download: days before history_before whose inputs changed, at most a page of them."""
        rows = HI.changed_days(self.conn, self.uid, xid, seq, self.history_before)
        taken = rows[:min(budget, P.HISTORY_PAGE_DAYS)]
        if not taken:
            return [], f"c.{xid}.{seq}", False
        got = HI.bodies(self.conn, self.uid, [r["day"] for r in taken], self.tz)
        out = [_change(P.HISTORY_STREAM, "", r["day"], got.get(r["day"]), int(r["seq"])) for r in taken]
        last = taken[-1]
        return out, f"c.{last['xid']}.{last['seq']}", len(taken) < len(rows)

    def history_info(self) -> dict:
        return HI.total_days(self.conn, self.uid, self.history_before)


def pull(conn: psycopg.Connection, uid: int, cursors: dict[str, str | None], limit: int = P.PULL_LIMIT_DEFAULT,
         raw_days: list[date] | None = None, raw_since: date | None = None, history_before: date | None = None) -> dict:
    """The answer of POST /v1/sync/pull for the streams named in `cursors` (raises BadCursor / KeyError)."""
    for s in cursors:
        if s not in P.STREAMS:
            raise KeyError(s)
    parsed = {s: parse(c) for s, c in cursors.items()}
    p = Puller(conn, uid, raw_since, history_before)
    changes: list[dict] = []
    out_cursors: dict[str, str] = {}
    more = False
    for s in P.STREAMS:
        if s not in parsed:
            continue
        budget = limit - len(changes)
        mode, a, b = parsed[s]
        if budget <= 0:
            out_cursors[s] = cursors[s] or ""
            more = True
            continue
        got, cur, again = (p.restore(s, a, b, budget) if mode == "restore" else p.changes(s, a, b, budget))
        changes += got
        out_cursors[s] = cur
        more = more or again
    if raw_days:
        changes += p.emit("raw", [("", d) for d in sorted(set(raw_days))[:P.RAW_DAYS_MAX]])
    out = {"changes": changes, "cursors": out_cursors, "more": more,
           "data_version": store.data_version(conn, uid), "algo": _algo()}
    if P.HISTORY_STREAM in parsed and parsed[P.HISTORY_STREAM][0] == "restore" and parsed[P.HISTORY_STREAM][2] is None:
        out["history"] = p.history_info()   # the first page of a download: how much there is (progress)
    return out


def _algo() -> dict | None:
    from .. import algo
    try:
        m, _ = algo.bundle()
    except algo.Unavailable:
        return None
    return {"version": m["version"], "sha256": m["sha256"], "algo_versions": m["algo_versions"]}

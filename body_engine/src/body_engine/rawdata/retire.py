"""2.0.0 R1 (docs/ios-native-2.0.md §0 D2 / D4, §4 R1; docs/apple-sync-engine.md §5): retiring Open Wearables (OW)
and the iOS Shortcut upload. After 2.0.0 the app's own HealthKit sync engine is the only live source; everything that
came from OW, the Shortcut or an Apple Health export (apple_export) stays in Body Engine's own tables forever.

Three parts, nothing here turns anything off by itself:

1. verify(conn, user, client) (`body-engine retire-legacy verify --user-id N | --all`, read-only): per user, what the
   server keeps of each legacy source (days / nights / workouts / raw samples with date ranges, counts only, never a
   health value), and what still lives only in OW:
   - OW's raw samples must be in the local archive (rawdata/ow_archive: `ow_sample_cache` + `ow_archive_segments`),
     complete (`ow_archive.check`) and fresh (covering OW's newest sample, `sync_state` cursor);
   - with an OW client: OW's nights whose wake date has no night at all in `sleep_sessions` (any source: the app mode
     replaces OW's nights by its own, that is not a loss) and OW's workouts that are not in `workouts` (source ow);
   - local days of the archived OW samples that have no OW daily value (`daily_metric_sources`): kept as raw samples,
     a warning only;
   - Shortcut upload files on disk that were never imported (`shortcut_uploads`), Apple Health history imports still
     queued;
   - optional (`--scores`): a full score recompute without OW (client None, the app mode reads the archive) equals the
     stored `daily_scores` within the cut-over tolerance (rawdata/cutover.score_check).
2. archive(conn, user, client) (`--archive`): copies what verify found missing, idempotently and with source kept:
   `ow_archive.archive` (raw samples + stages, refreshed up to now), the missing OW workouts (insert only, nothing
   deleted), the missing OW nights (only on wake dates without any night), the missing OW daily values (only for days
   without any OW value), the pending Shortcut files (`importers/shortcut.run`). Never deletes anything.
3. The cutover switch: the environment variable BODY_LEGACY_SOURCES_CUTOFF (ISO date = 00:00 UTC of that day, or an ISO
   date-time). Unset (the default) = nothing changes. From that moment the Shortcut receiver (health_sync/
   health_server.py, same variable, stdlib copy of parse_cutoff) answers 410 Gone, and every OW import skips: the
   hourly-batch step import-ow, `body-engine import-ow` / `import-workouts` (unless --force), the on-demand refresh
   (sync/refresh.py) and the OW upload poller (sync/ow_watch.py). Already imported data is never touched.
   status() (`retire-legacy status --json`, the doctor) reports the switch and how many users still sent OW / Shortcut
   data in the last RECENT_DAYS days.
"""
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg

UTC = timezone.utc
CUTOFF_ENV = "BODY_LEGACY_SOURCES_CUTOFF"
LEGACY_SOURCES = ("ow", "shortcut", "apple_export")
RECENT_DAYS = 7                    # status(): "still sending" = data received within this many days
DAILY_FETCH_DAYS = 30              # archive(): OW timeseries pulled at most this many days at a time (= importers/ow.WINDOW)
OW_HISTORY_START = date(2000, 1, 1)


# ---------------------------------------------------------------- the cutover switch
def parse_cutoff(raw: str | None) -> datetime | None:
    """'' / None = not set. 'YYYY-MM-DD' = 00:00 UTC of that day; an ISO date-time without offset counts as UTC.
    Anything else raises ValueError. (health_sync/health_server.py has a stdlib copy: keep both the same.)"""
    raw = (raw or "").strip()
    if not raw:
        return None
    if len(raw) == 10:
        return datetime.combine(date.fromisoformat(raw), time.min, UTC)
    t = datetime.fromisoformat(raw)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def cutoff_state(now: datetime | None = None, raw: str | None = None) -> dict:
    """{"set", "cutoff", "active", "error"}: active = set, valid and reached. An invalid value is never active (the
    receivers keep accepting; nothing is lost) and the doctor shows it."""
    raw = os.environ.get(CUTOFF_ENV, "") if raw is None else raw
    try:
        at = parse_cutoff(raw)
    except ValueError:
        return {"set": True, "cutoff": None, "active": False, "error": f"{CUTOFF_ENV} 不是 ISO 日期：{raw[:40]!r}"}
    now = now or datetime.now(UTC)
    return {"set": at is not None, "cutoff": at.isoformat() if at else None, "active": at is not None and now >= at,
            "error": None}


def ow_import_stopped(now: datetime | None = None) -> bool:
    """True once the cutoff is reached: no OW import runs any more (OW can be stopped)."""
    return cutoff_state(now)["active"]


SKIP_NOTE = "已过旧数据源停用时间（BODY_LEGACY_SOURCES_CUTOFF），不再从 Open Wearables 导入"


# ---------------------------------------------------------------- what the server keeps (local, read-only)
def _tz(conn, user_id: int) -> ZoneInfo:
    return ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])


def _span(r: dict, n: str = "n") -> dict:
    return {"n": int(r[n] or 0), "first": r["first"], "last": r["last"]}


def inventory(conn: psycopg.Connection, user_id: int) -> dict:
    """{source: {"daily", "sleep", "workouts", "raw"}} for ow / shortcut / apple_export: counts and local date ranges."""
    tz = str(_tz(conn, user_id))
    out = {s: {} for s in LEGACY_SOURCES}
    for r in conn.execute("""SELECT source, count(DISTINCT date) AS n, min(date) AS first, max(date) AS last
                             FROM daily_metric_sources WHERE user_id = %s AND source = ANY(%s) GROUP BY source""",
                          (user_id, list(LEGACY_SOURCES))):
        out[r["source"]]["daily"] = _span(r)
    for r in conn.execute("""SELECT source, count(*) AS n, min(wake_date) AS first, max(wake_date) AS last
                             FROM sleep_sessions WHERE user_id = %s AND source = ANY(%s) GROUP BY source""",
                          (user_id, list(LEGACY_SOURCES))):
        out[r["source"]]["sleep"] = _span(r)
    for r in conn.execute("""SELECT source, count(*) AS n, min(local_date) AS first, max(local_date) AS last
                             FROM workouts WHERE user_id = %s AND source = ANY(%s) GROUP BY source""",
                          (user_id, list(LEGACY_SOURCES))):
        out[r["source"]]["workouts"] = _span(r)
    r = conn.execute("""SELECT sum(cardinality(ts)) AS n,
                               min((to_timestamp(ts[1] / 1e6) AT TIME ZONE %s)::date) AS first,
                               max((to_timestamp(ts[cardinality(ts)] / 1e6) AT TIME ZONE %s)::date) AS last
                        FROM ow_sample_cache WHERE user_id = %s AND cardinality(ts) > 0""", (tz, tz, user_id)).fetchone()
    if r["n"]:
        out["ow"]["raw"] = _span(r)
    r = conn.execute("""SELECT count(*) AS n, min((start_at AT TIME ZONE %s)::date) AS first,
                               max((start_at AT TIME ZONE %s)::date) AS last
                        FROM shortcut_samples WHERE user_id = %s""", (tz, tz, user_id)).fetchone()
    if r["n"]:
        out["shortcut"]["raw"] = _span(r)
    r = conn.execute("""SELECT count(*) AS n, min((start_at AT TIME ZONE %s)::date) AS first,
                               max((start_at AT TIME ZONE %s)::date) AS last
                        FROM export_samples WHERE user_id = %s""", (tz, tz, user_id)).fetchone()
    if r["n"]:
        out["apple_export"]["raw"] = _span(r)
    return out


def _ow_cache_days(conn, user_id: int, tz: ZoneInfo) -> set[date]:
    """Local dates that have archived OW samples. A UTC day of samples spans at most two local dates, and the first /
    last sample of each bucket fall on them, so the two ends give every date without unnesting the arrays."""
    return {r["d"] for r in conn.execute(
        """SELECT DISTINCT (to_timestamp(t / 1e6) AT TIME ZONE %s)::date AS d
           FROM ow_sample_cache, LATERAL (VALUES (ts[1]), (ts[cardinality(ts)])) AS e(t)
           WHERE user_id = %s AND cardinality(ts) > 0""", (str(tz), user_id))}


def _ow_daily_days(conn, user_id: int) -> set[date]:
    return {r["date"] for r in conn.execute(
        "SELECT DISTINCT date FROM daily_metric_sources WHERE user_id = %s AND source = 'ow'", (user_id,))}


def _last_ow_sample(conn, user_id: int) -> datetime | None:
    r = conn.execute("SELECT cursor FROM sync_state WHERE user_id = %s AND source = 'ow'", (user_id,)).fetchone()
    try:
        return datetime.fromisoformat(r["cursor"]) if r and r["cursor"] else None
    except ValueError:
        return None


def pending_shortcut_files(conn: psycopg.Connection, user_id: int, archive_dir=None) -> int:
    from ..importers import shortcut
    folder = archive_dir if archive_dir is not None else shortcut.user_archive_dir(user_id)
    if not folder.is_dir():
        return 0
    done = {r["file"] for r in conn.execute("SELECT file FROM shortcut_uploads WHERE user_id = %s", (user_id,))}
    return sum(1 for f in shortcut.archive_files(folder) if shortcut.upload_name(f) not in done)


def _pending_history_imports(conn, user_id: int) -> int:
    return conn.execute("""SELECT count(*) AS n FROM history_imports WHERE user_id = %s
                           AND status IN ('uploading', 'queued', 'processing')""", (user_id,)).fetchone()["n"]


# ---------------------------------------------------------------- what still lives only in OW (reads OW)
def _ow_nights(client, ow_user: str, tz: ZoneInfo, today: date) -> list:
    from ..importers import common, ow as ow_imp
    sessions = list(client.paginate(f"/users/{ow_user}/events/sleep", include="stages", limit=100,
                                    start_date=OW_HISTORY_START.isoformat(),
                                    end_date=(today + timedelta(days=1)).isoformat()))
    return common.sessionize(ow_imp.sleep_segments(sessions, tz))


def _ow_workouts(client, ow_user: str, tz: ZoneInfo, today: date) -> list[dict]:
    from ..training import importer as TI
    raws = client.paginate(f"/users/{ow_user}/events/workouts",
                           start_date=datetime(OW_HISTORY_START.year, 1, 1, tzinfo=UTC).isoformat(),
                           end_date=datetime.combine(today + timedelta(days=2), time.min, tz).isoformat(), limit=100)
    return [TI.parse_ow(r, tz) for r in raws]


def only_in_ow(conn: psycopg.Connection, client, user_id: int, ow_user: str, tz: ZoneInfo) -> dict:
    """{"nights": [SleepSession], "workouts": [row]} that OW has and Body Engine does not."""
    today = datetime.now(tz).date()
    have_nights = {r["wake_date"] for r in conn.execute(
        "SELECT DISTINCT wake_date FROM sleep_sessions WHERE user_id = %s", (user_id,))}
    nights = [s for s in _ow_nights(client, ow_user, tz, today) if s.wake_date not in have_nights]
    have_workouts = {r["external_id"] for r in conn.execute(
        "SELECT external_id FROM workouts WHERE user_id = %s AND source = 'ow'", (user_id,))}
    workouts = [w for w in _ow_workouts(client, ow_user, tz, today) if w["external_id"] not in have_workouts]
    return {"nights": nights, "workouts": workouts}


# ---------------------------------------------------------------- verify
def _date_span(days) -> dict:
    days = sorted(days)
    return {"n": len(days), "first": days[0] if days else None, "last": days[-1] if days else None}


def verify(conn: psycopg.Connection, user_id: int, client=None, scores: bool = False, archive_dir=None) -> dict:
    """Read-only. Counts and dates only. status: ✓ everything kept / ⚠ kept, with something to look at / ✗ something
    would be lost when OW stops / — no legacy data."""
    from . import engine as EN, ow_archive as OA
    user = conn.execute("SELECT ow_user_id FROM users WHERE id = %s", (user_id,)).fetchone()
    if user is None:
        raise ValueError(f"没有用户 {user_id}")
    tz = _tz(conn, user_id)
    inv = inventory(conn, user_id)
    has_ow = bool(inv["ow"])
    res: dict = {"user_id": user_id, "mode": EN.mode(conn, user_id), "ow_linked": bool(user["ow_user_id"]),
                 "sources": inv, "fails": [], "warns": []}
    fails, warns = res["fails"], res["warns"]

    # OW: the local archive of the raw samples
    if has_ow or user["ow_user_id"]:
        chk = OA.check(conn, user_id)
        last = _last_ow_sample(conn, user_id)
        arch = {"archived": chk["archived"], "complete": chk.get("complete"), "gaps": chk.get("gaps", []),
                "covered_to": chk.get("covered_to"), "last_ow_sample": last}
        arch["stale"] = bool(chk["archived"] and last and chk["covered_to"] < last)
        res["ow_archive"] = arch
        if has_ow and not chk["archived"]:
            fails.append("ow_not_archived")
        elif has_ow and not chk["complete"]:
            fails.append("ow_archive_gaps")
        elif arch["stale"]:
            fails.append("ow_archive_stale")
        missing_days = _ow_cache_days(conn, user_id, tz) - _ow_daily_days(conn, user_id)
        res["ow_days_raw_only"] = _date_span(missing_days)
        if missing_days:
            warns.append("ow_days_raw_only")
        if has_ow and res["mode"] != "app":
            warns.append("not_app_mode")   # the scores read the archive only in the app mode (ow_archive.reads_archive)

    # OW: rows that still live only in OW's database
    if user["ow_user_id"]:
        if client is None:
            res["only_in_ow"] = None
            warns.append("ow_not_checked")
        else:
            try:
                got = only_in_ow(conn, client, user_id, user["ow_user_id"], tz)
                res["only_in_ow"] = {"nights": _date_span(s.wake_date for s in got["nights"]),
                                     "workouts": _date_span(w["local_date"] for w in got["workouts"])}
                if got["nights"]:
                    fails.append("nights_only_in_ow")
                if got["workouts"]:
                    fails.append("workouts_only_in_ow")
            except Exception as e:  # noqa: BLE001  OW down: the local checks still count
                res["only_in_ow"] = {"error": type(e).__name__}
                warns.append("ow_unreachable")

    # Shortcut files never imported; Apple Health history imports still running
    res["shortcut_pending_files"] = pending_shortcut_files(conn, user_id, archive_dir)
    if res["shortcut_pending_files"]:
        warns.append("shortcut_files_pending")
    res["history_imports_pending"] = _pending_history_imports(conn, user_id)
    if res["history_imports_pending"]:
        warns.append("history_import_pending")

    if scores:
        res["scores"] = score_check(conn, user_id, tz, res["mode"], res.get("ow_archive"))
        if res["scores"].get("ok") is False:
            fails.append("scores_differ")

    legacy = any(inv[s] for s in LEGACY_SOURCES) or user["ow_user_id"] or res["shortcut_pending_files"]
    res["status"] = "✗" if fails else "⚠" if warns else "✓" if legacy else "—"
    return res


def score_check(conn, user_id: int, tz: ZoneInfo, mode: str, arch: dict | None) -> dict:
    """Full recompute without OW (no client: the app mode reads the archive) vs the stored scores, whole history up to
    the newest closed day. Pass / fail and day counts only."""
    from ..scores import engine as SE
    from . import cutover as CO, shadow as SH
    if arch is not None and mode != "app":
        return {"ok": None, "skipped": "not_app_mode"}
    origin = SE.scoring_origin(conn, user_id)
    if origin is None:
        return {"ok": None, "skipped": "no_scores"}
    end = SH.last_closed_day(tz)
    sc = CO.score_check(conn, None, user_id, origin, end)
    return {"ok": sc["ok"], "days": sc["days"], "lost": sum((sc.get(k) or {}).get("lost", 0) for k in SH.SCORE_KEYS),
            "out_of_tolerance": [k for k in SH.SCORE_KEYS if (sc.get(k) or {}).get("ok") is False], "start": origin,
            "end": end}


# ---------------------------------------------------------------- archive (copy what is missing)
def _contiguous(days: list[date], max_len: int) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    for d in sorted(days):
        if out and d == out[-1][1] + timedelta(days=1) and (d - out[-1][0]).days < max_len:
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return out


def _ow_daily(conn, client, user_id: int, ow_user: str, tz: ZoneInfo, days: set[date]) -> int:
    """OW's daily values of exactly these days (which have no OW value yet): the same computation as importers/ow.run,
    stored with source ow; other days, the sleep and the raw-sample copy are not touched."""
    from ..importers import common, ow as ow_imp
    n = 0
    for a, b in _contiguous(list(days), DAILY_FETCH_DAYS):
        lo, hi = datetime.combine(a, time.min, tz), datetime.combine(b + timedelta(days=1), time.min, tz)
        by_type: dict = {}
        for raw in client.paginate(f"/users/{ow_user}/timeseries", start_time=lo.isoformat(), end_time=hi.isoformat(),
                                   resolution="raw", limit=1000):
            if raw.get("is_daily_total"):
                continue
            code, unit, s = ow_imp.parse_sample(raw)
            by_type.setdefault(code, (unit, []))[1].append(s)
        by_type = {code: (unit, ow_imp.drop_xml_duplicates(xs)) for code, (unit, xs) in by_type.items()}
        rows = [r for r in ow_imp.daily_rows(by_type, tz) if r["date"] in days]
        with conn.transaction():
            n += common.upsert_daily(conn, user_id, ow_imp.SOURCE, rows)
    return n


def _ow_nights_insert(conn, user_id: int, nights: list) -> int:
    """Only wake dates without any night (checked again here): one date at a time, so replace_sleep's delete of the
    source's own rows covers exactly that date."""
    from ..importers import common
    n = 0
    by_day: dict[date, list] = {}
    for s in nights:
        by_day.setdefault(s.wake_date, []).append(s)
    for d, group in sorted(by_day.items()):
        with conn.transaction():
            if conn.execute("SELECT 1 FROM sleep_sessions WHERE user_id = %s AND wake_date = %s LIMIT 1",
                            (user_id, d)).fetchone():
                continue
            n += common.replace_sleep(conn, user_id, "ow", group)["inserted"]
    return n


def _ow_workouts_insert(conn, client, user_id: int, rows: list[dict]) -> int:
    """Insert only (training/importer.upsert by (source, external_id)); unlike import_ow nothing is deleted."""
    from ..training import importer as TI
    if not rows:
        return 0
    with conn.transaction():
        res = TI.upsert(conn, user_id, rows)
        TI.refresh_duplicates(conn, user_id)
    with conn.transaction():
        TI.enrich_pending(conn, client, user_id, set(res["ids"]))
    return res["inserted"]


def archive(conn: psycopg.Connection, user_id: int, client=None, archive_dir=None) -> dict:
    """Copy into Body Engine's tables what still lives only in OW, and import the pending Shortcut files. Idempotent,
    resumable (commits per step), never deletes. Returns counts of what was added."""
    from ..importers import shortcut
    from ..profile import consent
    from . import ow_archive as OA
    if msg := consent.gate(conn, user_id):
        return {"user_id": user_id, "skipped": msg}
    out: dict = {"user_id": user_id}
    if pending_shortcut_files(conn, user_id, archive_dir):
        r = shortcut.run(conn, user_id, archive_dir if archive_dir is not None else shortcut.user_archive_dir(user_id))
        conn.commit()
        out["shortcut_files"] = r["files"]
    user = conn.execute("SELECT ow_user_id FROM users WHERE id = %s", (user_id,)).fetchone()
    if not user["ow_user_id"]:
        return out
    if client is None:
        out["ow"] = "no_client"
        return out
    tz = _tz(conn, user_id)
    try:
        a = OA.archive(conn, client, user_id)
        out["ow_archive"] = {"covered_from": a["covered_from"], "covered_to": a["covered_to"], "windows": a["windows"],
                             "segments": a["segments"]}
    except ValueError as e:   # no watch data yet: nothing to archive
        out["ow_archive"] = {"skipped": str(e)}
    got = only_in_ow(conn, client, user_id, user["ow_user_id"], tz)
    out["nights_added"] = _ow_nights_insert(conn, user_id, got["nights"])
    out["workouts_added"] = _ow_workouts_insert(conn, client, user_id, got["workouts"])
    missing = _ow_cache_days(conn, user_id, tz) - _ow_daily_days(conn, user_id)
    out["daily_rows_added"] = _ow_daily(conn, client, user_id, user["ow_user_id"], tz, missing) if missing else 0
    conn.commit()
    return out


# ---------------------------------------------------------------- everyone / report / status
def users(conn: psycopg.Connection) -> list[int]:
    """Users with any legacy data or an OW link."""
    return [r["id"] for r in conn.execute(
        """SELECT u.id FROM users u WHERE u.ow_user_id IS NOT NULL
             OR EXISTS (SELECT 1 FROM daily_metric_sources m WHERE m.user_id = u.id AND m.source = ANY(%(s)s))
             OR EXISTS (SELECT 1 FROM sleep_sessions s WHERE s.user_id = u.id AND s.source = ANY(%(s)s))
             OR EXISTS (SELECT 1 FROM workouts w WHERE w.user_id = u.id AND w.source = ANY(%(s)s))
             OR EXISTS (SELECT 1 FROM shortcut_uploads x WHERE x.user_id = u.id)
             OR EXISTS (SELECT 1 FROM export_samples e WHERE e.user_id = u.id)
           ORDER BY u.id""", {"s": list(LEGACY_SOURCES)})]


def status(conn: psycopg.Connection, now: datetime | None = None) -> dict:
    """The switch and who still sends legacy data (counts only): Shortcut = an upload imported within RECENT_DAYS;
    OW = OW's newest sample (sync cursor) or OW's own last sync (ow_watch) within RECENT_DAYS."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=RECENT_DAYS)
    shortcut = {r["user_id"] for r in conn.execute(
        "SELECT DISTINCT user_id FROM shortcut_uploads WHERE imported_at >= %s", (since,))}
    ow: set[int] = set()
    for r in conn.execute("""SELECT u.id, s.cursor, w.ow_synced_at FROM users u
                             LEFT JOIN sync_state s ON s.user_id = u.id AND s.source = 'ow'
                             LEFT JOIN ow_watch w ON w.user_id = u.id WHERE u.ow_user_id IS NOT NULL"""):
        try:
            cur = datetime.fromisoformat(r["cursor"]) if r["cursor"] else None
        except ValueError:
            cur = None
        if (cur and cur >= since) or (r["ow_synced_at"] and r["ow_synced_at"] >= since):
            ow.add(r["id"])
    return {"cutoff": cutoff_state(now), "recent_days": RECENT_DAYS, "ow_users_recent": len(ow),
            "shortcut_users_recent": len(shortcut), "ow_linked": conn.execute(
                "SELECT count(*) AS n FROM users WHERE ow_user_id IS NOT NULL").fetchone()["n"]}


_REASONS = {
    "ow_not_archived": "OW 原始样本没存档", "ow_archive_gaps": "OW 存档有缺口", "ow_archive_stale": "OW 存档没覆盖最新样本",
    "nights_only_in_ow": "有睡眠只在 OW", "workouts_only_in_ow": "有训练只在 OW", "scores_differ": "不读 OW 重算分数对不上",
    "ow_days_raw_only": "部分天只有原始样本、没有 OW 日值", "not_app_mode": "不是 app 模式（算分不读存档）",
    "ow_not_checked": "没连 OW，只查了本地", "ow_unreachable": "OW 读不到，只查了本地",
    "shortcut_files_pending": "有快捷指令文件没导入", "history_import_pending": "健康历史导入还在排队",
}


def _cell(x: dict | None) -> str:
    if not x or not x.get("n"):
        return "—"
    return f"{x['n']}（{x['first']}–{x['last']}）"


def markdown(results: list[dict]) -> str:
    """The per-user table: counts and dates only (no names, no health values)."""
    lines = ["| user | 状态 | 模式 | OW 日值天 | OW 睡眠 | OW 训练 | OW 原始样本 | OW 存档到 | 只在 OW（夜 / 训练） | "
             "快捷指令 日值天 / 样本 | 待导入文件 | 导出 日值天 / 样本 | 说明 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        s = r["sources"]
        arch = r.get("ow_archive") or {}
        oo = r.get("only_in_ow")
        only = ("—" if oo is None else "读不到" if "error" in oo
                else f"{oo['nights']['n']} / {oo['workouts']['n']}")
        covered = arch.get("covered_to").date().isoformat() if arch.get("covered_to") else "—"
        why = "；".join(_REASONS.get(k, k) for k in r["fails"] + r["warns"]) or ""
        lines.append(" | ".join([
            f"| {r['user_id']}", r["status"], r["mode"], _cell(s["ow"].get("daily")), _cell(s["ow"].get("sleep")),
            _cell(s["ow"].get("workouts")), _cell(s["ow"].get("raw")), covered, only,
            f"{_cell(s['shortcut'].get('daily'))} / {_cell(s['shortcut'].get('raw'))}", str(r["shortcut_pending_files"]),
            f"{_cell(s['apple_export'].get('daily'))} / {_cell(s['apple_export'].get('raw'))}", why]) + " |")
    total = {k: sum(1 for r in results if r["status"] == k) for k in ("✓", "⚠", "✗", "—")}
    lines.append("")
    lines.append(f"合计 {len(results)} 人：✓ {total['✓']}，⚠ {total['⚠']}，✗ {total['✗']}，无旧数据 {total['—']}")
    return "\n".join(lines)

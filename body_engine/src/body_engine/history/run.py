"""Background job: process queued history imports (body-engine history-import-run).

On Ali a systemd timer checks every minute (deploy/ali/ops_jobs.py imports: a one-shot container is started only if something is queued, 512MB memory cap,
so it does not crowd out the resident API container); on the Mac ops.py hourly runs it as a side task. Globally one at a time (advisory lock), in queue order.

Per job: parse and import (importers/apple_zip.py, progress written back to the job) -> delete the uploaded file (on success or failure) -> if linked to OW,
pull all OW history once (the app's first 90-day sync may arrive in several batches) -> workout heart rate / TRIMP -> recompute scores over the whole range -> completion summary.
A scoring failure does not count as an import failure (data is already in the DB; tonight's full recompute will catch up).
"""
import json
import logging
import time
import traceback
from datetime import datetime

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..config import settings
from ..i18n import L
from ..importers import apple_zip
from . import store

log = logging.getLogger(__name__)
LOCK_KEY = 0x48495354   # 'HIST'
STALE_PROCESSING = "30 minutes"
STALE_UPLOADING = "48 hours"


def _connect():
    return psycopg.connect(settings.dsn, row_factory=dict_row, autocommit=True)


def housekeeping(conn: psycopg.Connection) -> dict:
    """Re-queue crashed jobs; void abandoned uploads and delete their files."""
    requeued = conn.execute(f"""UPDATE history_imports SET status = 'queued', phase = NULL, progress = NULL,
                                  message = '上次中断了，重新排队', updated_at = now()
                                WHERE status = 'processing' AND updated_at < now() - interval '{STALE_PROCESSING}'
                                RETURNING id""").fetchall()
    gone = conn.execute(f"""UPDATE history_imports SET status = 'cancelled', message = '上传 48 小时没完成，已作废',
                              finished_at = now(), updated_at = now()
                            WHERE status = 'uploading' AND updated_at < now() - interval '{STALE_UPLOADING}'
                            RETURNING id, user_id""").fetchall()
    for r in gone:
        store.remove_file(r["user_id"], r["id"])
    return {"requeued": len(requeued), "expired": len(gone)}


def claim(conn: psycopg.Connection) -> dict | None:
    return conn.execute("""UPDATE history_imports SET status = 'processing', phase = 'parsing', progress = 0,
                             started_at = now(), updated_at = now(), message = NULL
                           WHERE id = (SELECT h.id FROM history_imports h JOIN users u ON u.id = h.user_id
                                       WHERE h.status = 'queued' AND (u.role = 'owner' OR u.consent_at IS NOT NULL)
                                       ORDER BY h.id LIMIT 1 FOR UPDATE OF h SKIP LOCKED)
                           RETURNING *""").fetchone()


def _set(conn, job_id: int, **kv) -> None:
    kv = {k: (Jsonb(v) if k == "result" else v) for k, v in kv.items()}
    conn.execute(f"UPDATE history_imports SET {', '.join(f'{k} = %s' for k in kv)}, updated_at = now() WHERE id = %s",
                 (*kv.values(), job_id))


def summary(res: dict) -> str:
    """The completion message (stored in Chinese; store.public rebuilds it from the result in the request's language)."""
    if not res.get("days"):
        return L("导入完成，但导出里没有可用的每日数据", "Import done, but the export has no usable daily data")
    s = L(f"导入完成：{res['first_day']} 到 {res['last_day']}，{res['days']} 天每日数据、{res['nights']} 晚睡眠、{res['workouts']} 次训练",
          f"Import done: {res['first_day']} to {res['last_day']}. Days: {res['days']}, nights of sleep: {res['nights']}, "
          f"workouts: {res['workouts']}")
    if res.get("ecg"):
        s += L(f"、{res['ecg']} 条心电图", f", ECGs: {res['ecg']}")
    if res.get("scores_note"):
        s += L("。" + res["scores_note"], ". Scores are recalculated tonight")
    return s


def process(conn: psycopg.Connection, job: dict, client=None) -> dict:
    """Do one already-claimed job; return the result (also written back to the job)."""
    uid, jid = job["user_id"], job["id"]
    path = store.file_path(uid, jid)
    last = [0.0]

    def progress(x: float) -> None:
        if time.monotonic() - last[0] > 3:
            last[0] = time.monotonic()
            _set(conn, jid, progress=round(x, 3))

    try:
        if not path.exists():
            raise apple_zip.ExportError("上传的文件不见了（可能已被清理）；请重新上传")
        res = apple_zip.run(conn, uid, path, jid, progress)
    except Exception as e:  # noqa: BLE001
        store.remove_file(uid, jid)
        friendly = str(e) if isinstance(e, apple_zip.ExportError) else "导入出错了，已通知管理员；可以稍后重新上传"
        log.error("history import %s failed: %s", jid, traceback.format_exc())
        _set(conn, jid, status="failed", phase=None, message=friendly, finished_at=datetime.now().astimezone(),
             result={"error": f"{type(e).__name__}: {e}"[:500]})
        return {"id": jid, "status": "failed", "error": str(e)}
    store.remove_file(uid, jid)
    _set(conn, jid, phase="scores", progress=1.0, result=res)
    notes = []
    try:
        from ..training import importer as wk
        from ..importers import ow
        if client is not None and conn.execute("SELECT ow_user_id FROM users WHERE id = %s", (uid,)).fetchone()["ow_user_id"]:
            try:
                with conn.transaction():
                    ow.run(conn, client, uid, full=True)
            except Exception as e:  # noqa: BLE001  OW being unreachable does not affect the history import
                notes.append(f"OW：{e}")
        with conn.transaction():
            res["workouts_enriched"] = wk.enrich_pending(conn, client, uid)
        from ..insights import jobs as insights_jobs
        from ..scores import engine as scores_engine
        from ..importers.shortcut import user_archive_dir
        archive = user_archive_dir(uid)
        with conn.transaction():
            sc = scores_engine.run(conn, client, uid, archive_dir=archive)
        res["scores"] = {k: sc.get(k) for k in ("origin", "rows", "with_sleep_score", "with_recovery", "with_strain")}
        try:
            with conn.transaction():
                insights_jobs.run(conn, client, uid)
        except Exception as e:  # noqa: BLE001
            notes.append(f"insights：{e}")
    except Exception as e:  # noqa: BLE001
        log.error("history import %s scores failed: %s", jid, traceback.format_exc())
        res["scores_note"] = "评分今晚自动重算"
        notes.append(f"scores：{e}")
    if notes:
        res["notes"] = [n[:300] for n in notes]
    _set(conn, jid, status="done", phase=None, progress=1.0, result=json.loads(json.dumps(res, default=str)),
         message=summary(res), finished_at=datetime.now().astimezone())
    return {"id": jid, "status": "done", **res}


def run_pending(client=None, max_jobs: int = 5) -> dict:
    """Process queued jobs (at most max_jobs). Return immediately if another process is already running."""
    with _connect() as conn:
        if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK_KEY,)).fetchone()["ok"]:
            return {"skipped": "另一个 history-import-run 正在跑"}
        try:
            out = {"housekeeping": housekeeping(conn), "jobs": []}
            for _ in range(max_jobs):
                job = claim(conn)
                if job is None:
                    break
                out["jobs"].append(process(conn, job, client))
            return out
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


def pending_count(conn: psycopg.Connection) -> int:
    return conn.execute("SELECT count(*) AS n FROM history_imports WHERE status = 'queued'").fetchone()["n"]

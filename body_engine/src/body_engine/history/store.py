"""History import jobs (table history_imports) and uploaded files (health_data/imports/u<id>/<job id>.upload).

- Limits (env vars): max size per file BODY_IMPORT_MAX_MB (default 1024; an iPhone export zip is usually 20-100MB),
  keep at least BODY_IMPORT_MIN_FREE_MB (default 2048) of disk free during upload and parsing; files others are uploading are reserved at full size.
- Chunks: each <= CHUNK_BYTES (1.5MB; the public nginx client_max_body_size is 2m), appended by offset; after a disconnect, resume from the position the server recorded.
- A job with no heartbeat (no progress update) for 30 minutes counts as a crashed background job and is re-queued; a half-finished upload idle for 48 hours is voided and its file deleted.
"""
import os
import shutil
from pathlib import Path

import psycopg

from ..config import settings

CHUNK_BYTES = 1536 * 1024
MAX_CHUNK_BYTES = 2 * 1024 * 1024 - 64 * 1024
ACTIVE = ("uploading", "queued", "processing")
FIELDS = ("id", "status", "filename", "size_bytes", "received_bytes", "phase", "progress", "message", "result",
          "created_at", "updated_at", "started_at", "finished_at")


def max_bytes() -> int:
    return int(os.environ.get("BODY_IMPORT_MAX_MB", "1024")) * 1024 * 1024


def min_free_bytes() -> int:
    return int(os.environ.get("BODY_IMPORT_MIN_FREE_MB", "2048")) * 1024 * 1024


def import_dir() -> Path:
    return Path(os.environ.get("BODY_IMPORT_DIR") or settings.health_data_dir / "imports")


def file_path(user_id: int, job_id: int) -> Path:
    return import_dir() / f"u{int(user_id)}" / f"{int(job_id)}.upload"


def free_bytes() -> int:
    d = import_dir()
    d.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(d).free


def public(job: dict | None) -> dict | None:
    return None if job is None else {k: job[k] for k in FIELDS}


def latest(conn: psycopg.Connection, user_id: int) -> dict | None:
    return conn.execute("SELECT * FROM history_imports WHERE user_id = %s ORDER BY id DESC LIMIT 1",
                        (user_id,)).fetchone()


def get(conn: psycopg.Connection, user_id: int, job_id: int, lock: bool = False) -> dict | None:
    return conn.execute("SELECT * FROM history_imports WHERE id = %s AND user_id = %s" + (" FOR UPDATE" if lock else ""),
                        (job_id, user_id)).fetchone()


def remove_file(user_id: int, job_id: int) -> None:
    try:
        file_path(user_id, job_id).unlink()
    except FileNotFoundError:
        pass


class ImportRefused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def create(conn: psycopg.Connection, user_id: int, filename: str, size: int) -> dict:
    """Start (or resume) an upload. If the same file (same name and size) is still uploading, resume it."""
    name = os.path.basename(filename or "").strip()[:200] or "export.zip"
    if not name.lower().endswith((".zip", ".xml")):
        raise ImportRefused(400, "请选「健康」app 导出的 export.zip（中文系统叫 导出.zip）")
    if size <= 0:
        raise ImportRefused(400, "文件是空的")
    if size > max_bytes():
        raise ImportRefused(413, f"文件太大（{size / 1048576:.0f}MB，最大 {max_bytes() // 1048576}MB）；请联系管理员")
    from ..profile import consent
    if consent.needs_consent(conn, user_id):
        raise ImportRefused(403, "先在 app 里看完并同意《健康数据使用同意书》，再导入历史")
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('history_imports_create'))")
        active = conn.execute("SELECT * FROM history_imports WHERE user_id = %s AND status = ANY(%s)",
                              (user_id, list(ACTIVE))).fetchone()
        if active:
            if active["status"] == "uploading" and active["filename"] == name and active["size_bytes"] == size:
                return active
            if active["status"] == "uploading":
                raise ImportRefused(409, "还有一个没传完的文件；先取消它再传新的")
            raise ImportRefused(409, "上一次导入还在进行中，完成后再传")
        reserved = conn.execute("""SELECT COALESCE(sum(size_bytes - received_bytes), 0) AS n FROM history_imports
                                   WHERE status = 'uploading'""").fetchone()["n"]
        if free_bytes() - int(reserved) - size < min_free_bytes():
            raise ImportRefused(507, "服务器空间暂时不够，请过几个小时再试，或联系管理员")
        job = conn.execute("""INSERT INTO history_imports (user_id, status, filename, size_bytes)
                              VALUES (%s, 'uploading', %s, %s) RETURNING *""", (user_id, name, size)).fetchone()
    p = file_path(user_id, job["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return job


def append(conn: psycopg.Connection, user_id: int, job_id: int, offset: int, data: bytes) -> dict:
    """Append a chunk at offset; if offset differs from what the server has received -> 409 (with received_bytes, the client resumes from there)."""
    if not data:
        raise ImportRefused(400, "空的分片")
    if len(data) > MAX_CHUNK_BYTES:
        raise ImportRefused(413, "分片太大")
    with conn.transaction():
        job = get(conn, user_id, job_id, lock=True)
        if job is None:
            raise ImportRefused(404, "没有这个导入")
        if job["status"] != "uploading":
            raise ImportRefused(409, "这个导入已经不在上传状态")
        if offset != job["received_bytes"]:
            raise ImportRefused(409, f"received_bytes={job['received_bytes']}")
        if offset + len(data) > job["size_bytes"]:
            raise ImportRefused(400, "超过了开始时说的文件大小")
        if free_bytes() - len(data) < min_free_bytes() // 2:
            raise ImportRefused(507, "服务器空间暂时不够，请过几个小时再试，或联系管理员")
        p = file_path(user_id, job_id)
        with open(p, "r+b" if p.exists() else "wb") as f:
            f.truncate(offset)    # discard the part written last time that was not recorded in the DB
            f.seek(offset)
            f.write(data)
        return conn.execute("""UPDATE history_imports SET received_bytes = %s, updated_at = now() WHERE id = %s
                               RETURNING *""", (offset + len(data), job_id)).fetchone()


def complete(conn: psycopg.Connection, user_id: int, job_id: int) -> dict:
    from ..importers.apple_zip import first_bytes_ok
    with conn.transaction():
        job = get(conn, user_id, job_id, lock=True)
        if job is None:
            raise ImportRefused(404, "没有这个导入")
        if job["status"] != "uploading":
            return job
        if job["received_bytes"] != job["size_bytes"]:
            raise ImportRefused(409, f"还没传完（{job['received_bytes']} / {job['size_bytes']}）")
        p = file_path(user_id, job_id)
        with open(p, "rb") as f:
            head = f.read(64)
        if p.stat().st_size != job["size_bytes"] or not first_bytes_ok(head):
            remove_file(user_id, job_id)
            conn.execute("""UPDATE history_imports SET status = 'failed', message = %s, finished_at = now(),
                            updated_at = now() WHERE id = %s""", ("文件格式不对：请上传「健康」app 导出的 export.zip", job_id))
            raise ImportRefused(400, "文件格式不对：请上传「健康」app 导出的 export.zip")
        return conn.execute("""UPDATE history_imports SET status = 'queued', message = NULL, updated_at = now()
                               WHERE id = %s RETURNING *""", (job_id,)).fetchone()


def cancel(conn: psycopg.Connection, user_id: int, job_id: int) -> dict:
    with conn.transaction():
        job = get(conn, user_id, job_id, lock=True)
        if job is None:
            raise ImportRefused(404, "没有这个导入")
        if job["status"] == "processing":
            raise ImportRefused(409, "正在导入，不能取消（几分钟就好）")
        if job["status"] in ("uploading", "queued"):
            job = conn.execute("""UPDATE history_imports SET status = 'cancelled', message = '已取消', finished_at = now(),
                                  updated_at = now() WHERE id = %s RETURNING *""", (job_id,)).fetchone()
    remove_file(user_id, job_id)
    return job

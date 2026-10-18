#!/usr/bin/env python3
"""Local ops for the body management system: scheduled jobs, backup / restore drill, health check, startup recovery.

Standard library only (works with the system /usr/bin/python3 3.9), independent of the Body Engine virtualenv:
if Body Engine is broken, doctor and backup still run. Databases are reached through `docker exec`, so no local Postgres is needed.

Usage: python3 deploy/ops/ops.py <command>
  hourly         every hour: import-ow for each user linked to OW; compute-scores for each user with data (writes only the last 3 days);
                 evaluate-rules (only if the command exists)
  nightly        every night at 03:30: import-ow + full compute-scores, then backup
  backup         pg_dump the three databases + secrets/ + Shortcut archive -> restic (encrypted) -> prune by retention policy
  restore-drill  restore from restic into a temporary database in a throwaway container and compare row counts table by table (production DBs untouched)
  restore-extract --target DIR [--snapshot ID]   extract the files of a backup (for a real restore, see README)
  doctor         check all services and print a table of passes/failures; --notify sends a macOS notification on problems
  start-all      bring all services up after a Mac reboot / login (Docker Desktop, the three compose projects, the Shortcut receiver)

Paths (all overridable with environment variables, see README):
  BODY_ROOT             deployment root: where secrets/, health_data/, compose.yaml and vendor/ live; defaults to this repo
  BODY_SECRETS_DIR      defaults to $BODY_ROOT/secrets
  BODY_HEALTH_DATA_DIR  defaults to $BODY_ROOT/health_data; logs and state live under its ops/
  BODY_RESTIC_REPO      defaults to ~/Backups/body-management-restic
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import secrets as pysecrets
import shutil
import socket
import subprocess
import sys
import time
import unicodedata
from contextlib import redirect_stdout
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("BODY_ROOT", HERE.parents[1]))
SECRETS = Path(os.environ.get("BODY_SECRETS_DIR", ROOT / "secrets"))
HEALTH = Path(os.environ.get("BODY_HEALTH_DATA_DIR", ROOT / "health_data"))
ENGINE_DIR = Path(os.environ.get("BODY_ENGINE_DIR", ROOT / "body_engine"))
OPS_DIR = HEALTH / "ops"
STATE_FILE = OPS_DIR / "state.json"
STAGING = OPS_DIR / "backup-staging"
RESTIC_REPO = Path(os.environ.get("BODY_RESTIC_REPO", Path.home() / "Backups" / "body-management-restic"))
RESTIC_PASSWORD_FILE = SECRETS / "restic_password"
RESTIC_HOST = "body-management"   # fixed host, so the retention policy still groups the same snapshots after a machine / hostname change
RESTIC_TAG = "nightly"
KEEP = ["--keep-daily", "7", "--keep-weekly", "4", "--keep-monthly", "6"]
LOG_KEEP_DAYS = 30
TZ_NAME = "Australia/Sydney"
TZ = ZoneInfo(TZ_NAME) if ZoneInfo else None
LABEL_PREFIX = "org.watch2whoop.body"
HEALTHSYNC_LABEL = "org.watch2whoop.healthsync"
# Where the primary server is: mac (default, this machine runs all services) / ali (since 2026-10-18: the services run on Alibaba Cloud Ali,
# this machine only pulls backups, does restore drills and watches Ali's health). install.sh --ali-primary sets BODY_PRIMARY=ali in the LaunchAgent.
PRIMARY = os.environ.get("BODY_PRIMARY", "mac")
ALI_HOST = os.environ.get("ALI_HOST", "Ali")              # name in ~/.ssh/config
ALI_ROOT = os.environ.get("ALI_ROOT", "/opt/body-management")
ALI_MIRROR = OPS_DIR / "ali-mirror"                       # mirror of Ali's secrets/ and health_data/archive/ (restic backs it up)
ALI_DUMP_MAX_AGE = 3600                                   # the pulled dump must be freshly made (seconds)

# launchd gives a very short PATH: add Homebrew (restic), /usr/local/bin (docker, tailscale), ~/.local/bin (uv)
os.environ["PATH"] = ":".join(dict.fromkeys(
    ["/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local/bin"),
     *os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin").split(":")]))

# Databases to back up: (compose project, service) locates the container, no hard-coded container names
DATABASES = [
    {"key": "body", "title": "Body Engine", "project": "body", "service": "body-db", "user": "body", "db": "body"},
    {"key": "open-wearables", "title": "Open Wearables", "project": "open-wearables", "service": "db",
     "user": "open-wearables", "db": "open-wearables"},
    {"key": "sparkyfitness", "title": "SparkyFitness", "project": "sparkyfitness", "service": "sparkyfitness-db",
     "user": "sparky", "db": "sparkyfitness_db"},
]
# Containers that should always be running
EXPECTED_SERVICES = {
    "body": ("Body Engine", ["body-db", "body-engine"]),
    "open-wearables": ("Open Wearables", ["db", "app", "celery-worker", "celery-beat", "flower", "redis",
                                          "svix-server", "frontend", "seaweedfs"]),
    "sparkyfitness": ("SparkyFitness", ["sparkyfitness-db", "sparkyfitness-server", "sparkyfitness-frontend"]),
    "grafana": ("Grafana", ["grafana"]),
}
PORTS = {5433: "Body DB", 8100: "Body API", 8200: "OW API", 8201: "OW 门户", 8202: "OW Flower",
         8203: "OW S3", 5434: "OW DB", 8300: "SparkyFitness", 8400: "Grafana"}
# HTTPS ports of tailscale serve: (local target, path to check, string expected in the response, name)
TAILSCALE_SERVE = {8443: ("http://127.0.0.1:8200", "/", "Server is running", "OW"),
                   8444: ("http://127.0.0.1:8300", "/api/health", '"UP"', "SparkyFitness"),
                   8445: ("http://127.0.0.1:8400", "/api/health", '"database"', "Grafana"),
                   443: ("http://127.0.0.1:8100", "/app/", "body-engine-today", "今天页"),
                   8446: ("http://127.0.0.1:8767", "/ping", "health-sync ok", "快捷指令上传")}
# Latest sample per Shortcut data type (shortcut_samples): these types are flagged when they have no new data for over 26 hours; the others (weight, VO2max,
# exercise minutes etc. are not there every day) are only listed
SHORTCUT_DAILY_TYPES = ["Heart Rate", "Steps", "Active Calories", "Resting Calories", "Sleep",
                        "Heart Rate Variability", "Resting Heart Rate", "Respiratory Rate", "Oxygen Saturation"]
SHORTCUT_STALE_HOURS = 26
GRAFANA_URL = "http://127.0.0.1:8400"
GRAFANA_DATASOURCES = {"body-engine": "Body Engine", "open-wearables": "Open Wearables", "sparkyfitness": "SparkyFitness"}


# ---------- common ----------


def shortcut_freshness(r, t_now: float) -> None:
    """Time of the latest sample of each Shortcut data type (per user). Types that should arrive daily and are older than SHORTCUT_STALE_HOURS hours -> ⚠."""
    try:
        rows = psql(body_db(), "body", "body",
                    "SELECT s.user_id, u.display_name, s.type, extract(epoch FROM max(s.end_at)) FROM shortcut_samples s "
                    "JOIN users u ON u.id = s.user_id GROUP BY 1, 2, 3 ORDER BY 1, 3")
    except Exception as e:  # noqa: BLE001
        r.warn("快捷指令各类数据", f"查不了 shortcut_samples（迁移 110 还没跑？）：{str(e)[:80]}")
        return
    by_user = {}
    for uid, name, typ, ts in rows:
        by_user.setdefault((int(uid), name), {})[typ] = float(ts)
    for (uid, name), latest in by_user.items():
        stale = [f"{t} {fmt_age(t_now - latest[t])}" if t in latest else f"{t} 没有"
                 for t in SHORTCUT_DAILY_TYPES if t not in latest or t_now - latest[t] > SHORTCUT_STALE_HOURS * 3600]
        other = [f"{t} {fmt_age(t_now - v)}" for t, v in latest.items() if t not in SHORTCUT_DAILY_TYPES]
        title = f"快捷指令各类数据（user {uid} {name}）"
        if stale:
            r.warn(title, f"超过 {SHORTCUT_STALE_HOURS} 小时没有新数据：{'；'.join(stale)}"
                          "（手机锁屏时读不到健康数据；打开一次微信或手动运行快捷指令）")
        else:
            r.ok(title, f"{len(SHORTCUT_DAILY_TYPES)} 类每天的数据都在 {SHORTCUT_STALE_HOURS} 小时内"
                        + (f"；其他：{'，'.join(other)}" if other else ""))

def now() -> datetime:
    return datetime.now(TZ) if TZ else datetime.now().astimezone()


def fmt_age(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)} 秒前"
    if seconds < 5400:
        return f"{int(seconds / 60)} 分钟前"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} 小时前"
    return f"{seconds / 86400:.1f} 天前"


def fmt_bytes(n: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024
    return str(n)


class Log:
    """Write to health_data/ops/<job>-YYYY-MM-DD.log and print at the same time."""

    def __init__(self, job: str, echo: bool = True):
        OPS_DIR.mkdir(parents=True, exist_ok=True)
        self.path = OPS_DIR / f"{job}-{now():%Y-%m-%d}.log"
        # don't print under launchd (it would duplicate into launchd-*.log); that file keeps crash output only
        self.echo = echo and not os.environ.get("BODY_OPS_QUIET")

    def __call__(self, msg: str = "") -> None:
        line = f"[{now():%Y-%m-%d %H:%M:%S}] {msg}"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if self.echo:
            print(line, flush=True)


def rotate_logs() -> None:
    """Keep the per-day logs for 30 days; truncate launchd stdout/stderr files over 5MB."""
    cutoff = now().date() - timedelta(days=LOG_KEEP_DAYS)
    for p in OPS_DIR.glob("*.log"):
        m = re.search(r"-(\d{4}-\d{2}-\d{2})\.log$", p.name)
        if m and date.fromisoformat(m.group(1)) < cutoff:
            p.unlink()
        elif p.name.startswith("launchd-") and p.stat().st_size > 5 * 1024 * 1024:
            p.write_text("")


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def save_state(**updates) -> None:
    OPS_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    state.update(updates)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str))
    tmp.replace(STATE_FILE)


class Lock:
    """hourly / nightly / backup are mutually exclusive so they never write the DB at the same time; start-all / watchdog use another lock (name="services"),
    so two `docker compose up` never work on the same project at once."""

    def __init__(self, wait_seconds: int, name: str = "jobs"):
        OPS_DIR.mkdir(parents=True, exist_ok=True)
        self.f = open(OPS_DIR / (".lock" if name == "jobs" else f".lock-{name}"), "w")
        self.wait = wait_seconds

    def __enter__(self):
        deadline = time.time() + self.wait
        while True:
            try:
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.time() >= deadline:
                    raise TimeoutError("另一个运维任务还在跑")
                time.sleep(5)

    def __exit__(self, *exc):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def run(cmd: list, timeout: int = 600, check: bool = False, env: dict | None = None,
        input_text: str | None = None) -> subprocess.CompletedProcess:
    try:
        p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, **(env or {})}, input=input_text)
    except subprocess.TimeoutExpired as e:
        p = subprocess.CompletedProcess(e.cmd, 124, e.stdout or "", f"超时（{timeout} 秒）")
    except FileNotFoundError as e:
        p = subprocess.CompletedProcess(cmd, 127, "", str(e))
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd[:4]))}… 失败（{p.returncode}）：{(p.stderr or p.stdout).strip()[-800:]}")
    return p


def notify(title: str, message: str) -> None:
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    p = run(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}" sound name "Basso"'],
            timeout=15)
    if p.returncode != 0:
        print(f"[{now():%Y-%m-%d %H:%M:%S}] 发通知失败：{p.stderr.strip()}", file=sys.stderr)


def notify_failure(job: str, message: str) -> None:
    """Remind at most once every 6 hours when the same job keeps failing."""
    state = load_state()
    last = state.get("notified", {}).get(job)
    if last and time.time() - last < 6 * 3600:
        return
    notify("身体管理系统", f"{job} 失败：{message[:180]}")
    save_state(notified={**state.get("notified", {}), job: time.time()})


def http_get(url: str, headers: dict | None = None, timeout: float = 8) -> tuple:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(4000).decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(500).decode(errors="replace")
    except Exception as e:  # noqa: BLE001 connection failure, timeout, certificate... all count as failure
        return None, f"{type(e).__name__}: {e}"


# ---------- Docker / database ----------

def container(project: str, service: str, running_only: bool = True) -> str | None:
    cmd = ["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={project}",
           "--filter", f"label=com.docker.compose.service={service}"]
    if not running_only:
        cmd.insert(2, "-a")
    out = run(cmd, timeout=30).stdout.split()
    return out[0] if out else None


def psql(c: str, user: str, db: str, sql: str, timeout: int = 120) -> list:
    p = run(["docker", "exec", "-i", c, "psql", "-U", user, "-d", db, "-At", "-v", "ON_ERROR_STOP=1", "-c", sql],
            timeout=timeout, check=True)
    return [line.split("|") for line in p.stdout.splitlines() if line]


def grafana_auth() -> dict:
    """Basic auth header of the Grafana admin (used by doctor to check datasources and by backup to export dashboards)."""
    pw_file = SECRETS / "grafana_admin_password"
    if not pw_file.exists():
        return {}
    token = base64.b64encode(f"admin:{pw_file.read_text().strip()}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def body_db() -> str:
    c = container("body", "body-db")
    if not c:
        raise RuntimeError("body-db 容器没在跑")
    return c


def list_users() -> list:
    """All users: id, name, time zone, whether linked to OW, whether they have any data (users without data are not scored)."""
    rows = psql(body_db(), "body", "body", """
        SELECT u.id, u.display_name, coalesce(u.timezone, 'Australia/Sydney'), u.ow_user_id IS NOT NULL,
               EXISTS (SELECT 1 FROM daily_metrics m WHERE m.user_id = u.id)
               OR EXISTS (SELECT 1 FROM sleep_sessions s WHERE s.user_id = u.id)
        FROM users u ORDER BY u.id""")
    return [{"id": int(r[0]), "name": r[1], "tz": r[2], "ow": r[3] == "t", "has_data": r[4] == "t"} for r in rows]


TABLES_SQL = """SELECT format('%I.%I', schemaname, tablename) FROM pg_tables
                WHERE schemaname !~ '^pg_' AND schemaname <> 'information_schema' ORDER BY 1"""


def count_sql(tables: list) -> str:
    return " UNION ALL ".join(f"SELECT '{t}', count(*) FROM {t}" for t in tables) or "SELECT NULL, NULL WHERE false"


class PsqlSession:
    """A long-lived psql session: exports a snapshot and counts rows inside that same snapshot, so pg_dump --snapshot uses the same one."""

    def __init__(self, c: str, user: str, db: str):
        self.p = subprocess.Popen(["docker", "exec", "-i", c, "psql", "-U", user, "-d", db, "-At", "-q",
                                   "-v", "ON_ERROR_STOP=1"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def query(self, sql: str) -> list:
        self.p.stdin.write(sql.strip().rstrip(";") + ";\n\\echo __END__\n")
        self.p.stdin.flush()
        rows = []
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError(f"psql 退出：{self.p.stderr.read()[-500:]}")
            line = line.rstrip("\n")
            if line == "__END__":
                return rows
            rows.append(line.split("|"))

    def close(self) -> None:
        try:
            self.p.stdin.write("COMMIT;\n\\q\n")
            self.p.stdin.close()
        except BrokenPipeError:
            pass
        self.p.wait(timeout=30)


# ---------- Body Engine commands ----------

def engine_env() -> dict:
    return {"BODY_DB_PASSWORD_FILE": str(SECRETS / "body_db_password"),
            "BODY_OW_API_KEY_FILE": str(SECRETS / "open_wearables_api_key"),
            "BODY_ENGINE_TOKEN_FILE": str(SECRETS / "body_engine_token_owner"),
            "BODY_SECRETS_DIR": str(SECRETS), "BODY_HEALTH_DATA_DIR": str(HEALTH)}


def engine(*args, timeout: int = 1800) -> subprocess.CompletedProcess:
    return run(["uv", "run", "--frozen", "--quiet", "--directory", ENGINE_DIR, "body-engine", *args],
               timeout=timeout, env=engine_env())


def engine_step(log: Log, errors: list, label: str, *args) -> bool:
    t0 = time.time()
    p = engine(*args)
    out = (p.stdout.strip() or p.stderr.strip())[-1500:]
    ok = p.returncode == 0
    log(f"{'✓' if ok else '✗'} {label}（{time.time() - t0:.0f} 秒）{out if not ok else out[:600]}")
    if not ok:
        log(p.stderr.strip()[-3000:])
        errors.append(f"{label}: {(p.stderr or p.stdout).strip().splitlines()[-1:] or ['?']}")
    return ok


def rules_command(log: Log) -> tuple:
    """evaluate-rules is developed by the rule-engine session: run it only if the command exists. Returns (exists?, whether it runs per user)."""
    p = engine("--help", timeout=300)
    if p.returncode != 0 or "evaluate-rules" not in p.stdout:
        log("· evaluate-rules 命令还不存在（规则引擎没合并），跳过")
        return False, False
    h = engine("evaluate-rules", "--help", timeout=300).stdout
    return True, "--user-id" in h


def user_today(u: dict) -> date:
    tz = ZoneInfo(u["tz"]) if ZoneInfo else None
    return datetime.now(tz).date() if tz else date.today()


def job_scores(log: Log, full: bool) -> list:
    errors: list = []
    help_text = engine("--help", timeout=300).stdout
    # queued Apple Health history uploads from the Today page (on Ali body-imports.timer handles them every minute; on the Mac they ride along with the hourly job)
    if "history-import-run" in help_text:
        engine_step(log, errors, "history-import-run", "history-import-run")
    users = list_users()
    log("用户：" + ", ".join(f"{u['id']} {u['name']}" for u in users))
    for u in users:
        uid = str(u["id"])
        if u["ow"]:
            engine_step(log, errors, f"import-ow user {uid}", "import-ow", "--user-id", uid)
        else:
            log(f"· user {uid} 没关联 Open Wearables，跳过 import-ow")
        if not u["has_data"]:
            log(f"· user {uid} 还没有任何数据，跳过 compute-scores")
            continue
        if full:
            engine_step(log, errors, f"compute-scores user {uid}（完整）", "compute-scores", "--user-id", uid)
        else:
            start = (user_today(u) - timedelta(days=3)).isoformat()
            engine_step(log, errors, f"compute-scores user {uid}（{start} 起）", "compute-scores", "--user-id", uid,
                        "--start", start)
    exists, per_user = rules_command(log)
    if exists:
        if per_user:
            for u in users:
                engine_step(log, errors, f"evaluate-rules user {u['id']}", "evaluate-rules", "--user-id", str(u["id"]))
        else:
            engine_step(log, errors, "evaluate-rules", "evaluate-rules")
    # auto-computed items of the status card (body butler; the Today page's "Me" tab also refreshes them on demand when it reads them)
    if "refresh-status" in help_text:
        for u in users:
            if u["has_data"]:
                engine_step(log, errors, f"refresh-status user {u['id']}", "refresh-status", "--user-id", str(u["id"]))
    return errors


def cmd_hourly(args) -> int:
    log = Log("hourly")
    rotate_logs()
    try:
        with Lock(wait_seconds=0):
            t0 = time.time()
            log("—— 每小时任务开始 ——")
            errors = job_scores(log, full=False)
    except TimeoutError:
        log("另一个任务（多半是每晚任务）还在跑，这次跳过")
        return 0
    except Exception as e:  # noqa: BLE001
        errors = [f"{type(e).__name__}: {e}"]
        log(f"✗ {errors[0]}")
    ok = not errors
    save_state(hourly={"at": now().isoformat(), "ok": ok, "errors": errors})
    log(f"—— 结束：{'全部成功' if ok else f'{len(errors)} 项失败'} ——")
    if not ok:
        notify_failure("每小时任务", "; ".join(errors))
    return 0 if ok else 1


def cmd_nightly(args) -> int:
    log = Log("nightly")
    rotate_logs()
    errors: list = []
    try:
        with Lock(wait_seconds=1800):
            log("—— 每晚任务开始 ——")
            try:
                errors = job_scores(log, full=True)
                save_state(hourly={"at": now().isoformat(), "ok": not errors, "errors": errors})
            except Exception as e:  # noqa: BLE001 a scoring failure must not stop the backup
                errors = [f"{type(e).__name__}: {e}"]
                log(f"✗ {errors[0]}")
            if not do_backup(log):
                errors.append("备份失败")
    except TimeoutError as e:
        errors = [str(e)]
        log(f"✗ {e}")
    save_state(nightly={"at": now().isoformat(), "ok": not errors, "errors": errors})
    log(f"—— 结束：{'全部成功' if not errors else '; '.join(errors)} ——")
    if errors:
        notify_failure("每晚任务", "; ".join(errors))
    return 0 if not errors else 1


# ---------- backup ----------

def restic_env() -> dict:
    return {"RESTIC_REPOSITORY": str(RESTIC_REPO), "RESTIC_PASSWORD_FILE": str(RESTIC_PASSWORD_FILE)}


def restic(*args, timeout: int = 3600, check: bool = True) -> subprocess.CompletedProcess:
    return run(["restic", *args], timeout=timeout, env=restic_env(), check=check)


def ensure_restic_repo(log: Log) -> None:
    if not shutil.which("restic"):
        raise RuntimeError("没装 restic：brew install restic")
    repo_exists = (RESTIC_REPO / "config").exists()
    if not RESTIC_PASSWORD_FILE.exists():
        if repo_exists:
            raise RuntimeError(f"restic 仓库已存在但找不到密码文件 {RESTIC_PASSWORD_FILE}（从密码管理器找回后写回去）")
        RESTIC_PASSWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(RESTIC_PASSWORD_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(pysecrets.token_urlsafe(32) + "\n")
        log(f"生成了 restic 密码：{RESTIC_PASSWORD_FILE}（请抄进密码管理器，丢了备份就打不开）")
    if not repo_exists:
        RESTIC_REPO.parent.mkdir(parents=True, exist_ok=True)
        restic("init", "--repository-version", "2")
        log(f"初始化了 restic 仓库：{RESTIC_REPO}")


def dump_database(log: Log, spec: dict) -> dict:
    """In one snapshot: count rows of every table, pg_dump (-Fc uncompressed, so restic dedups and compresses better), export roles."""
    c = container(spec["project"], spec["service"])
    if not c:
        raise RuntimeError(f"{spec['title']} 的数据库容器没在跑")
    dump_path = STAGING / f"{spec['key']}.dump"
    t0 = time.time()
    session = PsqlSession(c, spec["user"], spec["db"])
    try:
        session.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        snapshot = session.query("SELECT pg_export_snapshot()")[0][0]
        tables = [r[0] for r in session.query(TABLES_SQL)]
        counts = {t: int(n) for t, n in session.query(count_sql(tables))}
        version = session.query("SHOW server_version")[0][0]
        with open(dump_path, "wb") as f:
            p = subprocess.run(["docker", "exec", c, "pg_dump", "-U", spec["user"], "-d", spec["db"], "-Fc", "-Z0",
                                f"--snapshot={snapshot}"], stdout=f, stderr=subprocess.PIPE, timeout=3600)
        if p.returncode != 0:
            raise RuntimeError(f"pg_dump {spec['key']} 失败：{p.stderr.decode(errors='replace')[-500:]}")
    finally:
        session.close()
    globals_sql = run(["docker", "exec", c, "pg_dumpall", "-U", spec["user"], "--globals-only"], check=True).stdout
    (STAGING / f"{spec['key']}.globals.sql").write_text(globals_sql)
    # make sure pg_restore can read the dump's table of contents
    with open(dump_path, "rb") as f:
        toc = subprocess.run(["docker", "exec", "-i", c, "pg_restore", "-l"], stdin=f, capture_output=True, timeout=600)
    if toc.returncode != 0:
        raise RuntimeError(f"{spec['key']}.dump 读不出来：{toc.stderr.decode(errors='replace')[-300:]}")
    image = run(["docker", "inspect", "-f", "{{.Image}}", c], check=True).stdout.strip()
    image_ref = run(["docker", "inspect", "-f", "{{.Config.Image}}", c], check=True).stdout.strip()
    manifest = {"key": spec["key"], "db": spec["db"], "user": spec["user"], "server_version": version,
                "image": image, "image_ref": image_ref, "dumped_at": now().isoformat(),
                "tables": len(counts), "rows": sum(counts.values()), "counts": counts,
                "dump_bytes": dump_path.stat().st_size}
    (STAGING / f"{spec['key']}.manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    log(f"✓ {spec['title']}：{len(counts)} 张表、{manifest['rows']:,} 行，dump {fmt_bytes(manifest['dump_bytes'])}"
        f"（{time.time() - t0:.0f} 秒）")
    return manifest


def export_grafana_dashboards(log: Log) -> None:
    """Grafana's data volume is not backed up: the datasources and the "身体管理" dashboards are provisioning files under deploy/grafana/ (in git),
    and the admin password is in secrets/. The volume otherwise holds only login sessions, preferences and dashboards you created in the web UI - only the latter are exported as JSON and backed up.
    If Grafana is not running, just log one line; never fail the backup."""
    def get(path: str):
        req = urllib.request.Request(GRAFANA_URL + path, headers=grafana_auth())
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    try:
        out = {}
        for d in get("/api/search?type=dash-db&limit=5000"):
            full = get(f"/api/dashboards/uid/{d['uid']}")
            if not full.get("meta", {}).get("provisioned"):
                out[d["uid"]] = full["dashboard"]
    except Exception as e:  # noqa: BLE001
        log(f"⚠ Grafana 看板没导出（{type(e).__name__}: {e}），其余照常备份")
        return
    (STAGING / "grafana-dashboards.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    log(f"✓ Grafana：网页上新建的看板 {len(out)} 个已导出（provisioning 的看板在 git 里，不用备份）")


def secrets_manifest() -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(SECRETS.iterdir()) if p.is_file()}


def ssh_ali(cmd: str, timeout: int = 600, check: bool = True) -> subprocess.CompletedProcess:
    return run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", ALI_HOST, cmd], timeout=timeout, check=check)


def rsync_from_ali(src: str, dest: Path, delete: bool = False) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    run(["rsync", "-az", *(["--delete"] if delete else []), "-e", "ssh -o BatchMode=yes -o ConnectTimeout=20",
         f"{ALI_HOST}:{src}/", f"{dest}/"], timeout=3600, check=True)


def stage_from_ali(log: Log) -> tuple:
    """When the primary server is Ali: have Ali dump the three databases now (deploy/ali/ops_jobs.py dump: row counts in one snapshot + pg_dump + roles,
    same format as this machine's dump_database), pull them into the local STAGING; mirror Ali's secrets/ and Shortcut archive into ALI_MIRROR.
    Returns (manifests, paths to put into restic)."""
    t0 = time.time()
    p = ssh_ali(f"python3 {ALI_ROOT}/deploy/ali/ops_jobs.py dump", timeout=3600, check=False)
    if p.returncode != 0:
        raise RuntimeError(f"Ali 上导出失败：{(p.stdout + p.stderr).strip()[-500:]}")
    rsync_from_ali(f"{ALI_ROOT}/health_data/ops/backup-staging", STAGING, delete=True)
    manifests = []
    for spec in DATABASES:
        m = json.loads((STAGING / f"{spec['key']}.manifest.json").read_text())
        # Ali (Python 3.6) writes 2026-10-18T00:30:05+0800, which fromisoformat cannot parse (+0800)
        age = time.time() - datetime.strptime(m["dumped_at"], "%Y-%m-%dT%H:%M:%S%z").timestamp()
        if age > ALI_DUMP_MAX_AGE:
            raise RuntimeError(f"Ali 的 {spec['key']}.dump 是 {fmt_age(age)}导的，不是刚导的")
        log(f"✓ {spec['title']}（Ali）：{m['tables']} 张表、{m['rows']:,} 行，dump {fmt_bytes(m['dump_bytes'])}")
        manifests.append(m)
    ALI_MIRROR.mkdir(parents=True, exist_ok=True)
    os.chmod(ALI_MIRROR, 0o700)
    rsync_from_ali(f"{ALI_ROOT}/secrets", ALI_MIRROR / "secrets", delete=True)
    rsync_from_ali(f"{ALI_ROOT}/health_data/archive", ALI_MIRROR / "archive")   # add only, never delete
    sec = ALI_MIRROR / "secrets"
    (STAGING / "secrets.manifest.json").write_text(json.dumps(
        {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sec.iterdir()) if p.is_file()}, indent=1))
    (STAGING / "secrets.path").write_text(str(sec))   # the restore drill uses this to find where secrets sit in the snapshot
    log(f"✓ 从 Ali 拉回 dump、secrets/、快捷指令存档（{time.time() - t0:.0f} 秒）")
    return manifests, [STAGING, sec, ALI_MIRROR / "archive"]


def stage_local(log: Log) -> tuple:
    manifests = [dump_database(log, spec) for spec in DATABASES]
    (STAGING / "secrets.manifest.json").write_text(json.dumps(secrets_manifest(), indent=1))
    export_grafana_dashboards(log)
    paths = [STAGING, SECRETS]
    if (HEALTH / "archive").exists():
        paths.append(HEALTH / "archive")   # raw Shortcut uploads (small; cannot be recreated if lost)
    return manifests, paths


def do_backup(log: Log, from_ali: bool = False) -> bool:
    t0 = time.time()
    log(f"—— 备份开始{'（从 Ali 拉）' if from_ali else ''} ——")
    try:
        ensure_restic_repo(log)
        if STAGING.exists():
            shutil.rmtree(STAGING)
        STAGING.mkdir(parents=True)
        manifests, paths = stage_from_ali(log) if from_ali else stage_local(log)
        p = restic("backup", "--json", "--quiet", "--host", RESTIC_HOST, "--tag", RESTIC_TAG, *paths)
        summary = next((json.loads(l) for l in reversed(p.stdout.splitlines())
                        if l.startswith("{") and '"summary"' in l), {})
        snap = summary.get("snapshot_id", "?")[:8]
        log(f"✓ restic 快照 {snap}：新增 {fmt_bytes(summary.get('data_added_packed', summary.get('data_added', 0)))}（压缩后），"
            f"共 {summary.get('total_files_processed', '?')} 个文件 {fmt_bytes(summary.get('total_bytes_processed', 0))}")
        forget = restic("forget", "--host", RESTIC_HOST, "--tag", RESTIC_TAG, "--group-by", "host,tags", *KEEP,
                        "--prune", "--quiet")
        log(f"✓ 保留策略：7 天日备 + 4 周周备 + 6 月月备 {forget.stdout.strip()[-300:]}")
        check_args = ["check"] + (["--read-data-subset=10%"] if now().weekday() == 6 else [])
        restic(*check_args, "--quiet")
        log(f"✓ restic check{'（周日：抽查 10% 数据）' if len(check_args) > 1 else ''}")
        snaps = json.loads(restic("snapshots", "--json", "--host", RESTIC_HOST, "--tag", RESTIC_TAG).stdout or "[]")
        repo_bytes = sum(f.stat().st_size for f in RESTIC_REPO.rglob("*") if f.is_file())
        shutil.rmtree(STAGING)   # plaintext dumps are not left on disk
        if from_ali:   # tell Ali that this machine has pulled a backup (Ali's doctor uses it to warn "the Mac hasn't pulled a backup for days")
            ssh_ali(f"mkdir -p {ALI_ROOT}/health_data/ops && echo '{json.dumps({'at': now().isoformat(), 'snapshot': snap})}'"
                    f" > {ALI_ROOT}/health_data/ops/mac-pull.json", check=False)
        save_state(backup={"at": now().isoformat(), "ok": True, "snapshot": snap, "snapshots": len(snaps),
                           "source": "ali" if from_ali else "local",
                           "repo": str(RESTIC_REPO), "repo_bytes": repo_bytes,
                           "databases": {m["key"]: {"tables": m["tables"], "rows": m["rows"],
                                                    "dump_bytes": m["dump_bytes"]} for m in manifests},
                           "seconds": round(time.time() - t0)})
        log(f"—— 备份完成：{len(snaps)} 个快照，仓库 {fmt_bytes(repo_bytes)}，用时 {time.time() - t0:.0f} 秒 ——")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"✗ 备份失败：{type(e).__name__}: {e}")
        save_state(backup={**load_state().get("backup", {}), "ok": False, "failed_at": now().isoformat(),
                           "error": str(e)[:500]})
        return False


def cmd_backup(args) -> int:
    from_ali = args.from_ali or PRIMARY == "ali"
    log = Log("backup")
    rotate_logs()
    try:
        with Lock(wait_seconds=1800):
            ok = do_backup(log, from_ali=from_ali)
    except TimeoutError as e:
        log(f"✗ {e}")
        ok = False
    if not ok:
        notify_failure("备份", "看 health_data/ops/backup-*.log")
    elif from_ali and args.drill:
        ok = cmd_restore_drill(argparse.Namespace(snapshot="latest")) == 0
    return 0 if ok else 1


# ---------- restore ----------

def restore_snapshot(snapshot: str, target: Path) -> Path:
    """Extract a snapshot; returns the location of the staging directory under target."""
    host = ["--host", RESTIC_HOST] if snapshot == "latest" else []
    restic("restore", snapshot, "--target", target, *host, timeout=3600)
    return target / str(STAGING).lstrip("/")


def wait_postgres(c: str, user: str, db: str, timeout: int = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        logs = run(["docker", "logs", c], timeout=30)
        if "PostgreSQL init process complete" in logs.stdout + logs.stderr:
            if run(["docker", "exec", c, "pg_isready", "-U", user, "-d", db], timeout=30).returncode == 0:
                return
        time.sleep(2)
    raise TimeoutError(f"临时数据库 {c} 没起来")


def drill_one(log: Log, spec: dict, staged: Path, stamp: str) -> dict:
    manifest = json.loads((staged / f"{spec['key']}.manifest.json").read_text())
    image = manifest["image"]
    if run(["docker", "image", "inspect", image], timeout=30).returncode != 0:
        image = manifest["image_ref"]   # don't pull new images: if not present locally use the tag (usually present locally too)
    name = f"body-restore-drill-{spec['key']}-{stamp}"
    # no network, no published ports, --rm (also removes the anonymous volume): fully isolated from the production DBs
    run(["docker", "run", "-d", "--rm", "--name", name, "--network", "none", "--pull", "never",
         "-e", f"POSTGRES_USER={manifest['user']}", "-e", f"POSTGRES_DB={manifest['db']}",
         "-e", f"POSTGRES_PASSWORD={pysecrets.token_urlsafe(16)}", image], timeout=120, check=True)
    try:
        wait_postgres(name, manifest["user"], manifest["db"])
        run(["docker", "cp", staged / f"{spec['key']}.dump", f"{name}:/tmp/db.dump"], check=True, timeout=600)
        run(["docker", "cp", staged / f"{spec['key']}.globals.sql", f"{name}:/tmp/globals.sql"], check=True)
        # roles (SparkyFitness's sparky_app etc.); the superuser itself already exists, that error can be ignored
        run(["docker", "exec", name, "psql", "-U", manifest["user"], "-d", "postgres", "-q", "-f", "/tmp/globals.sql"],
            timeout=120)
        t0 = time.time()
        r = run(["docker", "exec", name, "pg_restore", "-U", manifest["user"], "-d", manifest["db"], "/tmp/db.dump"],
                timeout=3600)
        m = re.search(r"errors ignored on restore: (\d+)", r.stderr)
        restore_errors = int(m.group(1)) if m else (0 if r.returncode == 0 else -1)
        tables = [row[0] for row in psql(name, manifest["user"], manifest["db"], TABLES_SQL)]
        counts = {t: int(n) for t, n in psql(name, manifest["user"], manifest["db"], count_sql(tables), timeout=900)}
    finally:
        run(["docker", "rm", "-f", "-v", name], timeout=120)
    expected = manifest["counts"]
    diff = {t: (expected.get(t), counts.get(t)) for t in set(expected) | set(counts) if expected.get(t) != counts.get(t)}
    ok = not diff and restore_errors == 0
    log(f"{'✓' if ok else '✗'} {spec['title']}：{len(counts)}/{len(expected)} 张表，"
        f"{sum(counts.values()):,}/{manifest['rows']:,} 行一致，pg_restore 报错 {restore_errors} 个，"
        f"恢复用时 {time.time() - t0:.0f} 秒")
    for t, (a, b) in sorted(diff.items())[:20]:
        log(f"    {t}: 备份时 {a} 行，恢复后 {b} 行")
    if restore_errors:
        log(f"    pg_restore 输出：{r.stderr.strip()[-1500:]}")
    return {"ok": ok, "tables": len(counts), "rows": sum(counts.values()), "expected_rows": manifest["rows"],
            "mismatched_tables": len(diff), "restore_errors": restore_errors,
            "dumped_at": manifest["dumped_at"]}


def cmd_restore_drill(args) -> int:
    log = Log("restore-drill")
    log(f"—— 恢复演练：快照 {args.snapshot}（恢复到临时容器，不碰线上库）——")
    stamp = now().strftime("%Y%m%d%H%M%S")
    tmp = OPS_DIR / f"restore-drill-{stamp}"
    results: dict = {}
    ok = False
    try:
        staged = restore_snapshot(args.snapshot, tmp)
        for spec in DATABASES:
            try:
                results[spec["key"]] = drill_one(log, spec, staged, stamp)
            except Exception as e:  # noqa: BLE001
                results[spec["key"]] = {"ok": False, "error": str(e)[:300]}
                log(f"✗ {spec['title']}：{e}")
        expected = json.loads((staged / "secrets.manifest.json").read_text())
        sec_path = staged / "secrets.path"   # backup pulled from Ali: secrets are in ALI_MIRROR/secrets
        restored_dir = tmp / (sec_path.read_text().strip() if sec_path.exists() else str(SECRETS)).lstrip("/")
        got = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in restored_dir.iterdir() if p.is_file()}
        sec_ok = all(got.get(k) == v for k, v in expected.items())
        log(f"{'✓' if sec_ok else '✗'} secrets/：{sum(got.get(k) == v for k, v in expected.items())}/{len(expected)} 个文件"
            f"和备份时的校验和一致")
        results["secrets"] = {"ok": sec_ok, "files": len(expected)}
        ok = all(r.get("ok") for r in results.values())
    except Exception as e:  # noqa: BLE001
        log(f"✗ 演练失败：{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)   # delete the extracted plaintext (incl. secrets) when done
    save_state(restore_drill={"at": now().isoformat(), "ok": ok, "snapshot": args.snapshot, "results": results})
    log(f"—— 恢复演练{'成功' if ok else '失败'} ——")
    return 0 if ok else 1


def cmd_restore_extract(args) -> int:
    target = Path(args.target).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    staged = restore_snapshot(args.snapshot, target)
    print(f"已解到 {target}\n  数据库 dump、角色、行数清单：{staged}\n  secrets：{target / str(SECRETS).lstrip('/')}")
    print("下一步见 deploy/ops/README.md「真正恢复」。用完记得删掉这个目录（里面是明文）。")
    return 0


# ---------- doctor ----------

def disp_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - disp_width(s))


class Report:
    def __init__(self):
        self.rows: list = []

    def add(self, status: str, item: str, detail: str) -> None:
        self.rows.append((status, item, detail))

    def ok(self, item, detail=""): self.add("✓", item, detail)
    def warn(self, item, detail=""): self.add("⚠", item, detail)
    def fail(self, item, detail=""): self.add("✗", item, detail)

    @property
    def failures(self) -> list:
        return [f"{i}：{d}" for s, i, d in self.rows if s == "✗"]


def lan_ip() -> str | None:
    iface = re.search(r"interface: (\S+)", run(["route", "-n", "get", "default"], timeout=10).stdout)
    for name in ([iface.group(1)] if iface else []) + ["en0", "en1"]:
        ip = run(["ipconfig", "getifaddr", name], timeout=10).stdout.strip()
        if ip:
            return ip
    return None


def launchd_loaded(label: str) -> tuple:
    p = run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], timeout=10)
    if p.returncode != 0:
        return False, "没加载"
    state = re.search(r"\n\s*state = (\S+)", p.stdout)
    last = re.search(r"last exit code = (\S+)", p.stdout)
    return True, f"{state.group(1) if state else '?'}，上次退出码 {last.group(1) if last else '-'}"


def watchdog_checks(r: Report, state: dict) -> None:
    """Latest watchdog run and the latest abnormal Docker exit (time span, reason, how it recovered)."""
    wd = state.get("watchdog", {})
    if not wd.get("at"):
        r.fail("看门狗", "还没跑过（deploy/ops/install.sh 装 org.watch2whoop.body.watchdog）")
    else:
        age = time.time() - datetime.fromisoformat(wd["at"]).timestamp()
        if age > 10 * 60:
            r.fail("看门狗", f"上次运行 {fmt_age(age)}（应该每 3 分钟一次）")
        elif wd.get("failures"):
            r.fail("看门狗", f"连续 {wd['failures']} 次自动恢复失败：{wd.get('last_error', '')}")
        else:
            paused = WATCHDOG_PAUSE.exists()
            r.ok("看门狗", f"{fmt_age(age)}检查正常" + ("（暂停中）" if paused else ""))
    outages = wd.get("outages", [])
    if not outages:
        r.ok("Docker 非正常退出", "看门狗装上以后没有发现过")
        return
    o = outages[-1]
    start, end = datetime.fromisoformat(o["from"]), datetime.fromisoformat(o["to"])
    age = time.time() - end.timestamp()
    detail = (f"最近一次 {start:%m-%d %H:%M:%S}–{end:%H:%M:%S}（{fmt_age(age)}，共 {len(outages)} 次）；"
              f"{o['how']}；原因：{o['cause']}")
    (r.warn if age < 86400 else r.ok)("Docker 非正常退出", detail)


def check_backups(r: Report, state: dict, t_now: float) -> None:
    """Latest backup and restore drill (used when this machine is the primary server, or when backups are pulled from Ali)."""
    b = state.get("backup")
    if not b or not b.get("at"):
        r.fail("最近备份", "还没备份过")
    else:
        age = t_now - datetime.fromisoformat(b["at"]).timestamp()
        if not b.get("ok"):
            r.fail("最近备份", f"最近一次失败：{b.get('error', '')[:120]}（上次成功 {fmt_age(age)}）")
        elif age > 26 * 3600:
            r.fail("最近备份", f"{fmt_age(age)}（每晚应该有一次）")
        else:
            src = "，从 Ali 拉" if b.get("source") == "ali" else ""
            r.ok("最近备份", f"{fmt_age(age)}{src}，快照 {b['snapshot']}，共 {b['snapshots']} 个，仓库 {fmt_bytes(b['repo_bytes'])}")
    dr = state.get("restore_drill")
    if not dr:
        r.warn("恢复演练", "还没演练过：deploy/ops/ops.sh restore-drill")
    else:
        age = t_now - datetime.fromisoformat(dr["at"]).timestamp()
        if not dr["ok"]:
            r.fail("恢复演练", f"{fmt_age(age)}失败，看 health_data/ops/restore-drill-*.log")
        elif age > 90 * 86400:
            r.warn("恢复演练", f"{fmt_age(age)}，建议每季度演练一次")
        else:
            r.ok("恢复演练", f"{fmt_age(age)}成功")


def doctor_checks_ali_primary(r: Report) -> None:
    """When the primary server is Ali, this machine is only responsible for: pulling the backup from Ali into restic every night, the restore drill; and bringing back Ali's own doctor result.
    The local Docker services are stopped then (kept for rollback) and not checked."""
    t_now = time.time()
    p = ssh_ali(f"python3 {ALI_ROOT}/deploy/ali/doctor.py --json", timeout=300, check=False)
    line = next((l for l in reversed(p.stdout.splitlines()) if l.startswith("{")), "")
    if not line:
        r.fail("Ali", f"ssh {ALI_HOST} 跑 doctor 失败：{(p.stderr or p.stdout).strip()[-150:]}")
    else:
        d = json.loads(line)
        bad = [f"{st} {item}：{detail}" for st, item, detail in d["rows"] if st != "✓"]
        if d["fails"]:
            r.fail("Ali", f"{d['fails']} 项 ✗：" + "；".join(b for b in bad if b.startswith("✗"))[:300])
        elif d["warns"]:
            r.warn("Ali", f"{d['warns']} 项 ⚠：" + "；".join(bad)[:300])
        else:
            r.ok("Ali", f"{len(d['rows'])} 项全部正常（含 other-app、公网入口、证书）")
    check_backups(r, load_state(), t_now)
    path = RESTIC_REPO
    while not path.exists():
        path = path.parent
    du = shutil.disk_usage(path)
    (r.fail if du.free < 5 * 1024 ** 3 else r.ok)("磁盘（备份仓库）", f"剩 {fmt_bytes(du.free)} / {fmt_bytes(du.total)}")
    for job in ("pull-ali", "doctor"):
        loaded, detail = launchd_loaded(f"{LABEL_PREFIX}.{job}")
        (r.ok if loaded else r.fail)(f"LaunchAgent {job}", detail)


def doctor_checks(r: Report) -> None:
    if PRIMARY == "ali":
        return doctor_checks_ali_primary(r)
    # Docker
    info = run(["docker", "info", "--format", "{{json .}}"], timeout=30)
    if info.returncode != 0:
        r.fail("Docker", "守护进程连不上（Docker Desktop 没开？运行 deploy/ops/ops.sh start-all）")
        return
    d = json.loads(info.stdout)
    mem_total = d.get("MemTotal", 0)
    r.ok("Docker", f"{d.get('ServerVersion')}，VM {d.get('NCPU')} 核 {fmt_bytes(mem_total)}")

    # containers
    ps = run(["docker", "ps", "-a", "--format", "{{json .}}"], timeout=30).stdout.splitlines()
    by_key = {}
    for line in ps:
        c = json.loads(line)
        labels = dict(kv.split("=", 1) for kv in c.get("Labels", "").split(",") if "=" in kv)
        by_key[(labels.get("com.docker.compose.project"), labels.get("com.docker.compose.service"))] = c
    for project, (title, services) in EXPECTED_SERVICES.items():
        bad, policies = [], []
        for s in services:
            c = by_key.get((project, s))
            if not c:
                bad.append(f"{s} 不存在")
            elif c["State"] != "running":
                bad.append(f"{s} {c['State']}")
            elif "unhealthy" in c["Status"] or "health: starting" in c["Status"]:
                bad.append(f"{s} {c['Status']}")
            if c:
                pol = run(["docker", "inspect", "-f", "{{.HostConfig.RestartPolicy.Name}}", c["ID"]], timeout=30)
                if pol.stdout.strip() not in ("unless-stopped", "always"):
                    policies.append(f"{s}={pol.stdout.strip()}")
        if bad:
            r.fail(f"{title} 容器", f"{len(services) - len(bad)}/{len(services)} 在跑；{'、'.join(bad)}")
        elif policies:
            r.warn(f"{title} 容器", f"都在跑，但重启策略不是 unless-stopped：{'、'.join(policies)}")
        else:
            r.ok(f"{title} 容器", f"{len(services)}/{len(services)} 在跑，重启策略 unless-stopped")

    # ports
    closed = []
    for port, name in PORTS.items():
        try:
            socket.create_connection(("127.0.0.1", port), timeout=3).close()
        except OSError:
            closed.append(f"{port} {name}")
    if closed:
        r.fail("本机端口", f"连不上：{'、'.join(closed)}")
    else:
        r.ok("本机端口", f"{len(PORTS)}/{len(PORTS)} 可连（{' '.join(map(str, PORTS))}）")

    # Body Engine API
    code, body = http_get("http://127.0.0.1:8100/healthz")
    token_file = SECRETS / "body_engine_token_owner"
    if code != 200:
        r.fail("Body Engine API", f"/healthz → {code} {body[:80]}")
    elif token_file.exists():
        code2, me = http_get("http://127.0.0.1:8100/v1/me",
                             {"Authorization": f"Bearer {token_file.read_text().strip()}"})
        if code2 == 200:
            r.ok("Body Engine API", f"/healthz 正常，token 登录为 {json.loads(me).get('display_name', '?')}")
        else:
            r.fail("Body Engine API", f"/healthz 正常，但 /v1/me → {code2}")
    else:
        r.ok("Body Engine API", "/healthz 正常")

    # OW API
    code, body = http_get("http://127.0.0.1:8200/")
    key_file = SECRETS / "open_wearables_api_key"
    if code != 200:
        r.fail("Open Wearables API", f"/ → {code} {body[:80]}")
    else:
        code2, _ = http_get("http://127.0.0.1:8200/api/v1/users?limit=1",
                            {"X-Open-Wearables-API-Key": key_file.read_text().strip()} if key_file.exists() else {})
        (r.ok if code2 == 200 else r.fail)("Open Wearables API", f"运行中；API key 查询 → {code2}")

    # SparkyFitness
    code, body = http_get("http://127.0.0.1:8300/api/health")
    (r.ok if code == 200 and '"UP"' in body else r.fail)("SparkyFitness", f"/api/health → {code} {body[:40]}")

    # Grafana: itself + three datasources (a lost grant of the read-only role, a wrong password or an expired SparkyFitness key are all reported here)
    code, body = http_get(f"{GRAFANA_URL}/api/health")
    if code != 200 or '"ok"' not in body:
        r.fail("Grafana", f"/api/health → {code} {body[:60]}")
    else:
        auth = grafana_auth()
        bad = []
        for uid, title in GRAFANA_DATASOURCES.items():
            c2, b2 = http_get(f"{GRAFANA_URL}/api/datasources/uid/{uid}/health", auth, timeout=20)
            if c2 != 200 or '"OK"' not in b2:
                bad.append(f"{title} → {c2} {b2[:80]}")
        if bad:
            r.fail("Grafana 数据源", "；".join(bad) + "（数据库的看 deploy/grafana/README.md，重跑 bootstrap.py）")
        else:
            r.ok("Grafana", f"运行中，{len(GRAFANA_DATASOURCES)} 个数据源都能连（只读）")

    # Shortcut receiver: LAN IP:8765 (127.0.0.1:8765 is taken by another project) + local 127.0.0.1:8767 (used by tailscale serve 8446)
    loaded, detail = launchd_loaded(HEALTHSYNC_LABEL)
    ip = lan_ip()
    code, body = http_get(f"http://{ip}:8765/ping", timeout=5) if ip else (None, "没有局域网 IP")
    code2, body2 = http_get("http://127.0.0.1:8767/ping", timeout=5)
    if loaded and code == 200 and "health-sync ok" in body and code2 == 200 and "health-sync ok" in body2:
        r.ok("快捷指令接收器", f"http://{ip}:8765 和 127.0.0.1:8767 正常（LaunchAgent {detail}）")
    else:
        r.fail("快捷指令接收器", f"LaunchAgent {detail}；{ip}:8765/ping → {code}，127.0.0.1:8767/ping → {code2}"
                               "（8767 不通：接收器是旧版，重跑 health_sync/install.sh）")

    # The body butler runs on Ali since 2026-10-18 (systemd body-butler) and is checked by deploy/ali/doctor.py

    # Tailscale serve
    ts = run(["tailscale", "status", "--json"], timeout=20)
    if ts.returncode != 0:
        r.fail("Tailscale", f"tailscale status 失败：{ts.stderr.strip()[:100]}")
    else:
        st = json.loads(ts.stdout)
        dns = st.get("Self", {}).get("DNSName", "").rstrip(".")
        if st.get("BackendState") != "Running":
            r.fail("Tailscale", f"状态 {st.get('BackendState')}（打开 Tailscale 登录）")
        else:
            serve = json.loads(run(["tailscale", "serve", "status", "--json"], timeout=20).stdout or "{}")
            for port, (target, path, expect, title) in TAILSCALE_SERVE.items():
                proxy = serve.get("Web", {}).get(f"{dns}:{port}", {}).get("Handlers", {}).get("/", {}).get("Proxy")
                name = f"Tailscale {port}（{title}）"
                if proxy != target:
                    r.fail(name, f"serve 配置是 {proxy}，应为 {target}：tailscale serve --bg --https={port} {target}")
                    continue
                code, body = http_get(f"https://{dns}:{port}{path}", timeout=10)
                (r.ok if code == 200 and expect in body else r.fail)(name, f"https://{dns}:{port} → {code}")

    # data freshness
    try:
        c = body_db()
        sync = {(int(u), s): v for u, s, v in psql(c, "body", "body",
                "SELECT user_id, source, extract(epoch FROM last_synced_at) FROM sync_state "
                "WHERE last_synced_at IS NOT NULL")}
        scores = {int(u): v for u, v in psql(c, "body", "body",
                  "SELECT user_id, extract(epoch FROM max(computed_at)) FROM daily_scores GROUP BY 1")}
        users = list_users()
    except Exception as e:  # noqa: BLE001
        r.fail("Body 数据库查询", str(e)[:120])
        sync, scores, users = {}, {}, []
    t_now = time.time()
    latest = HEALTH / "health_latest.txt"
    if latest.exists():
        up_age = t_now - latest.stat().st_mtime
        (r.ok if up_age < 48 * 3600 else r.warn)(
            "最近快捷指令上传", f"{fmt_age(up_age)}（打开微信时自动上传；超过 2 天提示）")
        imp = sync.get((1, "shortcut"))
        log_tail = (HEALTH / "import.log").read_text(errors="replace")[-2000:] if (HEALTH / "import.log").exists() else ""
        if imp is None:
            r.fail("最近快捷指令导入", "从没导入过")
        else:
            imp_ts = float(imp)
            if latest.stat().st_mtime - imp_ts > 600:
                r.fail("最近快捷指令导入", f"上传后没导入（导入 {fmt_age(t_now - imp_ts)}），看 health_data/import.log")
            elif "Traceback" in log_tail.split("import-shortcut")[-1]:
                r.fail("最近快捷指令导入", "最近一次导入报错，看 health_data/import.log")
            else:
                r.ok("最近快捷指令导入", fmt_age(t_now - imp_ts))
    else:
        r.warn("最近快捷指令上传", "还没有 health_latest.txt")
    shortcut_freshness(r, t_now)
    for u in users:
        who = f"user {u['id']} {u['name']}"
        if u["ow"]:
            v = sync.get((u["id"], "ow"))
            age = t_now - float(v) if v else None
            if age is None or age > 3 * 3600:
                r.fail(f"OW 同步（{who}）", f"上次 {fmt_age(age) if age else '从没同步'}（每小时任务应该在跑）")
            else:
                r.ok(f"OW 同步（{who}）", fmt_age(age))
        if u["has_data"]:
            v = scores.get(u["id"])
            age = t_now - float(v) if v else None
            if age is None or age > 3 * 3600:
                r.fail(f"评分计算（{who}）", f"上次 {fmt_age(age) if age else '从没算过'}")
            else:
                r.ok(f"评分计算（{who}）", fmt_age(age))

    state = load_state()
    h = state.get("hourly")
    if not h:
        r.fail("每小时任务", "还没跑过")
    else:
        age = t_now - datetime.fromisoformat(h["at"]).timestamp()
        if not h["ok"] or age > 2 * 3600:
            r.fail("每小时任务", f"{fmt_age(age)}{'，失败：' + '; '.join(h['errors'])[:150] if not h['ok'] else ''}")
        else:
            r.ok("每小时任务", f"{fmt_age(age)}成功")

    check_backups(r, state, t_now)

    # disk
    for label, path in [("磁盘（系统盘）", Path.home()), ("磁盘（备份仓库）", RESTIC_REPO)]:
        while not path.exists():
            path = path.parent
        du = shutil.disk_usage(path)
        detail = f"剩 {fmt_bytes(du.free)} / {fmt_bytes(du.total)}（已用 {du.used / du.total:.0%}）"
        (r.fail if du.free < 20 * 1024 ** 3 else r.warn if du.free < 50 * 1024 ** 3 else r.ok)(label, detail)
        if label.endswith("仓库）") and os.stat(path).st_dev == os.stat(Path.home()).st_dev:
            status = "⚠" if r.rows[-1][0] == "✓" else r.rows[-1][0]
            r.rows[-1] = (status, label, detail + "；和系统盘是同一块盘，Mac 坏了备份也没了：加一份异地备份（见 README）")

    # Docker memory
    stats = run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}"], timeout=60).stdout.splitlines()
    used = 0.0
    units = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "kB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3}
    for line in stats:
        m = re.match(r"([\d.]+)(\w+)", line.strip())
        if m:
            used += float(m.group(1)) * units.get(m.group(2), 1)
    if mem_total:
        ratio = used / mem_total
        (r.fail if ratio > 0.9 else r.warn if ratio > 0.8 else r.ok)(
            "Docker 内存", f"容器共用 {fmt_bytes(used)} / {fmt_bytes(mem_total)}（{ratio:.0%}）")

    # scheduled jobs, autostart, sleep, time zone
    missing = []
    for job in ["hourly", "nightly", "doctor", "startup", "watchdog"]:
        loaded, _ = launchd_loaded(f"{LABEL_PREFIX}.{job}")
        if not loaded:
            missing.append(job)
    if missing:
        r.fail("LaunchAgent", f"没加载：{'、'.join(missing)}（运行 deploy/ops/install.sh）")
    else:
        r.ok("LaunchAgent", "hourly / nightly / doctor / startup / watchdog 都已加载")
    watchdog_checks(r, state)
    pm = run(["pmset", "-g", "custom"], timeout=10).stdout
    ac = re.search(r"AC Power:(.*?)(?:\n\S|\Z)", pm, re.S)
    sleep = re.search(r"\n\s*sleep\s+(\d+)", ac.group(1)) if ac else None
    if sleep and sleep.group(1) == "0":
        r.ok("Mac 睡眠", "插电时不会自动睡眠（合盖仍会睡）")
    else:
        r.fail("Mac 睡眠", f"插电时 {sleep.group(1) if sleep else '?'} 分钟后睡眠：系统设置 → 电池 → 选项 → 打开"
                         f"「使用电源适配器且显示器关闭时防止自动睡眠」")
    tz = os.path.realpath("/etc/localtime")
    (r.ok if tz.endswith(TZ_NAME) else r.warn)("Mac 时区", f"{tz.split('zoneinfo/')[-1]}（每晚任务按本机时间 03:30）")


def print_report(r: Report) -> None:
    w = max(disp_width(i) for _, i, _ in r.rows) + 2
    print(f"身体管理系统健康检查  {now():%Y-%m-%d %H:%M}（{TZ_NAME}）")
    print("─" * 72)
    for s, i, d in r.rows:
        print(f" {s}  {pad(i, w)}{d}")
    print("─" * 72)
    n_fail = sum(s == "✗" for s, _, _ in r.rows)
    n_warn = sum(s == "⚠" for s, _, _ in r.rows)
    if n_fail:
        print(f"✗ {n_fail} 项异常" + (f"，⚠ {n_warn} 项提示" if n_warn else ""))
    else:
        print("没有异常 ✓" + (f"（⚠ {n_warn} 项提示，不影响运行）" if n_warn else ""))


def cmd_doctor(args) -> int:
    r = Report()
    try:
        doctor_checks(r)
    except Exception as e:  # noqa: BLE001 doctor's own errors must be reported too
        r.fail("doctor 自身", f"{type(e).__name__}: {e}")
    if args.json:
        print(json.dumps([{"status": s, "item": i, "detail": d} for s, i, d in r.rows], ensure_ascii=False, indent=1))
    elif not os.environ.get("BODY_OPS_QUIET"):
        print_report(r)
    OPS_DIR.mkdir(parents=True, exist_ok=True)
    with open(OPS_DIR / "doctor-latest.txt", "w", encoding="utf-8") as f, redirect_stdout(f):
        print_report(r)
    fails = r.failures
    if args.notify:
        log = Log("doctor", echo=False)
        rotate_logs()
        log(f"{len(fails)} 项异常" + ("：" + " | ".join(fails) if fails else ""))
        prev = load_state().get("doctor", {})
        changed = sorted(fails) != sorted(prev.get("failures", []))
        stale = time.time() - prev.get("notified_at", 0) > 6 * 3600
        notified_at = prev.get("notified_at", 0)
        if fails and (changed or stale):
            notify("身体管理系统：有异常", f"{len(fails)} 项：" + "；".join(f.split("：")[0] for f in fails)[:200])
            notified_at = time.time()
        elif not fails and prev.get("failures"):
            notify("身体管理系统", "已恢复正常 ✓")
        save_state(doctor={"at": now().isoformat(), "failures": fails, "notified_at": notified_at})
    return 1 if fails else 0


# ---------- startup recovery ----------

def compose_up(log: Log, title: str, cmd: list, env: dict | None = None) -> bool:
    # --no-build / --pull never: do not build or pull images (pulling in Docker Desktop can hang); --wait waits until healthy
    p = run([*cmd, "up", "-d", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "300"],
            timeout=420, env=env)
    ok = p.returncode == 0
    out = (p.stderr or p.stdout).strip()
    started = sorted({m for m in re.findall(r"Container (\S+)\s+(?:Started|Recreated|Created)", out)})
    detail = (f"拉起了 {'、'.join(started)}" if started else "本来就都在跑") if ok else out[-800:]
    log(f"{'✓' if ok else '✗'} {title}：{detail}")
    return ok


def compose_cmd(project: str) -> tuple:
    """compose project -> (docker compose command prefix, extra environment variables). Keys match EXPECTED_SERVICES one to one."""
    sec = {"BODY_SECRETS_DIR": str(SECRETS)}
    return {"body": (["docker", "compose", "-f", ROOT / "compose.yaml"], None),
            "open-wearables": ([ROOT / "deploy/open-wearables/ow.sh"], None),
            "sparkyfitness": ([ROOT / "deploy/sparkyfitness/sf.sh"], sec),
            "grafana": ([ROOT / "deploy/grafana/gf.sh"], sec)}[project]


def compose_up_project(log: Log, project: str) -> bool:
    cmd, env = compose_cmd(project)
    return compose_up(log, EXPECTED_SERVICES[project][0], cmd, env)


# ---------- watchdog (every 3 minutes) ----------

WATCHDOG_PAUSE = OPS_DIR / "watchdog.pause"


def docker_up() -> bool:
    return run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=20).returncode == 0


def down_services() -> dict:
    """{project: [services not running]}; restarting / still-in-health-check ones do not count as down."""
    out = run(["docker", "ps", "-a", "--format", "{{json .}}"], timeout=30).stdout.splitlines()
    state = {}
    for line in out:
        c = json.loads(line)
        labels = dict(kv.split("=", 1) for kv in c.get("Labels", "").split(",") if "=" in kv)
        state[(labels.get("com.docker.compose.project"), labels.get("com.docker.compose.service"))] = c["State"]
    down = {}
    for project, (_, services) in EXPECTED_SERVICES.items():
        bad = [s for s in services if state.get((project, s)) not in ("running", "restarting")]
        if bad:
            down[project] = bad
    return down


def docker_procs() -> dict:
    """Docker Desktop processes {kind: [(pid, start time)]}: the UI (Docker Desktop), the engine (com.docker.backend,
    the main program of Docker.app), the VM (com.docker.virtualization)."""
    out = run(["ps", "-axo", "pid=,lstart=,command="], timeout=10, env={"LC_ALL": "C"}).stdout.splitlines()
    procs = {"ui": [], "backend": [], "vm": []}
    for line in out:
        m = re.match(r"\s*(\d+) (\w{3} \w{3} +\d+ [\d:]{8} \d{4}) (.*)", line)
        if not m:
            continue
        pid, started, cmd = int(m.group(1)), m.group(2), m.group(3)
        kind = ("ui" if "/Contents/MacOS/Docker Desktop" in cmd and "Helper" not in cmd else
                "backend" if "/Contents/MacOS/com.docker.backend" in cmd else
                "vm" if "/Contents/MacOS/com.docker.virtualization" in cmd else None)
        if kind:
            t = datetime.strptime(" ".join(started.split()), "%a %b %d %H:%M:%S %Y").replace(tzinfo=now().tzinfo)
            procs[kind].append((pid, t))
    return procs


def clear_stuck_docker(log: Log, down_for: timedelta, healthy_backend: datetime | None) -> str | None:
    """Docker stuck while quitting: on 2026-10-17, in two measurements, the VM stopped right after `quit`, but the UI and the engine took almost 10 minutes to exit;
    during that time Docker.app still counts as "running" and `open -a Docker` has no effect.
    How to recognise it: the daemon is unreachable, the VM is gone, and the engine is still the one from the last healthy check (same start time, not a freshly started instance).
    Wait 30 seconds first; if it is still there and the daemon has been unreachable for over 2 minutes, kill the old instance's engine and UI (the VM stopped long ago, container data is unaffected)."""
    procs = docker_procs()
    if procs["vm"] or not procs["backend"] or not healthy_backend:
        return None
    old = lambda: [pid for kind in ("backend", "ui") for pid, t in docker_procs()[kind]
                   if t <= healthy_backend + timedelta(minutes=2)]
    if min(t for _, t in procs["backend"]) > healthy_backend + timedelta(seconds=5):
        return None   # the engine was just started: Docker is coming up, leave it alone
    log(f"Docker 正在退出（虚拟机已停，旧实例的引擎 / 界面 {old()} 还在），等它退完")
    deadline = time.time() + 30
    while old() and time.time() < deadline:
        time.sleep(5)
    left = old()
    waited = down_for + timedelta(seconds=30)
    if not left or waited < timedelta(minutes=2):
        return None
    mins = int(waited.total_seconds() // 60)
    log(f"✗ Docker 卡在退出（守护进程已经连不上约 {mins} 分钟），结束旧实例的进程 {left}")
    global KILLED_AT
    KILLED_AT = now()
    run(["kill", "-TERM", *map(str, left)], timeout=10)
    deadline = time.time() + 20
    while old() and time.time() < deadline:
        time.sleep(2)
    if old():
        run(["kill", "-KILL", *map(str, old())], timeout=10)
        time.sleep(3)
    return f"结束卡在退出的旧 Docker 进程（卡了约 {mins} 分钟）"


KILLED_AT: datetime | None = None   # when the watchdog itself killed Docker processes: crash records after it are its own doing and not counted as the reason


def docker_backend_started() -> datetime | None:
    """Start time of the Docker Desktop engine (main com.docker.backend process): a change = Docker was restarted."""
    p = run(["ps", "-axo", "lstart=,command="], timeout=10, env={"LC_ALL": "C"})
    starts = []
    for line in p.stdout.splitlines():
        if line.rstrip().endswith("/Contents/MacOS/com.docker.backend"):
            try:
                starts.append(datetime.strptime(line[:24].strip(), "%a %b %d %H:%M:%S %Y"))
            except ValueError:
                pass
    return min(starts).replace(tzinfo=now().tzinfo) if starts else None


def docker_desktop_version() -> str | None:
    p = run(["defaults", "read", "/Applications/Docker.app/Contents/Info.plist", "CFBundleShortVersionString"],
            timeout=10)
    return p.stdout.strip() or None


def boot_time() -> datetime | None:
    m = re.search(r"sec = (\d+)", run(["sysctl", "-n", "kern.boottime"], timeout=10).stdout)
    return datetime.fromtimestamp(int(m.group(1)), now().tzinfo) if m else None


def docker_exit_cause(start: datetime, end: datetime, old_version: str | None) -> str:
    """Find why Docker went down. Read only places that trigger no privacy prompt: the macOS system log (/usr/bin/log),
    ~/Library/Logs/DiagnosticReports, the Docker.app version, the boot time.
    Docker Desktop's own logs are in ~/Library/Containers/com.docker.docker/Data/log/; reading them pops up "access data from other apps", so they are not read."""
    causes = []
    bt = boot_time()
    if bt and start <= bt <= end + timedelta(minutes=1):
        causes.append(f"Mac 重启过（{bt:%m-%d %H:%M} 开机）")
    ver = docker_desktop_version()
    if old_version and ver and ver != old_version:
        causes.append(f"Docker Desktop 从 {old_version} 更新到 {ver}")
    reports = Path.home() / "Library/Logs/DiagnosticReports"
    try:
        crashes = [f.name for f in reports.iterdir() if "docker" in f.name.lower()
                   and start.timestamp() - 60 <= f.stat().st_mtime <= end.timestamp() + 300]
    except OSError:
        crashes = []
    if crashes:
        causes.append("崩溃报告：" + "、".join(sorted(crashes)[:3]))
    # system log: from the last healthy check up to the recovery (if the watchdog itself killed processes, only look before that: a TERMed Docker process
    # also reports NSInternalInconsistencyException, which must not count as the crash reason); at most 30 minutes, longer is very slow
    hi = min(end + timedelta(minutes=1), KILLED_AT - timedelta(seconds=1)) if KILLED_AT else end + timedelta(minutes=1)
    lo = max(start, hi - timedelta(minutes=30))
    p = run(["/usr/bin/log", "show", "--style", "compact",
             "--start", f"{lo:%Y-%m-%d %H:%M:%S}", "--end", f"{hi:%Y-%m-%d %H:%M:%S}",
             "--predicate", '(process BEGINSWITH "com.docker" OR process == "Docker Desktop") AND '
                            '(messageType == fault OR eventMessage CONTAINS "Entering exit handler")'],
            timeout=180)
    exits, faults = {}, []
    for line in p.stdout.splitlines():
        m = re.match(r"\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d)\.\d+ (\w+)\s+([\w. ]+?)\[\d+", line)
        if not m:
            continue
        t, kind, proc = m.groups()
        if "Entering exit handler" in line:
            exits.setdefault(proc.replace("com.docker.", ""), t)
        elif kind == "F" and "FAULT:" in line:
            exc = re.search(r"FAULT: (\w+)", line)
            faults.append(f"{t} {proc}{'（' + exc.group(1) + '）' if exc else ''}")
    if exits:
        order = sorted(exits.items(), key=lambda kv: kv[1])
        causes.append(("Docker 进程退出" if faults else "Docker 进程正常退出（不是崩溃，像是被退出 / 重启）") + "："
                      + "、".join(f"{name} {t}" for name, t in order))
        if "backend" in exits and "virtualization" in exits:
            lag = (datetime.strptime(exits["backend"], "%H:%M:%S") -
                   datetime.strptime(exits["virtualization"], "%H:%M:%S")).total_seconds()
            if lag > 120:
                causes.append(f"引擎 com.docker.backend 比虚拟机晚 {int(lag // 60)} 分钟才退出（卡在退出，期间打不开 Docker）")
    if faults:
        causes.append("同时崩溃：" + "、".join(dict.fromkeys(f.split(' ', 1)[1] for f in faults[:6])) +
                      f"（{faults[0].split(' ')[0]} 起）")
    return "；".join(causes) if causes else "原因不明（系统日志里没找到 Docker 的退出 / 崩溃记录）"


def record_outage(log: Log, wd: dict, start: datetime, end: datetime, recovered: bool, how: str) -> dict:
    cause = docker_exit_cause(start, end, wd.get("version"))
    outage = {"from": start.isoformat(), "to": end.isoformat(), "seconds": round((end - start).total_seconds()),
              "recovered": recovered, "how": how, "cause": cause}
    wd["outages"] = (wd.get("outages", []) + [outage])[-20:]
    log(f"记录宕机：{start:%m-%d %H:%M:%S} – {end:%H:%M:%S}，{how}；原因：{cause}")
    return outage


def cmd_watchdog(args) -> int:
    """Every 3 minutes: the Docker daemon and each compose project. Open Docker if it is not running; compose up if a service is down;
    notify "recovered automatically" once fixed; notify after 3 consecutive failures to fix. `--pause MINUTES` pauses it (for maintenance)."""
    log = Log("watchdog", echo=not os.environ.get("BODY_OPS_QUIET"))
    if args.pause is not None:
        if args.pause <= 0:
            WATCHDOG_PAUSE.unlink(missing_ok=True)
            print("看门狗已恢复")
        else:
            OPS_DIR.mkdir(parents=True, exist_ok=True)
            WATCHDOG_PAUSE.write_text((now() + timedelta(minutes=args.pause)).isoformat())
            print(f"看门狗暂停到 {now() + timedelta(minutes=args.pause):%H:%M}（ops.sh watchdog --pause 0 提前恢复）")
        return 0
    if WATCHDOG_PAUSE.exists():
        try:
            until = datetime.fromisoformat(WATCHDOG_PAUSE.read_text().strip())
        except ValueError:
            until = now()
        if now() < until:
            log(f"暂停中（到 {until:%H:%M}），跳过")
            return 0
        WATCHDOG_PAUSE.unlink()
    try:
        lock = Lock(wait_seconds=0, name="services")
        lock.__enter__()
    except TimeoutError:
        log("start-all 正在跑，跳过")
        return 0
    state = load_state()
    wd = state.get("watchdog", {})
    t_start = now()
    last_ok = datetime.fromisoformat(wd["last_ok"]) if wd.get("last_ok") else None
    actions, ok = [], True
    try:
        was_down = not docker_up()
        if was_down:
            log(f"✗ Docker 守护进程连不上（上次正常 {last_ok:%H:%M:%S}）" if last_ok else "✗ Docker 守护进程连不上")
            prev_healthy = datetime.fromisoformat(wd["backend_started"]) if wd.get("backend_started") else None
            stuck = clear_stuck_docker(log, t_start - (last_ok or t_start), prev_healthy)
            if stuck:
                actions.append(stuck)
            procs = docker_procs()
            if not procs["ui"] and not procs["backend"]:
                log("Docker Desktop 没在运行，打开它")
            run(["open", "-a", "Docker"], timeout=30)
            actions.insert(0, "重新打开 Docker Desktop")
            deadline = time.time() + 240
            while not docker_up() and time.time() < deadline:
                time.sleep(5)
            if not docker_up():
                procs = docker_procs()
                raise RuntimeError("打开 Docker Desktop 后 4 分钟内守护进程还没起来（进程：界面 "
                                   f"{len(procs['ui'])}、引擎 {len(procs['backend'])}、虚拟机 {len(procs['vm'])}）")
            log("✓ Docker 守护进程已就绪")
        backend = docker_backend_started()
        prev_backend = datetime.fromisoformat(wd["backend_started"]) if wd.get("backend_started") else None
        # Docker restarted by itself between two checks (not caught by the watchdog, e.g. the 2026-10-17 18:56 one that was down for only half a minute)
        silent_restart = (not was_down and backend and prev_backend and backend > prev_backend + timedelta(seconds=5))
        down = down_services()
        if down:
            # a freshly started Docker restarts containers itself per unless-stopped; another session may be rebuilding containers: wait a bit before looking
            deadline = time.time() + (120 if was_down or silent_restart else 60)
            while down and time.time() < deadline:
                time.sleep(10)
                down = down_services()
        for project, services in down.items():
            log(f"✗ {EXPECTED_SERVICES[project][0]} 没在跑：{'、'.join(services)}，compose up")
            actions.append(f"拉起 {EXPECTED_SERVICES[project][0]}（{'、'.join(services)}）")
            ok &= compose_up_project(log, project)
        still = down_services()
        if still:
            raise RuntimeError("还有服务没起来：" + "；".join(f"{EXPECTED_SERVICES[k][0]} {'、'.join(v)}"
                                                       for k, v in still.items()))
        if was_down or silent_restart:
            start = last_ok or (backend - timedelta(minutes=5) if backend else t_start)
            end = now() if was_down else backend
            extra = actions[1:] if was_down else actions
            if was_down:
                how = "看门狗重新打开了 Docker Desktop"
            elif wd.get("failures"):
                how = f"看门狗之前 {wd['failures']} 次没修好（{wd.get('last_error', '')[12:]}），之后 Docker 重新起来了"
            else:
                how = "Docker Desktop 自己重启了"
            how += f"，{'；'.join(extra)}" if extra else ""
            outage = record_outage(log, wd, start, end, True, how)
            mins = max(1, round(outage["seconds"] / 60))
            notify("身体管理系统：已自动恢复",
                   f"Docker 在 {start:%H:%M}–{end:%H:%M} 停了（约 {mins} 分钟），{how}。{outage['cause'][:120]}")
        elif actions:
            notify("身体管理系统：已自动恢复", "；".join(actions))
        if actions:
            log("✓ 已自动恢复：" + "；".join(actions))
        backend = docker_backend_started() or backend
        wd.pop("last_error", None)
        wd.update(last_ok=now().isoformat(), failures=0, version=docker_desktop_version(),
                  backend_started=backend.isoformat() if backend else None)
        if actions or was_down or silent_restart:
            wd["last_recovery"] = {"at": now().isoformat(), "actions": actions}
    except Exception as e:  # noqa: BLE001
        ok = False
        wd["failures"] = wd.get("failures", 0) + 1
        wd["last_error"] = f"{now():%m-%d %H:%M} {e}"[:300]
        if not wd.get("down_since"):
            wd["down_since"] = (last_ok or t_start).isoformat()
        log(f"✗ 第 {wd['failures']} 次没修好：{e}")
        # notify after 3 consecutive failures (about 10 minutes), then remind hourly
        if wd["failures"] == 3 or (wd["failures"] > 3 and wd["failures"] % 20 == 3):
            notify("身体管理系统：自动恢复失败", f"连续 {wd['failures']} 次没修好：{str(e)[:150]}")
    else:
        if wd.pop("down_since", None):
            log("✓ 之前的故障已恢复")
    wd["at"] = now().isoformat()
    save_state(watchdog=wd)
    lock.__exit__()
    return 0 if ok else 1


def cmd_start_all(args) -> int:
    log = Log("start-all")
    rotate_logs()
    log("—— 拉起所有服务 ——")
    if run(["docker", "info"], timeout=30).returncode != 0:
        log("Docker 没在跑，打开 Docker Desktop")
        run(["open", "-a", "Docker"], timeout=30)
        deadline = time.time() + 600
        while run(["docker", "info"], timeout=30).returncode != 0:
            if time.time() > deadline:
                log("✗ 10 分钟内 Docker 没起来")
                notify_failure("开机拉起", "Docker Desktop 10 分钟内没起来")
                return 1
            time.sleep(10)
        log("✓ Docker 已就绪")
    ok = True
    with Lock(wait_seconds=600, name="services"):
        for project in EXPECTED_SERVICES:
            ok &= compose_up_project(log, project)
    loaded, _ = launchd_loaded(HEALTHSYNC_LABEL)
    plist = Path.home() / "Library/LaunchAgents" / f"{HEALTHSYNC_LABEL}.plist"
    if not loaded and plist.exists():
        p = run(["launchctl", "bootstrap", f"gui/{os.getuid()}", plist], timeout=30)
        log(f"{'✓' if p.returncode == 0 else '✗'} 重新加载快捷指令接收器 {p.stderr.strip()}")
    else:
        log(f"✓ 快捷指令接收器 LaunchAgent {'已加载' if loaded else '没安装（health_sync/install.sh）'}")
    if not args.no_doctor:
        time.sleep(5)
        cmd_doctor(argparse.Namespace(json=False, notify=not args.quiet_notify))
    if not ok:
        notify_failure("开机拉起", "有服务没起来，看 health_data/ops/start-all-*.log")
    log(f"—— {'全部拉起' if ok else '有服务没起来'} ——")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(prog="ops.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hourly").set_defaults(fn=cmd_hourly)
    sub.add_parser("nightly").set_defaults(fn=cmd_nightly)
    b = sub.add_parser("backup")
    b.add_argument("--from-ali", action="store_true",
                   help="primary server is Ali: have Ali dump the three databases now and pull them into restic here (the default when BODY_PRIMARY=ali)")
    b.add_argument("--drill", action="store_true", help="run a restore drill right after the backup")
    b.set_defaults(fn=cmd_backup)
    d = sub.add_parser("restore-drill")
    d.add_argument("--snapshot", default="latest")
    d.set_defaults(fn=cmd_restore_drill)
    e = sub.add_parser("restore-extract")
    e.add_argument("--target", required=True)
    e.add_argument("--snapshot", default="latest")
    e.set_defaults(fn=cmd_restore_extract)
    doc = sub.add_parser("doctor")
    doc.add_argument("--notify", action="store_true", help="send a macOS notification on problems (used by the LaunchAgent)")
    doc.add_argument("--json", action="store_true")
    doc.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("start-all")
    s.add_argument("--no-doctor", action="store_true")
    s.add_argument("--quiet-notify", action="store_true", help="do not send notifications from the final doctor run")
    s.set_defaults(fn=cmd_start_all)
    w = sub.add_parser("watchdog", help="every 3 minutes: automatically bring Docker and the compose projects back up if they are down")
    w.add_argument("--pause", type=int, metavar="MINUTES", help="pause the watchdog (for maintenance); 0 = resume immediately")
    w.set_defaults(fn=cmd_watchdog)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

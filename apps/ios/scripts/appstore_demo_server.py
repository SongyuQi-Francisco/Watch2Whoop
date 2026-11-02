#!/usr/bin/env python3
"""A throw-away Body Engine with SYNTHETIC data only, for the App Store screenshots (docs/appstore/README.md).

    python3 apps/ios/scripts/appstore_demo_server.py up     # prints one line of JSON: {"server": ..., "token": ...}
    python3 apps/ios/scripts/appstore_demo_server.py down

`up` starts a one-off Postgres container bound to 127.0.0.1:55433 (a name and port of its own, never the real database
on 5433), runs the migrations, imports 60 days of synthetic Apple Health data (quickstart/demo_data.py), adds synthetic
meals / strength sessions / weigh-ins (quickstart/demo_sparky.py), creates three made-up circle members and one circle
with a training feed, and serves it with uvicorn on 127.0.0.1:18199. Everything lives under the scratch directory
`$W2W_SHOTS_DIR` (default /tmp/w2w-appstore-shots), which `down` deletes together with the container. Nothing here talks
to a production server, and no secrets/ file of the repository is read: the database password and keys are generated.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ENGINE = ROOT / "body_engine"
WORK = Path(os.environ.get("W2W_SHOTS_DIR", "/tmp/w2w-appstore-shots"))
CONTAINER = "w2w-appstore-shots-db"
DB_PORT = 55433
API_PORT = int(os.environ.get("W2W_SHOTS_PORT", "18199"))
IMAGE = os.environ.get("W2W_SHOTS_PG_IMAGE", "postgres:17.11-alpine")
STATE = WORK / "state.json"


def local_timezone() -> str:
    if os.environ.get("TZ") and "/" in os.environ["TZ"]:
        return os.environ["TZ"].lstrip(":")
    target = os.path.realpath("/etc/localtime")
    return target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else "UTC"


# Rollout flags (body_engine/rollout/registry.py) that are on for everybody in a release; a fresh database has them all off,
# which would show the old layouts. Kill switches and the sync / on-phone compute flags stay off (legacy pull path).
UI_FLAGS = ["friends", "circle_feed", "circles_by_circle", "circle_prefs", "friend_card_v2", "avatars", "member_remarks",
            "sleep_detail", "training_day_v2", "muscle_load", "jarvis_rich", "jarvis_cards_v2", "jarvis_ui_v2", "jarvis_quick",
            "jarvis_orbit", "jarvis_dial", "trends_v2", "water", "meal_merge", "me_settings", "data_status", "sync_center",
            "notifications", "offline_mode", "pantry_view", "grocery_staples", "own_food_store", "own_food_read"]


# demo_sparky.py never commits (inside the quickstart container the connection autocommits); do the same here
AUTOCOMMIT = """from body_engine import db as _db
_orig = _db.connect
def _connect(*a, **k):
    c = _orig(*a, **k)
    c.autocommit = True
    return c
_db.connect = _connect
"""


def pretend_now(tz: str) -> str:
    """Local time the synthetic data runs up to. Before 10:00 last night's sleep would not be in the data yet (the Today
    page would show empty sleep and recovery rings), so pretend it is 10:00."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo(tz))
    return now.replace(hour=10, minute=0, second=0, microsecond=0, tzinfo=None).isoformat() if now.hour < 10 else now.replace(tzinfo=None).isoformat()


def env() -> dict[str, str]:
    e = dict(os.environ)
    for k in list(e):
        if k.startswith("BODY_"):
            del e[k]   # never inherit a real configuration
    e.update({
        "BODY_DB_HOST": "127.0.0.1", "BODY_DB_PORT": str(DB_PORT), "BODY_DB_NAME": "body", "BODY_DB_USER": "body",
        "BODY_DB_PASSWORD_FILE": str(WORK / "db_password"),
        "BODY_SECRETS_DIR": str(WORK / "secrets"),
        "BODY_HEALTH_DATA_DIR": str(WORK / "health"),
        "BODY_AI_MASTER_KEY_FILE": str(WORK / "secrets" / "ai_master_key"),
        "BODY_OW_URL": "http://127.0.0.1:9",   # nothing there: Open Wearables is not used (the CLI wants a key file)
        "BODY_OW_API_KEY_FILE": str(WORK / "secrets" / "ow_unused"),
        "BODY_PUBLIC_URL": f"http://127.0.0.1:{API_PORT}",
        "BODY_MAIL_MODE": "console",
    })
    return e


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, check=True, text=True, capture_output=kw.pop("capture", False), **kw)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"failed: {' '.join(cmd[:6])}\n{(e.stdout or '')[-1500:]}\n{(e.stderr or '')[-1500:]}\n")
        raise SystemExit(1)


def engine(*args: str, capture: bool = False) -> str:
    r = run(["uv", "run", "--quiet", "body-engine", *args], cwd=ENGINE, env=env(), capture=True)
    return r.stdout if capture else ""


def say(msg: str) -> None:
    print(f"==> {msg}", file=sys.stderr, flush=True)


def up() -> None:
    if STATE.exists():
        down(quiet=True)
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "secrets").mkdir(exist_ok=True)
    (WORK / "health" / "imports").mkdir(parents=True, exist_ok=True)
    password = secrets.token_hex(16)
    (WORK / "db_password").write_text(password)
    (WORK / "secrets" / "ow_unused").write_text("unused\n")
    say(f"starting Postgres ({IMAGE}) as {CONTAINER} on 127.0.0.1:{DB_PORT}")
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    run(["docker", "run", "-d", "--name", CONTAINER, "-e", f"POSTGRES_PASSWORD={password}", "-e", "POSTGRES_USER=body",
         "-e", "POSTGRES_DB=body", "-p", f"127.0.0.1:{DB_PORT}:5432", IMAGE], capture=True)
    for _ in range(60):
        r = subprocess.run(["docker", "exec", CONTAINER, "pg_isready", "-U", "body", "-d", "body"], capture_output=True)
        if r.returncode == 0:
            break
        time.sleep(1)
    else:
        raise SystemExit("Postgres did not become ready")
    time.sleep(2)   # pg_isready answers during the init restart
    tz = local_timezone()
    now = pretend_now(tz)
    say("migrations")
    engine("ai-key-init")
    engine("migrate")
    say("demo user + 60 days of synthetic Apple Health data")
    engine("user-add", "--name", "Demo", "--role", "owner", "--timezone", tz, "--birth-date", "1990-01-01",
           "--height-cm", "178", "--sex", "male")
    zip_path = WORK / "health" / "imports" / "demo-export.zip"
    run([sys.executable, str(ROOT / "quickstart" / "demo_data.py"), "--days", "60", "--tz", tz, "--now", now, "--out", str(zip_path)], stdout=sys.stderr)
    engine("import-apple-zip", "--user-id", "1", "--file", str(zip_path))
    zip_path.unlink()
    engine("journal-init", "--user-id", "1")
    engine("memory-init", "--user-id", "1")
    engine("set-sleep-goal", "--user-id", "1", "--bedtime", "23:00")
    say("synthetic meals, strength sessions, weigh-ins, circles")
    for flag in ["own_food_only", *UI_FLAGS]:
        engine("flags", "set", flag, "--state", "all", "--by", "appstore-shots")
    run(["uv", "run", "--quiet", "python", "-", "1"], cwd=ENGINE, env=env(),
        input=AUTOCOMMIT + (ROOT / "quickstart" / "demo_sparky.py").read_text(), capture=True)
    seed = ["uv", "run", "--quiet", "python", str(HERE / "appstore_seed_circles.py")]
    sys.stderr.write(run([*seed, "users"], cwd=ENGINE, env=env(), capture=True).stdout)   # users 2-4: made-up members
    for uid in (2, 3, 4):   # a month of synthetic data each, so the leaderboard has scores
        run([sys.executable, str(ROOT / "quickstart" / "demo_data.py"), "--days", "30", "--tz", tz, "--now", now, "--seed", str(uid),
             "--out", str(zip_path)], stdout=sys.stderr)
        engine("import-apple-zip", "--user-id", str(uid), "--file", str(zip_path))
        zip_path.unlink()
    sys.stderr.write(run([*seed, "circle"], cwd=ENGINE, env=env(), capture=True).stdout)
    engine("refresh-status", "--user-id", "1")
    engine("evaluate-rules", "--user-id", "1")
    token = engine("token-create", "--user-id", "1", "--name", "appstore-shots", capture=True).strip().splitlines()[-1].strip()
    say(f"serving on 127.0.0.1:{API_PORT}")
    log = open(WORK / "server.log", "w")
    proc = subprocess.Popen(["uv", "run", "--quiet", "uvicorn", "body_engine.api:app", "--host", "127.0.0.1",
                             "--port", str(API_PORT)], cwd=ENGINE, env=env(), stdout=log, stderr=log, start_new_session=True)
    import urllib.request
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}/healthz", timeout=2).read()
            break
        except Exception:
            time.sleep(1)
    else:
        raise SystemExit(f"the server did not start, see {WORK / 'server.log'}")
    STATE.write_text(json.dumps({"pid": proc.pid}))
    circle = subprocess.run(["docker", "exec", CONTAINER, "psql", "-U", "body", "-d", "body", "-Atc",
                             "SELECT id FROM circles WHERE NOT personal ORDER BY id LIMIT 1"], capture_output=True, text=True)
    print(json.dumps({"server": f"http://127.0.0.1:{API_PORT}", "token": token, "timezone": tz,
                      "circle_id": circle.stdout.strip()}))


def down(quiet: bool = False) -> None:
    if STATE.exists():
        try:
            os.killpg(json.loads(STATE.read_text())["pid"], signal.SIGTERM)
        except (ProcessLookupError, PermissionError, ValueError, KeyError):
            pass
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    shutil.rmtree(WORK, ignore_errors=True)
    if not quiet:
        say("demo server stopped, scratch directory deleted")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "up":
        up()
    elif cmd == "down":
        down()
    else:
        raise SystemExit(__doc__)

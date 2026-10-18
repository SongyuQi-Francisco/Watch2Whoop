#!/usr/bin/env python3
"""One-time Grafana setup (idempotent, safe to re-run): generate passwords, set passwords on the read-only roles of both databases, verify read-only access.

1. Generated under secrets/ (left untouched if they exist, mode 600):
   grafana_admin_password      password of the Grafana admin (user name admin)
   grafana_ro_body_password    password of grafana_ro in the Body Engine database
   grafana_ro_ow_password      password of grafana_ro in the Open Wearables database
2. Body Engine (body-db): the role and grants are created by migration 050_grafana_ro.sql (body-engine migrate; also run on container start),
   so here we only ALTER ROLE grafana_ro LOGIN PASSWORD.
3. Open Wearables: run ow_grafana_ro.sql (role + grants), then set the password. Re-run after an OW upgrade.
4. Verify: log in as grafana_ro over TCP with a password; it can read the granted tables, cannot read api_tokens / api_key, and INSERT and CREATE TABLE are both rejected.

Standard library only; databases are accessed through docker exec. Passwords go to psql via stdin, never in process arguments.
Usage: python3 deploy/grafana/bootstrap.py (the secrets directory defaults to secrets/ at the repo root; override with BODY_SECRETS_DIR)
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SECRETS = Path(os.environ.get("BODY_SECRETS_DIR", HERE.parents[1] / "secrets"))
DBS = [
    {"key": "body", "title": "Body Engine", "project": "body", "service": "body-db", "user": "body", "db": "body",
     "secret": "grafana_ro_body_password", "sql": None,
     "allowed": "daily_scores", "denied": "api_tokens", "write": "INSERT INTO sync_state (user_id, source) VALUES (1, 'grafana_test')"},
    {"key": "ow", "title": "Open Wearables", "project": "open-wearables", "service": "db", "user": "open-wearables",
     "db": "open-wearables", "secret": "grafana_ro_ow_password", "sql": HERE / "ow_grafana_ro.sql",
     "allowed": "data_point_series", "denied": "api_key",
     "write": "DELETE FROM series_type_definition WHERE false"},
]


def secret(name: str) -> str:
    path = SECRETS / name
    if not path.exists():
        SECRETS.mkdir(exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(24))   # alphanumeric only, no escaping needed
        print(f"生成了 {path}")
    return path.read_text().strip()


def container(project: str, service: str) -> str:
    out = subprocess.run(["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={project}",
                          "--filter", f"label=com.docker.compose.service={service}"],
                         capture_output=True, text=True, check=True).stdout.split()
    if not out:
        sys.exit(f"✗ {project}/{service} 的容器没在跑")
    return out[0]


def psql(c: str, user: str, db: str, sql: str, password: str | None = None, host: bool = False) -> subprocess.CompletedProcess:
    cmd, env = ["docker", "exec", "-i"], None
    if password:   # -e passes only the variable name; the value comes from the docker command environment, never in process arguments
        cmd += ["-e", "PGPASSWORD"]
        env = {**os.environ, "PGPASSWORD": password}
    cmd += [c, "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-At", "-U", user, "-d", db]
    if host:
        cmd += ["-h", "127.0.0.1"]
    return subprocess.run(cmd + ["-f", "-"], input=sql, capture_output=True, text=True, timeout=120, env=env)


def main() -> int:
    secret("grafana_admin_password")
    ok = True
    for spec in DBS:
        pw = secret(spec["secret"])
        c = container(spec["project"], spec["service"])
        if spec["sql"]:
            r = psql(c, spec["user"], spec["db"], spec["sql"].read_text())
            if r.returncode:
                sys.exit(f"✗ {spec['title']}：执行 {spec['sql'].name} 失败：{r.stderr.strip()}")
        exists = psql(c, spec["user"], spec["db"], "SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro'").stdout.strip()
        if exists != "1":
            sys.exit(f"✗ {spec['title']}：没有 grafana_ro 角色。先跑迁移：cd body_engine && uv run body-engine migrate")
        r = psql(c, spec["user"], spec["db"],
                 f"ALTER ROLE grafana_ro WITH LOGIN CONNECTION LIMIT 10 PASSWORD '{pw}';")
        if r.returncode:
            sys.exit(f"✗ {spec['title']}：设置密码失败：{r.stderr.strip()}")

        # Verify: TCP + password login (scram-sha-256, same as Grafana)
        def as_ro(sql: str) -> subprocess.CompletedProcess:
            return psql(c, "grafana_ro", spec["db"], sql, password=pw, host=True)

        checks = []
        r = as_ro(f"SELECT count(*) FROM {spec['allowed']}")
        checks.append((r.returncode == 0, f"能读 {spec['allowed']}（{r.stdout.strip() or r.stderr.strip()[:80]} 行）"))
        r = as_ro(f"SELECT 1 FROM {spec['denied']} LIMIT 1")
        checks.append((r.returncode != 0 and "permission denied" in r.stderr, f"读 {spec['denied']} 被拒"))
        r = as_ro(spec["write"])
        checks.append((r.returncode != 0 and ("read-only" in r.stderr or "permission denied" in r.stderr),
                       f"写被拒（{r.stderr.strip().splitlines()[0][:70] if r.stderr.strip() else '没报错！'}）"))
        r = as_ro("SET default_transaction_read_only = off; " + spec["write"])
        checks.append((r.returncode != 0 and "permission denied" in r.stderr,
                       f"关掉只读事务后写也被拒（{r.stderr.strip().splitlines()[0][:60] if r.stderr.strip() else '没报错！'}）"))
        r = as_ro("SET default_transaction_read_only = off; CREATE TABLE grafana_probe (x int);")
        checks.append((r.returncode != 0 and "permission denied" in r.stderr,
                       f"关掉只读事务后建表也被拒（{r.stderr.strip().splitlines()[0][:60] if r.stderr.strip() else '没报错！'}）"))
        for good, msg in checks:
            print(f"{'✓' if good else '✗'} {spec['title']} grafana_ro：{msg}")
            ok &= good
    print("完成。" if ok else "有检查没通过，看上面的 ✗。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

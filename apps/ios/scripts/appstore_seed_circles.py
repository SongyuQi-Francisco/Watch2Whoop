#!/usr/bin/env python3
"""Seed made-up circle members and one circle into the throw-away screenshot database (appstore_demo_server.py).

    uv run python apps/ios/scripts/appstore_seed_circles.py users     # three members (Ann, Ben, Cat), consent, flags
    uv run python apps/ios/scripts/appstore_seed_circles.py circle    # one circle, everybody joins, some training days

Run from body_engine/ with the BODY_* environment of the demo server. Refuses to run against anything but the demo
database port. Fictional names, synthetic training; nothing is read from a real account.
"""
from __future__ import annotations

import json
import sys

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import settings
from body_engine.friends import params as P
from body_engine.rollout import flags
from body_engine.web import views

MEMBERS = ("Ann", "Ben", "Cat")
CIRCLE = "Sunrise Club"


def h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


def users(conn) -> None:
    for name in MEMBERS:
        conn.execute("""INSERT INTO users (display_name, birth_date, height_cm, sex, timezone)
                        SELECT %s, '1992-04-12', 172, 'female', timezone FROM users WHERE id = 1""", (name,))
    conn.execute("""UPDATE users SET consent_at = now(), consent_version = %s, personal_circle_at = now()""", (P.CONSENT_VERSION,))
    for flag in ("friends", P.FEED_FLAG):
        flags.set_flag(conn, flag, "all", actor="appstore-shots")
    print("members:", [r["id"] for r in conn.execute("SELECT id FROM users ORDER BY id").fetchall()])


def circle(conn) -> None:
    from fastapi.testclient import TestClient

    ids = {r["display_name"]: r["id"] for r in conn.execute("SELECT id, display_name FROM users").fetchall()}
    tok = {n: auth.create_token(conn, i, "shots-seed", ["read", "write"]) for n, i in ids.items()}
    conn.commit()
    api.pool = ConnectionPool(settings.dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    client = TestClient(api.app)
    r = client.post("/v1/friends/circles", headers=h(tok["Demo"]), json={"name": CIRCLE, "scopes": list(P.DEFAULT_SCOPES)})
    assert r.status_code == 201, r.text
    code = r.json()["invite"]["code"]
    for n in MEMBERS:
        r = client.post("/v1/friends/join", headers=h(tok[n]), json={"code": code, "scopes": list(P.DEFAULT_SCOPES)})
        assert r.status_code == 200, r.text
    days = [(0, "barbell_bench_press", [(8, 62.5), (8, 62.5), (6, 65)]), (0, "barbell_back_squat", [(5, 90), (5, 90), (5, 92.5)]),
            (1, "conventional_deadlift", [(5, 110), (5, 110)])]
    for k, n in enumerate(("Demo",) + MEMBERS):
        today = views.local_today(conn, ids[n])
        for back, slug, sets in days[: 1 + k % 3]:
            day = today.fromordinal(today.toordinal() - back)
            ex = [{"slug": slug, "sets": [{"reps": r, "weight_kg": w + 2.5 * k, "done": True} for r, w in sets]}]
            conn.execute("""INSERT INTO training_session_logs (user_id, date, source, exercises) VALUES (%s, %s, 'page', %s::jsonb)
                            ON CONFLICT (user_id, date, source) DO UPDATE SET exercises = EXCLUDED.exercises""",
                         (ids[n], day, json.dumps(ex)))
    conn.commit()
    posts = client.get("/v1/friends/feed", headers=h(tok["Demo"]))
    print("circle ok; feed status", posts.status_code, "posts", len(posts.json().get("posts", [])) if posts.status_code == 200 else "-")
    api.pool.close()


if __name__ == "__main__":
    if settings.db_port != 55433:
        raise SystemExit("refusing to run: this is not the screenshot database (BODY_DB_PORT must be 55433)")
    with psycopg.connect(settings.dsn, row_factory=dict_row) as conn:
        {"users": users, "circle": circle}[sys.argv[1]](conn)
        conn.commit()

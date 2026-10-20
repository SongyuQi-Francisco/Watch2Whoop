"""Automatic provisioning after an invite code is redeemed: what the admin's onboarding script used to do by hand, as an
idempotent job. Every step checks what already exists first, so running it again only fills in what is missing.

Steps (state in user_provisioning.steps):
  ow       Open Wearables user (OW API key Body Engine already uses; external_user_id w2w-u<id>, searched first) → users.ow_user_id
  sparky   SparkyFitness account with a synthetic login (w2w-u<id>@<BODY_SPARKY_EMAIL_DOMAIN>, random password — not the
           person's e-mail) + a read-write and a read-only API key, stored encrypted with the AI master key (ai/crypto.py)
           → users.sparky_user_id. Needs SparkyFitness sign-up to be open (SPARKY_FITNESS_DISABLE_SIGNUP=false); keep its
           port private (127.0.0.1 / tailnet only). While sign-up is closed the step fails and is retried.
  journal  default journal items (records/defaults.py)
  memory   the butler's initial profile card and status card (no owner seed data)

Runs right after redemption (in the background of the request) and again from `body-engine provision-run`
(hourly job) for anything not finished. Failures never undo the activation: the person can use the app; the missing
parts appear once a retry succeeds.
"""
from __future__ import annotations

import http.cookiejar
import json
import logging
import os
import secrets
import urllib.error
import urllib.request

import psycopg
from psycopg.types.json import Jsonb

from ..ai import crypto
from ..ow_client import OWClient, OWError
from ..sparky_client import SPARKY_URL

log = logging.getLogger(__name__)

STEPS = ("ow", "sparky", "journal", "memory")
MAX_ATTEMPTS = 48   # hourly retries for two days, then the admin looks (provision-run --user-id N resets nothing, it just runs)


class SparkySignup:
    """Talks to SparkyFitness like a browser would (better-auth e-mail sign-up / sign-in, then API key generation)."""

    def __init__(self, base_url: str | None = None, origin: str | None = None, timeout: float = 60):
        self.base = (base_url or SPARKY_URL).rstrip("/")
        self.origin = origin or os.environ.get("BODY_SPARKY_ORIGIN") or "http://localhost:8300"
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Content-Type": "application/json", "Accept": "application/json", "Origin": self.origin})
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                text = r.read().decode()
                return r.status, (json.loads(text) if text else None)
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            try:
                return e.code, json.loads(text)
            except ValueError:
                return e.code, text

    def ensure_account(self, email: str, password: str, name: str) -> str:
        """Sign in (sign up first if needed); returns the SparkyFitness user id. The session stays in this object."""
        status, body = self.call("POST", "/api/auth/sign-in/email", {"email": email, "password": password})
        if status != 200:
            status, body = self.call("POST", "/api/auth/sign-up/email", {"email": email, "password": password, "name": name})
            if status != 200:
                raise RuntimeError(f"SparkyFitness sign-up {status}: {str(body)[:200]}")
            status, body = self.call("POST", "/api/auth/sign-in/email", {"email": email, "password": password})
            if status != 200:
                raise RuntimeError(f"SparkyFitness sign-in {status}: {str(body)[:200]}")
        return body["user"]["id"]

    def api_key(self, scope: str, name: str) -> str:
        status, res = self.call("POST", "/api/identity/user/generate-api-key", {"name": name, "expiresIn": None, "scope": scope})
        if status != 201:
            raise RuntimeError(f"SparkyFitness API key {status}: {str(res)[:200]}")
        return res["apiKey"]["key"]


def _ow_step(conn: psycopg.Connection, user: dict, ow_client: OWClient) -> None:
    if user["ow_user_id"]:
        return
    ext = f"w2w-u{user['id']}"
    found = ow_client.get("/users", external_user_id=ext)
    items = found.get("items") if isinstance(found, dict) else None
    if items:
        ow_id = items[0]["id"]
    else:
        ow_id = ow_client.request("POST", "/users", body={"first_name": user["display_name"][:100], "external_user_id": ext})["id"]
    conn.execute("UPDATE users SET ow_user_id = %s WHERE id = %s", (str(ow_id), user["id"]))


def _sparky_step(conn: psycopg.Connection, user: dict, sparky: SparkySignup) -> None:
    uid = user["id"]
    p = conn.execute("SELECT * FROM user_provisioning WHERE user_id = %s", (uid,)).fetchone()
    if user["sparky_user_id"] and (user["sparky_key_name"] or (p and p["sparky_key_ro_enc"] and p["sparky_key_rw_enc"])):
        return
    if p and p["sparky_password_enc"]:
        email, password = p["sparky_email"], crypto.decrypt(uid, p["sparky_password_enc"])
    else:
        domain = os.environ.get("BODY_SPARKY_EMAIL_DOMAIN") or "users.watch2whoop.invalid"
        email, password = f"w2w-u{uid}@{domain}", secrets.token_urlsafe(24)
        # Saved (and committed) before the account exists, so a retry signs into the same account instead of a new one
        conn.execute("UPDATE user_provisioning SET sparky_email = %s, sparky_password_enc = %s WHERE user_id = %s",
                     (email, crypto.encrypt(uid, password), uid))
        conn.commit()
    sparky_user_id = sparky.ensure_account(email, password, user["display_name"][:60])
    rw = sparky.api_key("full", "body-engine-rw")
    ro = sparky.api_key("read", "body-engine-ro")
    conn.execute("""UPDATE user_provisioning SET sparky_key_rw_enc = %s, sparky_key_ro_enc = %s WHERE user_id = %s""",
                 (crypto.encrypt(uid, rw), crypto.encrypt(uid, ro), uid))
    conn.execute("UPDATE users SET sparky_user_id = %s WHERE id = %s", (sparky_user_id, uid))


def _journal_step(conn: psycopg.Connection, user: dict) -> None:
    from ..records import defaults
    defaults.init_journal_items(conn, user["id"])


def _memory_step(conn: psycopg.Connection, user: dict) -> None:
    from ..assistant import memory, status
    memory.init_profile(conn, user["id"], None)
    status.init_status(conn, user["id"], with_seed=False)


def run(conn: psycopg.Connection, user_id: int, ow_client: OWClient | None = None,
        sparky: SparkySignup | None = None) -> dict:
    """Run every unfinished step for one active user; commits after each step. Returns the provisioning row (no secrets)."""
    conn.execute("INSERT INTO user_provisioning (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    conn.commit()
    prov = conn.execute("SELECT steps FROM user_provisioning WHERE user_id = %s", (user_id,)).fetchone()
    steps = dict(prov["steps"] or {})
    errors = []
    for step in STEPS:
        if steps.get(step) == "done":
            continue
        user = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
        if user is None or user["status"] != "active":
            raise ValueError(f"user {user_id} is not active")
        try:
            if step == "ow":
                _ow_step(conn, user, ow_client or OWClient(timeout=20))
            elif step == "sparky":
                _sparky_step(conn, user, sparky or SparkySignup(timeout=30))
            elif step == "journal":
                _journal_step(conn, user)
            else:
                _memory_step(conn, user)
            steps[step] = "done"
            conn.commit()
        except (OWError, RuntimeError, OSError, ValueError, KeyError, TypeError, psycopg.Error, crypto.MasterKeyMissing) as e:
            conn.rollback()
            steps[step] = f"error: {str(e)[:200]}"
            errors.append(f"{step}: {e}")
            log.warning("provision user %s step %s failed: %s", user_id, step, e)
    state = "done" if all(steps.get(s) == "done" for s in STEPS) else "failed"
    conn.execute("""UPDATE user_provisioning SET steps = %s, state = %s, attempts = attempts + 1, last_error = %s,
                    updated_at = now() WHERE user_id = %s""",
                 (Jsonb(steps), state, "; ".join(errors)[:1000] or None, user_id))
    conn.commit()
    return status(conn, user_id)


def status(conn: psycopg.Connection, user_id: int) -> dict:
    row = conn.execute("SELECT state, steps, attempts, updated_at FROM user_provisioning WHERE user_id = %s", (user_id,)).fetchone()
    if row is None:
        return {"state": "none", "steps": {}}
    return {"state": row["state"], "steps": {k: ("done" if v == "done" else "error") for k, v in (row["steps"] or {}).items()},
            "attempts": row["attempts"], "updated_at": row["updated_at"]}


def run_pending(conn: psycopg.Connection, ow_client: OWClient | None = None, sparky_factory=None) -> list[dict]:
    """Every active user whose provisioning is not done (and not given up): run it again."""
    rows = conn.execute("""SELECT p.user_id FROM user_provisioning p JOIN users u ON u.id = p.user_id
                           WHERE u.status = 'active' AND p.state <> 'done' AND p.attempts < %s ORDER BY p.user_id""",
                        (MAX_ATTEMPTS,)).fetchall()
    out = []
    for r in rows:
        res = run(conn, r["user_id"], ow_client, sparky_factory() if sparky_factory else None)
        out.append({"user_id": r["user_id"], **res})
    return out


def sparky_key(conn: psycopg.Connection, user_id: int, kind: str) -> str | None:
    """The provisioned SparkyFitness key of a self-signed-up user (kind ro / rw), or None (admin-onboarded users use files)."""
    row = conn.execute("SELECT sparky_key_ro_enc, sparky_key_rw_enc FROM user_provisioning WHERE user_id = %s",
                       (user_id,)).fetchone()
    blob = row and row["sparky_key_rw_enc" if kind == "rw" else "sparky_key_ro_enc"]
    return crypto.decrypt(user_id, blob) if blob else None

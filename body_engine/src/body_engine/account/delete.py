"""Self-service account deletion (App Store guideline 5.1.1(v)): everything the person has in Body Engine, Open Wearables
and SparkyFitness, plus their uploaded files. The deployer (role owner) cannot delete themselves this way.

Order: the external accounts first (they are found through ids stored on the user row), then every Body Engine row that
belongs to the user (tables with a foreign key to users or a user_id column, deleted in rounds because some foreign keys
have no ON DELETE CASCADE), then the user. A failure on an external system does not stop the deletion of the Body
Engine data; it is logged at ERROR with the external id and returned as `leftovers` so the admin can finish it.

SparkyFitness users are deleted through its admin API with an admin's full-access API key, read from the file named by
BODY_SPARKY_ADMIN_KEY_FILE (default secrets/sparkyfitness_api_key_owner_rw — the owner is SparkyFitness's admin).
Open Wearables users are deleted with Body Engine's OW API key.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import psycopg
from psycopg import sql

from ..config import REPO_ROOT, settings
from ..ow_client import OWClient, OWError
from ..sparky_client import SparkyClient, SparkyError
from ..friends import store as friends
from . import invites

log = logging.getLogger(__name__)


class CannotDelete(Exception):
    pass


def _sparky_admin() -> SparkyClient | None:
    path = Path(os.environ.get("BODY_SPARKY_ADMIN_KEY_FILE", REPO_ROOT / "secrets" / "sparkyfitness_api_key_owner_rw"))
    return SparkyClient(path.read_text().strip(), timeout=30) if path.exists() else None


def _delete_rows(conn: psycopg.Connection, user_id: int) -> int:
    refs = conn.execute(
        """SELECT format('%I.%I', n.nspname, c.relname) AS t, a.attname AS col FROM pg_constraint k
           JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
           JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
           WHERE k.contype = 'f' AND k.confrelid = 'public.users'::regclass AND array_length(k.conkey, 1) = 1""").fetchall()
    refs += conn.execute(
        """SELECT format('%I.%I', table_schema, table_name) AS t, column_name AS col FROM information_schema.columns
           WHERE table_schema = 'public' AND column_name = 'user_id' AND table_name <> 'users'""").fetchall()
    # kept, their user columns become NULL with the user: redemptions (counts, identity records), the codes this user owns
    # (revoked first: invites.revoke_owned) and rewards earned by someone else for inviting this user
    keep = {("public.invite_codes", "owner_user_id"), ("public.invite_rewards", "invitee_user_id")}
    pending = sorted({(r["t"], r["col"]) for r in refs if r["t"] != "public.invite_redemptions" and (r["t"], r["col"]) not in keep})
    for _ in range(8):
        failed = []
        for t, col in pending:
            try:
                with conn.transaction():
                    schema, table = t.split(".", 1)
                    conn.execute(sql.SQL("DELETE FROM {} WHERE {} = %s").format(
                        sql.Identifier(schema.strip('"'), table.strip('"')), sql.Identifier(col)), (user_id,))
            except psycopg.errors.ForeignKeyViolation:
                failed.append((t, col))
        if not failed:
            break
        pending = failed
    with conn.transaction():
        conn.execute("DELETE FROM users WHERE id = %s", (user_id,))
    return len({t for t, _ in refs})


def delete_account(conn: psycopg.Connection, user_id: int, ow_client: OWClient | None = None,
                   sparky_admin: SparkyClient | None = None) -> dict:
    user = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
    if user is None:
        raise CannotDelete("no such user")
    if user["role"] == "owner":
        raise CannotDelete("the deployer account cannot be deleted here")
    leftovers = []
    if user["sparky_user_id"]:
        try:
            admin = sparky_admin or _sparky_admin()
            if admin is None:
                raise SparkyError("no SparkyFitness admin key (BODY_SPARKY_ADMIN_KEY_FILE)")
            try:
                admin.request("DELETE", f"/admin/users/{user['sparky_user_id']}")
            except SparkyError as e:
                if "→ 404" not in str(e):
                    raise
        except (SparkyError, OSError) as e:
            leftovers.append({"system": "sparkyfitness", "id": user["sparky_user_id"], "error": str(e)[:200]})
    if user["ow_user_id"]:
        try:
            (ow_client or OWClient(timeout=30)).request("DELETE", f"/users/{user['ow_user_id']}")
        except OWError as e:
            if "→ 404" not in str(e):
                leftovers.append({"system": "open-wearables", "id": user["ow_user_id"], "error": str(e)[:200]})
        except OSError as e:
            leftovers.append({"system": "open-wearables", "id": user["ow_user_id"], "error": str(e)[:200]})
    for item in leftovers:
        log.error("account deletion of user %s: %s user %s not deleted: %s", user_id, item["system"], item["id"], item["error"])
    invites.revoke_owned(conn, user_id)
    friends.leave_all(conn, user_id)   # circles this person owned pass on to the next member instead of vanishing
    conn.commit()
    tables = _delete_rows(conn, user_id)
    conn.commit()
    files = []
    if user_id != 1:   # user 1's Shortcut uploads live directly in archive/, never delete those
        for sub in ("imports", "archive"):
            d = settings.health_data_dir / sub / f"u{user_id}"
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                files.append(f"{sub}/u{user_id}")
    return {"deleted": True, "tables": tables, "files": files, "leftovers": leftovers}


PENDING_DAYS = 7


def purge_pending(conn: psycopg.Connection, days: int = PENDING_DAYS) -> int:
    """Accounts that signed in but never redeemed an invite code are removed after `days` days (they hold no health data)."""
    rows = conn.execute("""SELECT id FROM users WHERE status = 'pending' AND role <> 'owner'
                           AND created_at < now() - make_interval(days => %s)""", (days,)).fetchall()
    for r in rows:
        _delete_rows(conn, r["id"])
        conn.commit()
    return len(rows)

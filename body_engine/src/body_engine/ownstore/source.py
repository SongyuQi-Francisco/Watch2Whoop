"""Which store a user's food / weight / workout logs are read from and written to (issue #252 phases 3 and 4).

- `own_food_read`: reads come from the own tables (ownstore/read.py), writes still go to SparkyFitness and are synced back
  (mirror.py runs whenever reads are own, even with own_food_store off, so the tables never fall behind).
- `own_food_only`: writes go only to the own tables (ownstore/write.py), reads too; SparkyFitness is not contacted.

Both only apply to a user whose latest real `sparky-migrate` checked out (own_store_migrations.ok), so nobody reads half a
copy. `own_food_only` also applies to a user without a SparkyFitness account (nothing to migrate: new sign-ups once
SparkyFitness is gone). A user who has an account but no ok migration stays on SparkyFitness until the migration is run.
Flags off: nothing changes (one cached flag lookup per call).
"""
import psycopg

READ_FLAG = "own_food_read"
ONLY_FLAG = "own_food_only"


def _flag(conn: psycopg.Connection, user_id: int, key: str) -> bool:
    from ..rollout import flags
    return flags.enabled(conn, user_id, key)


def migrated_ok(conn: psycopg.Connection, user_id: int) -> bool:
    row = conn.execute("SELECT ok FROM own_store_migrations WHERE user_id = %s ORDER BY ran_at DESC, id DESC LIMIT 1",
                       (user_id,)).fetchone()
    return bool(row and row["ok"])


def has_sparky_account(conn: psycopg.Connection, user_id: int) -> bool:
    row = conn.execute("SELECT sparky_key_name, sparky_user_id FROM users WHERE id = %s", (user_id,)).fetchone()
    return bool(row and (row["sparky_key_name"] or row["sparky_user_id"]))


def only(conn: psycopg.Connection, user_id: int) -> bool:
    """Writes go to the own tables only (and reads come from them)."""
    if not _flag(conn, user_id, ONLY_FLAG):
        return False
    return migrated_ok(conn, user_id) or not has_sparky_account(conn, user_id)


def reads_own(conn: psycopg.Connection, user_id: int) -> bool:
    """Reads come from the own tables."""
    if only(conn, user_id):
        return True
    return _flag(conn, user_id, READ_FLAG) and migrated_ok(conn, user_id)


def only_for_everyone(conn: psycopg.Connection) -> bool:
    """own_food_only is on for everyone (state all): new accounts no longer get a SparkyFitness account."""
    from ..rollout import flags
    row = flags.rows(conn).get(ONLY_FLAG)
    return bool(row and row["state"] == "all" and not row["min_consent_version"])

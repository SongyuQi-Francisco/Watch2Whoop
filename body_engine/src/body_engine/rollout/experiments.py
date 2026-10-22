"""A/B tests: every user in an experiment gets one stable variant; exposures are counted per user and local day.

- Variants and weights, e.g. control:50,new:50. The variant = the user's bucket (sha256 of "exp" + key + user_id, see
  flags.bucket) laid over the cumulative weights, so it never changes while the experiment runs and is independent of other
  experiments and of flag buckets. Changing the variants of an experiment that already has exposures is refused: use a new key.
- Optional cohort (only its members take part) and time window (starts_at / ends_at); outside it, or when stopped, variant()
  returns None and the caller shows the normal page.
- Exposure = the variant was actually shown: the page calls POST /v1/users/me/experiments/{key}/exposure when it renders it
  (or server code calls variant(..., expose=True)). Stored: experiment, user, local day, variant, count. No content.
- results(): users and exposures per variant. METRICS is where outcome comparisons plug in later (e.g. active days from
  account/stats.py): each entry is fn(conn, key, {variant: [user_id, …]}) -> {variant: value}.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

import psycopg
from psycopg.types.json import Jsonb

from .. import queries
from . import audit, flags

METRICS: dict[str, Callable[[psycopg.Connection, str, dict[str, list[int]]], dict[str, float]]] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_variants(spec: str) -> list[dict]:
    """"control:50,new:50" -> [{"name": "control", "weight": 50}, …]."""
    out = []
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        name, _, w = part.partition(":")
        try:
            weight = int(w or 1)
        except ValueError:
            raise ValueError(f"bad weight in {part!r}")
        out.append({"name": name.strip(), "weight": weight})
    _check_variants(out)
    return out


def _check_variants(vs: list[dict]) -> None:
    names = [v["name"] for v in vs]
    if len(vs) < 2 or len(set(names)) != len(names) or not all(names):
        raise ValueError("need at least two variants with distinct names")
    if any(v["weight"] <= 0 for v in vs):
        raise ValueError("weights must be positive")


def get(conn: psycopg.Connection, key: str) -> dict | None:
    return conn.execute("SELECT * FROM experiments WHERE key = %s", (key,)).fetchone()


def list_all(conn: psycopg.Connection) -> list[dict]:
    return conn.execute("SELECT * FROM experiments ORDER BY status, key").fetchall()


def start(conn: psycopg.Connection, key: str, variants: list[dict], *, cohort: str | None = None, days: int | None = None,
          description: str | None = None, actor: str | None = None) -> dict:
    """Create and run an experiment, or restart a stopped one (same variants)."""
    _check_variants(variants)
    before = get(conn, key)
    if before and before["variants"] != variants and conn.execute(
            "SELECT 1 FROM experiment_exposures WHERE experiment_key = %s LIMIT 1", (key,)).fetchone():
        raise ValueError(f"experiment {key} already has exposures with other variants; use a new key")
    ends = _now() + timedelta(days=days) if days else None
    after = conn.execute(
        """INSERT INTO experiments (key, description, variants, cohort, status, starts_at, ends_at, updated_by)
           VALUES (%s, %s, %s, %s, 'running', now(), %s, %s)
           ON CONFLICT (key) DO UPDATE SET description = COALESCE(EXCLUDED.description, experiments.description),
               variants = EXCLUDED.variants, cohort = EXCLUDED.cohort, status = 'running', starts_at = now(),
               ends_at = EXCLUDED.ends_at, updated_at = now(), updated_by = EXCLUDED.updated_by
           RETURNING *""", (key, description, Jsonb(variants), cohort, ends, audit.actor(actor))).fetchone()
    audit.log(conn, audit.actor(actor), "experiment.start", key, flags._plain(before), flags._plain(after))
    return after


def stop(conn: psycopg.Connection, key: str, actor: str | None = None) -> bool:
    before = get(conn, key)
    after = conn.execute("""UPDATE experiments SET status = 'stopped', updated_at = now(), updated_by = %s
                            WHERE key = %s AND status = 'running' RETURNING *""", (audit.actor(actor), key)).fetchone()
    if after:
        audit.log(conn, audit.actor(actor), "experiment.stop", key, flags._plain(before), flags._plain(after))
    return after is not None


def _running(row: dict) -> bool:
    now = _now()
    return row["status"] == "running" and row["starts_at"] <= now and (row["ends_at"] is None or now < row["ends_at"])


def _pick(row: dict, user_id: int) -> str:
    total = sum(v["weight"] for v in row["variants"])
    point = flags.bucket("exp", row["key"], user_id) / 100 * total
    for v in row["variants"]:
        point -= v["weight"]
        if point < 0:
            return v["name"]
    return row["variants"][-1]["name"]


def _variant_for(conn: psycopg.Connection, row: dict, user_id: int) -> str | None:
    if not _running(row):
        return None
    if row["cohort"] and row["cohort"] not in flags.cohorts_of(conn, user_id):
        return None
    return _pick(row, user_id)


def variant(conn: psycopg.Connection, user_id: int, key: str, expose: bool = False) -> str | None:
    """This user's variant, or None (not running, not in the cohort, unknown key)."""
    row = get(conn, key)
    v = _variant_for(conn, row, user_id) if row else None
    if v and expose:
        record_exposure(conn, user_id, key, v)
    return v


def assignments(conn: psycopg.Connection, user_id: int) -> dict[str, str]:
    """{key: variant} of every running experiment this user takes part in (for /v1/web/me)."""
    out = {}
    for row in conn.execute("SELECT * FROM experiments WHERE status = 'running'"):
        if v := _variant_for(conn, row, user_id):
            out[row["key"]] = v
    return out


def record_exposure(conn: psycopg.Connection, user_id: int, key: str, v: str) -> None:
    conn.execute("""INSERT INTO experiment_exposures (experiment_key, user_id, day, variant) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (experiment_key, user_id, day, variant)
                    DO UPDATE SET exposures = experiment_exposures.exposures + 1, last_at = now()""",
                 (key, user_id, queries.local_today(conn, user_id), v))


def results(conn: psycopg.Connection, key: str) -> dict:
    row = get(conn, key)
    if row is None:
        raise ValueError(f"no experiment {key}")
    counts = {r["variant"]: r for r in conn.execute(
        """SELECT variant, count(DISTINCT user_id) AS users, sum(exposures) AS exposures, array_agg(DISTINCT user_id) AS ids
           FROM experiment_exposures WHERE experiment_key = %s GROUP BY variant""", (key,))}
    variants = [{"name": v["name"], "weight": v["weight"], "users": counts.get(v["name"], {}).get("users", 0),
                 "exposures": int(counts.get(v["name"], {}).get("exposures") or 0)} for v in row["variants"]]
    by_variant = {name: list(c["ids"]) for name, c in counts.items()}
    return {"key": key, "status": row["status"], "cohort": row["cohort"], "starts_at": row["starts_at"],
            "ends_at": row["ends_at"], "variants": variants,
            "metrics": {name: fn(conn, key, by_variant) for name, fn in METRICS.items()}}

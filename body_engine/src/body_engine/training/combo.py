"""Combination plan items (FB-21): a plan item written "A + B" is a superset of the exercises A and B.

The weekly plan used to keep such a name as one unknown exercise: nothing in the library matched it, so the training card
could not show its sets, weights or restrictions (and a plan from the SparkyFitness side lost it altogether). Now an item
whose name joins known exercises with `+` is split into its parts, each resolved through the catalog like any single
exercise (so a banned or careful part is flagged exactly as it would be alone), and the parts share one `group` id
("ss1", "ss2" ... per day) that the training card draws as one superset.

- `+` / `＋` / `&` / `＆` join the parts, and a name is split only when EVERY part is a known exercise. A name that only
  looks like a pair ("面拉 + 外旋" is one exercise; "5+3"; a part nothing matches) stays one item, which the card shows as
  "not recognised" with replacement candidates, instead of becoming a half-known superset.
- `/` never splits: real exercise names contain it ("杠铃提拉 / 直立划船").
- A name the catalog knows as a whole is one exercise even if it contains a `+` ("绳索面拉 + 外旋").
- At most params.COMBO_MAX_PARTS parts (a giant set); more is not read as a combination.
- Every part gets the item's sets / reps / rest / note; the item's `weight_kg` is dropped (one weight cannot fit two
  exercises; the recommended weight is looked up per exercise).
The split happens when a plan is proposed / saved and, for plans saved before this existed, whenever the active plan is read
(plans._active_plan_row), so an old "A + B" item is fixed without a data migration.
"""
from __future__ import annotations

import re

import psycopg

from . import catalog, params as P

_SEP = re.compile(r"\s*[+＋&＆]\s*")
_MARKS = re.compile(r"[+＋&＆]")


def name_of(item) -> str:
    """The exercise name a plan item carries (the keys plans._normalize_item reads; a built-in template tuple has none)."""
    if isinstance(item, dict):
        return str(item.get("slug") or item.get("exercise") or item.get("name") or "")
    return ""


def _known(conn: psycopg.Connection, user_id: int | None, name: str) -> bool:
    return catalog.resolve_with_candidates(conn, user_id, name)[0] is not None


def parts_of(conn: psycopg.Connection, user_id: int | None, name: str | None) -> list[str] | None:
    """The exercise names of a combination "A + B", or None when `name` is a single exercise (or not recognised as a pair)."""
    name = (name or "").strip()
    if not _MARKS.search(name) or _known(conn, user_id, name):
        return None
    parts = [p.strip() for p in _SEP.split(name) if p.strip()]   # "引体向上 8+" -> one part: not a combination
    if not 2 <= len(parts) <= P.COMBO_MAX_PARTS or not all(_known(conn, user_id, p) for p in parts):
        return None
    return parts


def expand_day(conn: psycopg.Connection, user_id: int | None, items: list, normalize) -> list:
    """One day's plan items with every combination replaced by its parts: `normalize(conn, user_id, raw)` items (plans.
    _normalize_item) that share a new `group` id, unused by the day's other items. Other items are returned as they are."""
    used = {str(it["group"]) for it in items if isinstance(it, dict) and it.get("group")}
    out, n = [], 0
    for it in items:
        parts = parts_of(conn, user_id, name_of(it))
        if not parts:
            out.append(it)
            continue
        n += 1
        while f"ss{n}" in used:
            n += 1
        gid = f"ss{n}"
        used.add(gid)
        out += [normalize(conn, user_id, {**it, "slug": part, "group": gid, "weight_kg": None}) for part in parts]
    return out


def expand_days(conn: psycopg.Connection, user_id: int | None, days: list[dict], normalize) -> list[dict]:
    """expand_day for every day of a stored plan (the days are copied; the stored row is not changed)."""
    return [{**d, "exercises": expand_day(conn, user_id, d.get("exercises") or [], normalize)} for d in days or []]

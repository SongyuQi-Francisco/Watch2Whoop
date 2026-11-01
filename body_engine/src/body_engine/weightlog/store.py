"""weight_context: the conditions of the day's weigh-in (one row per user and day, like body_measurements)."""
from datetime import date, datetime, timedelta

import psycopg

from . import params as P


def clean_tags(tags) -> list[str]:
    """Known keys only, no duplicates, in chip order; two exclusive ones (clothed / unclothed) are refused."""
    wanted = set(tags or [])
    unknown = sorted(wanted - set(P.CONTEXT_TAGS))
    if unknown:
        raise ValueError(f"未知的测量情况：{', '.join(unknown)}（可选：{', '.join(P.CONTEXT_TAGS)}）")
    for group in P.EXCLUSIVE:
        if len(wanted & set(group)) > 1:
            raise ValueError(f"{' / '.join(P.CONTEXT_TAGS[k] for k in group)} 只能选一个")
    return [k for k in P.CONTEXT_TAGS if k in wanted]


def clean_note(note) -> str | None:
    s = (note or "").strip()
    if len(s) > P.MAX_NOTE:
        raise ValueError(f"备注最多 {P.MAX_NOTE} 个字")
    return s or None


def save(conn: psycopg.Connection, user_id: int, day: date, *, tags=None, note=None, measured_at: datetime | None = None) -> None:
    """Set the day's conditions. None = leave that part as it is; [] / "" clears it. A row with nothing left is removed."""
    if tags is None and note is None and measured_at is None:
        return
    cur = conn.execute("SELECT tags, note, measured_at FROM weight_context WHERE user_id = %s AND date = %s", (user_id, day)).fetchone()
    new_tags = clean_tags(tags) if tags is not None else (cur["tags"] if cur else [])
    new_note = clean_note(note) if note is not None else (cur["note"] if cur else None)
    new_at = measured_at if measured_at is not None else (cur["measured_at"] if cur else None)
    if not new_tags and not new_note:
        conn.execute("DELETE FROM weight_context WHERE user_id = %s AND date = %s", (user_id, day))
        return
    conn.execute("""INSERT INTO weight_context (user_id, date, tags, note, measured_at) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, date) DO UPDATE SET tags = EXCLUDED.tags, note = EXCLUDED.note,
                      measured_at = EXCLUDED.measured_at, updated_at = now()""",
                 (user_id, day, new_tags, new_note, new_at))


def delete(conn: psycopg.Connection, user_id: int, day: date) -> None:
    conn.execute("DELETE FROM weight_context WHERE user_id = %s AND date = %s", (user_id, day))


def in_range(conn: psycopg.Connection, user_id: int, start: date, end: date) -> dict[str, dict]:
    """{date: {tags, note, measured_at}} for the days that have conditions."""
    rows = conn.execute("SELECT date, tags, note, measured_at FROM weight_context WHERE user_id = %s AND date BETWEEN %s AND %s",
                        (user_id, start, end)).fetchall()
    return {r["date"].isoformat(): {"tags": list(r["tags"]), "note": r["note"], "measured_at": r["measured_at"]} for r in rows}


def for_ai(ctx: dict | None, tz=None) -> dict:
    """The fields the tools add to a weigh-in: ctx = Chinese labels joined with 、, note, at = HH:MM local; empty ones left out."""
    if not ctx:
        return {}
    out = {}
    if ctx["tags"]:
        out["ctx"] = "、".join(P.CONTEXT_TAGS.get(k, k) for k in ctx["tags"])
    if ctx["note"]:
        out["note"] = ctx["note"]
    if ctx.get("measured_at"):
        at = ctx["measured_at"].astimezone(tz) if tz else ctx["measured_at"]
        out["at"] = at.strftime("%H:%M")
    return out


def window(today: date) -> tuple[date, date]:
    return today - timedelta(days=P.RECENT_WINDOW_DAYS), today

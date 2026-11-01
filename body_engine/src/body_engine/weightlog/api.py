"""GET /v1/users/{user}/weight/recent: the latest weigh-ins with their conditions, for the weigh-in sheet on the Today page.
Weights come from SparkyFitness (the source of truth while the double write runs), from the own table when that is not reachable.
Only for oneself."""
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query

from .. import sparky_client
from ..assistant import sparky_write
from ..records.api import _self, current_user, get_conn
from . import params as P, store

router = APIRouter(prefix="/v1/users/{user}", tags=["weight"])


def recent(conn, uid: int, limit: int) -> list[dict]:
    today = sparky_write.user_today(conn, uid)
    start, end = store.window(today)
    try:
        rows = [r for r in sparky_client.weight(sparky_client.reader_for_user(conn, uid), start, end) if "weight_kg" in r]
    except sparky_client.SparkyError:
        rows = [{"date": r["date"].isoformat(), "weight_kg": r["weight_kg"]} for r in conn.execute(
            "SELECT date, weight_kg FROM body_measurements WHERE user_id = %s AND date BETWEEN %s AND %s AND weight_kg IS NOT NULL ORDER BY date",
            (uid, start, end)).fetchall()]
    ctx = store.in_range(conn, uid, start, end)
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (uid,)).fetchone()["timezone"])
    out = []
    for r in sorted(rows, key=lambda r: r["date"], reverse=True)[:limit]:
        c = ctx.get(r["date"])
        out.append({"date": r["date"], "weight_kg": r["weight_kg"], "tags": c["tags"] if c else [], "note": c["note"] if c else None,
                    "at": c["measured_at"].astimezone(tz).strftime("%H:%M") if c and c["measured_at"] else None})
    return out


@router.get("/weight/recent")
def weight_recent(user: str, limit: int = Query(P.RECENT_DEFAULT, ge=1, le=P.RECENT_MAX), conn=Depends(get_conn), viewer=Depends(current_user)):
    uid = _self(viewer, user)
    return {"items": recent(conn, uid, limit)}

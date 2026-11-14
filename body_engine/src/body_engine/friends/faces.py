"""How a person appears in a small "who" row (UI 2.0.1 unread sources): name (the viewer's private remark first, a
moderation-hidden profile masked), its initial and the picture when the viewer may see it (avatars/urls.py: circle
co-members only). Only for people the caller already decided the viewer may know about."""
from __future__ import annotations

import psycopg

from ..avatars import urls as avatar_urls
from . import blocks, remarks


def initial(name: str | None) -> str:
    return (name or "").strip()[:1].upper() or "?"


def faces(conn: psycopg.Connection, viewer_id: int, ids) -> dict[int, dict]:
    ids = sorted({int(i) for i in ids if i is not None})
    if not ids:
        return {}
    rows = [dict(r) for r in conn.execute("SELECT id, display_name FROM users WHERE id = ANY(%s)", (ids,))]
    blocks.mask_rows(conn, viewer_id, rows)
    avatars = avatar_urls.urls_for(conn, viewer_id, ids)
    nick = remarks.for_viewer(conn, viewer_id, ids)
    out = {}
    for r in rows:
        name = nick.get(r["id"]) or r["display_name"] or ""
        out[r["id"]] = {"id": r["id"], "name": name, "initial": initial(name),
                        **({"avatar": avatars[r["id"]]} if r["id"] in avatars else {})}
    return out

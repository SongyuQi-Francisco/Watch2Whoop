"""Idempotent writes by client_id (table client_writes): a resend gets the first answer instead of writing again.

Used by POST /v1/sync/push and, during the move to it, by the record endpoints that take a client_id (journal, meal
template / recipe logging, the training day). Only successful answers are kept: a rejected or conflicting write can be
sent again after the user decided. A client_id belongs to one scope; reusing it for another write is a 422.
"""
from __future__ import annotations

import psycopg
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from psycopg.types.json import Jsonb

from ..i18n import L
from . import params as P


def check_id(client_id: str | None) -> str | None:
    if client_id is None:
        return None
    client_id = client_id.strip()
    if not client_id or len(client_id) > P.CLIENT_ID_MAX:
        raise HTTPException(422, L("client_id 不对（1–64 个字符）", "Bad client_id (1–64 characters)"))
    return client_id


def stored(conn: psycopg.Connection, uid: int, client_id: str, scope: str) -> dict | None:
    """{"status", "response"} of an earlier write with this client_id, or None."""
    r = conn.execute("SELECT scope, status, response FROM client_writes WHERE user_id = %s AND client_id = %s",
                     (uid, client_id)).fetchone()
    if r is None:
        return None
    if r["scope"] != scope:
        raise HTTPException(422, L("这个 client_id 已经用在别的写操作上了", "This client_id was already used for another write"))
    return {"status": r["status"], "response": r["response"]}


def remember(conn: psycopg.Connection, uid: int, client_id: str, scope: str, status: int, response) -> None:
    """Inside the caller's transaction (commits with the write it belongs to)."""
    conn.execute("""INSERT INTO client_writes (user_id, client_id, scope, status, response) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, client_id) DO NOTHING""",
                 (uid, client_id, scope, status, Jsonb(jsonable_encoder(response))))


def once(conn: psycopg.Connection, uid: int, client_id: str | None, scope: str, fn, status: int = 200):
    """Run fn() unless this client_id already wrote: then answer what it answered (same status code). HTTP errors from
    fn pass through and are not kept."""
    client_id = check_id(client_id)
    if client_id is None:
        return fn()
    prev = stored(conn, uid, client_id, scope)
    if prev is not None:
        if prev["status"] == 204:
            return Response(status_code=204, headers={"X-Duplicate": "1"})
        return JSONResponse(prev["response"], status_code=prev["status"], headers={"X-Duplicate": "1"})
    out = fn()
    if isinstance(out, Response):
        return out
    remember(conn, uid, client_id, scope, status, out)
    conn.commit()
    return out

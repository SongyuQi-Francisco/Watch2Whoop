"""AI feedback queue (docs/ai-feedback-plan.md §2, migration 340): report → sanitise → dedupe / vote → rate limit → store.

- Everything that may leave the server (digest e-mail, later a private repository) is the sanitised title / body_public;
  body_raw (the AI's fields as given) is only for the reporter and the admin.
- Dedupe: one fingerprint per open problem across all users. The same user again → the existing report; another user →
  a "+1" vote on it. Votes do not count against the rate limit.
- Users choose auto / ask / off (users.feedback_mode); off and an admin block are refused here too, not only by the root rule.
- Server-wide switch BODY_FEEDBACK_MODE: queue (default) / off.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import timedelta

import psycopg
from psycopg.types.json import Jsonb

from ..ai import root_rules
from . import params as P
from .sanitize import PLACEHOLDER_RE, from_db, placeholder_ratio

FIELDS = ("what", "expected", "steps", "error_text", "area")
FIELD_LABEL = {"what": "现象", "expected": "期望", "steps": "复现", "error_text": "错误", "area": "位置"}


def ref(report_id: int) -> str:
    return f"FB-{report_id}"


def parse_ref(value) -> int:
    m = re.fullmatch(r"(?:FB-)?(\d+)", str(value).strip(), re.I)
    if not m:
        raise ValueError(f"反馈编号写 FB-12 或 12：{value}")
    return int(m.group(1))


def server_mode() -> str:
    return (os.environ.get("BODY_FEEDBACK_MODE") or "queue").strip().lower()


def server_version() -> str:
    return os.environ.get("BODY_SERVER_VERSION") or "dev"


# ---------------------------------------------------------------- the user's mode
def mode(conn: psycopg.Connection, user_id: int) -> str:
    row = conn.execute("SELECT feedback_mode, feedback_blocked_at FROM users WHERE id = %s", (user_id,)).fetchone()
    if row is None or row["feedback_blocked_at"] or server_mode() == "off":
        return "off"
    return row["feedback_mode"]


def set_mode(conn: psycopg.Connection, user_id: int, value: str) -> str:
    if value not in P.MODES:
        raise ValueError(f"反馈模式只能是 {' / '.join(P.MODES)}")
    conn.execute("UPDATE users SET feedback_mode = %s WHERE id = %s", (value, user_id))
    return mode(conn, user_id)


def set_blocked(conn: psycopg.Connection, user_id: int, blocked: bool) -> None:
    conn.execute("UPDATE users SET feedback_blocked_at = CASE WHEN %s THEN now() END WHERE id = %s", (blocked, user_id))


# ---------------------------------------------------------------- report
def fingerprint(kind: str, area: str, key: str) -> str:
    """Same problem → same key: placeholders, digits, punctuation and spacing do not matter."""
    def norm(s: str) -> str:
        s = PLACEHOLDER_RE.sub("", (s or "").lower())
        return re.sub(r"[\d\W_]+", "", s)
    return hashlib.sha256(f"{kind}|{norm(area)}|{norm(key)}".encode()).hexdigest()[:24]


def _body(fields: dict) -> str:
    parts = []
    for k in FIELDS:
        v = fields.get(k)
        if not v:
            continue
        parts.append(f"**{FIELD_LABEL[k]}**：\n```\n{v}\n```" if k == "error_text" else f"**{FIELD_LABEL[k]}**：{v}")
    return "\n\n".join(parts)[:P.MAX_BODY]


def _diagnostics(conn, user_id: int) -> dict | None:
    rows = conn.execute("""SELECT tool, count(*) AS n FROM assistant_audit
                           WHERE user_id = %s AND NOT ok AND at >= now() - %s AND tool NOT IN ('report_feedback', 'list_feedback')
                           GROUP BY tool ORDER BY n DESC LIMIT 5""",
                        (user_id, timedelta(minutes=P.DIAG_WINDOW_MIN))).fetchall()
    return {"failed_tools": {r["tool"]: r["n"] for r in rows}} if rows else None


def _limits(conn, user_id: int) -> str | None:
    c = conn.execute("""SELECT count(*) FILTER (WHERE user_id = %(u)s AND created_at >= now() - interval '1 day') AS day,
                               count(*) FILTER (WHERE user_id = %(u)s) AS month,
                               count(*) FILTER (WHERE created_at >= now() - interval '1 day') AS all_day
                        FROM feedback_reports WHERE created_at >= now() - interval '30 days'""", {"u": user_id}).fetchone()
    if c["day"] >= P.PER_USER_DAY:
        return f"每人每天最多 {P.PER_USER_DAY} 条"
    if c["month"] >= P.PER_USER_MONTH:
        return f"每人 30 天最多 {P.PER_USER_MONTH} 条"
    if c["all_day"] >= P.GLOBAL_DAY:
        return "今天全服务器的反馈已满"
    return None


def report(conn: psycopg.Connection, user_id: int, kind: str, title: str, what: str, expected: str | None = None,
           steps: str | None = None, area: str | None = None, error_text: str | None = None, *,
           channel: str | None = None, model: str | None = None) -> dict:
    if kind not in P.KIND_LABEL:
        raise ValueError("kind 只能是 bug / feature")
    if not (title or "").strip() or not (what or "").strip():
        raise ValueError("title 和 what 必填")
    if mode(conn, user_id) == "off":
        return {"reported": False, "reason": "off",
                "tell_user": "你关闭了自动反馈，这条没有发出（可以在「我 → 反馈记录」打开）。"}
    san = from_db(conn)
    raw = {"title": title, "what": what, "expected": expected, "steps": steps, "area": area, "error_text": error_text}
    clean, redactions = {}, 0
    for k, v in raw.items():
        limit = P.MAX_TITLE if k == "title" else P.MAX_FIELD[k]
        text, n = san.sanitize((v or "").strip()[:limit * 2])
        clean[k], redactions = text[:limit], redactions + n
    fp = fingerprint(kind, clean["area"], clean["error_text"] or clean["title"])
    hit = conn.execute(f"""SELECT id, user_id, status FROM feedback_reports
                           WHERE fingerprint = %s AND status IN ({','.join(['%s'] * len(P.OPEN_STATUSES))})
                           ORDER BY id LIMIT 1""", (fp, *P.OPEN_STATUSES)).fetchone()
    if hit:
        if hit["user_id"] != user_id:
            conn.execute("INSERT INTO feedback_votes (report_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                         (hit["id"], user_id))
        votes = _votes(conn, hit["id"])
        mine = hit["user_id"] == user_id
        return {"reported": False, "duplicate_of": ref(hit["id"]), "votes": votes, "status": hit["status"],
                "tell_user": (f"这个问题你之前已经报告过（{ref(hit['id'])}），不用再报。" if mine else
                              f"这个问题已有人报告过（{ref(hit['id'])}），我帮你 +1 了。")}
    over = _limits(conn, user_id)
    if over:
        return {"reported": False, "reason": "rate_limited", "limit": over,
                "tell_user": f"反馈次数到上限了（{over}），这条先不报，过一天我再帮你报。"}
    body = _body(clean)
    needs_review = placeholder_ratio(clean["title"] + body) > P.REVIEW_RATIO
    row = conn.execute("""INSERT INTO feedback_reports (user_id, kind, title, body_public, body_raw, area, fingerprint,
                              needs_review, redactions, channel, model, rules_version, server_version, diagnostics)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                       (user_id, kind, clean["title"], body, Jsonb(raw), clean["area"] or None, fp, needs_review,
                        redactions, channel, model, root_rules.version(), server_version(),
                        Jsonb(d) if (d := _diagnostics(conn, user_id)) else None)).fetchone()
    rid = ref(row["id"])
    return {"reported": True, "id": rid, "status": "new", "redacted": redactions, "needs_review": needs_review,
            "tell_user": f"我已把这个{P.KIND_LABEL[kind]}报告给开发者（编号 {rid}），进展在「我 → 反馈记录」。"}


def _votes(conn, report_id: int) -> int:
    return conn.execute("SELECT count(*) AS n FROM feedback_votes WHERE report_id = %s", (report_id,)).fetchone()["n"]


# ---------------------------------------------------------------- the reporter's view
def _view(r: dict, raw: bool = False) -> dict:
    out = {"id": ref(r["id"]), "kind": r["kind"], "title": r["title"], "status": r["status"],
           "status_label": P.STATUS_LABEL[r["status"]], "votes": r["votes"], "created_at": r["created_at"],
           "status_changed_at": r["status_changed_at"], "note": r["note"], "public_url": r["public_url"],
           "duplicate_of": ref(r["duplicate_of"]) if r["duplicate_of"] else None,
           "withdrawable": r["status"] in P.WITHDRAWABLE}
    if raw:
        out["body"] = r["body_public"]
    return {k: v for k, v in out.items() if v not in (None, "")}


_SELECT = """SELECT f.*, (SELECT count(*) FROM feedback_votes v WHERE v.report_id = f.id) AS votes FROM feedback_reports f"""


def list_for_user(conn: psycopg.Connection, user_id: int, limit: int = P.LIST_LIMIT, body: bool = False) -> list[dict]:
    """The user's own reports, plus the ones they voted for (+1)."""
    rows = conn.execute(f"""{_SELECT} WHERE f.user_id = %(u)s
                               OR f.id IN (SELECT report_id FROM feedback_votes WHERE user_id = %(u)s)
                            ORDER BY f.id DESC LIMIT %(n)s""", {"u": user_id, "n": limit}).fetchall()
    out = []
    for r in rows:
        v = _view(r, body)
        if r["user_id"] != user_id:
            v["voted"] = True
            v.pop("withdrawable", None)
        out.append(v)
    return out


def withdraw(conn: psycopg.Connection, user_id: int, report_id: int) -> bool:
    row = conn.execute("SELECT status FROM feedback_reports WHERE id = %s AND user_id = %s", (report_id, user_id)).fetchone()
    if row is None:
        return False
    if row["status"] not in P.WITHDRAWABLE:
        raise ValueError("开发者已经在处理，不能撤回了")
    conn.execute("DELETE FROM feedback_reports WHERE id = %s", (report_id,))
    return True


def updates_for_briefing(conn: psycopg.Connection, user_id: int) -> list[str]:
    """Status changes the reporter has not been told about yet; marked as told once returned (shown once)."""
    rows = conn.execute(f"""UPDATE feedback_reports SET notified_at = now()
                            WHERE user_id = %s AND status IN ({','.join(['%s'] * len(P.TELL_USER_STATUSES))})
                              AND status_changed_at >= now() - %s
                              AND (notified_at IS NULL OR notified_at < status_changed_at)
                            RETURNING id, title, status, note""",
                        (user_id, *P.TELL_USER_STATUSES, timedelta(days=P.UPDATE_DAYS))).fetchall()
    return [f"{ref(r['id'])}「{r['title'][:30]}」{P.STATUS_LABEL[r['status']]}" + (f"：{r['note'][:60]}" if r["note"] else "")
            for r in sorted(rows, key=lambda r: r["id"])]


# ---------------------------------------------------------------- admin
def admin_list(conn: psycopg.Connection, status: str | None = None, limit: int = 50) -> list[dict]:
    where, args = ("WHERE f.status = %s", [status]) if status else ("", [])
    return conn.execute(f"{_SELECT} {where} ORDER BY f.id DESC LIMIT %s", (*args, limit)).fetchall()


def admin_get(conn: psycopg.Connection, report_id: int) -> dict | None:
    return conn.execute(f"{_SELECT} WHERE f.id = %s", (report_id,)).fetchone()


def set_status(conn: psycopg.Connection, report_id: int, status: str, note: str | None = None,
               public_url: str | None = None, duplicate_of: int | None = None) -> dict:
    if status not in P.STATUS_LABEL:
        raise ValueError(f"状态只能是 {' / '.join(P.STATUS_LABEL)}")
    if status == "duplicate" and not duplicate_of:
        raise ValueError("duplicate 要写 --duplicate-of")
    row = conn.execute("""UPDATE feedback_reports SET status = %s, status_changed_at = now(),
                              note = COALESCE(%s, note), public_url = COALESCE(%s, public_url),
                              duplicate_of = COALESCE(%s, duplicate_of)
                          WHERE id = %s RETURNING id, status""",
                       (status, note, public_url, duplicate_of, report_id)).fetchone()
    if row is None:
        raise ValueError(f"没有这条反馈：{ref(report_id)}")
    return row

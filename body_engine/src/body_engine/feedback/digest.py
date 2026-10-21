"""Daily digest e-mail of the feedback queue for the maintainer (docs/ai-feedback-plan.md §3 C).

Contents: reports not in a digest yet (sanitised title and body; needs_review ones only by title), open reports by status,
new "+1" votes, and the tools that failed most in the last 24 h (assistant_audit: tool names and counts only — the server's
own view, independent of what the AIs reported). Nothing raw (body_raw) and no user names leave the server.

Recipient: BODY_FEEDBACK_DIGEST_TO (set only in the deployment's environment, never in the repository); sent with
account/mailer.py (BODY_MAIL_MODE / BODY_SMTP_*). Run once a day by the deployment's timer: `body-engine feedback digest`.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import psycopg

from ..account import mailer
from . import params as P, store

BODY_PREVIEW = 600


def build(conn: psycopg.Connection, hours: int = 24) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    new = conn.execute(f"""{store._SELECT} WHERE f.digest_at IS NULL ORDER BY f.id""").fetchall()
    open_counts = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, count(*) AS n FROM feedback_reports GROUP BY status")}
    votes = conn.execute("""SELECT report_id, count(*) AS n FROM feedback_votes WHERE created_at >= %s
                            GROUP BY report_id ORDER BY n DESC""", (since,)).fetchall()
    errors = conn.execute("""SELECT tool, count(*) AS n, count(DISTINCT user_id) AS users FROM assistant_audit
                             WHERE NOT ok AND at >= %s GROUP BY tool ORDER BY n DESC LIMIT 10""", (since,)).fetchall()
    return {"new": new, "open": open_counts, "votes": votes, "errors": errors, "hours": hours}


def render(d: dict) -> tuple[str, str]:
    n_bug = sum(1 for r in d["new"] if r["kind"] == "bug")
    subject = f"Watch2Whoop 反馈日报：新 {len(d['new'])} 条（问题 {n_bug}，需求 {len(d['new']) - n_bug}）"
    lines = [f"新反馈（{len(d['new'])} 条）", ""]
    for r in d["new"]:
        flag = "【需要人工看：去敏后内容很少】" if r["needs_review"] else ""
        lines.append(f"{store.ref(r['id'])} [{P.KIND_LABEL[r['kind']]}] {r['title']} {flag}".rstrip())
        meta = [x for x in (r["area"] and f"位置 {r['area']}", r["channel"] and f"通道 {r['channel']}",
                            r["votes"] and f"+1 ×{r['votes']}", r["diagnostics"] and f"诊断 {r['diagnostics']}") if x]
        if meta:
            lines.append("  " + "；".join(meta))
        if not r["needs_review"]:
            body = r["body_public"][:BODY_PREVIEW] + ("…" if len(r["body_public"]) > BODY_PREVIEW else "")
            lines += ["  " + l for l in body.splitlines() if l.strip()]
        lines.append("")
    if not d["new"]:
        lines += ["（没有）", ""]
    if d["votes"]:
        lines += [f"过去 {d['hours']} 小时的 +1：" + "、".join(f"{store.ref(v['report_id'])} ×{v['n']}" for v in d["votes"]), ""]
    lines.append("队列：" + ("、".join(f"{P.STATUS_LABEL[k]} {v}" for k, v in sorted(d["open"].items())) or "空"))
    lines += ["", f"过去 {d['hours']} 小时报错最多的工具（服务器自己看到的）："]
    lines += [f"  {e['tool']}：{e['n']} 次，{e['users']} 人" for e in d["errors"]] or ["  （没有）"]
    lines += ["", "处理：body-engine feedback show FB-N / feedback status FB-N <状态> [--note …] [--public-url …]"]
    return subject, "\n".join(lines) + "\n"


def send(conn: psycopg.Connection, to: str | None = None, hours: int = 24, dry_run: bool = False,
         always: bool = False) -> dict:
    """Builds, sends and marks the new reports as digested. Nothing new and no errors → no e-mail unless always."""
    d = build(conn, hours)
    subject, body = render(d)
    out = {"new": len(d["new"]), "errors": len(d["errors"]), "subject": subject}
    if dry_run:
        return {**out, "sent": False, "body": body}
    if not d["new"] and not d["errors"] and not always:
        return {**out, "sent": False, "reason": "nothing to report"}
    to = to or os.environ.get("BODY_FEEDBACK_DIGEST_TO")
    if not to:
        raise mailer.MailNotConfigured("BODY_FEEDBACK_DIGEST_TO is not set")
    mailer.send(to, subject, body)
    if d["new"]:
        conn.execute("UPDATE feedback_reports SET digest_at = now() WHERE id = ANY(%s)", ([r["id"] for r in d["new"]],))
    return {**out, "sent": True}

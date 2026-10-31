"""body-engine circles-reports <list | resolve | log | unban | overdue> (admin; on the server through the engine wrapper).
The review queue of circle reports (friends/moderation.py). Decisions made here are audited with actor `cli`."""
import argparse
import json

import psycopg

from . import moderation

COMMANDS = ("circles-reports",)


def add_parser(sub) -> None:
    r = sub.add_parser("circles-reports", help="circle reports (App Store 1.2): review queue, keep / remove / ban-user, audit log")
    rs = r.add_subparsers(dest="reports_cmd", required=True)
    ls = rs.add_parser("list", help="reported items, oldest first (default: waiting for review = open + auto-hidden)")
    ls.add_argument("--status", default="pending", choices=("pending", "open", "hidden", "kept", "removed", "all"))
    ls.add_argument("--limit", type=int, default=100)
    ls.add_argument("--json", action="store_true")
    rv = rs.add_parser("resolve", help="decide one item: keep (show again), remove, ban-user (remove + out of every circle)")
    rv.add_argument("--id", type=int, required=True, help="item id from `list`")
    rv.add_argument("--action", required=True, choices=moderation.ACTIONS)
    rv.add_argument("--note", help="why (kept in the audit log)")
    lg = rs.add_parser("log", help="the audit log, newest first")
    lg.add_argument("--id", type=int, help="only this item")
    lg.add_argument("--limit", type=int, default=50)
    ub = rs.add_parser("unban", help="let a banned person use circles again (they join circles anew by invite)")
    ub.add_argument("--user-id", type=int, required=True)
    od = rs.add_parser("overdue", help="items waiting longer than friends/params.py REVIEW_WITHIN_HOURS (for health checks)")
    od.add_argument("--json", action="store_true")


def _when(t) -> str:
    return t.astimezone().strftime("%Y-%m-%d %H:%M") if t else "—"


def _what(r: dict) -> str:
    c = r["content"]
    if c.get("gone"):
        return "（已不存在）"
    if r["kind"] == "comment":
        return ("（已删）" if c.get("deleted") else "") + (c.get("text") or "")[:60]
    if r["kind"] == "profile":
        return f"称呼「{c.get('name')}」" + ("，有头像" if c.get("has_avatar") else "")
    return f"{c.get('date')} 的训练动态"


def run(conn: psycopg.Connection, args: argparse.Namespace) -> None:
    cmd = args.reports_cmd
    if cmd == "list":
        rows = moderation.queue(conn, args.status, args.limit)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, default=str))
            return
        print(f"{'id':>5}  {'类型':<8}{'状态':<8}{'举报':>4}  {'等待(h)':>7}  {'作者':<14}内容 / 原因")
        for r in rows:
            reasons = ", ".join(f"{k}×{v}" for k, v in (r["reasons"] or {}).items())
            author = f"{r['user_id']} {(r['author_name'] or '')[:9]}" + ("（已禁）" if r["author_banned"] else "")
            print(f"{r['id']:>5}  {r['kind']:<8}{r['status']:<8}{r['reports']:>4}  {float(r['age_hours']):>7.1f}  {author:<14}"
                  f"{_what(r)}  [{reasons}]")
            for n in r["notes"] or []:
                print(f"{'':>48}备注：{n}")
        o = moderation.overdue(conn)
        print(f"\n待处理 {o['pending']} 条，超过 {o['within_hours']:g} 小时的 {o['overdue']} 条。"
              f"处理：body-engine circles-reports resolve --id N --action keep|remove|ban-user [--note …]")
    elif cmd == "resolve":
        try:
            row = moderation.resolve(conn, args.id, args.action, None, args.note, actor="cli")
        except ValueError as e:
            raise SystemExit(str(e))
        conn.commit()
        print(f"item {row['id']}: {row['kind']} {row['target_id']} → {row['status']} ({args.action})")
    elif cmd == "log":
        for r in moderation.log(conn, args.id, args.limit):
            print(f"{_when(r['created_at'])}  item {r['item_id']}  user {r['user_id']}  {r['action']:<9} by {r['actor']}"
                  + (f"  — {r['note']}" if r["note"] else ""))
    elif cmd == "unban":
        ok = moderation.unban(conn, args.user_id, None, actor="cli")
        conn.commit()
        print("unbanned" if ok else "not banned")
    else:
        o = moderation.overdue(conn)
        print(json.dumps(o, ensure_ascii=False) if args.json
              else f"待处理 {o['pending']} 条，超过 {o['within_hours']:g} 小时的 {o['overdue']} 条，最早的已等 {o['oldest_hours'] or 0:g} 小时")

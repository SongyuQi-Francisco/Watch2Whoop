"""body-engine ai-cards <grant | list | ledger | revoke | rules | rule-set | rule-on | rule-off | periodic> (admin):
Jarvis quota cards (ai/quota_cards.py, #182). Every change is in the ledger with who did it (--by, else BODY_ADMIN, else
the OS user). `ai-cards periodic` runs hourly (deploy/ops/ops.py): expiry + periodic rules, idempotent."""
import argparse
import json

import psycopg

from ..rollout.audit import actor as admin_actor
from . import quota_cards as qc
from . import quota_cards_params as P


def add_parser(sub) -> None:
    c = sub.add_parser("ai-cards", help="Jarvis quota cards: grant / list / ledger / revoke, periodic rules (#182)")
    cs = c.add_subparsers(dest="cards_cmd", required=True)
    g = cs.add_parser("grant", help="give cards to one person (--user) or to everyone the ai_cards flag is on for (--all)")
    who = g.add_mutually_exclusive_group(required=True)
    who.add_argument("--user", type=int, help="user id")
    who.add_argument("--all", action="store_true", help="every user with the ai_cards flag on and a Jarvis quota > 0")
    g.add_argument("--kind", choices=P.KINDS, required=True, help="reset = clear this month's used allowance; extend = add --amount")
    g.add_argument("--amount", type=float, help="CNY, extend cards only")
    g.add_argument("--count", type=int, default=1)
    g.add_argument("--expires", help=f"30d / 2w / 12h / 2026-11-30 / never (default {P.DEFAULT_EXPIRES_DAYS}d)")
    g.add_argument("--reason", required=True, help="shown to the person on the card and in the notification")
    g.add_argument("--source", choices=("admin", "other"), default="admin")
    g.add_argument("--key", help="idempotency key: a second grant with the same key gives nothing (per person)")
    g.add_argument("--no-notify", action="store_true")
    g.add_argument("--by", help="who (ledger)")
    ls = cs.add_parser("list", help="cards (newest first)")
    ls.add_argument("--user", type=int)
    ls.add_argument("--status", choices=("unused", "used", "expired", "revoked"))
    ls.add_argument("--limit", type=int, default=100)
    ls.add_argument("--json", action="store_true")
    lg = cs.add_parser("ledger", help="the audit trail: grant / use / expire / revoke")
    lg.add_argument("--user", type=int)
    lg.add_argument("--limit", type=int, default=100)
    lg.add_argument("--json", action="store_true")
    rv = cs.add_parser("revoke", help="withdraw unused cards")
    rg = rv.add_mutually_exclusive_group(required=True)
    rg.add_argument("--card", type=int, action="append", help="card id (repeatable)")
    rg.add_argument("--user", type=int, help="every unused card of this user")
    rv.add_argument("--reason", required=True)
    rv.add_argument("--by")
    rl = cs.add_parser("rules", help="periodic grant rules")
    rl.add_argument("--json", action="store_true")
    rs = cs.add_parser("rule-set", help="add or change a periodic rule (granted hourly by `ai-cards periodic`, once per period)")
    rs.add_argument("name", help="a-z 0-9 _ -")
    rs.add_argument("--period", choices=("monthly", "weekly"), required=True)
    rs.add_argument("--day", type=int, required=True, help="monthly: day of month 1–28; weekly: ISO weekday 1 (Mon)–7")
    rs.add_argument("--kind", choices=P.KINDS, required=True)
    rs.add_argument("--amount", type=float, help="CNY, extend only")
    rs.add_argument("--count", type=int, default=1)
    rs.add_argument("--expires-days", type=int, default=P.DEFAULT_EXPIRES_DAYS, help="0 = never")
    rs.add_argument("--active-days", type=int, help="only people who used Jarvis within this many days")
    rs.add_argument("--reason", required=True)
    rs.add_argument("--off", action="store_true", help="save it switched off")
    rs.add_argument("--by")
    for name in ("rule-on", "rule-off"):
        r = cs.add_parser(name, help=f"switch a periodic rule {name[5:]}")
        r.add_argument("name")
        r.add_argument("--by")
    pr = cs.add_parser("periodic", help="hourly: expire due cards, grant the periodic rules that are due (idempotent)")
    pr.add_argument("--dry-run", action="store_true", help="only print who would get cards")


def _p(rows, as_json: bool, fmt) -> None:
    for r in rows:
        print(json.dumps(r, ensure_ascii=False, default=str) if as_json else fmt(r))


def _card_line(r: dict) -> str:
    amount = f" ¥{float(r['amount_cny']):g}" if r["amount_cny"] is not None else ""
    exp = r["expires_at"].strftime("%Y-%m-%d %H:%M") if r["expires_at"] else "never"
    used = f" used {r['used_at']:%Y-%m-%d %H:%M}" if r["used_at"] else ""
    return (f"#{r['id']:<5} user {r['user_id']:<4} {r['kind']:<6}{amount:<7} {r['status']:<8} {r['source']:<8} "
            f"expires {exp}{used}  {r['reason']}  ({r['granted_by']})")


def run(conn: psycopg.Connection, args: argparse.Namespace) -> None:
    c = args.cards_cmd
    if c == "grant":
        from ..rollout import flags
        from . import fallback
        if args.all:
            users = [u["id"] for u in conn.execute("SELECT id FROM users ORDER BY id").fetchall()
                     if flags.enabled(conn, u["id"], P.FLAG) and fallback.quota_cny(conn, u["id"]) > 0]
        else:
            users = [args.user]
        try:
            expires = qc.parse_expires(args.expires)
            total = 0
            for uid in users:
                res = qc.grant(conn, uid, args.kind, source=args.source, reason=args.reason, actor=f"cli:{admin_actor(args.by)}",
                               amount_cny=args.amount, count=args.count, expires_at=expires, key=args.key,
                               notify=not args.no_notify)
                total += len(res["created"])
                print(f"user {uid}: +{len(res['created'])}" + (f"（{res['existing']} 张这个 key 已经发过）" if res["existing"] else "")
                      + ("，已通知" if res["notified"] else ""))
        except ValueError as e:
            raise SystemExit(str(e))
        print(f"共发出 {total} 张（{len(users)} 人）")
    elif c == "list":
        qc.expire_due(conn, args.user)
        _p(qc.list_cards(conn, args.user, args.status, args.limit), args.json, _card_line)
    elif c == "ledger":
        _p(qc.ledger(conn, args.user, args.limit), args.json,
           lambda r: (f"{r['at']:%Y-%m-%d %H:%M} user {r['user_id']:<4} {r['event']:<7} {r['kind']:<6} card #{r['card_id']}"
                      + (f" ¥{float(r['amount_cny']):g}" if r["amount_cny"] is not None else "")
                      + (f" 用前已用 ¥{float(r['spent_before_cny']):.3f} / ¥{float(r['allowance_before_cny']):.2f}"
                         if r["spent_before_cny"] is not None else "")
                      + f"  {r['actor']}" + (f"  {r['note']}" if r["note"] else "")))
    elif c == "revoke":
        who = f"cli:{admin_actor(args.by)}"
        ids = args.card or [r["id"] for r in qc.list_cards(conn, args.user, "unused", 1000)]
        n = sum(1 for i in ids if qc.revoke(conn, i, actor=who, reason=args.reason))
        print(f"收回 {n} 张（{len(ids) - n} 张不是未用状态，没动）")
    elif c == "rules":
        _p(qc.list_rules(conn), args.json,
           lambda r: (f"{r['name']:<20} {'开' if r['enabled'] else '关'} {r['period']:<7} day {r['day']:<2} {r['kind']:<6}"
                      + (f" ¥{float(r['amount_cny']):g}" if r["amount_cny"] is not None else "")
                      + f" ×{r['count']} 有效 {r['expires_days'] or '∞'} 天"
                      + (f" 近 {r['active_within_days']} 天用过的人" if r["active_within_days"] else " 所有开了开关的人")
                      + f"  {r['reason']}  ({r['updated_by']})"))
    elif c == "rule-set":
        try:
            r = qc.set_rule(conn, args.name, period=args.period, day=args.day, kind=args.kind, reason=args.reason,
                            actor=f"cli:{admin_actor(args.by)}", amount_cny=args.amount, count=args.count,
                            expires_days=args.expires_days or None, active_within_days=args.active_days, enabled=not args.off)
        except ValueError as e:
            raise SystemExit(str(e))
        print(json.dumps(r, ensure_ascii=False, default=str))
    elif c in ("rule-on", "rule-off"):
        ok = qc.set_rule_enabled(conn, args.name, c == "rule-on", f"cli:{admin_actor(args.by)}")
        print(("已" + ("打开" if c == "rule-on" else "关闭")) if ok else "没有这条规则")
    elif c == "periodic":
        print(json.dumps(qc.run_periodic(conn, dry_run=args.dry_run), ensure_ascii=False, default=str))

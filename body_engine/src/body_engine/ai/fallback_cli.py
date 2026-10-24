"""body-engine ai-fallback <status | quota | enable | disable | try> (admin; on Ali: deploy/ali/engine.sh ai-fallback …,
or `docker exec <body-engine container> body-engine ai-fallback status --json`, which the Ali doctor uses)."""
import argparse
import json

import psycopg

from . import fallback


def add_parser(sub) -> None:
    f = sub.add_parser("ai-fallback", help="fallback AI (the admin's DeepSeek for users without their own AI): status / per-user quota / switch / test")
    fs = f.add_subparsers(dest="fallback_cmd", required=True)
    st = fs.add_parser("status", help="balance (checked now), this month's spend per user, switch")
    st.add_argument("--json", action="store_true")
    st.add_argument("--no-refresh", action="store_true", help="do not call DeepSeek's balance endpoint")
    q = fs.add_parser("quota", help="set one person's monthly quota in CNY (0 = not allowed; --default = back to the default)")
    q.add_argument("--user-id", type=int, required=True)
    g = q.add_mutually_exclusive_group(required=True)
    g.add_argument("--cny", type=float)
    g.add_argument("--default", action="store_true")
    q.add_argument("--note")
    fs.add_parser("enable", help="turn the fallback on for everyone")
    fs.add_parser("disable", help="turn the fallback off for everyone (chats get 'paused by the admin')")
    t = fs.add_parser("try", help="one real chat turn as this user (counts against their quota; the owner may test despite the butler link)")
    t.add_argument("--user-id", type=int, required=True)
    t.add_argument("--message", required=True)


def _table(s: dict) -> str:
    bal = "?" if s["balance_cny"] is None else f"¥{s['balance_cny']:.2f}"
    lines = [f"托底 AI（{s['model']}）：{'可用' if s['available'] else '不可用 — ' + (s['reason'] or '')}；"
             f"key {'有' if s['key'] else '没有'}；开关 {'开' if s['enabled'] else '关'}",
             f"余额 {bal}（停用线 ¥{s['stop_cny']:g}，告警线 ¥{s['warn_cny']:g}）"
             + (f"；余额查询失败 {s['balance_error']}" if s["balance_error"] else ""),
             f"本月合计 ¥{s['month_spent_cny']:.2f}；{s['eligible_users']} 人可用，本月最多还会花 ¥{s['month_remaining_cny']:.2f}"
             f"（其中没用的额度卡最多 ¥{s['month_cards_max_cny']:.2f}）",
             *_open_signup_line(s.get("open_signup")),
             f"价格：{s['price_source']}", "",
             f"{'id':>3}  {'称呼':<10} {'本月':>8} {'额度':>6} {'调用':>5} {'输入 tok':>9} {'输出 tok':>8} {'被拦':>4}  同意  额度卡  Jarvis 用"]
    for u in s["users"]:
        lines.append(f"{u['user_id']:>3}  {u['name'][:10]:<10} ¥{u['spent_cny']:>7.3f} ¥{u['quota_cny']:>5g} {u['calls']:>5} "
                     f"{u['input_tokens']:>9} {u['output_tokens']:>8} {u['scope_blocked']:>4}  {_consent(u):<4}  "
                     f"{_cards(u):<6}  {u['uses']}")
    return "\n".join(lines)


def _open_signup_line(o: dict | None) -> list[str]:
    """Open sign-up on: what the fallback may cost a month at the account cap and how long the balance lasts."""
    if not o or not o["on"]:
        return []
    m = lambda v: "?" if v is None else f"{v:g} 个月"  # noqa: E731
    return [f"开放注册开着（{o['active']} / {o['cap']}）：满额每月最多 ¥{o['month_max_at_cap_cny']:.2f}，"
            f"余额可撑 {m(o['runway_months_at_cap'])}（按现在 ¥{o['month_max_now_cny']:.2f} / 月，{m(o['runway_months_now'])}）"]


def _consent(u: dict) -> str:
    """✓ = agreed on the Jarvis sheet, 一键 = on the one-tap screen / sheet (flag consent_one_tap), — = not agreed."""
    if not u["consent"]:
        return "—"
    return "一键" if u.get("consent_method") == "one_tap" else "✓"


def _cards(u: dict) -> str:
    """Unused quota cards: R = reset, E = extend (ai-cards list for details)."""
    parts = ([f"R{u['cards_reset']}"] if u["cards_reset"] else []) + ([f"E{u['cards_extend']}"] if u["cards_extend"] else [])
    return " ".join(parts) or "—"


def run(conn: psycopg.Connection, args: argparse.Namespace) -> None:
    c = args.fallback_cmd
    if c == "status":
        s = fallback.admin_status(conn, refresh=not args.no_refresh)
        print(json.dumps(s, ensure_ascii=False, default=str) if args.json else _table(s))
    elif c == "quota":
        fallback.set_quota(conn, args.user_id, None if args.default else args.cny, args.note)
        print(f"user {args.user_id}: 每月 ¥{fallback.quota_cny(conn, args.user_id):g}")
    elif c in ("enable", "disable"):
        fallback.set_enabled(conn, c == "enable")
        print("托底 AI 已" + ("开启" if c == "enable" else "关闭"))
    elif c == "try":
        from .chat import ChatError, chat_events
        viewer = conn.execute("SELECT id, display_name, role, timezone FROM users WHERE id = %s", (args.user_id,)).fetchone()
        if viewer is None:
            raise SystemExit("没有这个用户")
        viewer = {**viewer, "token_scopes": ["read"]}  # read-only: write tools are refused, nothing is written for the user
        try:
            for e in chat_events(conn, viewer, args.message, [], None, allow_owner_fallback=True):
                if e["event"] == "text":
                    print(e["data"]["delta"], end="", flush=True)
                else:
                    print(f"\n[{e['event']}] {json.dumps(e['data'], ensure_ascii=False, default=str)}")
        except ChatError as e:
            raise SystemExit(f"{e.code}: {e.message}")

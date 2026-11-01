"""body-engine open-signup <status | list> (admin; on the server through the engine wrapper). Read-only: the switches are
the feature flags (`body-engine flags set open_signup --state all` / `--state off`; the App Store switch without a cap:
`flags set store_signup --state all`), the limits are in account/params.py."""
import argparse
import json

import psycopg

from . import open_signup


def add_parser(sub) -> None:
    o = sub.add_parser("open-signup", help="open sign-up (no invite code below the account cap): status, fallback-AI cost, "
                                           "accounts it opened")
    os_ = o.add_subparsers(dest="open_signup_cmd", required=True)
    st = os_.add_parser("status", help="switch, active accounts / cap, per-IP limit, what the fallback AI may cost a month")
    st.add_argument("--json", action="store_true")
    st.add_argument("--refresh", action="store_true", help="check the DeepSeek balance now (otherwise the last checked value)")
    ls = os_.add_parser("list", help="accounts opened by open sign-up, newest first (identities masked)")
    ls.add_argument("--days", type=int, help="only the last N days")
    ls.add_argument("--all", action="store_true", help="also the accounts held back by the per-IP limit")
    ls.add_argument("--json", action="store_true")


def _money(v) -> str:
    return "?" if v is None else f"¥{v:,.2f}"


def _months(v) -> str:
    return "?（余额未知）" if v is None else f"{v:g} 个月"


def render_status(s: dict) -> str:
    f = s["fallback"]
    state = ("开着，正在开放" if s["open"] else "开着，但已满额（新账号要激活码）") if s["on"] else f"关（flag {s['flag_state']}）"
    return "\n".join([
        f"App Store 注册（store_signup）：{'开着，新账号直接开通、不限总数' if s.get('store') else '关'}"
        + (f"；已开通 {s.get('opened_store', 0)} 个" if s.get("store") or s.get("opened_store") else ""),
        f"开放注册：{state}",
        f"活跃账号 {s['active']} / 上限 {s['cap']}（还能开 {s['room']} 个）；计数 = status active，不算 pending {s['pending']}、"
        f"disabled {s['disabled']}、cohort {', '.join(s['excluded_cohorts']) or '—'}（全部 active {s['active_all']}）",
        f"开放注册已开通 {s['opened']} 个（24 小时内 {s['opened_24h']}）；因同一 IP 超限没开通 {s['held_ip_limit']} 个；"
        f"每个 IP 每 24 小时最多 {s['per_ip_day']} 个；开通后 {s['code_days']} 天内还能补填一个邀请码",
        f"托底 AI：每人每月额度 ¥{f['quota_cny']:g}；按现在的账号每月最多 {_money(f['month_max_now_cny'])}，"
        f"满额（{s['cap']} 个）每月最多 {_money(f['month_max_at_cap_cny'])}（额度卡另算）",
        f"余额 {_money(f['balance_cny'])}（停用线 ¥{f['stop_cny']:g}）：按现在最多能撑 {_months(f['runway_months_now'])}，"
        f"满额时 {_months(f['runway_months_at_cap'])}",
    ])


def run(conn: psycopg.Connection, args: argparse.Namespace) -> None:
    if args.open_signup_cmd == "status":
        s = open_signup.status(conn)
        if args.refresh:
            s["fallback"] = open_signup.fallback_cost(conn, s["active"], refresh=True)
            conn.commit()
        print(json.dumps(s, ensure_ascii=False, default=str) if args.json else render_status(s))
    else:
        if args.days is not None and args.days < 1:
            raise SystemExit("--days 要 ≥ 1")
        rows = open_signup.list_opened(conn, args.days, args.all)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, default=str))
            return
        print(f"{'id':>5}  {'称呼':<12}{'状态':<9}{'结果':<10}{'来源':<8}{'开通 / 记录时间':<18}{'激活码':<6}登录方式")
        for r in rows:
            at = (r["activated_at"] or r["created_at"]).astimezone().strftime("%Y-%m-%d %H:%M")
            ids = ", ".join(i["hint"] for i in r["identities"]) or "—"
            print(f"{r['user_id']:>5}  {(r['name'] or '')[:11]:<12}{r['status']:<9}{r['decision']:<10}{r['via']:<8}{at:<18}"
                  f"{'有' if r['code_redeemed'] else '—':<6}{ids}")
        print(f"\n共 {len(rows)} 个（其中 App Store 注册 {sum(r.get('mode') == 'store' for r in rows)} 个）。"
              "来源 signup = 开放期间新注册；pending = 开放前就在等激活码、开放后自动开通。")

"""body-engine upgrade status | set | on | off | send (R2 forced upgrade; admin, on the server through the engine wrapper).
Every change goes to rollout_audit (`body-engine flags audit`); --by names who did it (default BODY_ADMIN or the OS user)."""
import json

import psycopg

from ..rollout import flags
from . import notice, params as P, policy

COMMANDS = ("upgrade",)


def add_parser(sub) -> None:
    u = sub.add_parser("upgrade", help="forced upgrade of old app clients (R2): status / set / on / off / send")
    us = u.add_subparsers(dest="upgrade_cmd", required=True)
    us.add_parser("status", help="flag, dates, minimum versions, link, messages sent")
    s = us.add_parser("set", help="dates / minimum versions / link (none = remove); nothing changes for users while the flag is off")
    s.add_argument("--notice-from", help=f"ISO date (midnight in each user's time zone) or date-time; default {P.NOTICE_DAYS} days before the block")
    s.add_argument("--block-from", help="ISO date or date-time from which old clients get 426 (none = never block)")
    s.add_argument("--min-ios", help=f"minimum native app version, e.g. 2.0.0 or 2.0.0+57 (default {P.DEFAULT_MIN['ios-native']}; none = not asked)")
    s.add_argument("--min-web", choices=P.WEB_VALUES, help="the Today page (also inside the 1.0.8 app): all = asked to upgrade (default), none = not")
    s.add_argument("--url", help=f"App Store / TestFlight link (https; none = fall back to {P.URL_ENV})")
    s.add_argument("--by", help="who (audit log)")
    on = us.add_parser("on", help="turn the notice / block on (flag force_upgrade all, or one cohort to try it first)")
    on.add_argument("--cohort", help="only this cohort (e.g. inner) instead of everyone")
    on.add_argument("--by")
    off = us.add_parser("off", help="kill switch: no notice, no block for anyone (takes effect within 30 s)")
    off.add_argument("--by")
    sd = us.add_parser("send", help="admin notice + push to everyone still on an old client (idempotent)")
    sd.add_argument("--stage", choices=(notice.STAGE_AUTO, *P.STAGES), default=notice.STAGE_AUTO,
                    help="auto (default) = the stage due for each person now; notice; last-day")
    sd.add_argument("--user-id", type=int, help="only this user")
    sd.add_argument("--include-upgraded", action="store_true", help="also people whose phone already reported the new version")
    sd.add_argument("--dry-run", action="store_true", help="say who would get what, write and send nothing")


def _p(obj) -> None:
    print(json.dumps(obj, default=str, ensure_ascii=False, indent=2))


def run(conn: psycopg.Connection, args) -> None:
    cmd = args.upgrade_cmd
    if cmd == "status":
        out = policy.describe(conn)
        out["sent"] = conn.execute("""SELECT stage, block_from, push_status, count(*) AS n FROM upgrade_notice_sends
                                      GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""").fetchall()
        _p(out)
    elif cmd == "set":
        mins = {k: v for k, v in (("ios-native", args.min_ios), ("web", args.min_web)) if v is not None}
        kw = {k: v for k, v in (("notice_from", args.notice_from), ("block_from", args.block_from), ("url", args.url))
              if v is not None}
        if not kw and not mins:
            raise ValueError("nothing to set: give --notice-from / --block-from / --min-ios / --min-web / --url")
        policy.update(conn, mins=mins, actor=args.by, **kw)
        conn.commit()
        _p(policy.describe(conn))
    elif cmd in ("on", "off"):
        if cmd == "on":
            flags.set_flag(conn, P.FLAG, "cohort" if args.cohort else "all", cohort=args.cohort, actor=args.by)
        else:
            flags.set_flag(conn, P.FLAG, "off", actor=args.by)
        conn.commit()
        _p(policy.describe(conn))
    else:
        out = notice.run(conn, args.stage, args.user_id, args.dry_run, args.include_upgraded)
        conn.commit()
        _p(out)

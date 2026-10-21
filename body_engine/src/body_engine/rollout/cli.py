"""body-engine flags | cohort | experiments (admin; on the server through the engine wrapper). Every change goes to rollout_audit;
--by names who did it (default BODY_ADMIN or the OS user)."""
import json

import psycopg

from . import audit, experiments, flags

COMMANDS = ("flags", "cohort", "experiments")


def add_parser(sub) -> None:
    f = sub.add_parser("flags", help="feature flags: list / set / consent-base / audit (rollout/, docs/rollout.md)")
    fs = f.add_subparsers(dest="flags_cmd", required=True)
    fl = fs.add_parser("list", help="every declared flag: state, cohort, percent, min consent")
    fl.add_argument("--json", action="store_true")
    st = fs.add_parser("set", help="change one flag (off | cohort | percent | all)")
    st.add_argument("key")
    st.add_argument("--state", required=True, choices=flags.STATES)
    st.add_argument("--cohort", help=f"cohort for cohort / percent (default stays; new flags: {flags.DEFAULT_COHORT})")
    st.add_argument("--percent", type=int, help="0–100, for --state percent")
    st.add_argument("--min-consent", help="consent version a user must have agreed to (none = clear)")
    st.add_argument("--by", help="who (audit log)")
    cb = fs.add_parser("consent-base", help="the consent version everyone must confirm (default: the version in consent.md)")
    g = cb.add_mutually_exclusive_group()
    g.add_argument("--set", metavar="VERSION")
    g.add_argument("--clear", action="store_true")
    cb.add_argument("--by")
    au = fs.add_parser("audit", help="recent changes to flags, cohorts, experiments and settings")
    au.add_argument("--limit", type=int, default=30)

    c = sub.add_parser("cohort", help="user cohorts, e.g. beta testers: add / remove / list")
    cs = c.add_subparsers(dest="cohort_cmd", required=True)
    for name in ("add", "remove"):
        p = cs.add_parser(name)
        p.add_argument("--user-id", type=int, required=True, action="append", help="repeat for several users")
        p.add_argument("--cohort", default=flags.DEFAULT_COHORT)
        p.add_argument("--by")
    cl = cs.add_parser("list")
    cl.add_argument("--cohort")

    e = sub.add_parser("experiments", help="A/B tests: list / start / stop / results")
    es = e.add_subparsers(dest="exp_cmd", required=True)
    es.add_parser("list")
    s = es.add_parser("start", help="create or restart an experiment")
    s.add_argument("key")
    s.add_argument("--variants", required=True, help="name:weight,… e.g. control:50,new:50")
    s.add_argument("--cohort", help="only this cohort takes part (default: everyone)")
    s.add_argument("--days", type=int, help="ends after D days (default: until stopped)")
    s.add_argument("--description")
    s.add_argument("--by")
    sp = es.add_parser("stop")
    sp.add_argument("key")
    sp.add_argument("--by")
    r = es.add_parser("results", help="users and exposures per variant")
    r.add_argument("key")


def _p(obj) -> None:
    print(json.dumps(obj, default=str, ensure_ascii=False))


def run(conn: psycopg.Connection, args) -> None:
    if args.cmd == "flags":
        if args.flags_cmd == "list":
            rows = flags.list_flags(conn)
            if args.json:
                _p(rows)
                return
            if not rows:
                print("no flags declared (rollout/registry.py)")
            for r in rows:
                extra = f" {r['percent']}%" if r["state"] == "percent" else ""
                print(f"{r['key']:<28} {r['state']:<8}{extra:<5} cohort={r['cohort']}({r['cohort_size']})"
                      f" consent>={r['min_consent_version'] or '-'}  {'' if r['declared'] else '[not declared] '}{r['description'] or ''}")
            from ..profile import consent
            print(f"consent base version: {consent.base_version(conn)} (text {consent.version()})")
        elif args.flags_cmd == "set":
            clear = (args.min_consent or "").lower() == "none"
            row = flags.set_flag(conn, args.key, args.state, cohort=args.cohort, percent=args.percent,
                                 min_consent=None if clear else args.min_consent, clear_min_consent=clear, actor=args.by)
            _p(row)
        elif args.flags_cmd == "consent-base":
            from ..profile import consent
            if args.set or args.clear:
                flags.set_setting(conn, consent.BASE_SETTING, None if args.clear else args.set, args.by)
            print(f"consent base version: {consent.base_version(conn)} (text {consent.version()})")
        else:
            for r in audit.recent(conn, args.limit):
                _p({k: r[k] for k in ("at", "actor", "action", "target", "before", "after")})
    elif args.cmd == "cohort":
        if args.cohort_cmd == "list":
            for r in flags.cohort_list(conn, args.cohort):
                _p(r)
            return
        fn = flags.cohort_add if args.cohort_cmd == "add" else flags.cohort_remove
        for uid in args.user_id:
            changed = fn(conn, uid, args.cohort, args.by)
            print(f"{args.cohort_cmd} user {uid} {args.cohort}: {'done' if changed else 'no change'}")
    else:
        if args.exp_cmd == "list":
            for r in experiments.list_all(conn):
                _p(r)
        elif args.exp_cmd == "start":
            _p(experiments.start(conn, args.key, experiments.parse_variants(args.variants), cohort=args.cohort,
                                 days=args.days, description=args.description, actor=args.by))
        elif args.exp_cmd == "stop":
            print("stopped" if experiments.stop(conn, args.key, args.by) else "not running")
        else:
            _p(experiments.results(conn, args.key))

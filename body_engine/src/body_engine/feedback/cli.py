"""body-engine feedback <list | show | status | block | unblock | digest> (admin; on the server through the deployment's
engine wrapper). Promotion to the public repository is manual: `feedback show FB-N --public` prints the sanitised issue text
to paste into `gh issue create`, then `feedback status FB-N public --public-url …`."""
import argparse
import json

import psycopg

from . import digest, params as P, store


def add_parser(sub) -> None:
    f = sub.add_parser("feedback", help="AI feedback queue (bugs / feature requests filed by users' AIs): list, show, status, block, daily digest")
    fs = f.add_subparsers(dest="feedback_cmd", required=True)
    ls = fs.add_parser("list", help="newest first")
    ls.add_argument("--status", choices=list(P.STATUS_LABEL))
    ls.add_argument("--limit", type=int, default=50)
    sh = fs.add_parser("show", help="one report (sanitised; --raw adds what the AI sent, which may contain personal data)")
    sh.add_argument("ref")
    g = sh.add_mutually_exclusive_group()
    g.add_argument("--raw", action="store_true")
    g.add_argument("--public", action="store_true", help="only the issue title and body, to paste into a public issue")
    st = fs.add_parser("status", help="set the status the reporter sees (their AI tells them once)")
    st.add_argument("ref")
    st.add_argument("status", choices=list(P.STATUS_LABEL))
    st.add_argument("--note", help="short note shown to the reporter (no personal data)")
    st.add_argument("--public-url")
    st.add_argument("--duplicate-of")
    for name, text in (("block", "stop one user's reports (abuse)"), ("unblock", "allow again")):
        b = fs.add_parser(name, help=text)
        b.add_argument("--user-id", type=int, required=True)
    d = fs.add_parser("digest", help="send the daily digest e-mail to BODY_FEEDBACK_DIGEST_TO (no e-mail when there is nothing)")
    d.add_argument("--to", help="override the recipient (test)")
    d.add_argument("--hours", type=int, default=24)
    d.add_argument("--dry-run", action="store_true", help="print instead of sending; marks nothing")
    d.add_argument("--always", action="store_true", help="send even when there is nothing new")


def run(conn: psycopg.Connection, args: argparse.Namespace) -> None:
    c = args.feedback_cmd
    if c == "list":
        for r in store.admin_list(conn, args.status, args.limit):
            flag = " needs-review" if r["needs_review"] else ""
            print(f"{store.ref(r['id']):>7}  {r['created_at']:%m-%d %H:%M}  {r['status']:<9} {r['kind']:<7} "
                  f"u{r['user_id']:<3} +{r['votes']:<2} {r['title']}{flag}")
    elif c == "show":
        r = store.admin_get(conn, store.parse_ref(args.ref))
        if r is None:
            raise SystemExit("没有这条反馈")
        if args.public:
            print(f"{r['title']}\n\n{r['body_public']}\n\n---\n"
                  f"{P.KIND_LABEL[r['kind']]}，AI 自动反馈（{store.ref(r['id'])}，根规则 {r['rules_version']}）")
            return
        out = {k: r[k] for k in ("kind", "title", "status", "area", "channel", "model", "rules_version", "server_version",
                                 "needs_review", "redactions", "diagnostics", "votes", "note", "public_url", "created_at")}
        out = {"id": store.ref(r["id"]), "user_id": r["user_id"], **out, "body": r["body_public"]}
        if args.raw:
            out["raw"] = r["body_raw"]
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    elif c == "status":
        dup = store.parse_ref(args.duplicate_of) if args.duplicate_of else None
        r = store.set_status(conn, store.parse_ref(args.ref), args.status, args.note, args.public_url, dup)
        print(f"{store.ref(r['id'])} → {r['status']}")
    elif c in ("block", "unblock"):
        store.set_blocked(conn, args.user_id, c == "block")
        print(f"user {args.user_id}: feedback {'blocked' if c == 'block' else 'allowed'}")
    elif c == "digest":
        res = digest.send(conn, args.to, args.hours, args.dry_run, args.always)
        if args.dry_run:
            print(res.pop("body"))
        print(json.dumps(res, ensure_ascii=False))

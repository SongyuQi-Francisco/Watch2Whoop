"""Command line: body-engine <command>. On the Mac use `uv run body-engine ...`; in the container the command has the same name."""
import argparse
import json
import os
import re
from datetime import date
from pathlib import Path

from . import auth, sparky_client
from .account import stats as user_stats
from .assistant import meals as assistant_meals, memory as assistant_memory, memory_seed
from .assistant import recipes as assistant_recipes, status as assistant_status
from .config import settings
from .db import connect, migrate
from .importers import apple_export, ow, shortcut
from .ow_client import OWClient
from .scores import engine as scores_engine
from .insights import jobs as insights_jobs
from .records import defaults as record_defaults, exercise_link, importer as records_importer
from .rules import engine as rules_engine
from .goals import store as goal_store
from .training import importer as workouts_importer
from .ai import crypto as ai_crypto, fallback_cli as ai_fallback_cli, quota_cards_cli as ai_cards_cli, tokens as ai_tokens
from .feedback import cli as feedback_cli
from .rollout import cli as rollout_cli


CONSENT_GATED = {"import-ow", "import-shortcut", "import-workouts", "compute-scores", "import-apple-zip", "import-raw", "hrv-compare", "hrv-kind",
                 "evaluate-rules", "refresh-status", "sync-shadow-day"}


def _export_date(processed_dir: Path) -> date:
    """Export date: prefer the file name of full_export/export_YYYY-MM-DD.zip, otherwise the latest date in daily_metrics.csv."""
    zips = sorted(processed_dir.parent.glob("full_export/export_*.zip"))
    if zips and (m := re.search(r"\d{4}-\d{2}-\d{2}", zips[-1].name)):
        return date.fromisoformat(m.group())
    with open(processed_dir / "daily_metrics.csv") as f:
        return max(date.fromisoformat(line.split(",")[1]) for line in list(f)[1:])


def _expiry(conn, circle_id: int, expires: str | None, days: int | None):
    """--expires (a date = the end of that day in the circle owner's time zone, or a date-time) / --days -> aware datetime."""
    from datetime import datetime, time, timedelta
    from zoneinfo import ZoneInfo
    if days is not None:
        if not 1 <= days <= 3650:
            raise ValueError("--days 1–3650")
        return datetime.now().astimezone() + timedelta(days=days)
    if not expires:
        return None
    tz = conn.execute("SELECT u.timezone FROM circles c JOIN users u ON u.id = c.owner_id WHERE c.id = %s",
                      (circle_id,)).fetchone()
    zone = ZoneInfo(tz["timezone"] if tz and tz["timezone"] else "UTC")
    if len(expires) == 10:
        d = datetime.fromisoformat(expires).date()
        return datetime.combine(d + timedelta(days=1), time(), zone)
    at = datetime.fromisoformat(expires)
    return at if at.tzinfo else at.replace(tzinfo=zone)


def _circle_admin(conn, args, p) -> None:
    from pathlib import Path
    from .friends import admin as circle_admin, codes as circle_codes
    from .friends.store import FriendsError

    def read(path):
        f = Path(path).expanduser()
        if not f.is_file():
            raise ValueError(f"没有文件 {path}")
        return f.read_bytes()

    def show(rows):
        for r in rows:
            print(json.dumps(r, default=str, ensure_ascii=False))

    try:
        if args.cmd == "circle-admin":
            if args.ca_cmd == "create":
                scopes = [x.strip() for x in args.scopes.split(",") if x.strip()] if args.scopes else None
                show([circle_admin.create_circle(conn, args.name, args.owner_user_id,
                                                 read(args.avatar) if args.avatar else None, args.max_members, scopes)])
            elif args.ca_cmd == "avatar":
                cid = circle_admin.find_circle(conn, args.circle)
                img = circle_admin.set_avatar(conn, cid, None if args.clear else read(args.file))
                print(f"circle {cid}: " + (f"avatar image {img}" if img else "avatar cleared"))
            else:
                show(circle_admin.list_circles(conn))
        elif args.cc_cmd == "create":
            cid = circle_admin.find_circle(conn, args.circle)
            row = circle_codes.create(conn, cid, args.code, args.max_uses, _expiry(conn, cid, args.expires, args.days),
                                      args.label, args.created_by)
            print(f"code {row['id']} · 「{row['code']}」 → circle {cid} · {row['max_uses']} uses · expires "
                  f"{row['expires_at'].isoformat() if row['expires_at'] else 'never'}")
        elif args.cc_cmd == "list":
            for r in circle_codes.list_codes(conn, circle_admin.find_circle(conn, args.circle) if args.circle else None):
                print(circle_codes.summary(r))
        elif args.cc_cmd == "uses":
            rows = circle_codes.uses(conn, args.id)
            for kind, title in (("new_account", "新开户"), ("existing", "老用户加入")):
                part = [r for r in rows if r["kind"] == kind]
                print(f"# {title} {len(part)}")
                show(part)
        else:
            print("disabled" if circle_codes.disable(conn, args.id) else "not found / already disabled")
    except (ValueError, FriendsError) as e:
        conn.rollback()
        p.error(str(e))


def main() -> None:
    p = argparse.ArgumentParser(prog="body-engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="create / upgrade the table schema (and the migrations of BODY_PLUGIN_DIR plugins)")
    pl = sub.add_parser("plugins", help="optional server plugins from BODY_PLUGIN_DIR (plugins.py): list / run a job now")
    pls = pl.add_subparsers(dest="plugins_cmd", required=True)
    pls.add_parser("list", help="loaded plugins, their jobs and page files")
    plr = pls.add_parser("run", help="run one plugin job now (with its advisory lock)")
    plr.add_argument("plugin")
    plr.add_argument("job")

    u = sub.add_parser("user-add", help="create a user")
    u.add_argument("--name", required=True)
    u.add_argument("--role", choices=["owner", "member"], default="member")
    u.add_argument("--birth-date", type=date.fromisoformat)
    u.add_argument("--height-cm", type=float)
    u.add_argument("--sex")
    u.add_argument("--timezone", default="Australia/Sydney")
    u.add_argument("--email", help="bind this e-mail as the account's sign-in (the person then signs in with an e-mail code)")
    il = sub.add_parser("identity-link", help="bind / list / remove sign-in identities of accounts (account/identities.py)")
    il.add_argument("--user-id", type=int)
    il.add_argument("--email", help="e-mail to bind (or to remove with --unlink)")
    il.add_argument("--move", action="store_true", help="take the e-mail over from the account it is bound to now")
    il.add_argument("--list", action="store_true", help="list identities (all, or --user-id's)")
    il.add_argument("--unlink", action="store_true", help="remove --id, or --user-id's --email")
    il.add_argument("--id", type=int, help="identity id (from --list), for --unlink")
    sub.add_parser("user-list", help="list users")
    us = sub.add_parser("user-stats", help="read-only: accounts by status, sign-ups / activations / active users in the last N days, "
                        "last activity per user", epilog=user_stats.HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    us.add_argument("--days", type=int, default=7, help="window in days (default 7; e.g. 1 / 30 / 90)")
    us.add_argument("--signals", default=",".join(user_stats.SIGNALS),
                    help=f"which signals count as active (comma separated, default all: {','.join(user_stats.SIGNALS)})")
    us.add_argument("--active-only", action="store_true", help="only users active in the window")
    us.add_argument("--new-only", action="store_true", help="only users who signed up or activated in the window")
    us.add_argument("--json", action="store_true", help="one JSON document instead of the table")
    us.add_argument("--full-email", action="store_true", help="print full e-mail addresses (default masked: a***@domain)")
    nt = sub.add_parser("notify", help="notifications and APNs push (notify/, #175): status / test / prune")
    nts = nt.add_subparsers(dest="notify_cmd", required=True)
    nts.add_parser("status", help="whether APNs is configured, devices per user and environment, notifications of the last 7 days")
    ntt = nts.add_parser("test", help="send a test notification (kind system) to one user now, ignoring quiet hours; prints Apple's answer")
    ntt.add_argument("--user-id", type=int, required=True)
    ntp = nts.add_parser("prune", help="delete notifications older than --days (default 90)")
    ntp.add_argument("--days", type=int, default=90)
    lb = sub.add_parser("lookup-barcode", help="look up one barcode on Open Food Facts (needs BODY_OFF_CONTACT; nothing is written)")
    lb.add_argument("barcode")
    gs = sub.add_parser("grocery-sync", help="opt-in weekly refresh of the supermarkets' public food catalogue "
                                             "(BODY_GROCERY_SYNC + BODY_GROCERY_SYNC_CONTACT; docs/grocery-store-data-plan.md §8)")
    gss = gs.add_subparsers(dest="gs_cmd", required=True)
    gsr = gss.add_parser("run", help="crawl the enabled stores politely (robots.txt, delay, stop when refused); skips if run < 6 days ago")
    gsr.add_argument("--store", choices=["coles", "woolworths"])
    gsr.add_argument("--force", action="store_true", help="run even if an ok run is younger than 6 days")
    gsr.add_argument("--max-pages", type=int, help="stop after N pages (a test run; status = partial)")
    gss.add_parser("status", help="last run per store, product / special counts, staleness (JSON)")
    gsp = gss.add_parser("purge", help="kill switch: delete all scraped store data (products, price history, specials, run log)")
    gsp.add_argument("--store", choices=["coles", "woolworths"])
    gsp.add_argument("--yes", action="store_true", help="required")

    lc = sub.add_parser("login-code", help="issue a one-time login code (for the Watch2Whoop app setup info / QR code; the plaintext is shown only once)")
    lc.add_argument("--user-id", type=int, required=True)
    lc.add_argument("--label", help="who / which device, e.g. mom's iPhone (written into the redeemed token name app:<label>)")
    lc.add_argument("--hours", type=int, default=72, help="validity in hours (default 72, max 2160 = 90 days)")
    lc.add_argument("--scopes", default="read,write")
    ac = sub.add_parser("activation-code", help="reusable activation links for the Watch2Whoop app (web/activation.py)")
    acs = ac.add_subparsers(dest="ac_cmd", required=True)
    acc = acs.add_parser("create", help="issue an activation code and print it with its link (the plaintext is shown only once)")
    acc.add_argument("--user-id", type=int, required=True)
    acc.add_argument("--label", help="for whom, e.g. Mum (shown in list; minted login codes are named activation:<label>)")
    acc.add_argument("--days", type=int, help="validity in days (default: until revoked)")
    acc.add_argument("--email", help="also bind this e-mail as the account's sign-in (identity-link)")
    acl = acs.add_parser("list", help="list activation codes (no plaintext)")
    acl.add_argument("--user-id", type=int)
    acr = acs.add_parser("revoke", help="revoke an activation code by id (devices already set up stay signed in)")
    acr.add_argument("--id", type=int, required=True)
    iv = sub.add_parser("invite-code", help="beta invite codes for self-service sign-up (account/invites.py)")
    ivs = iv.add_subparsers(dest="iv_cmd", required=True)
    ivc = ivs.add_parser("create", help="one code that opens up to N accounts (one per person); prints the code (shown only once)")
    ivc.add_argument("--label", required=True, help="who / which channel the code is for")
    ivc.add_argument("--uses", type=int, required=True, help="how many accounts it can open")
    ivc.add_argument("--days", type=int, help="valid for D days (default: no expiry)")
    ivc.add_argument("--created-by", help="free text recorded with the code")
    ivc.add_argument("--code", help="a readable code instead of a random one (4-40 letters / digits, not 12 or 16 long)")
    ivb = ivs.add_parser("batch", help="create a batch; prints CSV code,batch_id,label,expires_at,max_uses (plaintext shown only once)")
    ivb.add_argument("--count", type=int, required=True)
    ivb.add_argument("--label", required=True, help="who / which channel the batch is for")
    ivb.add_argument("--days", type=int, help="valid for D days (default: no expiry)")
    ivb.add_argument("--max-uses", type=int, default=1, help="accounts per code (default 1)")
    ivb.add_argument("--created-by", help="free text recorded with the batch")
    ivl = ivs.add_parser("list", help="batches with uses / remaining and who redeemed (masked identity, time); --batch N: per code")
    ivl.add_argument("--batch", type=int)
    ivl.add_argument("--kind", choices=["admin", "referral"], help="referral: users' own codes — who invited whom")
    ivr = ivs.add_parser("revoke", help="stop a batch or one code (accounts already opened stay)")
    ivg = ivr.add_mutually_exclusive_group(required=True)
    ivg.add_argument("--batch", type=int)
    ivg.add_argument("--code-id", type=int)
    ca = sub.add_parser("circle-admin", help="circles for groups (clubs, societies) made by the admin, and circle avatars (friends/admin.py)")
    cas = ca.add_subparsers(dest="ca_cmd", required=True)
    cac = cas.add_parser("create", help="make a circle with an owner (its first member; must have given the circle consent)")
    cac.add_argument("--name", required=True)
    cac.add_argument("--owner-user-id", type=int, required=True)
    cac.add_argument("--avatar", help="PNG / JPEG / WebP file; cropped to a square, resized, metadata removed")
    cac.add_argument("--max-members", type=int, help="member cap (default friends/params.py MAX_MEMBERS); set it above the codes' uses")
    cac.add_argument("--scopes", help="comma-separated default scopes of people joining with a code (default DEFAULT_SCOPES)")
    caa = cas.add_parser("avatar", help="set or clear a circle's avatar")
    caa.add_argument("--circle", required=True, help="circle id (or its exact name)")
    cag = caa.add_mutually_exclusive_group(required=True)
    cag.add_argument("--file")
    cag.add_argument("--clear", action="store_true")
    cas.add_parser("list", help="circles that are not personal ones: owner, members, cap, avatar")
    cc = sub.add_parser("circle-code", help="circle activation codes: sign up and join one circle with a readable code (friends/codes.py)")
    ccs = cc.add_subparsers(dest="cc_cmd", required=True)
    ccc = ccs.add_parser("create", help="bind a code to a circle; matching ignores case, spaces and dashes")
    ccc.add_argument("--circle", required=True, help="circle id (or its exact name)")
    ccc.add_argument("--code", required=True, help="the code as people will type it, e.g. 'Example Club 2026'")
    ccc.add_argument("--max-uses", type=int, required=True, help="how many NEW accounts it may open (existing accounts join without limit)")
    ccx = ccc.add_mutually_exclusive_group()
    ccx.add_argument("--expires", help="ISO date (end of that day in the circle owner's time zone) or ISO date-time")
    ccx.add_argument("--days", type=int, help="valid for D days")
    ccc.add_argument("--label", help="free text shown in list")
    ccc.add_argument("--created-by", help="free text recorded with the code")
    ccl = ccs.add_parser("list", help="one line per code: state, new accounts N / max_uses, existing accounts joined, waiting for consent")
    ccl.add_argument("--circle", help="only this circle (id or exact name)")
    ccu = ccs.add_parser("uses", help="every use of one code, new accounts and existing accounts apart: who, when, joined, still a member")
    ccu.add_argument("--id", type=int, required=True)
    ccd = ccs.add_parser("disable", help="stop a code (people who joined stay)")
    ccd.add_argument("--id", type=int, required=True)
    pr = sub.add_parser("provision-run", help="finish provisioning of self-signed-up users (OW user, SparkyFitness account, journal items; idempotent, hourly)")
    pr.add_argument("--user-id", type=int, help="only this user (default: every active user not done yet)")
    mt = sub.add_parser("mail-test", help="send a test sign-in e-mail with the configured mailer (BODY_MAIL_MODE / BODY_SMTP_*)")
    mt.add_argument("--to", required=True)
    t = sub.add_parser("token-create", help="issue an API token to a user (the plaintext is shown only once)")
    t.add_argument("--user-id", type=int, required=True)
    t.add_argument("--name", required=True, help="purpose, e.g. iphone-shortcut, claude-mcp")
    t.add_argument("--scopes", default="read,write")
    tl = sub.add_parser("token-list", help="list a user's API tokens (names, scopes, last use, revoked)")
    tl.add_argument("--user-id", type=int, required=True)
    tr = sub.add_parser("token-revoke", help="revoke a user's API tokens by name, or --all (also voids unused one-time login codes)")
    tr.add_argument("--user-id", type=int, required=True)
    g = tr.add_mutually_exclusive_group(required=True)
    g.add_argument("--name")
    g.add_argument("--all", action="store_true")

    a = sub.add_parser("import-apple-export", help="import a full Apple Health export (processed/*.csv)")
    a.add_argument("--user-id", type=int, required=True)
    a.add_argument("--processed-dir", type=Path, default=settings.health_data_dir / "processed")
    a.add_argument("--export-date", type=date.fromisoformat, help="the export day (that day's data is incomplete and is dropped)")

    s = sub.add_parser("import-shortcut", help="import data uploaded by the iOS Shortcut (archive/health_*.txt)")
    s.add_argument("--user-id", type=int, required=True)
    s.add_argument("--archive-dir", type=Path, help="default: this user's archive (archive/ for user 1, archive/u<id>/ for others)")
    s.add_argument("--all", action="store_true", help="re-import uploaded files that were already imported (default: only new files)")
    s.add_argument("--rebuild-since", type=date.fromisoformat,
                   help="afterwards recompute the Shortcut's daily metrics / sleep / inferred workouts from the stored samples and re-choose "
                        "every daily metric from this day to today (after the merge rules change; idempotent)")

    o = sub.add_parser("ow-link", help="link the user to a user in Open Wearables (without --ow-user-id, a new one is created in OW)")
    o.add_argument("--user-id", type=int, required=True)
    o.add_argument("--ow-user-id", help="existing user UUID in OW")

    w = sub.add_parser("import-ow", help="pull daily metrics and sleep from Open Wearables (by default starting 2 days before the last sync)")
    w.add_argument("--user-id", type=int, required=True)
    w.add_argument("--start", type=date.fromisoformat, help="start from this day (user local date)")
    w.add_argument("--end", type=date.fromisoformat, help="up to and including this day, default today")
    w.add_argument("--full", action="store_true", help="recompute everything from the earliest data in OW")
    w.add_argument("--xml-energy-unit", choices=["kJ", "kcal"], default="kJ",
                   help="energy unit in the export.xml imported into OW (see the unit of ActiveEnergyBurned in export.xml)")

    wk = sub.add_parser("import-workouts", help="pull only the workout records from Open Wearables -> workouts (import-ow also does this every time; this is for separate backfill / recompute)")
    wk.add_argument("--user-id", type=int, required=True)
    wk.add_argument("--start", type=date.fromisoformat, help="start from this day (user local date), default all history")
    wk.add_argument("--end", type=date.fromisoformat, help="up to and including this day, default today")

    hb = sub.add_parser("history-backfill-run", help="full-history backfill for users whose app read the whole Apple Health history: import from OW window by window, then recompute all scores (resumable, time-budgeted; nothing queued = nothing done)")
    hb.add_argument("--user-id", type=int, default=None)
    sr = sub.add_parser("sync-refresh-run", help="recompute scores for users whose on-demand refresh (app opened / pull to refresh) is queued; nothing queued = nothing done")
    sr.add_argument("--user-id", type=int, default=None)
    hd = sub.add_parser("hk-derive-run", help="app sync engine (flag apple_sync_engine): derive daily values, sleep and workouts "
                                              "from the uploaded HealthKit samples of the queued days (hk_dirty_days), oldest "
                                              "first; idempotent, time-budgeted, nothing queued = nothing done")
    hd.add_argument("--user-id", type=int, default=None)
    hd.add_argument("--max-days", type=int, default=None, help="at most this many days per user in this run")
    sd = sub.add_parser("sync-shadow-day", help="app sync engine: Open Wearables and the app's own sync engine side by side per "
                                                "day (daily values, sleep, workouts; reads only). Chinese Markdown with personal "
                                                "data, or --json: the terminal / health_data/reports/ only, never git")
    sd.add_argument("--user-id", type=int, required=True)
    sd.add_argument("--date", type=date.fromisoformat, required=True, help="first day (the user's local date)")
    sd.add_argument("--end", type=date.fromisoformat, default=None, help="last day, default --date (at most 31 days)")
    sd.add_argument("--json", action="store_true")
    sd.add_argument("--out", type=Path, help="write the report here instead of stdout")
    c = sub.add_parser("compute-scores", help="compute sleep / recovery / strain scores into daily_scores (always recomputed in full from the scoring start; only --start to --end is written)")
    c.add_argument("--user-id", type=int, required=True)
    c.add_argument("--start", type=date.fromisoformat, help="first day written, default the scoring start (the day the watch was first worn)")
    c.add_argument("--end", type=date.fromisoformat, help="last day written, default today")
    c.add_argument("--archive-dir", type=Path,
                   help="iOS Shortcut archive (fills in HRV, respiration, blood oxygen and sleep stages for days OW lacks); "
                        "default: this user's own archive (archive/ for user 1, archive/u<id>/ for others)")

    k = sub.add_parser("sparky-link", help="link the user to a SparkyFitness account (validates the read-only API key in secrets)")
    k.add_argument("--user-id", type=int, required=True)
    k.add_argument("--key-name", required=True, help="secrets/sparkyfitness_api_key_<key-name>_ro, e.g. owner")

    r = sub.add_parser("import-records", help="migrate supplements, lab results, events, banned exercises and journal backfill from body-management.md into the database (idempotent)")
    r.add_argument("--user-id", type=int, required=True)
    j = sub.add_parser("journal-init", help="create the default journal items for a user (existing ones are left alone)")
    j.add_argument("--user-id", type=int, required=True)

    e = sub.add_parser("evaluate-rules", help="run rule reminders (double progression, calorie adjustment, pre-workout eating, frequency caps, banned exercises, recovery score) and write them to reminders (idempotent)")
    e.add_argument("--user-id", type=int, required=True)
    e.add_argument("--date", type=date.fromisoformat, help="which day to evaluate (user local date), default today")
    x = sub.add_parser("link-exercises", help="write SparkyFitness exercise IDs into exercise_meta.app_exercise_id (matched by name / alias, idempotent)")
    x.add_argument("--user-id", type=int, required=True)
    x.add_argument("--dry-run", action="store_true", help="only print the comparison table, do not write to the database")
    sx = sub.add_parser("seed-exercises", help="seed the bilingual exercise_catalog table (curated overlay + optional free-exercise-db exercises.json; idempotent)")
    sx.add_argument("--free-exercise-db", "--free-db-json", dest="free_exercise_db", type=Path, help="optional path to yuhonas/free-exercise-db dist/exercises.json (Unlicense)")
    sx.add_argument("--free-db", action="store_true", help="import the pinned free-exercise-db (downloads exercises.json into the media dir first if missing)")
    exc = sub.add_parser("exercises", help="exercise library admin (issue #189)")
    excs = exc.add_subparsers(dest="exercises_cmd", required=True)
    excs.add_parser("custom-list", help="every user's own custom exercise names and how many users added each (never who), "
                                       "to fold common ones into the shared catalog")
    og = sub.add_parser("exercises-import-opengym", help="import the openGym exercise text (pinned snapshot, text only, MIT / AGPL-3.0 per entry) into exercise_catalog; idempotent (#248)")
    og.add_argument("--file", type=Path, help="another snapshot (.json / .json.gz built by tools/build_opengym_snapshot.py); default: the one in the repository")
    og.add_argument("--dry-run", action="store_true", help="only plan: write nothing")
    og.add_argument("--report", type=Path, help="also write the full report (including every unmapped name) as JSON")
    fx = sub.add_parser("fetch-exercise-db", help="download the pinned free-exercise-db exercises.json (sha256-checked) and, with --images, its demo images "
                        "into the media dir (BODY_MEDIA_DIR, default <repo>/media); idempotent, resumable")
    fx.add_argument("--images", action="store_true", help="also download the ~1,750 demo images (about 100 MB)")

    from .nutrition import constraint_params as diet_cp
    dc = sub.add_parser("diet-constraint-set", help="add / change one of a user's own diet constraints (issue #76; versioned, "
                        "idempotent: the same value again changes nothing); --list shows what is in effect")
    dc.add_argument("--user-id", type=int, required=True)
    dc.add_argument("--list", action="store_true", help="only list the constraints in effect (with --history: every version)")
    dc.add_argument("--history", action="store_true")
    dc.add_argument("--kind", choices=list(diet_cp.KINDS))
    dc.add_argument("--value", type=float, help="the limit (g / meals / times per week); leave out for on/off kinds")
    dc.add_argument("--target", help="food_times_per_week: the food")
    dc.add_argument("--match", action="append", help="another name of the target food (repeatable)")
    dc.add_argument("--basis", help="why / source, e.g. 医生建议")
    dc.add_argument("--effective-from", type=date.fromisoformat, help="YYYY-MM-DD, default today in the user's time zone")
    dc.add_argument("--end", action="store_true", help="stop this constraint from --effective-from (history is kept)")

    m = sub.add_parser("memory-init", help="generate the body butler's initial profile card (existing database data + optional body-management.md transcription; only fills in missing items)")
    m.add_argument("--user-id", type=int, required=True)
    m.add_argument("--from-md", action="store_true", help="add the body-management.md transcription (assistant/memory_seed.py, only valid for the owner), "
                   "plus the initial status-judgement items and the 「固定早餐」 (fixed breakfast) meal template")
    rc = sub.add_parser("recipes-init", help="create metadata for recipes already in SparkyFitness (per body-management.md: which meal it suits, tags, ingredients, method; existing ones are left alone)")
    rc.add_argument("--user-id", type=int, required=True)
    sub.add_parser("memory-review-run", help="long-term memory review (issue #113): for every user with the flag memory_hygiene on, purge archived entries older than "
                   "memory_params.ARCHIVE_PURGE_DAYS and refresh the review state behind the briefing reminder (idempotent, nightly)")
    rs = sub.add_parser("refresh-status", help="recompute the status card items the backend computes automatically (weight, recovery, sleep debt, bedtime, lab results, workout count, red meat)")
    rs.add_argument("--user-id", type=int, required=True)

    g = sub.add_parser("set-sleep-goal", help="set / change the bedtime goal (moving earlier step by step from the average bedtime of the last 7 nights)")
    g.add_argument("--user-id", type=int, required=True)
    g.add_argument("--bedtime", required=True, help="target bedtime HH:MM, e.g. 00:00")
    g.add_argument("--wake", help="target wake time HH:MM (optional)")
    g.add_argument("--pace", type=int, help="minutes to move earlier per step, default 20")
    g.add_argument("--step-days", type=int, help="days per step, default 3")

    ng = sub.add_parser("nutrition-goals-import", help="backfill nutrition goal versions from the user's SparkyFitness weekly goal plans (active and inactive; idempotent; dry run unless --apply)")
    ng.add_argument("--user-id", type=int, required=True)
    ng.add_argument("--apply", action="store_true", help="write (default: only show what would be written)")
    sb = sub.add_parser("nutrition-sugar-backfill", help="parse sugar / fiber grams written in old food entry notes into food_entry_extras (idempotent; dry run unless --apply)")
    sb.add_argument("--user-id", type=int, required=True)
    sb.add_argument("--start", type=date.fromisoformat, required=True)
    sb.add_argument("--end", type=date.fromisoformat, required=True)
    sb.add_argument("--apply", action="store_true", help="write (default: only show what would be written)")

    ak = sub.add_parser("ai-key-init", help="generate the master encryption key secrets/ai_master_key for AI provider keys (mode 600; an existing one is left alone)")
    at = sub.add_parser("ai-token-create", help="issue a remote MCP token to a user's AI client (scope mcp; the plaintext is shown only once)")
    at.add_argument("--user-id", type=int, required=True)
    at.add_argument("--client", required=True, help="client name: claude-code / codex / gemini-cli / ... (recorded in the audit as mcp:<client>)")
    at.add_argument("--read-only", action="store_true", help="read-only: the client cannot see write tools")
    al = sub.add_parser("ai-token-list", help="list a user's AI client tokens")
    al.add_argument("--user-id", type=int, required=True)
    ar = sub.add_parser("ai-token-revoke", help="revoke an AI client token (takes effect immediately)")
    ar.add_argument("--user-id", type=int, required=True)
    ar.add_argument("--token-id", type=int, required=True)
    bs = sub.add_parser("briefing-stats", help="get_briefing: calls, cache hit rate and size in characters over the last days (admin, counts only)")
    bs.add_argument("--days", type=int, default=7)
    bs.add_argument("--json", action="store_true")
    oc = sub.add_parser("oauth-cleanup", help="MCP OAuth housekeeping: expired requests / codes / refresh and access tokens, clients never approved (idempotent, hourly)")
    oc.add_argument("--json", action="store_true")
    ol = sub.add_parser("oauth-connections", help="a user's approved OAuth connections to /mcp (Claude, ChatGPT, …)")
    ol.add_argument("--user-id", type=int, required=True)
    orv = sub.add_parser("oauth-revoke", help="end one OAuth connection (its access and refresh tokens stop at once)")
    orv.add_argument("--user-id", type=int, required=True)
    orv.add_argument("--connection-id", type=int, required=True)
    ai_fallback_cli.add_parser(sub)
    ai_cards_cli.add_parser(sub)
    feedback_cli.add_parser(sub)
    rollout_cli.add_parser(sub)

    hz = sub.add_parser("import-apple-zip", help="import an export.zip / export.xml exported by the Health app directly (streaming, no extraction to disk)")
    hz.add_argument("--user-id", type=int, required=True)
    hz.add_argument("--file", type=Path, required=True)
    hz.add_argument("--no-scores", action="store_true", help="do not recompute scores after importing")
    rw = sub.add_parser("import-raw", help="import only the beat-to-beat HRV series and ECG recordings from an export.zip / export.xml (rawdata/; touches no other table, idempotent)")
    rw.add_argument("--user-id", type=int, required=True)
    rw.add_argument("--file", type=Path, required=True)
    rs_ = sub.add_parser("raw-slim", help="write a small zip with only HRV beat series + ECG (no name / birth date) from a big export, to copy to the server for import-raw")
    rs_.add_argument("--file", type=Path, required=True)
    rs_.add_argument("--out", type=Path, required=True)
    hc = sub.add_parser("hrv-compare", help="SDNN vs RMSSD recovery report (computes only, writes nothing; Markdown with personal data — keep it out of git)")
    hc.add_argument("--user-id", type=int, required=True)
    hc.add_argument("--start", type=date.fromisoformat)
    hc.add_argument("--end", type=date.fromisoformat)
    hc.add_argument("--out", type=Path, help="write the report here instead of stdout")
    hc.add_argument("--no-ow", action="store_true", help="do not pull raw samples from Open Wearables (only DB sources)")
    hk = sub.add_parser("hrv-kind", help="per-user HRV kind for scoring (#117): show the rule's inputs for a user, or switch RMSSD on / off "
                                         "for them (takes effect at the next full compute-scores, which rewrites the whole history)")
    hk.add_argument("--user-id", type=int, required=True)
    hk_set = hk.add_mutually_exclusive_group()
    hk_set.add_argument("--enable-rmssd", action="store_true", help="score this user with RMSSD once they have enough beat data")
    hk_set.add_argument("--disable-rmssd", action="store_true", help="back to SDNN")
    hk.add_argument("--by", default=None, help="who made the change (stored with it)")
    hr = sub.add_parser("history-import-run", help="process history imports queued by Today page uploads (Ali: systemd timer every minute; Mac: hourly)")
    hr.add_argument("--max-jobs", type=int, default=5)

    args = p.parse_args()
    if args.cmd == "history-import-run":  # manages its own connection (autocommit + global lock)
        from .history import run as history_run
        try:
            client = OWClient()
        except Exception:  # noqa: BLE001  import works even without OW configured
            client = None
        print(json.dumps(history_run.run_pending(client, args.max_jobs), default=str, ensure_ascii=False))
        return
    if args.cmd == "lookup-barcode":  # does not connect to the database
        from .grocery import off
        print(json.dumps(off.lookup(0, args.barcode), ensure_ascii=False, indent=2))
        return
    if args.cmd == "raw-slim":  # does not connect to the database
        from .rawdata import export as rx_export
        print(json.dumps(rx_export.slim(args.file, args.out), ensure_ascii=False))
        return
    if args.cmd == "mail-test":  # does not connect to the database
        from .account import mailer
        subject, text = mailer.code_message("123456", 10)
        mailer.send(args.to, "[test] " + subject, text)
        print("sent via", mailer.mode())
        return
    if args.cmd == "fetch-exercise-db":  # does not connect to the database
        from .training import media as tr_media
        print(json.dumps(tr_media.fetch(images=args.images), ensure_ascii=False, indent=1))
        return
    if args.cmd == "ai-key-init":  # does not connect to the database
        path, created = ai_crypto.init_master_key()
        print(("created: " if created else "exists (unchanged): ") + str(path))
        return
    with connect() as conn:
        # Tester users who have not consented in the app: health data is stored but not processed (profile/consent.py)
        if args.cmd in CONSENT_GATED and getattr(args, "user_id", None) is not None:
            from .profile import consent
            if msg := consent.gate(conn, args.user_id):
                print("skip:", msg)
                return
        if args.cmd == "migrate":
            print("applied:", migrate(conn) or "nothing")
            from . import plugins
            if plugins.load():
                print("plugins applied:", plugins.migrate(conn) or "nothing")
        elif args.cmd == "plugins":
            from . import plugins
            if args.plugins_cmd == "list":
                if not plugins.load():
                    print("no plugins (BODY_PLUGIN_DIR is not set or empty)")
                for p in plugins.load():
                    jobs = ", ".join(f"{j.name} ({f'every {j.every_s}s' if j.every_s else f'daily {j.daily_at} {j.tz}'})" for j in p.jobs)
                    print(f"{p.name}: {p.path}; router {'yes' if getattr(p.module, 'router', None) else 'no'}; jobs: {jobs or '-'}")
            else:
                p = next((x for x in plugins.load() if x.name == args.plugin), None)
                job = next((j for j in (p.jobs if p else []) if j.name == args.job), None)
                if job is None:
                    raise SystemExit(f"no job {args.plugin}/{args.job}")
                print(json.dumps(plugins.run_job(p, job, conn), default=str, ensure_ascii=False))
        elif args.cmd == "grocery-sync":
            from .grocery.sync import runner as gsync
            if args.gs_cmd == "run":
                out = gsync.run(conn, [args.store] if args.store else None, force=args.force, max_pages=args.max_pages)
            elif args.gs_cmd == "status":
                out = gsync.status(conn)
            else:
                if not args.yes:
                    raise SystemExit("purge deletes all scraped store data: add --yes")
                out = gsync.purge(conn, args.store)
            print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        elif args.cmd == "user-add":
            row = conn.execute(
                """INSERT INTO users (display_name, role, birth_date, height_cm, sex, timezone)
                   VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                (args.name, args.role, args.birth_date, args.height_cm, args.sex, args.timezone)).fetchone()
            print("user id:", row["id"])
            if args.email:
                from .account import identities
                print("email:", identities.link_email(conn, row["id"], args.email))
        elif args.cmd == "identity-link":
            from .account import identities
            if args.list:
                for r in identities.list_identities(conn, args.user_id):
                    print(json.dumps(r, default=str, ensure_ascii=False))
            elif args.unlink:
                print("removed:", identities.unlink(conn, args.id, args.user_id, args.email))
            else:
                if args.user_id is None or not args.email:
                    raise SystemExit("identity-link: give --user-id and --email (or --list / --unlink)")
                try:
                    print(identities.link_email(conn, args.user_id, args.email, move=args.move))
                except identities.IdentityConflict as e:
                    raise SystemExit(f"refused: {args.email} already signs in to user {e.user_id} ({e.status}); "
                                     "--move takes it over")
        elif args.cmd == "user-list":
            for r in conn.execute("SELECT id, display_name, role, birth_date, height_cm, timezone FROM users ORDER BY id"):
                print(json.dumps(r, default=str, ensure_ascii=False))
        elif args.cmd == "notify":
            from .notify import admin as notify_admin
            out = {"status": notify_admin.status, "test": lambda c: notify_admin.test(c, args.user_id),
                   "prune": lambda c: notify_admin.prune(c, args.days)}[args.notify_cmd](conn)
            print(json.dumps(out, default=str, ensure_ascii=False, indent=2))
        elif args.cmd == "user-stats":
            try:
                res = user_stats.collect(conn, args.days, tuple(x.strip() for x in args.signals.split(",") if x.strip()))
            except ValueError as e:
                p.error(str(e))
            res = user_stats.filter_users(res, args.active_only, args.new_only)
            if not args.full_email:
                res = user_stats.masked(res)
            print(json.dumps(res, default=str, ensure_ascii=False) if args.json else user_stats.render(res))
        elif args.cmd == "login-code":
            from .web import login_codes
            print(login_codes.create(conn, args.user_id, args.label, args.hours, args.scopes.split(",")))
        elif args.cmd == "activation-code":
            from .web import activation
            if args.ac_cmd == "create":
                res = activation.create(conn, args.user_id, args.label, args.days)
                base = (os.environ.get("BODY_PUBLIC_URL") or "").rstrip("/")
                print(res["code"])
                print(f"{base or '<BODY_PUBLIC_URL>'}/app/activate.html#{res['code']}")
                print(f"# id={res['id']} {'valid until revoked' if args.days is None else f'valid {args.days} days'}; "
                      "plaintext shown only this once" + ("" if base else "; BODY_PUBLIC_URL is not set"))
                if args.email:
                    from .account import identities
                    print("# email:", identities.link_email(conn, args.user_id, args.email))
            elif args.ac_cmd == "list":
                for r in activation.list_codes(conn, args.user_id):
                    print(json.dumps(r, default=str, ensure_ascii=False))
            else:
                print("revoked" if activation.revoke(conn, args.id) else "not found / already revoked")
        elif args.cmd == "invite-code":
            from .account import invites
            if args.iv_cmd == "create":
                res = invites.create_code(conn, args.label, args.uses, args.days, args.created_by, args.code)
                print(res["code"] if args.code else invites.pretty(res["code"]))
                print(f"batch {res['batch_id']} · {res['label']} · {res['max_uses']} uses · "
                      f"expires {res['expires_at'].isoformat() if res['expires_at'] else 'never'} · plaintext shown only this once")
            elif args.iv_cmd == "batch":
                import csv
                import sys
                res = invites.create_batch(conn, args.count, args.label, args.days, args.max_uses, args.created_by)
                w = csv.writer(sys.stdout)
                w.writerow(["code", "batch_id", "label", "expires_at", "max_uses"])
                for code in res["codes"]:
                    w.writerow([invites.pretty(code), res["batch_id"], res["label"],
                                res["expires_at"].isoformat() if res["expires_at"] else "", res["max_uses"]])
            elif args.iv_cmd == "list":
                rows = invites.list_codes(conn, args.batch) if args.batch else invites.list_batches(conn, args.kind)
                for r in rows:
                    print(json.dumps(r, default=str, ensure_ascii=False))
            else:
                print("revoked" if invites.revoke(conn, args.batch, args.code_id) else "not found / already revoked")
        elif args.cmd in ("circle-admin", "circle-code"):
            _circle_admin(conn, args, p)
        elif args.cmd == "provision-run":
            from .account import delete as account_delete, provision, signin
            signin.purge_old_codes(conn)
            print("purged pending accounts:", account_delete.purge_pending(conn))
            conn.commit()
            res = [provision.run(conn, args.user_id)] if args.user_id else provision.run_pending(conn)
            print(json.dumps(res, default=str, ensure_ascii=False))
        elif args.cmd == "token-create":
            print(auth.create_token(conn, args.user_id, args.name, args.scopes.split(",")))
        elif args.cmd == "token-list":
            for r in auth.list_tokens(conn, args.user_id):
                print(json.dumps(r, default=str, ensure_ascii=False))
        elif args.cmd == "token-revoke":
            print(json.dumps(auth.revoke_tokens(conn, args.user_id, args.name, args.all)))
        elif args.cmd == "ai-token-create":
            t = ai_tokens.create(conn, args.user_id, args.client, args.read_only)
            print(t["token"])
            print(f"# id={t['id']} name={t['name']} scopes={','.join(t['scopes'])}（明文只显示这一次）")
        elif args.cmd == "ai-token-list":
            for r in ai_tokens.list_tokens(conn, args.user_id):
                print(json.dumps(r, default=str, ensure_ascii=False))
        elif args.cmd == "ai-token-revoke":
            print("revoked" if ai_tokens.revoke(conn, args.user_id, args.token_id) else "not found / already revoked")
        elif args.cmd == "briefing-stats":
            from .assistant import briefing
            st = briefing.stats(conn, args.days)
            if args.json:
                print(json.dumps(st, ensure_ascii=False))
            else:
                rate = "-" if st["hit_rate"] is None else f"{st['hit_rate']:.0%}"
                print(f"近 {st['days']} 天 get_briefing：{st['calls']} 次，缓存命中 {st['hits']} 次（{rate}），"
                      f"平均 {st['avg_chars'] or 0} 字符，最大 {st['max_chars'] or 0} 字符")
                for d in st["per_day"]:
                    print(f"  {d['day']}  {d['calls']:>4} 次  命中 {d['hit_rate']:.0%}  平均 {d['avg_chars']} / 最大 {d['max_chars']} 字符  {d['users']} 人")
        elif args.cmd == "oauth-cleanup":
            from .ai import oauth
            print(json.dumps(oauth.cleanup(conn)))
        elif args.cmd == "oauth-connections":
            from .ai import oauth
            for r in oauth.list_grants(conn, args.user_id):
                print(json.dumps(r, default=str, ensure_ascii=False))
        elif args.cmd == "oauth-revoke":
            from .ai import oauth
            print("revoked" if oauth.revoke_grant(conn, args.connection_id, "admin", user_id=args.user_id) else "not found / already revoked")
        elif args.cmd == "ai-fallback":
            ai_fallback_cli.run(conn, args)
        elif args.cmd == "ai-cards":
            ai_cards_cli.run(conn, args)
        elif args.cmd in rollout_cli.COMMANDS:
            try:
                rollout_cli.run(conn, args)
            except (KeyError, ValueError) as e:
                p.error(str(e))
        elif args.cmd == "feedback":
            feedback_cli.run(conn, args)
        elif args.cmd == "import-apple-export":
            export_date = args.export_date or _export_date(args.processed_dir)
            print(f"export date: {export_date}")
            print(json.dumps(apple_export.run(conn, args.user_id, args.processed_dir, export_date), default=str))
        elif args.cmd == "import-apple-zip":
            from .importers import apple_zip
            res = apple_zip.run(conn, args.user_id, args.file)
            conn.commit()
            if not args.no_scores:
                ow_client = OWClient()
                res["workouts_enriched"] = workouts_importer.enrich_pending(conn, ow_client, args.user_id)
                res["scores"] = scores_engine.run(conn, ow_client, args.user_id)
            print(json.dumps(res, default=str, ensure_ascii=False))
        elif args.cmd == "import-raw":
            from .rawdata import export as rx_export
            print(json.dumps(rx_export.run(conn, args.user_id, args.file), default=str, ensure_ascii=False))
        elif args.cmd == "hrv-kind":
            from zoneinfo import ZoneInfo
            from .scores import hrv_kind as score_hrv_kind
            if args.enable_rmssd or args.disable_rmssd:
                score_hrv_kind.set_rmssd_enabled(conn, args.user_id, args.enable_rmssd, args.by)
            tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (args.user_id,)).fetchone()["timezone"])
            from . import queries
            today = queries.local_today(conn, args.user_id)
            origin = scores_engine.scoring_origin(conn, args.user_id) or today
            kind, reason = score_hrv_kind.choose(conn, args.user_id, tz, origin, today)
            print(json.dumps({"user_id": args.user_id, "rmssd_enabled": score_hrv_kind.rmssd_enabled(conn, args.user_id),
                              "stored_kinds": sorted(score_hrv_kind.stored_kinds(conn, args.user_id)),
                              "coverage": score_hrv_kind.coverage(conn, args.user_id, tz, today),
                              "full_recompute_would_use": kind, "reason": reason,
                              "note": "只有完整的 compute-scores（夜间任务，或不带 --start）才会切换口径并重写全部历史"},
                             default=str, ensure_ascii=False))
        elif args.cmd == "hrv-compare":
            from .rawdata import compare as rx_compare
            res = rx_compare.compare(conn, None if args.no_ow else OWClient(), args.user_id, args.start, args.end)
            text = rx_compare.markdown(res)
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(text, encoding="utf-8")
                print("written:", args.out)
            else:
                print(text)
        elif args.cmd == "import-shortcut":
            print(json.dumps(shortcut.run(conn, args.user_id, args.archive_dir or shortcut.user_archive_dir(args.user_id),
                                          args.all), default=str))
            if args.rebuild_since:
                print(json.dumps(shortcut.rebuild(conn, args.user_id, args.rebuild_since), default=str, ensure_ascii=False))
        elif args.cmd == "ow-link":
            print("ow user id:", ow.link_user(conn, OWClient(), args.user_id, args.ow_user_id))
        elif args.cmd == "sparky-link":
            print("sparkyfitness user id:", sparky_client.link_user(conn, args.user_id, args.key_name))
        elif args.cmd == "import-ow":
            print(json.dumps(ow.run(conn, OWClient(), args.user_id, args.start, args.end, args.full,
                                    args.xml_energy_unit), default=str))
        elif args.cmd == "import-workouts":
            print(json.dumps(workouts_importer.import_ow(conn, OWClient(), args.user_id, args.start, args.end),
                             default=str, ensure_ascii=False))
        elif args.cmd == "history-backfill-run":
            from .sync import history as health_history
            print(json.dumps(health_history.run_queued(conn, args.user_id), default=str, ensure_ascii=False))
        elif args.cmd == "sync-refresh-run":
            from .sync import refresh as sync_refresh
            print(json.dumps(sync_refresh.run_queued(conn, args.user_id), default=str, ensure_ascii=False))
        elif args.cmd == "hk-derive-run":
            from .rawdata import derive as hk_derive
            print(json.dumps(hk_derive.run(conn, args.user_id, args.max_days), default=str, ensure_ascii=False))
        elif args.cmd == "sync-shadow-day":
            from .rawdata import side_by_side
            res = side_by_side.days(conn, args.user_id, args.date, args.end)
            text = json.dumps(res, default=str, ensure_ascii=False, indent=1) if args.json else side_by_side.markdown(res)
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(text, encoding="utf-8")
                print("written:", args.out)
            else:
                print(text)
        elif args.cmd == "compute-scores":
            ow_client = OWClient()
            res = scores_engine.run(conn, ow_client, args.user_id, args.start, args.end,
                                    args.archive_dir or shortcut.user_archive_dir(args.user_id))
            res["insights"] = insights_jobs.run(conn, ow_client, args.user_id)   # body age (weekly), stress
            print(json.dumps(res, default=str, ensure_ascii=False))
        elif args.cmd == "import-records":
            print(records_importer.format_report(records_importer.run(conn, args.user_id)))
        elif args.cmd == "evaluate-rules":
            print(rules_engine.format_report(rules_engine.run(conn, args.user_id, args.date)))
        elif args.cmd == "link-exercises":
            print(exercise_link.format_report(exercise_link.run(conn, args.user_id, dry_run=args.dry_run)))
        elif args.cmd == "exercises":
            from .training import custom as tr_custom
            rows = tr_custom.admin_names(conn)
            print("\n".join(f"{r['users']:>4}  {r['name_zh']}" + (f"  ({r['name_en']})" if r["name_en"] else "") + f"  {r['last_added']}"
                            for r in rows) or "(none)")
        elif args.cmd == "seed-exercises":
            from .training import catalog as tr_catalog, media as tr_media
            src = tr_media.fetch_json() if args.free_db and not args.free_exercise_db else args.free_exercise_db
            print(json.dumps(tr_catalog.seed_catalog(conn, src), ensure_ascii=False))
        elif args.cmd == "exercises-import-opengym":
            from .training.opengym import importer as og_importer
            rep = og_importer.run(conn, og_importer.load_snapshot(args.file) if args.file else None, dry_run=args.dry_run)
            if args.report:
                args.report.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
            print(og_importer.format_report(rep))
        elif args.cmd == "diet-constraint-set":
            from .nutrition import constraints as diet_constraints
            if not conn.execute("SELECT 1 FROM users WHERE id = %s", (args.user_id,)).fetchone():
                p.error(f"no user {args.user_id}")
            if args.list:
                out = diet_constraints.listing(conn, args.user_id, args.history)
                out.pop("kinds")
            elif not args.kind:
                p.error("--kind is required (or use --list)")
            else:
                try:
                    out = (diet_constraints.end_constraint(conn, args.user_id, args.kind, args.target, args.effective_from)
                           if args.end else
                           diet_constraints.set_constraint(conn, args.user_id, args.kind, args.value, args.target, args.match,
                                                           args.basis, args.effective_from, source="user"))
                except ValueError as e:
                    p.error(str(e))
            print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        elif args.cmd == "memory-init":
            res = assistant_memory.init_profile(conn, args.user_id, memory_seed.MD_PROFILE if args.from_md else None)
            res["status"] = assistant_status.init_status(conn, args.user_id, with_seed=args.from_md)
            if args.from_md:
                res["meal_templates"] = assistant_meals.seed_templates(conn, args.user_id)
            print(json.dumps(res, ensure_ascii=False))
        elif args.cmd == "nutrition-goals-import":
            from .goals import nutrition as nutrition_goals
            print(json.dumps(nutrition_goals.import_sparky_plans(conn, args.user_id, apply=args.apply),
                             default=str, ensure_ascii=False, indent=1))
        elif args.cmd == "nutrition-sugar-backfill":
            from .nutrition import extras as nutrition_extras
            print(json.dumps(nutrition_extras.backfill_from_notes(conn, args.user_id, args.start, args.end, apply=args.apply),
                             default=str, ensure_ascii=False, indent=1))
        elif args.cmd == "set-sleep-goal":
            print(json.dumps(goal_store.set_goal(conn, args.user_id, args.bedtime, args.wake, args.pace, args.step_days),
                             default=str, ensure_ascii=False, indent=1))
        elif args.cmd == "recipes-init":
            print(json.dumps(assistant_recipes.seed(conn, args.user_id), ensure_ascii=False))
        elif args.cmd == "memory-review-run":
            from .assistant import memory_review
            for r in memory_review.run_all(conn):
                print(json.dumps(r, ensure_ascii=False))
        elif args.cmd == "refresh-status":
            print(json.dumps(assistant_status.refresh_auto(conn, args.user_id), ensure_ascii=False))
        elif args.cmd == "journal-init":
            print(json.dumps(record_defaults.init_journal_items(conn, args.user_id)))


if __name__ == "__main__":
    main()

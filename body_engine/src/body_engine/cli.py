"""Command line: body-engine <command>. On the Mac use `uv run body-engine ...`; in the container the command has the same name."""
import argparse
import json
import os
import re
from datetime import date
from pathlib import Path

from . import auth, sparky_client
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
from .ai import crypto as ai_crypto, fallback_cli as ai_fallback_cli, tokens as ai_tokens


CONSENT_GATED = {"import-ow", "import-shortcut", "import-workouts", "compute-scores", "import-apple-zip", "import-raw", "hrv-compare",
                 "evaluate-rules", "refresh-status"}


def _export_date(processed_dir: Path) -> date:
    """Export date: prefer the file name of full_export/export_YYYY-MM-DD.zip, otherwise the latest date in daily_metrics.csv."""
    zips = sorted(processed_dir.parent.glob("full_export/export_*.zip"))
    if zips and (m := re.search(r"\d{4}-\d{2}-\d{2}", zips[-1].name)):
        return date.fromisoformat(m.group())
    with open(processed_dir / "daily_metrics.csv") as f:
        return max(date.fromisoformat(line.split(",")[1]) for line in list(f)[1:])


def main() -> None:
    p = argparse.ArgumentParser(prog="body-engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="create / upgrade the table schema")

    u = sub.add_parser("user-add", help="create a user")
    u.add_argument("--name", required=True)
    u.add_argument("--role", choices=["owner", "member"], default="member")
    u.add_argument("--birth-date", type=date.fromisoformat)
    u.add_argument("--height-cm", type=float)
    u.add_argument("--sex")
    u.add_argument("--timezone", default="Australia/Sydney")
    sub.add_parser("user-list", help="list users")

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
    acl = acs.add_parser("list", help="list activation codes (no plaintext)")
    acl.add_argument("--user-id", type=int)
    acr = acs.add_parser("revoke", help="revoke an activation code by id (devices already set up stay signed in)")
    acr.add_argument("--id", type=int, required=True)
    iv = sub.add_parser("invite-code", help="beta invite codes for self-service sign-up (account/invites.py)")
    ivs = iv.add_subparsers(dest="iv_cmd", required=True)
    ivb = ivs.add_parser("batch", help="create a batch; prints CSV code,batch_id,label,expires_at,max_uses (plaintext shown only once)")
    ivb.add_argument("--count", type=int, required=True)
    ivb.add_argument("--label", required=True, help="who / which channel the batch is for")
    ivb.add_argument("--days", type=int, help="valid for D days (default: no expiry)")
    ivb.add_argument("--max-uses", type=int, default=1, help="accounts per code (default 1)")
    ivb.add_argument("--created-by", help="free text recorded with the batch")
    ivl = ivs.add_parser("list", help="batches with uses / free codes; --batch N: its codes and who redeemed them")
    ivl.add_argument("--batch", type=int)
    ivr = ivs.add_parser("revoke", help="stop a batch or one code (accounts already opened stay)")
    ivg = ivr.add_mutually_exclusive_group(required=True)
    ivg.add_argument("--batch", type=int)
    ivg.add_argument("--code-id", type=int)
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
    fx = sub.add_parser("fetch-exercise-db", help="download the pinned free-exercise-db exercises.json (sha256-checked) and, with --images, its demo images "
                        "into the media dir (BODY_MEDIA_DIR, default <repo>/media); idempotent, resumable")
    fx.add_argument("--images", action="store_true", help="also download the ~1,750 demo images (about 100 MB)")

    m = sub.add_parser("memory-init", help="generate the body butler's initial profile card (existing database data + optional body-management.md transcription; only fills in missing items)")
    m.add_argument("--user-id", type=int, required=True)
    m.add_argument("--from-md", action="store_true", help="add the body-management.md transcription (assistant/memory_seed.py, only valid for the owner), "
                   "plus the initial status-judgement items and the 「固定早餐」 (fixed breakfast) meal template")
    rc = sub.add_parser("recipes-init", help="create metadata for recipes already in SparkyFitness (per body-management.md: which meal it suits, tags, ingredients, method; existing ones are left alone)")
    rc.add_argument("--user-id", type=int, required=True)
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
    ai_fallback_cli.add_parser(sub)

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
        elif args.cmd == "user-add":
            row = conn.execute(
                """INSERT INTO users (display_name, role, birth_date, height_cm, sex, timezone)
                   VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                (args.name, args.role, args.birth_date, args.height_cm, args.sex, args.timezone)).fetchone()
            print("user id:", row["id"])
        elif args.cmd == "user-list":
            for r in conn.execute("SELECT id, display_name, role, birth_date, height_cm, timezone FROM users ORDER BY id"):
                print(json.dumps(r, default=str, ensure_ascii=False))
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
            elif args.ac_cmd == "list":
                for r in activation.list_codes(conn, args.user_id):
                    print(json.dumps(r, default=str, ensure_ascii=False))
            else:
                print("revoked" if activation.revoke(conn, args.id) else "not found / already revoked")
        elif args.cmd == "invite-code":
            from .account import invites
            if args.iv_cmd == "batch":
                import csv
                import sys
                res = invites.create_batch(conn, args.count, args.label, args.days, args.max_uses, args.created_by)
                w = csv.writer(sys.stdout)
                w.writerow(["code", "batch_id", "label", "expires_at", "max_uses"])
                for code in res["codes"]:
                    w.writerow([invites.pretty(code), res["batch_id"], res["label"],
                                res["expires_at"].isoformat() if res["expires_at"] else "", res["max_uses"]])
            elif args.iv_cmd == "list":
                rows = invites.list_codes(conn, args.batch) if args.batch else invites.list_batches(conn)
                for r in rows:
                    print(json.dumps(r, default=str, ensure_ascii=False))
            else:
                print("revoked" if invites.revoke(conn, args.batch, args.code_id) else "not found / already revoked")
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
        elif args.cmd == "ai-fallback":
            ai_fallback_cli.run(conn, args)
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
        elif args.cmd == "seed-exercises":
            from .training import catalog as tr_catalog, media as tr_media
            src = tr_media.fetch_json() if args.free_db and not args.free_exercise_db else args.free_exercise_db
            print(json.dumps(tr_catalog.seed_catalog(conn, src), ensure_ascii=False))
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
        elif args.cmd == "refresh-status":
            print(json.dumps(assistant_status.refresh_auto(conn, args.user_id), ensure_ascii=False))
        elif args.cmd == "journal-init":
            print(json.dumps(record_defaults.init_journal_items(conn, args.user_id)))


if __name__ == "__main__":
    main()

"""`python -m body_engine.validation synthetic | user` (see __init__.py). Prints Markdown to stdout."""
import argparse
import dataclasses
import sys
from datetime import timedelta

from . import study, synthetic


def _synthetic(args) -> str:
    p = synthetic.make_person(args.days, args.seed, args.reading_noise)
    base = study.score(p.inputs, p.first, p.last)
    clean = synthetic.make_person(args.days, args.seed, 0.08)   # same person, much less single-reading noise
    agree = study.agreement(base, study.score(clean.inputs, clean.first, clean.last))
    sens = None if args.no_sensitivity else study.sensitivity(p.inputs, p.first, p.last, base=base)
    out_noise = ""
    if args.noise_sweep:
        out_noise = "\n## Single-reading noise sweep (same person, only the HRV reading noise changes)\n\n" \
            "| ln HRV noise | recovery vs truth | nightly HRV vs truth | recovery lag-1 |\n|---|---|---|---|\n"
        for noise in (0.0, 0.15, 0.30, 0.45, 0.60):
            q = synthetic.make_person(args.days, args.seed, noise)
            rows = study.score(q.inputs, q.first, q.last)
            v = study.validity(rows, q.readiness)
            out_noise += f"| {noise} | {v['recovery_vs_truth']} | {v['hrv_vs_truth']} | {study.lag1(rows, 'recovery')[0]} |\n"
    title = (f"Synthetic person ({args.days} days, seed {args.seed}, single-reading ln HRV noise {args.reading_noise})")
    return study.markdown(title, base, p.readiness, sens, agree, (f"noise {args.reading_noise}", "noise 0.08"),
                          p.illness) + out_noise


def _user(args) -> str:
    from .. import db, queries
    from ..ow_client import OWClient
    from ..scores import engine as SE
    from ..scores import inputs as SI
    from ..scores import params as P

    kinds = args.kinds.split(",")
    with db.connect() as conn:
        origin = SE.scoring_origin(conn, args.user_id)
        if origin is None:
            raise SystemExit("no watch data for this user")
        end = queries.local_today(conn, args.user_id) - timedelta(days=1)
        inp = SI.load(conn, None if args.no_ow else OWClient(), args.user_id,
                      origin - timedelta(days=P.HRMAX_WINDOW_DAYS), end, None, kinds[0])
        other = None
        if len(kinds) > 1:
            other = dataclasses.replace(inp, hrv_kind=kinds[1], hrv=SI.rmssd_hrv(
                conn, args.user_id, inp.tz, origin - timedelta(days=P.HRMAX_WINDOW_DAYS), end)) \
                if kinds[1] == "rmssd" else None
    rows = study.score(inp, origin, end)
    alt = study.score(other, origin, end) if other is not None else None
    agree = study.agreement(rows, alt) if alt is not None else None
    sens = None if args.no_sensitivity else study.sensitivity(inp, origin, end, base=rows)
    out = study.markdown(f"User {args.user_id} (HRV {kinds[0]}; aggregates only)", rows, None, sens, agree,
                         tuple(kinds[:2]) if alt is not None else ("A", "B"))
    if alt is not None:
        out += "\n" + study.markdown(f"User {args.user_id} (HRV {kinds[1]}; aggregates only)", alt)
    return out


def _healthspan(args) -> str:
    import json

    from .. import db
    from . import healthspan_compare

    with db.connect() as conn:
        res = healthspan_compare.compare(conn, args.user_id, args.weeks, not args.no_sensitivity)
    if res is None:
        raise SystemExit("no watch data or no birth date for this user")
    return json.dumps(res, default=str, ensure_ascii=False, indent=1) + "\n" if args.json \
        else healthspan_compare.markdown(res)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m body_engine.validation")
    sub = ap.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("synthetic")
    s.add_argument("--days", type=int, default=180)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--reading-noise", type=float, default=0.30)
    s.add_argument("--no-sensitivity", action="store_true")
    s.add_argument("--noise-sweep", action="store_true")
    u = sub.add_parser("user", help="reads the database only; prints aggregates (keep the output out of git)")
    u.add_argument("--user-id", type=int, required=True)
    u.add_argument("--kinds", default="sdnn", help="sdnn, or sdnn,rmssd to add the agreement of the two")
    u.add_argument("--no-ow", action="store_true")
    u.add_argument("--no-sensitivity", action="store_true")
    h = sub.add_parser("healthspan", help="body age v1.1 vs v1.2; reads the database only (keep the output out of git)")
    h.add_argument("--user-id", type=int, required=True)
    h.add_argument("--weeks", type=int, default=26)
    h.add_argument("--json", action="store_true")
    h.add_argument("--no-sensitivity", action="store_true")
    args = ap.parse_args(argv)
    sys.stdout.write({"synthetic": _synthetic, "user": _user, "healthspan": _healthspan}[args.mode](args))


if __name__ == "__main__":
    main()

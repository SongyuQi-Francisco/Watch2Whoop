"""Where each 2.0.0 package is computed for a user: off | shadow | primary (docs/ios-native-2.0.md sections 3.4 and 5).

- off: the app shows the server's documents and computes nothing of that package (the default for everyone).
- shadow: the package's flag is on for the user: the app computes locally, still shows the server's values, uploads its
  rows (derived.py); the server spot-checks them and keeps its own rows.
- primary: also `native_compute_primary` is on, a grant covers the user for the package (2.0.0 S9, primary.py:
  `body-engine app-compute primary`, checked against the spot-check pass rate), and for every upload kind of the package
  the last spot check passed, at least PRIMARY_MIN_DAYS days were uploaded, with the server's algorithm version, not
  forced back by an admin. The app shows its own values and the server stores its uploads as the user's rows; the
  server's scheduled jobs leave the package to the phone (primary.server_primary).
A package is off for everyone while this server does not implement it (algo/versions.py has no constant for one of its
algorithm keys, the served bundle lacks the key, or one of its upload kinds has no module in derived_kinds/), and for
a client older than params.MIN_APP_VERSION_BY_KIND.

GET /v1/sync/compute = the scoring context of GET /v1/sync/scoring (scoring.py) + `compute`: per package its state, per
upload kind its state and last check, and the server's algorithm and bundle versions.
"""
from __future__ import annotations

import re
from datetime import datetime

import psycopg

from ..algo import versions
from ..rollout import flags
from . import derived_kinds, params as P

_VERSION = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?")


# ---------------------------------------------------------------- app version
def _vtuple(v: str | None) -> tuple[int, int, int] | None:
    m = _VERSION.match((v or "").strip())
    return tuple(int(x or 0) for x in m.groups()) if m else None   # type: ignore[return-value]


def app_meets(version: str | None, build: str | None, spec: str) -> bool:
    """True when the client (X-App-Version, X-App-Build) is at least `spec` ("2.0.0", or "2.0.0+57": version 2.0.0 from
    build 57 on, for internal builds). A client that sends no version never meets it."""
    want_v, _, want_b = spec.partition("+")
    have, want = _vtuple(version), _vtuple(want_v)
    if have is None or want is None:
        return False
    if have != want or not want_b:
        return have >= want
    try:
        return int(build or "") >= int(want_b)
    except ValueError:
        return False


# ---------------------------------------------------------------- what this server implements
def _bundle_keys() -> dict[str, str]:
    from .. import algo
    try:
        m, _ = algo.bundle()
    except algo.Unavailable:
        return {}
    return dict(m.get("algo_versions") or {})


def algo_ready(key: str, bundle: dict[str, str] | None = None) -> bool:
    """The algorithm has a Python constant and the served bundle carries the same version of it."""
    v = versions.current(key)
    return v is not None and (bundle if bundle is not None else _bundle_keys()).get(key) == v


def package_implemented(pkg: str, bundle: dict[str, str] | None = None) -> bool:
    spec = P.COMPUTE_PACKAGES[pkg]
    bundle = _bundle_keys() if bundle is None else bundle
    return all(algo_ready(k, bundle) for k in spec["algo"]) and all(derived_kinds.get(k) for k in spec["kinds"])


# ---------------------------------------------------------------- per-user state
def kind_row(conn: psycopg.Connection, uid: int, kind: str) -> dict | None:
    return conn.execute("SELECT * FROM app_compute_state WHERE user_id = %s AND kind = %s", (uid, kind)).fetchone()


def trusted(row: dict | None, algo_version: str | None) -> bool:
    """The kind's uploads may be stored as the user's (the primary conditions of the module doc, without the flags)."""
    return bool(row and not row["server_forced"] and row["trusted"] and row["last_check_ok"]
                and row["days_uploaded"] >= P.PRIMARY_MIN_DAYS and algo_version and row["algo_version"] == algo_version)


def primary_on(conn: psycopg.Connection, uid: int, pkg: str) -> bool:
    """The global switch is on for the user and a grant covers them for this package (S9, primary.py)."""
    from . import primary
    return flags.enabled(conn, uid, P.COMPUTE_PRIMARY_FLAG) and primary.granted(conn, uid, pkg)


def kind_state(conn: psycopg.Connection, uid: int, kind: str, app_version: str | None, app_build: str | None,
               bundle: dict[str, str] | None = None) -> str:
    """off | shadow | primary of one upload kind for this user and client."""
    mod = derived_kinds.get(kind)
    if mod is None:
        return "off"
    pkg = mod.PACKAGE
    if not app_meets(app_version, app_build, P.MIN_APP_VERSION_BY_KIND[pkg]) or not algo_ready(mod.ALGO_KEY, bundle):
        return "off"
    if not flags.enabled(conn, uid, P.COMPUTE_PACKAGES[pkg]["flag"]):
        return "off"
    if primary_on(conn, uid, pkg) and trusted(kind_row(conn, uid, kind), versions.current(mod.ALGO_KEY)):
        return "primary"
    return "shadow"


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else v


def _last_check(conn: psycopg.Connection, uid: int, kind: str) -> dict | None:
    r = conn.execute("""SELECT day, ok, fields_over, compared, bundle_version, checked_at FROM app_compute_checks
                        WHERE user_id = %s AND kind = %s ORDER BY checked_at DESC, id DESC LIMIT 1""", (uid, kind)).fetchone()
    if r is None:
        return None
    return {"date": r["day"].isoformat(), "ok": r["ok"], "fields_over": list(r["fields_over"]), "compared": r["compared"],
            "bundle_version": r["bundle_version"], "checked_at": r["checked_at"].isoformat()}


def overview(conn: psycopg.Connection, uid: int, app_version: str | None, app_build: str | None) -> dict:
    """The `compute` block of GET /v1/sync/compute."""
    bundle = _bundle_keys()
    order = {s: i for i, s in enumerate(P.COMPUTE_STATES)}
    packages = {}
    for pkg, spec in P.COMPUTE_PACKAGES.items():
        implemented = package_implemented(pkg, bundle)
        flag_on = flags.enabled(conn, uid, spec["flag"])
        app_ok = app_meets(app_version, app_build, P.MIN_APP_VERSION_BY_KIND[pkg])
        kinds = {}
        for k in spec["kinds"]:
            row = kind_row(conn, uid, k)
            kinds[k] = {"state": kind_state(conn, uid, k, app_version, app_build, bundle) if implemented else "off",
                        "registered": derived_kinds.get(k) is not None,
                        "trusted": None if row is None else trusted(row, row["algo_version"]),
                        "days_uploaded": row["days_uploaded"] if row else 0,
                        "last_upload_at": _iso(row["last_upload_at"]) if row else None,
                        "last_check": _last_check(conn, uid, k)}
        if not (implemented and flag_on and app_ok):
            state = "off"
        elif kinds:   # the package is as far as its least advanced kind
            state = min((v["state"] for v in kinds.values()), key=order.__getitem__)
        else:         # views only (series, reports): nothing to spot-check on the server (developer comparison, 3.4)
            state = "primary" if primary_on(conn, uid, pkg) else "shadow"
        packages[pkg] = {"state": state, "flag": flag_on, "implemented": implemented, "app_ok": app_ok,
                         "primary_granted": primary_on(conn, uid, pkg),
                         "min_app_version": P.MIN_APP_VERSION_BY_KIND[pkg],
                         "algo_versions": {k: versions.current(k) for k in spec["algo"]}, "kinds": kinds}
    return {"packages": packages, "primary_flag": flags.enabled(conn, uid, P.COMPUTE_PRIMARY_FLAG),
            "server_algo_versions": versions.server_versions(), "bundle_algo_versions": bundle,
            "states": list(P.COMPUTE_STATES)}


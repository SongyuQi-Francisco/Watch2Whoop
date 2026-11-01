"""Registry of the kinds the phone uploads with PUT /v1/users/me/derived/app (docs/ios-native-2.0.md sections 2.2, 3.2).

One module per kind, `derived_kinds/<kind>.py`, found and loaded automatically: a package adds its module and nothing
else (derived.py, pull.py and bodies.py iterate this registry). No kind ships in P0; tests register a fictional one.

A kind module defines:
  KIND: str          its name (= the module name), e.g. "daily"
  PACKAGE: str       key of params.COMPUTE_PACKAGES it belongs to (its flag, its minimum app version)
  ALGO_KEY: str      key of algo/versions.py; an upload must carry exactly the server's version of it
  TOLERANCE: dict    field -> absolute tolerance (float) or ("rel", x); fields not listed are compared exactly.
                     Sources of the numbers in a comment next to them (section 3.4).
  validate(day, rows) -> list[dict]
                     the rows of one day as the server stores them; raises ValueError (text through i18n.L) when malformed
  write(conn, uid, days: {date: [rows]}, bundle_version) -> int
                     store the rows as the user's (only while the kind is primary); returns rows written; never commits
  recompute(conn, uid, day, uploaded: {date: [rows]}) -> list[dict] | None
                     the Python twin's rows of one day for the spot check, from the server's tables plus the phone's
                     uploaded inputs of the kinds below it (section 3.2); None = nothing to compare with
optional:
  row_key(row) -> hashable
                     matches app and server rows in the comparison (default: their position)
  RECORD_KIND, RECORD_TABLE, RECORD_DAYCOL, record_body(conn, uid, days) -> {day: body}
                     a kind of the `records` pull stream (whole-day replace), e.g. stress / healthspan / reminders; its
                     table needs the sync_changes trigger (sync_attach) in the package's migration
  extend_pull(stream, kind, conn, uid, found: {day: body}) -> None
                     adds fields to the bodies of an existing stream (e.g. `sources` in `daily`)
"""
from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType
from typing import Any

REQUIRED = ("KIND", "PACKAGE", "ALGO_KEY", "TOLERANCE", "validate", "write", "recompute")
_kinds: dict[str, Any] | None = None


class BadKind(Exception):
    """A kind module that does not follow the contract above (fails at load, so a test catches it)."""


def _check(mod: Any) -> Any:
    from .. import params as P
    missing = [a for a in REQUIRED if not hasattr(mod, a)]
    if missing:
        raise BadKind(f"derived kind {getattr(mod, 'KIND', mod)!r} misses {missing}")
    planned = mod.PACKAGE in P.COMPUTE_PACKAGES and mod.KIND in P.COMPUTE_PACKAGES[mod.PACKAGE]["kinds"]
    if mod.PACKAGE not in P.COMPUTE_PACKAGES or not (planned or getattr(mod, "TEST_ONLY", False)):
        raise BadKind(f"derived kind {mod.KIND!r}: not a kind of package {mod.PACKAGE!r} (params.COMPUTE_PACKAGES)")
    if hasattr(mod, "RECORD_KIND") and not all(hasattr(mod, a) for a in ("RECORD_TABLE", "RECORD_DAYCOL", "record_body")):
        raise BadKind(f"derived kind {mod.KIND!r}: RECORD_KIND needs RECORD_TABLE, RECORD_DAYCOL and record_body")
    return mod


def _load() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        mod: ModuleType = importlib.import_module(f"{__name__}.{info.name}")
        if getattr(mod, "KIND", None) != info.name:
            raise BadKind(f"derived_kinds/{info.name}.py: KIND must be {info.name!r}")
        out[info.name] = _check(mod)
    return out


def kinds() -> dict[str, Any]:
    """{kind: module} of every registered kind (loaded once per process)."""
    global _kinds
    if _kinds is None:
        _kinds = _load()
    return _kinds


def get(kind: str) -> Any | None:
    return kinds().get(kind)


def register(mod: Any) -> None:
    """Add a kind at run time (tests: a fictional kind with TEST_ONLY = True)."""
    kinds()[mod.KIND] = _check(mod)


def unregister(kind: str) -> None:
    kinds().pop(kind, None)


def record_kinds() -> dict[str, tuple]:
    """Extra kinds of the `records` pull stream: {record kind: (builder, table, day column)}, as bodies.RECORD_KINDS."""
    return {m.RECORD_KIND: (m.record_body, m.RECORD_TABLE, m.RECORD_DAYCOL)
            for m in kinds().values() if getattr(m, "RECORD_KIND", None)}


def extend_pull(stream: str, kind: str, conn, uid: int, found: dict) -> None:
    """Let every kind that extends an existing stream add its fields to the bodies just built (in place)."""
    for m in kinds().values():
        fn = getattr(m, "extend_pull", None)
        if fn is not None:
            fn(stream, kind, conn, uid, found)

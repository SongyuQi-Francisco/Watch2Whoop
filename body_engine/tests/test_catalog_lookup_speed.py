"""Exercise lookups stay fast with the ~6,300-row library (Today page slowdown, 2026-10-26).

- resolve_with_candidates / _match_meta_to_slug answer exactly what the old full scans answered (reference copies below),
  over the whole openGym import, with a user's restrictions, own names, SparkyFitness ids and custom exercises.
- The catalog change check is one primary-key read (migration 850 trigger), and a write to exercise_catalog is seen at once.
- Inside lookup_scope() a plan's ~30 lookups read the catalog, exercise_meta and the custom rows once.
Uses its own test database (body_test_catalog_lookup), dropped and recreated on every run; skipped if Postgres is unreachable.
"""
import os
import random

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.records import store as records_store
from body_engine.rollout import flags
from body_engine.training import catalog, custom
from body_engine.training.catalog import norm, strip_paren
from body_engine.training.opengym import importer

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_CATALOG_LOOKUP", "body_test_catalog_lookup")


# ---------------------------------------------------------------- reference: the scans before the index (2026-10-25)
def ref_match(cat_rows, meta_by_ref):
    by_slug, unmatched = {}, []
    cat_slugs = {r["slug"] for r in cat_rows}
    for ref, m in meta_by_ref.items():
        (by_slug.__setitem__(ref, m) if ref in cat_slugs else unmatched.append(m))
    if unmatched:
        m_names = [(m, {norm(x) for x in [m["exercise_ref"], m.get("name_zh"), m.get("name_en"), *(m.get("aliases") or [])] if x})
                   for m in unmatched]
        for r in cat_rows:
            if r["slug"] in by_slug:
                continue
            r_names = r.get("_names") or {norm(x) for x in [r["slug"], r["name_zh"], r["name_en"], *(r.get("aliases") or [])] if x}
            for m, names in m_names:
                if r_names & names:
                    by_slug[r["slug"]] = m
                    break
    for r in cat_rows:
        fam = r.get("family_slug")
        if fam and r["slug"] not in by_slug and fam in by_slug and by_slug[fam]["status"] != "active":
            by_slug[r["slug"]] = {**by_slug[fam], "app_exercise_id": None, "inherited_from": fam}
    return by_slug


def ref_resolve(cat_rows, meta_by_slug, query):
    q_raw = (query or "").strip()
    q, q_short = norm(q_raw), norm(strip_paren(q_raw))
    if not q:
        return None, None, []
    for r in cat_rows:
        m = meta_by_slug.get(r["slug"])
        if r["slug"] == q_raw or r["slug"] == q or (m and m.get("app_exercise_id") == q_raw):
            return r, m, []
    hits = []
    for r in cat_rows:
        m = meta_by_slug.get(r["slug"])
        names = [r["name_zh"], r["name_en"], r["slug"], *(r.get("aliases") or [])]
        if m:
            names += [m.get("name_zh"), m.get("name_en"), m.get("exercise_ref"), *(m.get("aliases") or [])]
        if any(norm(x) == q for x in names if x):
            hits.append((r, m))
    if len(hits) == 1:
        return hits[0][0], hits[0][1], []
    if not hits and q_short:
        for r in cat_rows:
            m = meta_by_slug.get(r["slug"])
            if any(norm(strip_paren(x)) == q_short for x in [r["name_zh"], r["name_en"], *(r.get("aliases") or [])] if x):
                hits.append((r, m))
        if len(hits) == 1:
            return hits[0][0], hits[0][1], []
    if hits:
        return hits[0][0], hits[0][1], [r["name_zh"] for r, _ in hits[:8]]
    cands = []
    for r in cat_rows:
        m = meta_by_slug.get(r["slug"])
        names = [r["name_zh"], r["name_en"], *(r.get("aliases") or [])]
        if m:
            names += [m.get("name_zh"), m.get("name_en"), *(m.get("aliases") or [])]
        if any(q in norm(x) or (norm(strip_paren(x)) and norm(strip_paren(x)) in q) for x in names if x):
            cands.append(r["name_zh"])
    return None, None, cands[:8]


# ---------------------------------------------------------------- database
@pytest.fixture(scope="module")
def conn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    with psycopg.connect(Settings(db_name=TEST_DB).dsn, row_factory=dict_row, autocommit=True) as c:
        migrate(c)
        catalog.ensure_seeded(c)
        importer.run(c)
        flags.set_flag(c, "exercise_opengym", "all", actor="test")
        flags.invalidate()
        yield c


@pytest.fixture(scope="module")
def uid(conn):
    u = conn.execute("INSERT INTO users (display_name, role) VALUES ('T', 'member') RETURNING id").fetchone()["id"]
    rows = catalog.catalog_rows(conn)
    imported = [r for r in rows if r.get("family_slug")]
    curated = [r for r in rows if r.get("source") == "curated"]
    records_store.upsert_exercise(conn, u, "upright_row", {"status": "banned", "reason": "肩"})        # family restriction
    records_store.upsert_exercise(conn, u, curated[3]["slug"], {"status": "caution", "name_zh": "我的叫法",
                                                                  "aliases": ["自定别名"], "app_exercise_id": "spk-1"})
    records_store.upsert_exercise(conn, u, "旧名字", {"status": "caution", "name_en": imported[5]["name_en"]})  # matched by name
    records_store.upsert_exercise(conn, u, "nowhere", {"status": "banned", "name_zh": "不存在的动作"})        # matches nothing
    records_store.upsert_exercise(conn, u, curated[7]["slug"], {"status": "active", "app_exercise_id": "spk-2"})
    custom.add(conn, u, {"name_zh": "神秘划船", "name_en": "Mystery Row", "aliases": ["mystery row v2"],
                         "primary_muscles": ["lats"], "secondary_muscles": [], "equipment": ["cable"]})
    return u


def _queries(conn, uid):
    rows = catalog.rows_for(conn, uid)
    rnd = random.Random(7)
    qs = ["", "  ", "推", "row", "卧推", "xyz不存在", "spk-1", "spk-2", "我的叫法", "自定别名", "旧名字", "不存在的动作",
          "神秘划船", "Mystery Row", "my_1", "upright_row", "UPRIGHT_ROW", "杠铃卧推（窄距）", "bench press (close grip)"]
    for r in rnd.sample(rows, 80) + [r for r in rows if r.get("source") == "curated"][:30]:
        qs += [r["slug"], r["name_zh"], r["name_en"], f"{r['name_zh']}（变式）", r["name_en"][:5], r["name_zh"][:2],
               *(r.get("aliases") or [])[:2]]
    return qs


@pytest.mark.parametrize("who", ["user", "no_meta", "anonymous"])
def test_resolve_matches_the_full_scan(conn, uid, who):
    user_id, with_meta = {"user": (uid, True), "no_meta": (uid, False), "anonymous": (None, True)}[who]
    cat_rows, meta_by_ref = catalog._load_catalog_and_meta(conn, user_id, with_meta)
    ref_meta = ref_match(cat_rows, meta_by_ref)
    shared = catalog._shared_rows(conn, user_id if with_meta else None)
    assert catalog._match_meta_to_slug(cat_rows, meta_by_ref, shared) == ref_meta
    if who == "user":
        assert ref_meta["upright_row"]["status"] == "banned" and any(m.get("inherited_from") for m in ref_meta.values())
    for q in _queries(conn, uid):
        want = ref_resolve(cat_rows, ref_meta, q)
        got = catalog.resolve_with_candidates(conn, user_id, q, with_meta=with_meta)
        assert (got[0] and got[0]["slug"], got[1], got[2]) == (want[0] and want[0]["slug"], want[1], want[2]), q


def test_catalog_check_is_a_key_read_and_sees_writes(conn):
    stamp = catalog._catalog_stamp(conn)
    assert stamp[0] == "v"   # migration 850: no full-table count
    rows = catalog.catalog_rows(conn)
    assert catalog.catalog_rows(conn) is rows
    slug = rows[0]["slug"]
    conn.execute("UPDATE exercise_catalog SET aliases = aliases || '{测试别名850}'::text[] WHERE slug = %s", (slug,))
    assert catalog._catalog_stamp(conn) != stamp
    fresh = catalog.catalog_rows(conn)
    assert fresh is not rows and "测试别名850" in fresh[0]["aliases"]
    assert catalog.resolve(conn, "测试别名850")["slug"] == slug


def test_scope_reads_each_thing_once(conn, uid, monkeypatch):
    names = [r["name_zh"] for r in catalog.catalog_rows(conn)[:30]]
    seen: list[str] = []
    orig = psycopg.Connection.execute

    def counting(self, query, *a, **k):
        seen.append(" ".join(str(query).split())[:60])
        return orig(self, query, *a, **k)

    catalog.resolve(conn, names[0], user_id=uid)   # warm the shared caches
    monkeypatch.setattr(psycopg.Connection, "execute", counting)
    with catalog.lookup_scope():
        for n in names:
            assert catalog.resolve(conn, n, user_id=uid)["name_zh"] == n
    # one change check of the catalog, the user's exercise_meta once, their custom exercises once; nothing per lookup
    assert len(seen) == 3 and sum("exercise_meta" in q for q in seen) == 1 and sum("table_versions" in q for q in seen) == 1, seen

"""A sweep over every GET endpoint, asked for English (issue #64): no Chinese comes back unless it is on the list below with a reason.

The per-feature tests (test_i18n_labels.py, test_i18n_server.py, ...) check the texts somebody thought of. This one catches the
rest: a new endpoint, or a new field on an old one, that returns a Chinese label / reason / hint in an English request fails here
until it is either written in both languages (`L("中文", "English")`, a `*_en` field next to it) or put on ALLOWED with the reason
why the page never shows it as it is. Routes are taken from the app itself, so a new route is covered without touching this file.

What is not looked at: keys ending in `_zh` (an explicit Chinese name), a field that has a `<field>_en` twin (the page picks), and
the routes the AI reads (tool definitions, the Chinese glossary, the static data bundle).

Own test database BODY_TEST_DB_NAME_I18N_ENDPOINTS (default body_test_i18n_endpoints), recreated on every run; skipped without
Postgres. SparkyFitness is not connected, which is itself a state the page has to word (the "not connected" texts).
"""
import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.friends.params import RULES_VERSION
from body_engine.rollout import flags, registry

CJK = re.compile(r"[　-〿一-鿿＀-￯]")
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_I18N_ENDPOINTS", "body_test_i18n_endpoints")
TZ = ZoneInfo("Australia/Sydney")
TODAY = datetime.now(TZ).date()
EN = {"X-Body-Lang": "en"}

# Routes that are not the Today page's: the AI's tool definitions, the Chinese keyword glossary, the static data bundle.
SKIP_ROUTES = re.compile(r"^/(?:mcp|oauth|\.well-known|docs|redoc|openapi)|^/v1/(?:assistant/tools|static-bundle|explain/glossary)")

# (route regex, JSON path regex, why the page does not show it as it is). A new Chinese text goes here only when the page really
# maps or never shows it; otherwise give it an English twin.
PAGE_MAPS = "the page maps it to a short English label (app.js short() / tr())"
AI_ONLY = "read by the AI (API / MCP), not shown on the page"
USER_DATA = "the user's own words (written by them or their AI), shown as written"
ALLOWED: list[tuple[str, str, str]] = [
    (r"/today$", r"\.scores\.(today|yesterday)_reasons\.", PAGE_MAPS),
    (r"/today$", r"\.scores\.(today\.)?flags\.|\.scores\.load_text$|\.sleep\.reason$|\.nutrition\.error$|\.training\.sparky\.", PAGE_MAPS),
    (r"/(?:today|training/(?:sparky|week))$", r"\.sparky\.(label|hint)$|^\.(label|hint)$", "label / hint have label_en / hint_en twins"),
    (r"/healthspan$", r"\.missing_reason$", PAGE_MAPS),
    (r"/explain/(?:lab|metric|status)/[^/]+$", r"\.missing$", PAGE_MAPS),
    (r"/explain/(?:lab|metric|status)/[^/]+$", r"\.profile\[\]$", "lines of the user's profile card: " + USER_DATA),
    (r"/sleep/nights?$", r"\.reason$", PAGE_MAPS),
    (r"/trends$", r"\.unavailable\.", PAGE_MAPS + "; the page only looks for 授权 in it"),
    (r"/memory$", r"\.profile_card\[\]\.(label|value)$", "the card's label is translated by the page (tr()), the value is " + USER_DATA),
    (r"/journal/(?:items|frequent|analysis)$", r"\.(label_zh|description|label|method|message)$|\.outcomes\.", "journal item names are " + USER_DATA +
        "; the analysis texts are " + AI_ONLY),
    (r"/(?:cardio|hr-zones|vo2max|stress|strain-coach|sleep/details|scores|reports|diet-constraints|grocery/context|health-monitor|sleep-coach)$", r".*", AI_ONLY),
    (r"/training/(?:plan|week|splits)$", r"\.(name|summary|weekday_zh)$", "built-in plan names are translated by the page (tr(), i18n-en.js)"),
    (r"/training/(?:calendar|day/[^/]+)$", r"\.(weekday|sparky_error)$", "the page builds its own weekday names; sparky_error is only a flag"),
]


def allowed(route: str, jpath: str) -> bool:
    return any(re.search(r, route) and re.search(j, jpath) for r, j, _ in ALLOWED)


def walk(o, path=""):
    """(json path, text) for every string with Chinese, leaving out `*_zh` keys and fields that have a `*_en` twin."""
    if isinstance(o, dict):
        for k, v in o.items():
            if k.endswith("_zh") or f"{k}_en" in o:
                continue
            yield from walk(v, f"{path}.{k}")
    elif isinstance(o, list):
        for v in o[:50]:
            yield from walk(v, f"{path}[]")
    elif isinstance(o, str) and CJK.search(o):
        yield path, o


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", "body_pr1", "body_test_accept", ""):
        pytest.exit("the test DB must not be a real DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # no password file, or the database is not running
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


@pytest.fixture(scope="module")
def world(dsn):
    """A (a circle owner with scores, workouts, journal, memory, a product), B (in A's circle), C (just signed up: nothing yet)."""
    from body_engine.assistant import memory
    from body_engine.records import defaults
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        for key in registry.FLAGS:
            flags.set_flag(conn, key, "all", actor="test")
        flags.invalidate()
        ids, tok = {}, {}
        for name, born in (("A", "1990-01-01"), ("B", "1992-05-05"), ("C", "1995-03-03")):
            ids[name] = conn.execute("""INSERT INTO users (display_name, birth_date, sex, height_cm, timezone, consent_at, consent_version)
                                        VALUES (%s, %s, 'm', 178, 'Australia/Sydney', now(), '2026-10-22.2') RETURNING id""",
                                     (name, born)).fetchone()["id"]
            tok[name] = auth.create_token(conn, ids[name], "web", ["read", "write"])
            # the circle community rules (flag circle_rules, on above) accepted, so circles can be made below
            conn.execute("INSERT INTO circle_rules_acceptances (user_id, version) VALUES (%s, %s)", (ids[name], RULES_VERSION))
        for name in ("A", "B"):
            for k in range(10):
                conn.execute("""INSERT INTO daily_scores (user_id, date, recovery, sleep_score, sleep_actual_min, strain, ctl, atl, tsb,
                                  flags, algo_version) VALUES (%s, %s, %s, 80, 450, 12.0, 50, 70, -20, '{}', 'test')""",
                             (ids[name], TODAY - timedelta(days=k), 60 + k))
        start = datetime.combine(TODAY - timedelta(days=1), datetime.min.time(), TZ) + timedelta(hours=18)
        conn.execute("""INSERT INTO workouts (user_id, source, external_id, type, name_zh, local_date, start_at, end_at, duration_min,
                          avg_hr, max_hr) VALUES (%s, 'ow', 'w1', 'running', '跑步', %s, %s, %s, 40, 150, 172)""",
                     (ids["A"], TODAY - timedelta(days=1), start, start + timedelta(minutes=40)))
        defaults.init_journal_items(conn, ids["A"])
        memory.init_profile(conn, ids["A"], None)
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn, world):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=4, open=True)
    c = TestClient(api.app)
    ids, tok = world
    h = {"Authorization": f"Bearer {tok['A']}", "X-Body-Web": "1"}
    r = c.post("/v1/friends/circles", headers=h, json={"name": "Gym crew"})
    assert r.status_code == 201, r.text
    code = r.json()["invite"]["code"]
    r = c.post("/v1/friends/join", headers={"Authorization": f"Bearer {tok['B']}", "X-Body-Web": "1"},
               json={"code": code, "scopes": ["scores", "training"]})
    assert r.status_code == 200, r.text
    r = c.post("/v1/users/me/grocery/products", headers=h, json={
        "name": "Greek yoghurt", "category": "dairy_eggs", "package_size": 500, "package_unit": "g", "price": 5.5, "currency": "AUD",
        "nutrition": {"kcal": 97, "protein_g": 9, "carbs_g": 4, "fat_g": 5}, "serving_size": 170, "serving_unit": "g",
        "image_url": "https://example.com/y.jpg", "ingredients": "Milk, cultures.", "allergens": ["milk"], "allergen_traces": ["soy"]})
    assert r.status_code == 201, r.text
    assert c.post("/v1/users/me/water", headers=h, json={"ml": 250}).status_code in (200, 201)
    yield c
    api.pool.close()


def path_for(template: str) -> str:
    values = {"user": "me", "user_id": "me", "date": TODAY.isoformat(), "day": TODAY.isoformat(), "month": TODAY.strftime("%Y-%m")}
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), "1"), template)


def get_routes() -> list[tuple[str, str]]:
    spec = api.app.openapi()["paths"]
    return [(t, path_for(t)) for t, ops in sorted(spec.items()) if "get" in ops and not SKIP_ROUTES.search(t)]


def sweep(client, tok):
    """{(route template, json path): text} for every Chinese text in an English answer, and how many routes answered 200."""
    found, ok = {}, 0
    for template, path in get_routes():
        r = client.get(path, headers={"Authorization": f"Bearer {tok}", **EN})
        if r.status_code != 200:
            continue
        ok += 1
        for jpath, text in walk(r.json()):
            if not allowed(path, jpath):
                found.setdefault((template, jpath), text[:80])
    return found, ok


@pytest.mark.parametrize("who", ["A", "C"])
def test_english_requests_return_no_unlisted_chinese(client, world, who):
    """A has data in every corner we can seed, C has none: the empty states are worded too."""
    _, tok = world
    found, ok = sweep(client, tok[who])
    assert ok >= 60, f"only {ok} routes answered: the sweep is not covering the app (path parameters?)"
    assert not found, ("Chinese in an English answer (give it an English twin, or list it in ALLOWED with the reason): "
                       + "; ".join(f"{t} {j}: {s!r}" for (t, j), s in sorted(found.items())))


def test_the_allow_list_is_not_a_hole():
    """Every entry says why, and nothing is allowed for a whole route unless the AI reads it."""
    for route, jpath, why in ALLOWED:
        assert why and re.compile(route) and re.compile(jpath)
        if jpath == r".*":
            assert why == AI_ONLY, route

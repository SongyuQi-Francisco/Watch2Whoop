"""Weekly what's-new message (whatsnew/).

Pure part: the hand-kept entries (one short sentence, both languages, no jargon / PR numbers / flag names, valid links), the
send window and the message text, the page wiring (both Me layouts, lazy script).
Database part (test database BODY_TEST_DB_NAME_WHATSNEW, default body_test_whatsnew, recreated on every run; skipped without
Postgres): who is due when, once per week however often it runs, only what is new since the last message, a new account only
hears about what came after it, no consent / category off / not active -> nothing, dry run writes nothing, GET /v1/whats-new.
"""
import json
import os
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.notify import params as NP
from body_engine.rollout.registry import FLAGS
from body_engine.whatsnew import entries as E, params as P, service

STATIC = Path(__file__).resolve().parents[1] / "src" / "body_engine" / "web" / "static"
SYD = ZoneInfo("Australia/Sydney")
TABS = ("today", "trends", "friends", "food", "record", "me")


# ---------------------------------------------------------------- the entries
def test_entries_are_newest_first_and_unique():
    days = [e.day for e in E.ENTRIES]
    assert days == sorted(days, reverse=True)
    assert len({(e.day, e.zh) for e in E.ENTRIES}) == len(E.ENTRIES)
    assert E.ENTRIES, "the first batch of entries"


@pytest.mark.parametrize("e", E.ENTRIES, ids=lambda e: f"{e.day}-{e.zh[:8]}")
def test_every_entry_is_one_short_plain_sentence(e):
    assert 0 < len(e.zh) <= P.MAX_ZH and 0 < len(e.en) <= P.MAX_EN, (len(e.zh), len(e.en))
    assert e.zh.endswith("。") and e.en.endswith("."), "one sentence, ended with a full stop"
    assert not re.search(r"[。！？!?]", e.zh[:-1]) and not re.search(r"[.!?](\s|$)", e.en[:-1]), "one sentence only"
    assert "\n" not in e.zh + e.en and e.zh == e.zh.strip() and e.en == e.en.strip()
    assert re.search(r"[一-鿿]", e.zh) and not re.search(r"[一-鿿]", e.en), "zh is Chinese, en has no Chinese"
    for text in (e.zh, e.en):
        assert not re.search(r"#\d+|\bPR\b|\bissue\b", text, re.I), "no PR / issue numbers"
        assert not re.search(r"\b[a-z0-9]+_[a-z0-9_]+\b", text), "no snake_case words (flag names)"
        assert not [k for k in FLAGS if re.search(rf"\b{re.escape(k)}\b", text)], "no flag names"
        for w in P.FORBIDDEN:
            hit = re.search(rf"\b{re.escape(w)}\b", text) if w.isascii() else w in text
            assert not hit, f"technical word {w!r} in {text!r}"
    if e.url:
        assert re.fullmatch(r"/app/[A-Za-z0-9_=&?./-]*", e.url), e.url
        tab = re.search(r"[?&]tab=(\w+)", e.url)
        assert not tab or tab.group(1) in TABS, e.url


def test_between_is_after_and_up_to_including():
    es = (E.Entry(date(2026, 10, 28), "甲。", "A."), E.Entry(date(2026, 10, 26), "乙。", "B."), E.Entry(date(2026, 10, 25), "丙。", "C."))
    assert [e.zh for e in E.between(date(2026, 10, 25), date(2026, 10, 28), es)] == ["甲。", "乙。"]
    assert E.between(date(2026, 10, 28), date(2026, 11, 3), es) == []


def test_weeks_group_by_monday_and_skip_empty_weeks():
    es = (E.Entry(date(2026, 10, 28), "甲。", "A."), E.Entry(date(2026, 10, 25), "丙。", "C."), E.Entry(date(2026, 10, 12), "丁。", "D."))
    got = E.weeks(date(2026, 10, 29), 4, es)
    assert [(w["week_start"], [e.zh for e in w["items"]]) for w in got] == [
        (date(2026, 10, 26), ["甲。"]), (date(2026, 10, 19), ["丙。"]), (date(2026, 10, 12), ["丁。"])]
    assert [w["week_start"] for w in E.weeks(date(2026, 10, 29), 2, es)] == [date(2026, 10, 26), date(2026, 10, 19)]


# ---------------------------------------------------------------- the send window and the text
def test_send_window_in_the_users_own_time_zone():
    la = ZoneInfo("America/Los_Angeles")
    sun = lambda h, m=0, tz=SYD: datetime(2026, 11, 1, h, m, tzinfo=tz)   # Sunday
    assert service.due_slot(sun(18, 59), SYD) is None                      # before 19:00 on Sunday: last Sunday's slot is 6 days old
    assert service.due_slot(sun(19), SYD) == (date(2026, 11, 1), date(2026, 11, 1))
    assert service.due_slot(sun(23, 59), SYD) == (date(2026, 11, 1), date(2026, 11, 1))
    mon = datetime(2026, 11, 2, 18, 59, tzinfo=SYD)                        # catch-up until 48 hours after the slot
    assert service.due_slot(mon, SYD) == (date(2026, 11, 1), date(2026, 11, 2))
    assert service.due_slot(datetime(2026, 11, 3, 19, 0, tzinfo=SYD), SYD) is None
    # the same instant is Saturday evening in Los Angeles: not due there
    assert service.due_slot(sun(19), la) is None
    assert service.due_slot(sun(19).astimezone(timezone.utc), SYD) == (date(2026, 11, 1), date(2026, 11, 1))
    # daylight saving ends in Sydney on Sunday 2026-04-05 (03:00 -> 02:00): the wall clock 19:00 still decides
    assert service.due_slot(datetime(2026, 4, 5, 19, 0, tzinfo=SYD), SYD) == (date(2026, 4, 5), date(2026, 4, 5))


def test_message_names_the_first_entry_and_the_count():
    es = list(E.between(date(2026, 10, 24), date(2026, 10, 29)))
    title, body = service.message(es[:1])
    assert title == {"zh": "本周更新", "en": "What's new this week"} and body == {"zh": es[0].zh, "en": es[0].en}
    title, body = service.message(es[:3])
    assert body["zh"] == f"{es[0].zh.rstrip('。')}，等 3 项" and body["en"] == f"{es[0].en.rstrip('.')}, 3 updates in all"
    assert len(body["zh"]) < NP.MAX_BODY and len(body["en"]) < NP.MAX_BODY


def test_updates_is_a_notification_category_on_by_default():
    assert NP.CATEGORIES["updates"]["default"] is True and "flag" not in NP.CATEGORIES["updates"]
    from body_engine.notify import core
    assert core.category_of(P.KIND) == "updates"


# ---------------------------------------------------------------- the page
def test_page_wiring():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert app.count("""item(tr("更新记录"), null, 'id="me-whatsnew"')""") == 2, "both Me layouts (settings page and the old one)"
    assert '["#me-whatsnew", "whatsnew.js", "BodyWhatsNew"]' in app
    assert '"whatsnew"' in (STATIC / "notify.js").read_text(encoding="utf-8") and "whatsnew.js" in (STATIC / "notify.js").read_text(encoding="utf-8")
    assert '"whatsnew.js"' in (STATIC / "sw.js").read_text(encoding="utf-8")
    assert '<script type="w2w/lazy" src="whatsnew.js"></script>' in (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "whatsnew.js").read_text(encoding="utf-8")
    assert "/v1/whats-new" in js and "window.BodyWhatsNew = { open }" in js
    assert P.URL == "/app/?whatsnew=1"


SHEET_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const events = [];
const sandbox = {
  document: { getElementById: () => ({}), createElement: () => ({}), head: { appendChild: () => {} } },
  URLSearchParams,
  CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
  I18N: { t: (k, p) => k.replace(/\{(\w+)\}/g, (_, x) => p[x]), monthDay: (d) => `${d.getMonth() + 1}/${d.getDate()}` }, BodyPlugins: [],
};
sandbox.window = sandbox;
sandbox.dispatchEvent = (e) => events.push([e.type, e.detail]);
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), sandbox);
const weeks = [
  { week_start: "2026-10-26", items: [{ date: "2026-10-28", text: "A <b>", url: "/app/?tab=me" }, { date: "2026-10-27", text: "B", url: null }] },
  { week_start: "2026-10-19", items: [{ date: "2026-10-20", text: "C", url: null }] },
  { week_start: "2026-10-05", items: [{ date: "2026-10-10", text: "D", url: null }] },
];
const calls = { closed: 0, tabs: [], api: [] };
const ctx = { esc: (v) => String(v).replace(/</g, "&lt;"), today: () => "2026-10-29", closeSheet: () => calls.closed++, setTab: (t) => calls.tabs.push(t),
  api: async (path) => { calls.api.push(path); return { weeks }; }, openSheet: (title, html, after) => { calls.title = title; calls.after = after; } };
sandbox.BodyPlugins[0].init(ctx);
sandbox.BodyWhatsNew.open();
const buttons = [];
const root = { innerHTML: "", querySelectorAll: () => buttons };
(async () => {
  await calls.after(root);
  const link = { dataset: { wn: "/app/?tab=me" } };
  buttons.push(link);
  root.innerHTML = "";
  await calls.after(root);
  link.onclick();
  console.log(JSON.stringify({ title: calls.title, api: calls.api, html: root.innerHTML, closed: calls.closed, tabs: calls.tabs, events }));
})();
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_sheet_groups_by_week_and_a_link_goes_to_its_tab():
    out = json.loads(subprocess.run([shutil.which("node"), "-e", SHEET_HARNESS, str(STATIC / "whatsnew.js")], capture_output=True, text=True, check=True).stdout)
    assert out["title"] == "本周更新" and out["api"] == ["/v1/whats-new", "/v1/whats-new"]
    h = out["html"]
    assert re.findall(r'class="wn-week">(?:<svg.*?</svg>)?<span>([^<]*)</span>', h) == ["本周", "上周", "10/5 起的一周"]
    assert h.count('data-wn="/app/?tab=me"') == 1 and "A &lt;b>" in h and "<b>" not in h.replace("&lt;b>", "")   # text is escaped, only linked rows are buttons
    assert h.count("<button") == 1 and h.index("10/28") < h.index("10/27") < h.index("10/20")
    assert out["closed"] == 1 and out["tabs"] == ["me"] and out["events"] == [["body:link", {"tab": "me"}]]


# ---------------------------------------------------------------- database
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_WHATSNEW", "body_test_whatsnew")
FAKE = (E.Entry(date(2026, 10, 28), "最新的一条。", "The newest one.", "/app/?tab=me"),
        E.Entry(date(2026, 10, 27), "第二条。", "The second one."),
        E.Entry(date(2026, 10, 10), "更早的一条。", "One from before."))
LATER = E.Entry(date(2026, 11, 3), "下周的新功能。", "Next week's feature.")


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"cannot connect to Postgres: {e}")
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    dsn = Settings(db_name=TEST_DB).dsn
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        migrate(conn)
    return dsn


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        c.execute("DELETE FROM users")   # cascades: sends, notifications, settings
        c.commit()
        yield c


def add_user(conn, name, tz="Australia/Sydney", role="member", consent=True, status="active", created="2026-09-01T00:00:00+00:00", lang=None):
    uid = conn.execute("""INSERT INTO users (display_name, role, timezone, language, consent_at, consent_version, status, created_at)
                          VALUES (%s, %s, %s, %s, CASE WHEN %s THEN now() END, '1', %s, %s) RETURNING id""",
                       (name, role, tz, lang, consent, status, created)).fetchone()["id"]
    conn.commit()
    return uid


SUNDAY_EVENING = datetime(2026, 11, 1, 19, 10, tzinfo=SYD)     # Sunday 2026-11-01


def notes(conn, uid):
    return conn.execute("SELECT kind, title, body, title_en, body_en, url, data FROM notifications WHERE user_id = %s ORDER BY id", (uid,)).fetchall()


def test_sent_once_per_week_however_often_it_runs(conn):
    uid = add_user(conn, "a")
    res = service.run(conn, now=SUNDAY_EVENING, entries=FAKE)
    assert [(r["user_id"], r["status"], r["items"]) for r in res] == [(uid, "sent", 2)]   # the 10-10 entry is older than the 14-day look-back
    n, = notes(conn, uid)
    assert (n["kind"], n["title"], n["title_en"], n["url"]) == ("updates.weekly", "本周更新", "What's new this week", "/app/?whatsnew=1")
    assert n["body"] == "最新的一条，等 2 项" and n["body_en"] == "The newest one, 2 updates in all" and n["data"] == {"items": 2, "week": "2026-11-01"}
    for later in (SUNDAY_EVENING + timedelta(hours=1), SUNDAY_EVENING + timedelta(hours=20)):   # hourly runs, catch-up window
        assert [r["status"] for r in service.run(conn, now=later, entries=FAKE)] == ["already"]
    assert len(notes(conn, uid)) == 1
    row = conn.execute("SELECT * FROM whats_new_sends WHERE user_id = %s", (uid,)).fetchone()
    assert (row["week_start"], row["through"], row["items"], row["outcome"], row["notification_id"]) == (date(2026, 11, 1), date(2026, 11, 1), 2, "sent", conn.execute("SELECT id FROM notifications").fetchone()["id"])


def test_not_due_outside_the_window_and_in_other_time_zones(conn):
    sydney = add_user(conn, "syd")
    la = add_user(conn, "la", tz="America/Los_Angeles")
    assert service.run(conn, now=datetime(2026, 11, 1, 18, 50, tzinfo=SYD), entries=FAKE) == []
    assert [r["user_id"] for r in service.run(conn, now=SUNDAY_EVENING, entries=FAKE)] == [sydney]   # LA is still on Saturday
    la_evening = datetime(2026, 11, 1, 19, 5, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert [(r["user_id"], r["status"]) for r in service.run(conn, now=la_evening, entries=FAKE)] == [(sydney, "already"), (la, "sent")]
    assert service.run(conn, now=datetime(2026, 11, 5, 12, 0, tzinfo=SYD), entries=FAKE) == []   # Thursday
    assert len(notes(conn, sydney)) == 1 and len(notes(conn, la)) == 1


def test_catch_up_when_the_hourly_job_missed_sunday(conn):
    uid = add_user(conn, "a")
    res = service.run(conn, now=datetime(2026, 11, 2, 9, 0, tzinfo=SYD), entries=FAKE)   # Monday morning
    assert [(r["user_id"], r["status"], r["week_start"]) for r in res] == [(uid, "sent", "2026-11-01")]


def test_next_message_starts_where_the_last_ended_and_empty_weeks_send_nothing(conn):
    uid = add_user(conn, "a")
    service.run(conn, now=SUNDAY_EVENING, entries=FAKE)
    nxt = SUNDAY_EVENING + timedelta(days=7)
    assert [r["status"] for r in service.run(conn, now=nxt, entries=FAKE)] == ["empty"]   # nothing new: no message, no row
    assert conn.execute("SELECT count(*) AS n FROM whats_new_sends").fetchone()["n"] == 1
    res = service.run(conn, now=nxt + timedelta(hours=1), entries=FAKE + (LATER,))         # an entry dated before the slot arrives
    assert [(r["status"], r["items"]) for r in res] == [("sent", 1)]
    assert notes(conn, uid)[-1]["body"] == "下周的新功能。"                                    # only the new one, named without a count


def test_a_new_account_only_hears_about_what_came_after_it(conn):
    old = add_user(conn, "old")
    new = add_user(conn, "new", created="2026-10-27T20:00:00+00:00")   # 2026-10-28 07:00 in Sydney: the 10-28 entry is not "after" it
    service.run(conn, now=SUNDAY_EVENING, entries=FAKE)
    assert len(notes(conn, old)) == 1 and notes(conn, old)[0]["body"].endswith("等 2 项")
    assert notes(conn, new) == []
    newer = add_user(conn, "newer", created="2026-10-27T05:00:00+00:00")   # made on 10-27 in Sydney: hears about 10-28 only
    res = service.run(conn, user_id=newer, now=SUNDAY_EVENING, entries=FAKE)
    assert [(r["status"], r["items"]) for r in res] == [("sent", 1)]
    assert notes(conn, newer)[0]["body"] == "最新的一条。"


def test_no_consent_inactive_and_switched_off_get_nothing(conn):
    owner = add_user(conn, "owner", role="owner", consent=False)
    unconsented = add_user(conn, "unconsented", consent=False)
    pending = add_user(conn, "pending", status="pending")
    off = add_user(conn, "off")
    conn.execute("INSERT INTO notification_settings (user_id, categories) VALUES (%s, '{\"updates\": false}')", (off,))
    conn.commit()
    got = {r["user_id"]: r["status"] for r in service.run(conn, now=SUNDAY_EVENING, entries=FAKE)}
    assert got == {owner: "sent", unconsented: "consent", pending: "inactive", off: "off"}
    assert len(notes(conn, owner)) == 1
    assert all(notes(conn, u) == [] for u in (unconsented, pending, off))
    assert [r["outcome"] for r in conn.execute("SELECT outcome FROM whats_new_sends WHERE user_id = %s", (off,))] == ["off"]
    # switched back on: the next message starts after this week, not with what they switched off
    conn.execute("DELETE FROM notification_settings WHERE user_id = %s", (off,))
    conn.commit()
    res = service.run(conn, user_id=off, now=SUNDAY_EVENING + timedelta(days=7), entries=FAKE + (LATER,))
    assert [(r["status"], r["items"]) for r in res] == [("sent", 1)]


def test_dry_run_writes_nothing(conn):
    uid = add_user(conn, "a")
    res = service.run(conn, now=SUNDAY_EVENING, dry_run=True, entries=FAKE)
    assert [(r["user_id"], r["status"], r["items"], r["first"]) for r in res] == [(uid, "would_send", 2, "最新的一条。")]
    assert notes(conn, uid) == []
    assert conn.execute("SELECT count(*) AS n FROM whats_new_sends").fetchone()["n"] == 0
    assert [r["status"] for r in service.run(conn, now=SUNDAY_EVENING, entries=FAKE)] == ["sent"]   # a real run afterwards still sends


def test_a_single_user_can_be_picked(conn):
    a, b = add_user(conn, "a"), add_user(conn, "b")
    assert [r["user_id"] for r in service.run(conn, user_id=b, now=SUNDAY_EVENING, entries=FAKE)] == [b]
    assert notes(conn, a) == [] and len(notes(conn, b)) == 1


@pytest.fixture()
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app, base_url="https://testserver")
    api.pool.close()
    api.pool = None


def test_list_endpoint(conn, client, monkeypatch):
    uid = add_user(conn, "a")
    tok = auth.create_token(conn, uid, "app", ["read"])
    conn.commit()
    monkeypatch.setattr(E, "ENTRIES", FAKE)
    monkeypatch.setattr("body_engine.assistant.sparky_write.user_today", lambda c, u: date(2026, 10, 29))
    assert client.get("/v1/whats-new").status_code == 401
    zh = client.get("/v1/whats-new", headers={"Authorization": f"Bearer {tok}"}).json()
    assert zh == {"weeks": [{"week_start": "2026-10-26", "items": [{"date": "2026-10-28", "text": "最新的一条。", "url": "/app/?tab=me"},
                                                                  {"date": "2026-10-27", "text": "第二条。", "url": None}]},
                            {"week_start": "2026-10-05", "items": [{"date": "2026-10-10", "text": "更早的一条。", "url": None}]}]}
    en = client.get("/v1/whats-new?weeks=1", headers={"Authorization": f"Bearer {tok}", "X-Body-Lang": "en"}).json()
    assert [i["text"] for w in en["weeks"] for i in w["items"]] == ["The newest one.", "The second one."]
    assert client.get("/v1/whats-new?weeks=0", headers={"Authorization": f"Bearer {tok}"}).status_code == 422

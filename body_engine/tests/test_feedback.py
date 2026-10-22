"""AI feedback, phase 1 (docs/ai-feedback-plan.md): root rules in every injection point, sanitising, dedupe / votes, rate
limits, feedback mode, briefing updates, API authorization, digest e-mail. Needs Postgres for the database part: separate test
DB BODY_TEST_DB_NAME (default body_test_feedback)."""
import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth, mcp_server
from body_engine.account import mailer
from body_engine.ai import fallback, prompt, root_rules
from body_engine.ai.chat import ALWAYS_CONFIRM
from body_engine.assistant.tools import TOOLS_BY_NAME, run_tool
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.feedback import digest, params as P, store
from body_engine.feedback.sanitize import Sanitizer, placeholder_ratio

TEST_DB = os.environ.get("BODY_TEST_DB_NAME", "body_test_feedback")


# ---------------------------------------------------------------- no database needed
def test_root_rules_versioned_and_everywhere():
    text, v = root_rules.text(), root_rules.version()
    assert v == "v3" and text.startswith("## 根规则 v3") and "report_feedback" in text and "<!--" not in text
    for lite in (False, True):   # in-page chat, fallback AI, MCP prompt `butler`: rules first, then the persona
        p = prompt.base_prompt(lite)
        assert p.startswith(text) and p.index("身体管家") > len(text)
    server = mcp_server.build_server(lambda ctx: mcp_server.Identity(1, "test"))
    assert root_rules.text() in server.instructions and "get_briefing" in server.instructions


def test_tools_registered_for_every_ai():
    r, l = TOOLS_BY_NAME["report_feedback"], TOOLS_BY_NAME["list_feedback"]
    assert r.writes and not l.writes and "user" not in r.input_schema["properties"]
    assert "report_feedback" not in ALWAYS_CONFIRM                       # not a delete: no confirmation step
    assert {"report_feedback", "list_feedback"} <= {t.name for t in fallback.tools()}


def test_sanitizer():
    s = Sanitizer(["Alex Li", "小明"], ["odd+addr@mail.example"])
    cases = {
        "体重89.2kg，心率 52 bpm，HRV46ms": "体重‹数值›，心率 ‹数值›，HRV‹数值›",
        "尿酸 480 μmol/L，血压 120/80": "尿酸 ‹数值›，血压 ‹数值›",
        "睡了 7.5 小时，吃 2000 kcal": "睡了 ‹数值›，吃 ‹数值›",
        "token是abcd1234abcd1234EF了": "token是‹码›了",
        "电话0412 345 678": "电话‹电话›",
        "https://x.example/app?code=1 和 watch2whoop://setup?x=1": "‹链接› 和 ‹链接›",
        "邮件 someone@example.org，ODD+ADDR@mail.example，192.168.1.2": "邮件 ‹邮箱›，‹邮箱›，‹IP›",
        "生日 1990-05-17，小明 和 alex li 说的": "生日 ‹数值›，‹某人› 和 ‹某人› 说的",
    }
    for raw, want in cases.items():
        assert s.sanitize(raw)[0] == want, raw
    # what makes a report reproducible stays
    keep = "get_training_calendar 报 HTTP 500，第 3 步，2026-10-21 到 2026-10-22，版本 v1"
    assert s.sanitize(keep) == (keep, 0)
    assert placeholder_ratio("‹数值›‹数值›") == 1 and placeholder_ratio("abc") == 0


def test_fingerprint_ignores_numbers_and_placeholders():
    a = store.fingerprint("bug", "get_sleep", "睡眠 ‹数值› 显示成 0，HTTP 500")
    assert a == store.fingerprint("bug", "Get_Sleep", "睡眠‹数值›显示成 12 , http 404")
    assert a != store.fingerprint("feature", "get_sleep", "睡眠 ‹数值› 显示成 0，HTTP 500")
    assert store.parse_ref("FB-12") == store.parse_ref("12") == 12
    with pytest.raises(ValueError):
        store.parse_ref("x12")


# ---------------------------------------------------------------- database
@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", "body_pr1", "body_accept", ""):
        pytest.exit("the test DB must not be a real DB")
    try:
        admin = psycopg.connect(Settings(db_name="postgres").dsn, autocommit=True)
    except Exception as e:
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
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {n: conn.execute("INSERT INTO users (display_name, birth_date, height_cm) VALUES (%s, '1990-01-01', 180) "
                               "RETURNING id", (n,)).fetchone()["id"] for n in ("Alice", "Bobby", "Carol", "Dave")}
        tokens = {n: auth.create_token(conn, ids[n], "t", ["read", "write"]) for n in ids}
        tokens["ro"] = auth.create_token(conn, ids["Alice"], "ro", ["read"])
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)",
                     (ids["Alice"], ids["Bobby"], ["profile", "memory", "metrics", "sleep"]))
    return ids, tokens


@pytest.fixture()
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


def h(token):
    return {"Authorization": f"Bearer {token}"}


def _report(conn, uid, title="训练日历游泳时长显示成 0", **kw):
    args = {"kind": "bug", "title": title, "what": "Bobby 说体重 71kg 那天的游泳时长是 0", "expected": "显示真实时长",
            "steps": "打开训练日历", "area": "get_training_calendar", "error_text": None, **kw}
    return run_tool(conn, uid, "mcp:test", "report_feedback", args)


def test_report_sanitises_and_stores(conn, world):
    ids, _ = world
    a = ids["Alice"]
    conn.execute("INSERT INTO assistant_audit (user_id, channel, tool, args, ok, error) VALUES (%s, 'mcp', 'get_sleep', '{}', false, 'x')",
                 (a,))
    out = _report(conn, a)
    assert out["reported"] and out["id"].startswith("FB-") and out["id"] in out["tell_user"]
    row = store.admin_get(conn, store.parse_ref(out["id"]))
    assert "Bobby" not in row["body_public"] and "71" not in row["body_public"] and "‹某人›" in row["body_public"]
    assert row["body_raw"]["what"].startswith("Bobby")                    # raw kept on the server
    assert row["rules_version"] == "v3" and row["channel"] == "mcp:test" and row["redactions"] >= 2
    assert row["diagnostics"] == {"failed_tools": {"get_sleep": 1}}
    log = conn.execute("SELECT kind FROM ai_write_log WHERE tool = 'report_feedback'").fetchall()
    assert log                                                            # AI writes are logged like any other
    conn.commit()


def test_dedupe_and_votes(conn, world):
    ids, _ = world
    again = _report(conn, ids["Alice"], title="训练日历 游泳时长显示成 12")      # same problem, other number
    assert not again["reported"] and "之前已经报告过" in again["tell_user"]
    other = _report(conn, ids["Carol"], title="训练日历游泳时长显示成 0")
    assert not other["reported"] and other["votes"] == 1 and "+1" in other["tell_user"]
    assert _report(conn, ids["Carol"])["votes"] == 1                      # one vote per user
    carol = store.list_for_user(conn, ids["Carol"])
    assert carol[0]["voted"] and "withdrawable" not in carol[0]
    conn.commit()


def test_rate_limit_and_modes(conn, world, monkeypatch):
    ids, _ = world
    d = ids["Dave"]
    for k in range(8):                                                    # no per-user limit any more
        assert _report(conn, d, title=f"问题 {chr(65 + k)}{chr(70 + k)}", area=f"page{chr(65 + k)}")["reported"]
    monkeypatch.setattr(P, "GLOBAL_DAY", 8)                               # only the server-wide runaway guard is left
    over = _report(conn, d, title="再来一个 XYZ", area="other")
    assert not over["reported"] and over["reason"] == "rate_limited"
    monkeypatch.undo()
    store.set_mode(conn, ids["Carol"], "off")
    off = _report(conn, ids["Carol"], title="全新的问题 QQ", area="x")
    assert off["reason"] == "off" and store.mode(conn, ids["Carol"]) == "off"
    store.set_mode(conn, ids["Carol"], "ask")
    store.set_blocked(conn, ids["Carol"], True)
    assert store.mode(conn, ids["Carol"]) == "off"                        # an admin block wins
    store.set_blocked(conn, ids["Carol"], False)
    assert store.mode(conn, ids["Carol"]) == "ask"
    with pytest.raises(ValueError):
        store.set_mode(conn, ids["Carol"], "sometimes")
    conn.rollback()


def test_briefing_rules_and_updates(conn, world):
    ids, _ = world
    a = ids["Alice"]
    b = run_tool(conn, a, "mcp", "get_briefing", {"user": "me"})
    assert b["system_rules"]["version"] == "v3" and b["system_rules"]["feedback_mode"] == "auto"
    assert b["system_rules"]["text"] == root_rules.text() and "feedback_updates" not in b
    rid = store.list_for_user(conn, a)[0]["id"]
    store.set_status(conn, store.parse_ref(rid), "fixed", note="已在新版修好")
    b = run_tool(conn, a, "mcp", "get_briefing", {"user": "me"})
    assert len(b["feedback_updates"]) == 1 and rid in b["feedback_updates"][0] and "已修复" in b["feedback_updates"][0]
    assert "feedback_updates" not in run_tool(conn, a, "mcp", "get_briefing", {"user": "me"})   # told once
    fam = run_tool(conn, ids["Bobby"], "mcp", "get_briefing", {"user": str(a)})
    assert "feedback_updates" not in fam and fam["system_rules"]["feedback_mode"] == "auto"
    assert "反馈模式：auto" in prompt.build_system_prompt(conn, a)
    with pytest.raises(ValueError):
        store.set_status(conn, 999999, "fixed")
    conn.commit()


def test_api_own_only(client, world):
    ids, tok = world
    r = client.get("/v1/users/me/feedback", headers=h(tok["Alice"]))
    assert r.status_code == 200 and r.json()["mode"] == "auto" and r.json()["reports"][0]["status_label"] == "已修复"
    assert client.get(f"/v1/users/{ids['Alice']}/feedback", headers=h(tok["Bobby"])).status_code == 403   # shares exclude it
    assert client.put("/v1/users/me/feedback-mode", json={"mode": "ask"}, headers=h(tok["ro"])).status_code == 403
    assert client.put("/v1/users/me/feedback-mode", json={"mode": "never"}, headers=h(tok["Alice"])).status_code == 422
    assert client.put("/v1/users/me/feedback-mode", json={"mode": "ask"}, headers=h(tok["Alice"])).json()["mode"] == "ask"
    fixed = r.json()["reports"][0]["id"]
    assert client.delete(f"/v1/users/me/feedback/{fixed}", headers=h(tok["Alice"])).status_code == 409   # already handled
    new = [x for x in client.get("/v1/users/me/feedback", headers=h(tok["Dave"])).json()["reports"] if x.get("withdrawable")][0]
    assert client.delete(f"/v1/users/me/feedback/{new['id']}", headers=h(tok["Alice"])).status_code == 404   # not hers
    assert client.delete(f"/v1/users/me/feedback/{new['id']}", headers=h(tok["Dave"])).status_code == 204
    assert '<script src="feedback.js" defer></script>' in client.get("/app/").text
    assert "反馈记录" in client.get("/app/feedback.js").text


def test_digest(conn, world, monkeypatch):
    monkeypatch.setenv("BODY_MAIL_MODE", "console")
    monkeypatch.delenv("BODY_FEEDBACK_DIGEST_TO", raising=False)
    dry = digest.send(conn, dry_run=True)
    assert dry["new"] >= 1 and not dry["sent"] and "Bobby" not in dry["body"] and "Alice" not in dry["body"]
    assert "get_sleep：1 次，1 人" in dry["body"]
    with pytest.raises(mailer.MailNotConfigured):
        digest.send(conn)
    mailer.OUTBOX.clear()
    monkeypatch.setenv("BODY_FEEDBACK_DIGEST_TO", "maintainer@example.com")
    assert digest.send(conn)["sent"] and mailer.OUTBOX[-1][0] == "maintainer@example.com"
    assert digest.build(conn)["new"] == []                                 # marked as sent
    conn.commit()


def test_account_delete_takes_feedback(conn, world):
    from body_engine.account import delete as account_delete
    ids, _ = world
    rows = account_delete._delete_rows(conn, ids["Dave"])
    assert rows > 0 and not conn.execute("SELECT 1 FROM feedback_reports WHERE user_id = %s", (ids["Dave"],)).fetchone()
    conn.rollback()

"""Admin statistics (account/stats.py, `body-engine user-stats`): activation per sign-up path, the activity signals and
the window, filters, e-mail masking, referral columns when the server has them, and the command line.

Uses its own test database (body_test_user_stats), dropped and recreated on every run; skipped if Postgres is unreachable.
"""
import json
import sys
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from body_engine import cli
from body_engine.account import stats
from body_engine.config import Settings
from body_engine.db import migrate

TEST_DB = "body_test_user_stats"
NOW = datetime.now(timezone.utc)


def ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


@pytest.fixture(scope="module")
def dsn():
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
        seed(conn)
    return dsn


@pytest.fixture
def conn(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        yield c


def user(conn, name, created, role="member", status="active", consent=None) -> int:
    return conn.execute("""INSERT INTO users (display_name, role, status, created_at, consent_at) VALUES (%s,%s,%s,%s,%s)
                           RETURNING id""", (name, role, status, created, consent)).fetchone()["id"]


def token(conn, uid, name, scopes=("read", "write"), used=None) -> int:
    return conn.execute("""INSERT INTO api_tokens (user_id, name, token_sha256, scopes, last_used_at)
                           VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                        (uid, name, f"{uid}-{name}", list(scopes), used)).fetchone()["id"]


def seed(conn) -> None:
    ids = {}
    # admin-created owner: set up with a login code long ago; uses the Today page and the butler today
    ids["owner"] = o = user(conn, "Owner", ago(days=400), role="owner")
    conn.execute("""INSERT INTO web_login_codes (user_id, code_sha256, scopes, expires_at, used_at)
                    VALUES (%s, 'c-owner', '{read,write}', %s, %s)""", (o, ago(days=300), ago(days=301)))
    t = token(conn, o, "app:owner", used=ago(hours=2))
    conn.execute("""INSERT INTO web_sessions (user_id, token_id, session_sha256, last_seen_at, expires_at)
                    VALUES (%s,%s,'s-owner',%s,%s)""", (o, t, ago(hours=2), NOW + timedelta(days=30)))
    conn.execute("INSERT INTO assistant_audit (user_id, channel, tool, ok, at) VALUES (%s,'butler','get_briefing',true,%s)",
                 (o, ago(hours=5)))
    conn.execute("""INSERT INTO daily_metrics (user_id, date, metric, value, agg, source)
                    VALUES (%s, '2026-10-20', 'step_count', 9000, 'sum', 'ow')""", (o,))
    # admin-created tester with an activation link, activated 3 days ago (first minted login code), syncs via OW
    ids["tester"] = m = user(conn, "Tester", ago(days=20), consent=ago(days=3))
    conn.execute("INSERT INTO activation_codes (user_id, code_sha256, uses, last_used_at) VALUES (%s,'a-m',2,%s)",
                 (m, ago(days=1)))
    for i, used in enumerate((ago(days=3), ago(days=1))):
        conn.execute("""INSERT INTO web_login_codes (user_id, code_sha256, scopes, expires_at, used_at)
                        VALUES (%s,%s,'{read,write}',%s,%s)""", (m, f"c-m{i}", NOW, used))
    conn.execute("INSERT INTO sync_state (user_id, source, last_synced_at, cursor) VALUES (%s,'ow',%s,%s)",
                 (m, NOW, ago(hours=6).isoformat()))
    # admin-created, consent only (old), last Shortcut upload 40 days ago, AI client token used 10 days ago
    ids["quiet"] = q = user(conn, "Quiet", ago(days=90), consent=ago(days=80))
    conn.execute("""INSERT INTO shortcut_uploads (user_id, file, format, bytes, rows, imported_at)
                    VALUES (%s,'health_x.txt','cols-v1',10,1,%s)""", (q, ago(days=40)))
    token(conn, q, "ai:claude", scopes=("mcp",), used=ago(days=10))
    # self-signup redeemed an invite 2 days ago; chats on the Today page, an AI wrote a record
    ids["signup"] = s = user(conn, "Signup", ago(days=2, hours=1))
    conn.execute("""INSERT INTO user_identities (user_id, provider, subject, email, last_login_at)
                    VALUES (%s,'email','zoe@example.com','zoe@example.com',%s)""", (s, ago(days=2)))
    b = conn.execute("INSERT INTO invite_batches (label) VALUES ('test') RETURNING id").fetchone()["id"]
    c = conn.execute("""INSERT INTO invite_codes (batch_id, code_sha256, hint, uses) VALUES (%s,'i-1','ABC',1)
                        RETURNING id""", (b,)).fetchone()["id"]
    conn.execute("INSERT INTO invite_redemptions (code_id, user_id, redeemed_at) VALUES (%s,%s,%s)", (c, s, ago(days=2)))
    conn.execute("INSERT INTO ai_usage (user_id, kind, model, at) VALUES (%s,'chat','m',%s)", (s, ago(days=1)))
    conn.execute("""INSERT INTO ai_write_log (user_id, channel, tool, kind, action, created_at)
                    VALUES (%s,'chat:x','log_food','food','create',%s)""", (s, ago(hours=20)))
    # pending: signed in with Apple yesterday, no code yet
    ids["pending"] = p = user(conn, "Pending", ago(days=1), status="pending")
    conn.execute("""INSERT INTO user_identities (user_id, provider, subject, email, last_login_at)
                    VALUES (%s,'apple','001234.abcd','pat@privaterelay.example.com',%s)""", (p, ago(days=1)))
    # disabled admin account, nothing recent; a health history upload 100 days ago
    ids["off"] = d = user(conn, "Off", ago(days=200), status="disabled", consent=ago(days=199))
    conn.execute("""INSERT INTO history_imports (user_id, status, filename, size_bytes, created_at)
                    VALUES (%s,'done','export.zip',100,%s)""", (d, ago(days=100)))
    conn.execute("CREATE TABLE test_ids (k TEXT PRIMARY KEY, id INT)")
    conn.cursor().executemany("INSERT INTO test_ids VALUES (%s,%s)", list(ids.items()))


def ids(conn) -> dict:
    return {r["k"]: r["id"] for r in conn.execute("SELECT k, id FROM test_ids")}


def by_name(res) -> dict:
    return {u["name"]: u for u in res["users"]}


def test_activation_and_methods(conn):
    u = by_name(stats.collect(conn, 7, now=NOW))
    assert (u["Owner"]["method"], u["Owner"]["activated_via"]) == ("admin", "login_code")
    assert u["Owner"]["activated_at"] == ago(days=301)
    assert (u["Tester"]["method"], u["Tester"]["activated_via"]) == ("activation", "login_code")
    assert u["Tester"]["activated_at"] == ago(days=3)          # the first login code, not the later one
    assert (u["Quiet"]["activated_via"], u["Quiet"]["activated_at"]) == ("consent", ago(days=80))
    assert (u["Signup"]["method"], u["Signup"]["activated_via"]) == ("invite", "invite")
    assert u["Signup"]["activated_at"] == ago(days=2)
    assert (u["Pending"]["method"], u["Pending"]["activated_at"]) == ("signup", None)


def test_signals_and_window(conn):
    res = stats.collect(conn, 7, now=NOW)
    u = by_name(res)
    assert u["Owner"]["active_signals"] == ["web", "api", "ai"]
    assert u["Owner"]["data_through"] == date(2026, 10, 20)
    assert u["Tester"]["active_signals"] == ["sync"]               # OW cursor = latest sample, not the import run
    assert u["Signup"]["active_signals"] == ["web", "ai", "write"]
    assert u["Pending"]["active_signals"] == ["web"]
    assert not u["Quiet"]["active"] and u["Quiet"]["last"]["mcp"] == ago(days=10)
    assert u["Quiet"]["last"]["sync"] == ago(days=40)
    assert u["Off"]["last"]["sync"] == ago(days=100) and not u["Off"]["active"]
    s = res["summary"]
    assert s["status"] == {"active": 4, "pending": 1, "disabled": 1}
    assert (s["signed_up"], s["activated"], s["active"]) == (2, 2, 4)
    assert s["active_by_signal"] == {"web": 3, "api": 1, "mcp": 0, "sync": 1, "ai": 2, "write": 1}
    # a longer window picks up the AI client token and the Shortcut upload
    month = by_name(stats.collect(conn, 30, now=NOW))
    assert month["Quiet"]["active_signals"] == ["mcp"]
    quarter = by_name(stats.collect(conn, 90, now=NOW))
    assert quarter["Quiet"]["active_signals"] == ["mcp", "sync"]
    # the signal set is configurable
    only_sync = stats.collect(conn, 7, ("sync",), now=NOW)
    assert [x["name"] for x in only_sync["users"] if x["active"]] == ["Tester"]
    assert only_sync["summary"]["active_by_signal"] == {"sync": 1}
    with pytest.raises(ValueError):
        stats.collect(conn, 7, ("web", "nope"), now=NOW)
    with pytest.raises(ValueError):
        stats.collect(conn, 0, now=NOW)


def test_sort_filters_and_masking(conn):
    res = stats.collect(conn, 7, now=NOW)
    # newest activation first; never activated (Pending) last
    assert [x["name"] for x in res["users"]] == ["Signup", "Tester", "Quiet", "Off", "Owner", "Pending"]
    assert {x["name"] for x in stats.filter_users(res, new_only=True)["users"]} == {"Signup", "Tester", "Pending"}
    assert {x["name"] for x in stats.filter_users(res, active_only=True, new_only=True)["users"]} == \
        {"Signup", "Tester", "Pending"}
    assert {x["name"] for x in stats.filter_users(res, active_only=True)["users"]} == {"Signup", "Tester", "Pending", "Owner"}
    m = by_name(stats.masked(res))
    assert m["Signup"]["email"] == "z***@example.com"
    assert m["Pending"]["email"] == "p***@privaterelay.example.com"
    assert m["Owner"]["email"] is None
    assert by_name(res)["Signup"]["email"] == "zoe@example.com"   # masking returns a copy
    text = stats.render(stats.masked(res))
    assert "zoe@" not in text and "z***@example.com" in text
    assert "signed up 2, activated 2, active 4" in text


def test_mask_email():
    assert stats.mask_email("someone@example.com") == "s***@example.com"
    assert stats.mask_email(None) is None
    assert stats.mask_email("no-at-sign") == "no-at-sign"


def test_referral_columns_when_present(dsn):
    """Servers with referral codes (invite_codes.kind, invite_redemptions.inviter_user_id) show the inviter."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("ALTER TABLE invite_codes ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'admin'")
        conn.execute("ALTER TABLE invite_redemptions ADD COLUMN IF NOT EXISTS inviter_user_id INT")
        i = ids(conn)
        conn.execute("""UPDATE invite_codes SET kind = 'referral'
                        WHERE id = (SELECT code_id FROM invite_redemptions WHERE user_id = %s)""", (i["signup"],))
        conn.execute("UPDATE invite_redemptions SET inviter_user_id = %s WHERE user_id = %s", (i["owner"], i["signup"]))
        res = stats.collect(conn, 7, now=NOW)
        u = by_name(res)
        assert res["window"]["referrals_tracked"] is True
        assert u["Signup"]["method"] == "referral"
        assert u["Signup"]["inviter"] == {"id": i["owner"], "name": "Owner"}
        assert u["Tester"]["inviter"] is None
        assert f"referral<{i['owner']}" in stats.render(res)
        conn.rollback()


def test_cli(dsn, monkeypatch, capsys):
    monkeypatch.setattr(cli, "connect", lambda: psycopg.connect(dsn, row_factory=dict_row))
    monkeypatch.setattr(sys, "argv", ["body-engine", "user-stats", "--days", "30", "--json", "--active-only"])
    cli.main()
    out = json.loads(capsys.readouterr().out)
    assert out["window"]["days"] == 30
    assert all(u["active"] for u in out["users"])
    assert "z***@example.com" in {u["email"] for u in out["users"]}
    monkeypatch.setattr(sys, "argv", ["body-engine", "user-stats", "--full-email"])
    cli.main()
    text = capsys.readouterr().out
    assert "zoe@example.com" in text and text.startswith("accounts 6 ")
    monkeypatch.setattr(sys, "argv", ["body-engine", "user-stats", "--signals", "web,bogus"])
    with pytest.raises(SystemExit):
        cli.main()
    monkeypatch.setattr(sys, "argv", ["body-engine", "user-stats", "--help"])
    with pytest.raises(SystemExit):
        cli.main()
    assert "activated:" in capsys.readouterr().out

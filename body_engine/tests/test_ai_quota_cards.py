"""Jarvis quota cards (ai/quota_cards.py, issue #182, flag ai_cards): grant (idempotent, validated, ledger, notification),
reward(), use (own cards only, a double tap counts once, refused while Jarvis is paused or with nothing to reset), expiry,
revoke, the allowance maths in fallback.summary / precheck, the doctor's "本月最多还会花", periodic rules, the endpoints, CLI.

Separate test database BODY_TEST_DB_NAME_CARDS (default body_test_ai_cards), dropped and recreated every run; skipped
without Postgres. No paid API: DeepSeek's balance endpoint is monkeypatched, spend is written straight into ai_usage.
Users: F (member, consented, uses the fallback), G (member, consented), O (owner).
"""
import argparse
import os
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from body_engine import api, auth
from body_engine.ai import fallback, quota_cards as qc, quota_cards_cli
from body_engine.ai import fallback_params as fp
from body_engine.ai import quota_cards_params as P
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_CARDS", "body_test_ai_cards")
DS_KEY = "test-fallback-key-not-real-0000"


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", ""):
        pytest.exit("the test DB must not be the production DB")
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
def keys(tmp_path_factory):
    d = tmp_path_factory.mktemp("cards")
    mp = pytest.MonkeyPatch()
    (d / "deepseek_api_key").write_text(DS_KEY + "\n")
    mp.setenv("BODY_DEEPSEEK_API_KEY_FILE", str(d / "deepseek_api_key"))
    yield d
    mp.undo()


@pytest.fixture(scope="module")
def world(dsn, keys):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {}
        for n, role in (("O", "owner"), ("F", "member"), ("G", "member")):
            ids[n] = conn.execute("INSERT INTO users (display_name, role, consent_at, timezone) VALUES (%s,%s,now(),%s) RETURNING id",
                                  (n, role, "Australia/Sydney")).fetchone()["id"]
            conn.execute("INSERT INTO ai_fallback_consents (user_id, version) VALUES (%s, %s)", (ids[n], fallback.CONSENT_VERSION))
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        flags.set_flag(conn, P.FLAG, "all", actor="test")
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn, world):
    mp = pytest.MonkeyPatch()
    mp.setattr(api, "settings", Settings(db_name=TEST_DB))
    with TestClient(api.app, base_url="https://testserver") as c:
        yield c
    mp.undo()


@pytest.fixture(autouse=True)
def balance(monkeypatch):
    monkeypatch.setattr(fallback, "fetch_balance", lambda key, timeout=8: 50.0)


def H(token):
    return {"Authorization": f"Bearer {token}"}


def db(dsn):
    return psycopg.connect(dsn, row_factory=dict_row)


@pytest.fixture(autouse=True)
def clean(dsn, world):
    with db(dsn) as c:
        for t in ("ai_quota_ledger", "ai_quota_cards", "ai_quota_card_rules", "ai_usage", "ai_fallback_quota", "notifications"):
            c.execute(f"DELETE FROM {t}")
        c.execute("UPDATE ai_fallback_state SET enabled = true, balance_cny = 50, balance_checked_at = now(), balance_error = NULL")
        flags.set_flag(c, P.FLAG, "all", actor="test")
    yield


def spend(dsn, uid, cny, at=None):
    with db(dsn) as c:
        c.execute("INSERT INTO ai_usage (user_id, kind, model, cost_cny, at) VALUES (%s, %s, %s, %s, coalesce(%s, now()))",
                  (uid, fallback.KIND, fp.MODEL, cny, at))


def give(dsn, uid, kind="reset", **kw):
    with db(dsn) as c:
        kw.setdefault("source", "admin")
        kw.setdefault("reason", "测试")
        kw.setdefault("actor", "test")
        return qc.grant(c, uid, kind, **kw)


# ---------------------------------------------------------------- pure
def test_parse_expires_and_period_key():
    now = datetime(2026, 10, 23, 5, 0, tzinfo=timezone.utc)
    assert qc.parse_expires("30d", now) == now + timedelta(days=30)
    assert qc.parse_expires("2w", now) == now + timedelta(weeks=2)
    assert qc.parse_expires("12h", now) == now + timedelta(hours=12)
    assert qc.parse_expires("never", now) is None and qc.parse_expires(None, now) == "default"
    assert qc.parse_expires("2026-11-30", now) == datetime(2026, 12, 1, tzinfo=timezone.utc)   # through the whole day
    with pytest.raises(ValueError):
        qc.parse_expires("soon", now)
    monthly, weekly = {"period": "monthly", "day": 5}, {"period": "weekly", "day": 3}
    assert qc.period_key(monthly, date(2026, 10, 4)) is None and qc.period_key(monthly, date(2026, 10, 5)) == "2026-10"
    assert qc.period_key(monthly, date(2026, 10, 31)) == "2026-10"     # a missed run catches up later in the month
    assert qc.period_key(weekly, date(2026, 10, 20)) is None            # Tuesday
    assert qc.period_key(weekly, date(2026, 10, 21)) == "2026-W43"      # Wednesday


# ---------------------------------------------------------------- grant / reward
def test_grant_validates(dsn, world):
    ids, _ = world
    bad = [dict(kind="gift"), dict(kind="extend"), dict(kind="extend", amount_cny=P.MAX_EXTEND_CNY + 1),
           dict(kind="reset", amount_cny=1), dict(count=0), dict(count=P.MAX_COUNT + 1), dict(reason=" "),
           dict(source="shop"), dict(actor="")]
    with db(dsn) as c:
        for b in bad:
            args = {"kind": "reset", "source": "admin", "reason": "x", "actor": "test", **b}
            kind = args.pop("kind")
            with pytest.raises(ValueError):
                qc.grant(c, ids["F"], kind, **args)
        with pytest.raises(ValueError):
            qc.grant(c, 999999, "reset", source="admin", reason="x", actor="test")


def test_grant_is_idempotent_with_key_and_audited(dsn, world):
    ids, _ = world
    r1 = give(dsn, ids["F"], count=2, key="welcome")
    r2 = give(dsn, ids["F"], count=2, key="welcome")
    assert len(r1["created"]) == 2 and r2["created"] == [] and r2["existing"] == 2
    assert {c["grant_key"] for c in r1["created"]} == {"welcome#1", "welcome#2"}
    assert len(give(dsn, ids["G"], key="welcome")["created"]) == 1      # keys are per person
    with db(dsn) as c:
        led = c.execute("SELECT * FROM ai_quota_ledger WHERE user_id = %s", (ids["F"],)).fetchall()
        assert [x["event"] for x in led] == ["grant", "grant"] and led[0]["actor"] == "test"
        card = r1["created"][0]
        assert card["status"] == "unused" and card["expires_at"] > datetime.now(timezone.utc) + timedelta(days=P.DEFAULT_EXPIRES_DAYS - 1)
        # one notification for the grant (kind ai_card, opens the card list)
        n = c.execute("SELECT * FROM notifications WHERE user_id = %s", (ids["F"],)).fetchall()
        assert len(n) == 1 and n[0]["kind"] == P.NOTIFY_KIND and n[0]["url"] == P.NOTIFY_URL and "2" in n[0]["title"]
        assert n[0]["title_en"] and n[0]["body_en"] and "测试" in n[0]["body"]


def test_no_notification_when_flag_off(dsn, world):
    ids, _ = world
    with db(dsn) as c:
        flags.set_flag(c, P.FLAG, "off", actor="test")
    r = give(dsn, ids["F"])
    assert r["created"] and r["notified"] is None
    with db(dsn) as c:
        assert c.execute("SELECT count(*) AS n FROM notifications").fetchone()["n"] == 0


def test_reward_is_idempotent(dsn, world):
    ids, _ = world
    with db(dsn) as c:
        a = qc.reward(c, ids["F"], "反馈被采纳", module="feedback", key="adopted:7")
        b = qc.reward(c, ids["F"], "反馈被采纳", module="feedback", key="adopted:7")
        e = qc.reward(c, ids["F"], "邀请朋友", module="invite", key="u:9", kind="extend", amount_cny=1.5)
        assert len(a["created"]) == 1 and b["created"] == [] and len(e["created"]) == 1
        assert a["created"][0]["source"] == "reward" and a["created"][0]["granted_by"] == "module:feedback"
        assert a["created"][0]["grant_key"] == "reward:feedback:adopted:7"
        with pytest.raises(ValueError):
            qc.reward(c, ids["F"], "x", module="feedback", key="")


# ---------------------------------------------------------------- use
def test_reset_card_restores_the_month(dsn, world):
    ids, _ = world
    spend(dsn, ids["F"], fp.MONTHLY_QUOTA_CNY)
    card = give(dsn, ids["F"])["created"][0]
    with db(dsn) as c:
        s = fallback.summary(c, ids["F"])
        assert s["reason"] == "quota" and s["remaining_pct"] == 0 and s["cards"] == {"reset": 1, "extend": 0, "total": 1}
        assert "额度卡" in s["message"]
        r = qc.use(c, ids["F"], card["id"])
        assert r["already"] is False and r["card"]["status"] == "used"
    with db(dsn) as c:
        s = fallback.summary(c, ids["F"])
        assert s["available"] and s["remaining_pct"] == 100 and s["spent_cny"] == 0 and s["cards"]["total"] == 0
        assert fallback.month_spent_cny(c, ids["F"]) == pytest.approx(fp.MONTHLY_QUOTA_CNY)   # the admin's real cost stays
        led = c.execute("SELECT * FROM ai_quota_ledger WHERE event = 'use'").fetchone()
        assert led["actor"] == f"user:{ids['F']}" and float(led["spent_before_cny"]) == pytest.approx(fp.MONTHLY_QUOTA_CNY)
        # a second tap: nothing changes, no second ledger row
        assert qc.use(c, ids["F"], card["id"])["already"] is True
        assert c.execute("SELECT count(*) AS n FROM ai_quota_ledger WHERE event = 'use'").fetchone()["n"] == 1
    spend(dsn, ids["F"], 0.75)   # spend after the reset counts again
    with db(dsn) as c:
        assert fallback.summary(c, ids["F"])["remaining_pct"] == 75


def test_extend_card_adds_to_the_month(dsn, world):
    ids, _ = world
    spend(dsn, ids["F"], fp.MONTHLY_QUOTA_CNY)
    card = give(dsn, ids["F"], "extend", amount_cny=1.5)["created"][0]
    with db(dsn) as c:
        assert qc.user_view(c, ids["F"])["cards"][0]["pct"] == 50     # shown as % of the monthly quota, no amount
        qc.use(c, ids["F"], card["id"])
        s = fallback.summary(c, ids["F"])
        assert s["quota_cny"] == pytest.approx(4.5) and s["remaining_pct"] == 33 and s["available"]
        a = fallback.allowance(c, ids["F"])
        assert a["base_cny"] == fp.MONTHLY_QUOTA_CNY and a["extended_cny"] == 1.5


def test_use_refusals(dsn, world):
    ids, _ = world
    reset_card = give(dsn, ids["F"])["created"][0]
    with db(dsn) as c:
        with pytest.raises(qc.CardError) as e:       # nothing used yet: keep the card
            qc.use(c, ids["F"], reset_card["id"])
        assert e.value.code == "nothing_to_reset"
    with db(dsn) as c:
        with pytest.raises(qc.CardError) as e:       # not your card
            qc.use(c, ids["G"], reset_card["id"])
        assert e.value.status == 404
    spend(dsn, ids["F"], 1)
    with db(dsn) as c:   # DeepSeek balance below the stop line: Jarvis is paused for everyone, cards wait
        c.execute("UPDATE ai_fallback_state SET balance_cny = %s, balance_checked_at = now()", (fp.BALANCE_STOP_CNY - 0.5,))
    with db(dsn) as c:
        with pytest.raises(qc.CardError) as e:
            qc.use(c, ids["F"], reset_card["id"])
        assert e.value.code == "paused"
    with db(dsn) as c:
        c.execute("UPDATE ai_fallback_state SET balance_cny = 50")
        c.execute("INSERT INTO ai_fallback_quota (user_id, monthly_cny) VALUES (%s, 0)", (ids["F"],))
    with db(dsn) as c:
        with pytest.raises(qc.CardError) as e:       # quota 0 = not allowed; a card does not change that
            qc.use(c, ids["F"], reset_card["id"])
        assert e.value.code == "no_quota"


def test_cards_never_get_past_the_pause(dsn, world):
    from body_engine.ai.chat import ChatError
    ids, _ = world
    spend(dsn, ids["F"], fp.MONTHLY_QUOTA_CNY)
    card = give(dsn, ids["F"])["created"][0]
    with db(dsn) as c:
        qc.use(c, ids["F"], card["id"])
        c.execute("UPDATE ai_fallback_state SET balance_cny = %s, balance_checked_at = now()", (fp.BALANCE_STOP_CNY - 0.5,))
        c.commit()
        assert fallback.summary(c, ids["F"])["reason"] == "fallback_paused"
        with pytest.raises(ChatError) as e:
            fallback.precheck(c, ids["F"], "sys", [{"content": [{"type": "text", "text": "hi"}]}], [])
        assert e.value.code == "fallback_paused"


def test_expiry_and_revoke(dsn, world):
    ids, _ = world
    old = give(dsn, ids["F"], expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))["created"][0]
    keep = give(dsn, ids["F"], expires_at=None)["created"][0]
    other = give(dsn, ids["F"])["created"][0]
    spend(dsn, ids["F"], 1)
    with db(dsn) as c:
        assert qc.counts(c, ids["F"])["total"] == 2                    # an expired card is not counted even before the job
        with pytest.raises(qc.CardError) as e:
            qc.use(c, ids["F"], old["id"])
        assert e.value.code == "expired"
    with db(dsn) as c:
        assert c.execute("SELECT status FROM ai_quota_cards WHERE id = %s", (old["id"],)).fetchone()["status"] == "expired"
        assert c.execute("SELECT count(*) AS n FROM ai_quota_ledger WHERE event = 'expire'").fetchone()["n"] == 1
        assert qc.revoke(c, other["id"], actor="cli:test", reason="发错了")["status"] == "revoked"
        assert qc.revoke(c, other["id"], actor="cli:test") is None     # only unused cards
        assert qc.revoke(c, old["id"], actor="cli:test") is None
        v = qc.user_view(c, ids["F"])
        assert [x["id"] for x in v["cards"]] == [keep["id"]] and v["cards"][0]["expires_on"] is None
        assert {x["status"] for x in v["history"]} == {"expired", "revoked"}
    with db(dsn) as c:
        with pytest.raises(qc.CardError) as e:
            qc.use(c, ids["F"], other["id"])
        assert e.value.code == "revoked"


# ---------------------------------------------------------------- admin / doctor
def test_admin_status_counts_unused_cards(dsn, world):
    ids, _ = world
    with db(dsn) as c:   # only F uses the fallback (O has no butler link here, G: quota 0 = not eligible)
        c.execute("INSERT INTO ai_fallback_quota (user_id, monthly_cny) VALUES (%s, 0), (%s, 0)", (ids["G"], ids["O"]))
    spend(dsn, ids["F"], 1)
    give(dsn, ids["F"])
    give(dsn, ids["F"], "extend", amount_cny=2)
    with db(dsn) as c:
        s = fallback.admin_status(c, refresh=False)
        f = next(u for u in s["users"] if u["user_id"] == ids["F"])
        assert f["cards_reset"] == 1 and f["cards_extend"] == 1 and f["cards_extend_cny"] == 2
        # left this month 3 - 1 = 2; the cards can add at most 2 (extend) + 1 reset x (3 + 2) = 7
        assert f["left_cny"] == pytest.approx(2) and f["cards_max_cny"] == pytest.approx(7)
        assert s["month_remaining_cny"] == pytest.approx(9) and s["month_cards_max_cny"] == pytest.approx(7)


def test_periodic_rules(dsn, world):
    ids, _ = world
    spend(dsn, ids["F"], 0.1)   # F used Jarvis recently, G did not
    with db(dsn) as c:
        qc.set_rule(c, "monthly-active", period="monthly", day=1, kind="reset", reason="每月一张", actor="test",
                    active_within_days=30)
        with pytest.raises(ValueError):
            qc.set_rule(c, "Bad Name", period="monthly", day=1, kind="reset", reason="x", actor="test")
    with db(dsn) as c:
        dry = qc.run_periodic(c, dry_run=True)
        assert [g["user_id"] for g in dry["would_grant"]] == [ids["F"]] and dry["granted"] == []
        assert c.execute("SELECT count(*) AS n FROM ai_quota_cards").fetchone()["n"] == 0
        first = qc.run_periodic(c)
        second = qc.run_periodic(c)
        assert [g["user_id"] for g in first["granted"]] == [ids["F"]] and second["granted"] == []
        card = c.execute("SELECT * FROM ai_quota_cards").fetchone()
        assert card["source"] == "periodic" and card["granted_by"] == "rule:monthly-active" and card["grant_key"].startswith("rule:monthly-active:")
        assert qc.run_periodic(c, dry_run=True)["would_grant"] == []
        # next month the same rule grants again
        nxt = datetime.now(timezone.utc).replace(day=1) + timedelta(days=40)
        assert [g["user_id"] for g in qc.run_periodic(c, now=nxt)["granted"]] == [ids["F"]]
        assert qc.set_rule_enabled(c, "monthly-active", False, "test")
        assert qc.run_periodic(c, now=nxt + timedelta(days=40))["granted"] == []
        # the flag off for everyone: nobody is eligible
        qc.set_rule_enabled(c, "monthly-active", True, "test")
        flags.set_flag(c, P.FLAG, "off", actor="test")
        off = qc.run_periodic(c, now=nxt + timedelta(days=80))
        assert off["granted"] == [] and off["skipped"] and off["expired"] == 0
        assert qc.run_periodic(c, now=nxt + timedelta(days=80), dry_run=True)["would_grant"] == []


def test_cli(dsn, world, capsys):
    ids, _ = world
    parser = argparse.ArgumentParser()
    quota_cards_cli.add_parser(parser.add_subparsers(dest="cmd"))
    with db(dsn) as c:
        quota_cards_cli.run(c, parser.parse_args(["ai-cards", "grant", "--user", str(ids["F"]), "--kind", "reset",
                                                  "--count", "2", "--expires", "7d", "--reason", "补偿", "--by", "admin"]))
        quota_cards_cli.run(c, parser.parse_args(["ai-cards", "grant", "--all", "--kind", "extend", "--amount", "1",
                                                  "--reason", "活动", "--key", "event-1", "--no-notify"]))
        quota_cards_cli.run(c, parser.parse_args(["ai-cards", "grant", "--all", "--kind", "extend", "--amount", "1",
                                                  "--reason", "活动", "--key", "event-1"]))
        out = capsys.readouterr().out
        assert "共发出 2 张" in out and "共发出 3 张" in out and "共发出 0 张" in out
        rows = qc.list_cards(c, ids["F"])
        assert len(rows) == 3 and {r["granted_by"] for r in rows} >= {"cli:admin"}
        quota_cards_cli.run(c, parser.parse_args(["ai-cards", "revoke", "--user", str(ids["F"]), "--reason", "测试"]))
        assert "收回 3 张" in capsys.readouterr().out
        quota_cards_cli.run(c, parser.parse_args(["ai-cards", "list", "--user", str(ids["F"])]))
        quota_cards_cli.run(c, parser.parse_args(["ai-cards", "ledger", "--user", str(ids["F"])]))
        quota_cards_cli.run(c, parser.parse_args(["ai-cards", "rule-set", "weekly-x", "--period", "weekly", "--day", "1",
                                                  "--kind", "extend", "--amount", "0.5", "--reason", "每周"]))
        quota_cards_cli.run(c, parser.parse_args(["ai-cards", "rules"]))
        quota_cards_cli.run(c, parser.parse_args(["ai-cards", "periodic", "--dry-run"]))
        out = capsys.readouterr().out
        assert "revoke" in out and "weekly-x" in out and "would_grant" in out
        with pytest.raises(SystemExit):
            quota_cards_cli.run(c, parser.parse_args(["ai-cards", "grant", "--user", str(ids["F"]), "--kind", "extend",
                                                      "--reason", "x"]))


# ---------------------------------------------------------------- endpoints
def test_endpoints(client, dsn, world):
    ids, tok = world
    spend(dsn, ids["F"], fp.MONTHLY_QUOTA_CNY)
    card = give(dsn, ids["F"])["created"][0]
    g_card = give(dsn, ids["G"])["created"][0]
    me = client.get("/v1/web/me", headers=H(tok["F"])).json()["ai"]
    assert me["mode"] == "fallback" and me["cards"]["reset"] == 1 and me["remaining_pct"] == 0
    assert "deepseek" not in str(me.get("cards")).lower()
    r = client.get("/v1/users/me/ai-cards", headers=H(tok["F"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert [c["id"] for c in body["cards"]] == [card["id"]] and body["fallback"]["remaining_pct"] == 0
    assert "amount_cny" not in body["cards"][0] and "granted_by" not in body["cards"][0]
    assert client.get(f"/v1/users/{ids['G']}/ai-cards", headers=H(tok["F"])).status_code == 403
    assert client.post(f"/v1/users/me/ai-cards/{g_card['id']}/use", headers=H(tok["F"])).status_code == 404
    r1 = client.post(f"/v1/users/me/ai-cards/{card['id']}/use", headers=H(tok["F"]))
    r2 = client.post(f"/v1/users/me/ai-cards/{card['id']}/use", headers=H(tok["F"]))
    assert r1.status_code == 200 and r1.json()["already"] is False and r1.json()["fallback"]["remaining_pct"] == 100
    assert r2.status_code == 200 and r2.json()["already"] is True and r2.json()["fallback"]["remaining_pct"] == 100
    give(dsn, ids["F"])
    nothing = client.post(f"/v1/users/me/ai-cards/{qc_unused(dsn, ids['F'])}/use", headers=H(tok["F"]))
    assert nothing.status_code == 409 and nothing.json()["detail"]["code"] == "nothing_to_reset"
    en = client.post(f"/v1/users/me/ai-cards/{qc_unused(dsn, ids['F'])}/use", headers={**H(tok["F"]), "X-Body-Lang": "en"})
    assert "used any" in en.json()["detail"]["message"]
    # flag off: no card endpoints, no card counts on the page
    with db(dsn) as c:
        flags.set_flag(c, P.FLAG, "off", actor="test")
    assert client.get("/v1/users/me/ai-cards", headers=H(tok["F"])).status_code == 404
    assert client.post(f"/v1/users/me/ai-cards/{card['id']}/use", headers=H(tok["F"])).status_code == 404
    assert "cards" not in client.get("/v1/web/me", headers=H(tok["F"])).json()["ai"]


def test_flag_off_is_invisible(client, dsn, world):
    """Off (the default after a deploy): no card entry anywhere on the page, the hourly job does nothing,
    the admin CLI still grants (for testing) but sends no notification."""
    ids, tok = world
    with db(dsn) as c:
        flags.set_flag(c, "notifications", "all", actor="test")
    on = client.get("/v1/users/me/notification-settings", headers=H(tok["F"])).json()
    assert "ai_card" in on["categories"] and on["labels"]["ai_card"]
    with db(dsn) as c:
        flags.set_flag(c, P.FLAG, "off", actor="test")
        qc.set_rule(c, "monthly", period="monthly", day=1, kind="reset", reason="每月", actor="test")
    off = client.get("/v1/users/me/notification-settings", headers=H(tok["F"])).json()
    assert "ai_card" not in off["categories"] and "ai_card" not in off["labels"]
    assert set(off["categories"]) >= {"circle", "system"}
    spend(dsn, ids["F"], fp.MONTHLY_QUOTA_CNY)
    r = give(dsn, ids["F"])   # what `ai-cards grant --user N` calls
    assert r["created"] and r["notified"] is None
    ai = client.get("/v1/web/me", headers=H(tok["F"])).json()["ai"]
    assert "cards" not in ai and "额度卡" not in (ai["message"] or "")
    with db(dsn) as c:
        assert qc.run_periodic(c)["skipped"] and c.execute("SELECT count(*) AS n FROM ai_quota_cards").fetchone()["n"] == 1
        flags.set_flag(c, "notifications", "off", actor="test")


def qc_unused(dsn, uid):
    with db(dsn) as c:
        return c.execute("SELECT id FROM ai_quota_cards WHERE user_id = %s AND status = 'unused' ORDER BY id LIMIT 1",
                         (uid,)).fetchone()["id"]

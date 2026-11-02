"""Posture photos in Jarvis (ai/posture.py, issue #174): sent once to the AI the user picked, then replaced by a placeholder.

Own test database, dropped and recreated on every run. No paid API is called: every provider adapter is FakeAdapter, the picture
is a synthetic 1x1 PNG, and no real model's result on posture is checked anywhere here (that is the plan's phase 0, not run).
"""
import argparse
import json
import logging
from datetime import date, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from body_engine import api, auth, batch
from body_engine.ai import adapters, catalog, context, crypto, fallback, posture
from body_engine.ai import chat as chat_mod
from body_engine.ai import posture_params as pp
from body_engine.ai import run_params as rp
from body_engine.ai.adapters.fake import FakeAdapter
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags

TEST_DB = "body_test_ai_posture"
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
IMG = {"media_type": "image/png", "data": PNG}
PLACEHOLDER = {"type": "image_redacted", "purpose": "posture", "n": 3}
LINE = "（这里有 3 张体态照片，按设置没有保存）"
ADD_EVENT = {"occurred_at": "2026-10-16T18:00", "title": "测试", "category": "pain", "body_part": "肩", "severity": 1, "note": None}


@pytest.fixture(scope="module")
def dsn():
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
        flags.set_flag(conn, pp.FLAG, "all", actor="test")
    return dsn


@pytest.fixture(scope="module")
def client(dsn, tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("BODY_AI_MASTER_KEY_FILE", str(tmp_path_factory.mktemp("ai") / "ai_master_key"))
    crypto.init_master_key()
    mp.setattr(api, "settings", Settings(db_name=TEST_DB))
    with TestClient(api.app, base_url="https://testserver") as c:
        yield c
    mp.undo()


@pytest.fixture(autouse=True)
def fake_adapters(monkeypatch):
    for kind in ("anthropic_api", "openai_api", "gemini_api", "openai_compatible"):
        monkeypatch.setitem(adapters.ADAPTERS, kind, lambda: FakeAdapter)
    FakeAdapter.script, FakeAdapter.calls = [], []


def db(dsn):
    return psycopg.connect(dsn, row_factory=dict_row)


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.fixture
def person(client, dsn):
    """A new adult with their own API key, so no test sees another's conversations."""
    with db(dsn) as conn:
        uid = conn.execute("INSERT INTO users (display_name, birth_date) VALUES ('P', '1990-01-01') RETURNING id").fetchone()["id"]
        token = auth.create_token(conn, uid, "web", ["read", "write"])
    r = client.post("/v1/users/me/ai-providers", headers=H(token),
                    json={"kind": "anthropic_api", "api_key": "sk-ant-test-first-1111", "monthly_budget_usd": 5})
    assert r.status_code == 201, r.text
    return uid, token


def send(client, token, message="看看体态", images=(IMG, IMG, IMG), purpose="posture", **kw):
    body = {"message": message, "images": list(images), **kw}
    if purpose:
        body["purpose"] = purpose
    return client.post("/v1/assistant/chat", headers=H(token), json=body)


def rows(dsn, uid) -> list[dict]:
    with db(dsn) as conn:
        return conn.execute("""SELECT id, role, content, content::text AS raw, redact_after FROM ai_messages
                               WHERE user_id = %s ORDER BY id""", (uid,)).fetchall()


def stored_user_message(dsn, uid) -> dict:
    return next(r for r in rows(dsn, uid) if r["role"] == "user")


def sent_images(call: dict) -> list[dict]:
    return [b for m in call["history"] for b in m["content"] if b.get("type") == "image"]


def no_photo_left(dsn, uid) -> bool:
    return all(PNG not in r["raw"] and r["redact_after"] is None for r in rows(dsn, uid))


# ---------------------------------------------------------------- the normal turn
def test_replaced_when_the_turn_ends_only_the_text_stays(client, person, dsn):
    uid, token = person
    FakeAdapter.script = [{"text": "肩膀看起来偏前"}]
    r = send(client, token)
    assert r.status_code == 200, r.text
    evs = sse(r.text)
    assert evs[-1][0] == "done"
    assert len(sent_images(FakeAdapter.calls[0])) == 3   # the AI did get them
    assert FakeAdapter.calls[0]["history"][-1]["content"][1]["purpose"] == "posture"
    user, answer = rows(dsn, uid)[:2]
    assert PNG not in user["raw"] and user["redact_after"] is None
    assert [b["type"] for b in user["content"]] == ["text", "image_redacted"] and user["content"][1] == PLACEHOLDER
    assert "看看体态" in user["content"][0]["text"]
    assert answer["content"] == [{"type": "text", "text": "肩膀看起来偏前"}]


def test_the_fixed_notice_comes_before_the_answer_and_when_reopened(client, person):
    _, token = person
    FakeAdapter.script = [{"text": "答"}]
    evs = sse(send(client, token).text)
    assert evs[1] == ("notice", {"code": "posture", "message": "这个模型看体态的准确度有限，结论仅供参考"})
    assert [e for e, _ in evs].index("text") > 1
    view = client.get(f"/v1/assistant/conversations/{evs[0][1]['id']}", headers=H(token))
    assert PNG not in view.text
    first = view.json()["messages"][0]
    assert first["images"] == 3 and first["notice"] == posture.notice()
    en = client.post("/v1/assistant/chat", headers={**H(token), "X-Body-Lang": "en"},
                     json={"message": "x", "images": [IMG], "purpose": "posture"})
    assert sse(en.text)[1][1]["message"] == "This model's accuracy on posture is limited; treat the result as for reference only"


def test_the_tool_loop_still_has_the_photos_until_the_turn_ends(client, person, dsn):
    uid, token = person
    FakeAdapter.script = [{"text": "", "tool_calls": [{"id": "r1", "name": "list_metrics", "args": {}}]}, {"text": "答"}]
    assert sse(send(client, token).text)[-1][0] == "done"
    assert [len(sent_images(c)) for c in FakeAdapter.calls] == [3, 3]
    assert no_photo_left(dsn, uid)


def test_later_turns_get_the_line_of_text_not_the_photos(client, person, dsn):
    uid, token = person
    FakeAdapter.script = [{"text": "答"}]
    conv = sse(send(client, token).text)[0][1]["id"]
    FakeAdapter.script = [{"text": "好"}]
    r = send(client, token, "接着说", images=(), purpose=None, conversation_id=conv)
    assert r.status_code == 200
    call = FakeAdapter.calls[-1]
    assert sent_images(call) == []
    assert any(b.get("text") == LINE for m in call["history"] for b in m["content"])
    assert not any(b.get("type") == "image_redacted" for m in call["history"] for b in m["content"])   # adapters never see one
    assert no_photo_left(dsn, uid)


def test_the_placeholder_in_the_text_of_a_fold():
    h = context.show_redacted([{"role": "user", "content": [{"type": "text", "text": "问"}, PLACEHOLDER]}])
    assert LINE in context.transcript(h) and h[0]["content"][1]["type"] == "text"
    assert context.redacted_text({"n": 2}) == "（这里有 2 张照片，按设置没有保存）"


# ---------------------------------------------------------------- switching AI
def _switch(client, token):
    r = client.post("/v1/users/me/ai-providers", headers=H(token),
                    json={"kind": "openai_api", "api_key": "sk-test-second-2222", "model": "gpt-6.1-sol",
                          "monthly_budget_usd": 1, "is_default": True})
    assert r.status_code == 201, r.text


def test_after_switching_ai_the_old_posture_photos_do_not_go_to_the_new_ai(client, person):
    _, token = person
    FakeAdapter.script = [{"text": "答"}]
    conv = sse(send(client, token).text)[0][1]["id"]
    first_key = FakeAdapter.calls[-1]["api_key"]
    _switch(client, token)
    FakeAdapter.script = [{"text": "好"}]
    assert send(client, token, "继续", images=(), purpose=None, conversation_id=conv).status_code == 200
    call = FakeAdapter.calls[-1]
    assert call["api_key"] != first_key and sent_images(call) == []
    assert any(b.get("text") == LINE for m in call["history"] for b in m["content"])


def test_known_gap_an_ordinary_photo_still_reaches_the_new_ai(client, person):
    """Not changed here, on purpose: decision D2 of the plan. When it is decided, this test changes with REDACT_PURPOSES."""
    _, token = person
    FakeAdapter.script = [{"text": "答"}]
    conv = sse(send(client, token, purpose=None).text)[0][1]["id"]
    first_key = FakeAdapter.calls[-1]["api_key"]
    _switch(client, token)
    FakeAdapter.script = [{"text": "好"}]
    assert send(client, token, "继续", images=(), purpose=None, conversation_id=conv).status_code == 200
    call = FakeAdapter.calls[-1]
    assert call["api_key"] != first_key and len(sent_images(call)) == 3


# ---------------------------------------------------------------- how a turn can end
class Boom(FakeAdapter):
    error: Exception = adapters.AdapterError("模型出错", "unavailable")

    def stream_turn(self, system, history, tools):
        raise self.error
        yield


@pytest.mark.parametrize("error", [adapters.AdapterError("模型出错", "unavailable"), RuntimeError("boom")],
                         ids=["adapter_error", "internal_error"])
def test_a_failed_answer_replaces_them_too_and_logs_nothing(client, person, dsn, monkeypatch, caplog, capfd, error):
    uid, token = person
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(Boom, "error", error)
    monkeypatch.setitem(adapters.ADAPTERS, "anthropic_api", lambda: Boom)
    evs = sse(send(client, token).text)
    assert evs[-1][0] == "error" and PNG not in json.dumps(evs)
    assert stored_user_message(dsn, uid)["content"][1] == PLACEHOLDER and no_photo_left(dsn, uid)
    out = capfd.readouterr()
    assert PNG not in out.out and PNG not in out.err and PNG not in caplog.text


def test_over_budget_between_two_rounds_replaces_them(client, person, dsn):
    uid, token = person
    huge = {"input_tokens": 50_000_000, "output_tokens": 0}
    FakeAdapter.script = [{"text": "", "tool_calls": [{"id": "r1", "name": "list_metrics", "args": {}}], "usage": huge}]
    evs = sse(send(client, token).text)
    assert evs[-1][0] == "error" and len(FakeAdapter.calls) == 1
    assert no_photo_left(dsn, uid)


def test_a_turn_stopped_for_taking_too_long_replaces_them(person, dsn):
    """runs.py closes the generator past MAX_RUN_SECONDS."""
    uid, _ = person
    with db(dsn) as conn:
        viewer = {"id": uid, "token_scopes": ["read", "write"]}
        gen = chat_mod.chat_events(conn, viewer, "看看", [IMG, IMG, IMG], None, purpose="posture")
        assert next(gen)["event"] == "conversation"
        assert next(gen)["event"] == "notice"
        assert PNG in stored_user_message(dsn, uid)["raw"]   # stored, the turn is running
        gen.close()
    assert no_photo_left(dsn, uid)


def _pending(client, token, monkeypatch):
    monkeypatch.setattr(chat_mod, "ALWAYS_CONFIRM", chat_mod.ALWAYS_CONFIRM | {"add_event"})
    FakeAdapter.script = [{"text": "我来记", "tool_calls": [{"id": "w1", "name": "add_event", "args": ADD_EVENT}]}]
    evs = sse(send(client, token).text)
    assert evs[-1][0] == "confirm"
    return evs[-1][1]["conversation_id"], evs[-1][1]["calls"][0]["call_id"]


@pytest.mark.parametrize("approve", [True, False], ids=["approved", "rejected"])
def test_a_write_confirmation_keeps_them_until_the_turn_really_ends(client, person, dsn, monkeypatch, approve):
    uid, token = person
    _, call_id = _pending(client, token, monkeypatch)
    waiting = stored_user_message(dsn, uid)
    assert PNG in waiting["raw"] and waiting["redact_after"] is not None
    FakeAdapter.script = [{"text": "好了"}]
    evs = sse(client.post("/v1/assistant/chat/confirm", headers=H(token), json={"call_id": call_id, "approve": approve}).text)
    assert evs[-1][0] == "done"
    assert len(sent_images(FakeAdapter.calls[-1])) == 3   # the same turn goes on
    assert no_photo_left(dsn, uid)


def test_a_new_message_instead_of_confirming_replaces_them(client, person, dsn, monkeypatch):
    uid, token = person
    conv, _ = _pending(client, token, monkeypatch)
    FakeAdapter.script = [{"text": "好"}]
    assert sse(send(client, token, "算了", images=(), purpose=None, conversation_id=conv).text)[-1][0] == "done"
    assert no_photo_left(dsn, uid)


# ---------------------------------------------------------------- the hourly clean-up
def test_threshold_is_longer_than_a_turn_may_run():
    assert pp.REDACT_AFTER_MINUTES * 60 > rp.MAX_RUN_SECONDS


def _crashed(dsn, uid, minutes: int) -> int:
    """What a server that stopped mid-turn leaves: the message stored, no turn end ever called."""
    with db(dsn) as conn:
        conv = conn.execute("INSERT INTO ai_conversations (user_id, title, system_prompt) VALUES (%s, 't', 's') RETURNING id",
                            (uid,)).fetchone()["id"]
        content = [{"type": "text", "text": "x"}, {**IMG, "type": "image", "purpose": "posture"}]
        return chat_mod._add_message(conn, conv, uid, "user", content, redact_after=minutes)


def test_hourly_cleanup_replaces_only_what_is_overdue_and_is_idempotent(client, person, dsn, monkeypatch):
    uid, token = person
    overdue = _crashed(dsn, uid, -1)
    recent = _crashed(dsn, uid, pp.REDACT_AFTER_MINUTES)
    FakeAdapter.script = [{"text": "答"}]
    assert send(client, token, purpose=None).status_code == 200   # an ordinary photo of the same person
    with db(dsn) as conn:
        assert posture.redact_overdue(conn) >= 1
        assert posture.redact_overdue(conn) == 0
    by_id = {r["id"]: r for r in rows(dsn, uid)}
    assert PNG not in by_id[overdue]["raw"] and by_id[overdue]["redact_after"] is None
    assert by_id[overdue]["content"][1] == {"type": "image_redacted", "purpose": "posture", "n": 1}
    assert PNG in by_id[recent]["raw"] and by_id[recent]["redact_after"] is not None
    ordinary = [r for i, r in by_id.items() if i not in (overdue, recent) and r["role"] == "user"][0]
    assert PNG in ordinary["raw"] and ordinary["redact_after"] is None


def test_hourly_cleanup_after_a_confirmation_never_answered(client, person, dsn, monkeypatch):
    uid, token = person
    _, call_id = _pending(client, token, monkeypatch)
    with db(dsn) as conn:
        conn.execute("UPDATE ai_messages SET redact_after = now() - interval '1 minute' WHERE user_id = %s AND redact_after IS NOT NULL", (uid,))
        conn.commit()
        assert posture.redact_overdue(conn) == 1
    assert no_photo_left(dsn, uid)
    FakeAdapter.script = [{"text": "好了"}]   # answering it late still works; the AI gets the line of text
    assert sse(client.post("/v1/assistant/chat/confirm", headers=H(token), json={"call_id": call_id, "approve": False}).text)[-1][0] == "done"
    assert sent_images(FakeAdapter.calls[-1]) == []


def test_the_hourly_batch_runs_the_cleanup(client, person, dsn, monkeypatch, capsys):
    uid, _ = person
    overdue = _crashed(dsn, uid, -1)
    monkeypatch.setattr(batch.db, "connect", lambda: db(dsn))
    args = argparse.Namespace(user_id=uid, steps="", workers=1, full=False, no_skip=False, max_age_hours=6.0)
    assert batch.main(args) == 0   # one user's run: not the all-users housekeeping
    assert PNG in {r["id"]: r for r in rows(dsn, uid)}[overdue]["raw"]
    monkeypatch.setattr(batch, "list_users", lambda conn, only=None: [])
    capsys.readouterr()
    args.user_id = None
    assert batch.main(args) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1].removeprefix(batch.RESULT_PREFIX))
    assert result["ai_images_redacted"] >= 1 and not result["errors"]
    assert PNG not in {r["id"]: r for r in rows(dsn, uid)}[overdue]["raw"]


# ---------------------------------------------------------------- ordinary chat photos: unchanged
def test_an_ordinary_photo_is_stored_and_sent_as_before(client, person, dsn):
    uid, token = person
    FakeAdapter.script = [{"text": "看到了"}]
    r = send(client, token, "这是什么", images=(IMG,), purpose=None)
    evs = sse(r.text)
    assert "notice" not in [e for e, _ in evs]
    assert FakeAdapter.calls[-1]["history"][-1]["content"][1] == {"type": "image", "media_type": "image/png", "data": PNG}
    user = stored_user_message(dsn, uid)
    assert user["content"][1] == {"type": "image", "media_type": "image/png", "data": PNG} and user["redact_after"] is None
    FakeAdapter.script = [{"text": "好"}]
    assert send(client, token, "再看看", images=(), purpose=None, conversation_id=evs[0][1]["id"]).status_code == 200
    assert len(sent_images(FakeAdapter.calls[-1])) == 1
    assert PNG in stored_user_message(dsn, uid)["raw"]


def test_a_conversation_with_both_keeps_the_ordinary_photo(client, person, dsn):
    uid, token = person
    FakeAdapter.script = [{"text": "答"}]
    conv = sse(send(client, token, "这是什么", images=(IMG,), purpose=None).text)[0][1]["id"]
    FakeAdapter.script = [{"text": "答"}]
    assert send(client, token, conversation_id=conv).status_code == 200
    user_rows = [r for r in rows(dsn, uid) if r["role"] == "user"]
    assert PNG in user_rows[0]["raw"] and user_rows[1]["content"][1] == PLACEHOLDER


def test_d2_only_the_one_setting_changes(client, person, dsn, monkeypatch):
    """The owner's decision D2: add None to REDACT_PURPOSES and ordinary photos follow the same path, with no notice."""
    uid, token = person
    monkeypatch.setattr(pp, "REDACT_PURPOSES", frozenset({None, "posture"}))
    FakeAdapter.script = [{"text": "答"}]
    evs = sse(send(client, token, "这是什么", images=(IMG,), purpose=None).text)
    assert "notice" not in [e for e, _ in evs] and len(sent_images(FakeAdapter.calls[-1])) == 1
    assert stored_user_message(dsn, uid)["content"][1] == {"type": "image_redacted", "purpose": None, "n": 1}
    assert no_photo_left(dsn, uid)


# ---------------------------------------------------------------- who may send them
def test_unknown_purpose_is_refused_and_stores_nothing(client, person, dsn):
    uid, token = person
    assert send(client, token, purpose="selfie").status_code == 422
    assert rows(dsn, uid) == []
    with db(dsn) as conn:
        with pytest.raises(chat_mod.ChatError) as e:
            chat_mod.chat_events(conn, {"id": uid, "token_scopes": ["read"]}, "x", [IMG], None, purpose="selfie")
    assert e.value.code == "bad_purpose" and e.value.status == 422


def _flag(dsn, state):
    with db(dsn) as conn:
        flags.set_flag(conn, pp.FLAG, state, actor="test")


def test_flag_off_refuses_rather_than_storing_an_ordinary_photo(client, person, dsn):
    uid, token = person
    _flag(dsn, "off")
    try:
        r = send(client, token)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "posture_off"
        assert rows(dsn, uid) == [] and FakeAdapter.calls == []
        assert client.get("/v1/users/me/ai-choice", headers=H(token)).json()["ai"]["posture"] == "off"
    finally:
        _flag(dsn, "all")
    assert client.get("/v1/users/me/ai-choice", headers=H(token)).json()["ai"]["posture"] == "photos"


def test_only_a_known_adult(client, person, dsn):
    uid, token = person
    today = date.today()
    almost = today.replace(year=today.year - 18) + timedelta(days=2)
    for birth in (almost, None):
        with db(dsn) as conn:
            conn.execute("UPDATE users SET birth_date = %s WHERE id = %s", (birth, uid))
        assert send(client, token).status_code == 403 and rows(dsn, uid) == []
        assert client.get("/v1/users/me/ai-choice", headers=H(token)).json()["ai"]["posture"] == "off"
    assert posture.age_on(date(2000, 3, 2), date(2018, 3, 1)) == 17 and posture.age_on(date(2000, 3, 2), date(2018, 3, 2)) == 18


def test_mode_by_ai(person, dsn, monkeypatch):
    uid, _ = person
    with db(dsn) as conn:
        assert posture.mode(conn, uid, True, False, True) == "photos"
        assert posture.mode(conn, uid, True, False, None) == "photos"    # openai_compatible: not known, the user's own key
        assert posture.mode(conn, uid, True, False, False) == "guide"
        assert posture.mode(conn, uid, False, False, True) == "guide"    # a subscription AI: photos never reach this server
        assert posture.mode(conn, uid, True, True, True) == "guide"      # the fallback: phase 0 has not run (D4)
        monkeypatch.setattr(pp, "FALLBACK_PHOTOS", True)
        assert posture.mode(conn, uid, True, True, True) == "photos"


def test_the_fallback_takes_no_posture_photos(client, person, dsn, monkeypatch):
    uid, token = person
    monkeypatch.setattr(chat_mod, "resolve_provider", lambda *a, **k: fallback.provider())
    r = send(client, token)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "posture_off"
    assert rows(dsn, uid) == [] and FakeAdapter.calls == []


def test_no_vision_stays_a_clear_refusal(client, person, dsn, monkeypatch):
    uid, token = person
    real = catalog.kind_info
    monkeypatch.setattr(catalog, "kind_info", lambda k: {**real(k), "vision": False})
    r = send(client, token)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "no_vision" and rows(dsn, uid) == []
    assert client.get("/v1/users/me/ai-choice", headers=H(token)).json()["ai"]["posture"] == "guide"


def test_a_message_without_pictures_is_an_ordinary_message(client, person, dsn):
    uid, token = person
    FakeAdapter.script = [{"text": "好"}]
    evs = sse(send(client, token, images=()).text)
    assert evs[-1][0] == "done" and "notice" not in [e for e, _ in evs]
    assert stored_user_message(dsn, uid)["redact_after"] is None

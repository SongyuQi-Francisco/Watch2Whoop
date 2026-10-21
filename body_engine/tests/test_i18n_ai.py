"""English for the AI settings / chat / fallback AI / voice input / history import texts the Today page shows (issue #42).

With `X-Body-Lang: en` the translated fields carry no Chinese; without the header everything is as before (Chinese, also what
the AI and the CLI read). The fallback consent version stays the one of the Chinese points (nobody has to re-consent).

Separate test database BODY_TEST_DB_NAME_I18N_AI (default body_test_i18n_ai), dropped and recreated every run; skipped without
Postgres. No paid API: every API kind is FakeAdapter, DeepSeek's balance endpoint is monkeypatched, the keys are temp files.
Users: O (owner, body butler link), F (member, consented, no AI of their own -> fallback), A (member with own providers).
"""
import json
import os
import re
from datetime import datetime, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from body_engine import api, auth
from body_engine.ai import adapters, catalog, crypto, fallback
from body_engine.ai.adapters.fake import FakeAdapter
from body_engine.config import Settings
from body_engine.db import migrate

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_I18N_AI", "body_test_i18n_ai")
CJK = re.compile(r"[　-〿一-鿿＀-￯]")
EN = {"X-Body-Lang": "en"}


def cjk(value) -> bool:
    return bool(CJK.search(json.dumps(value, ensure_ascii=False, default=str)))


@pytest.fixture(scope="module")
def dsn():
    if TEST_DB in ("body", "body_pr1", "body_test_accept", ""):
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
def world(dsn, tmp_path_factory):
    d = tmp_path_factory.mktemp("i18n_ai")
    mp = pytest.MonkeyPatch()
    mp.setenv("BODY_AI_MASTER_KEY_FILE", str(d / "ai_master_key"))
    crypto.init_master_key()
    (d / "deepseek_api_key").write_text("test-fallback-key-not-real-0000\n")
    mp.setenv("BODY_DEEPSEEK_API_KEY_FILE", str(d / "deepseek_api_key"))
    mp.setenv("BODY_ASSISTANT_URL", "https://claude.ai/code/session_test")
    mp.delenv("BODY_ASR_URL", raising=False)
    mp.setenv("BODY_IMPORT_DIR", str(d / "imports"))
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {}
        for n, role in (("O", "owner"), ("F", "member"), ("A", "member")):
            ids[n] = conn.execute("INSERT INTO users (display_name, role, consent_at, timezone) VALUES (%s,%s,%s,%s) RETURNING id",
                                  (n, role, datetime.now(timezone.utc), "Australia/Sydney")).fetchone()["id"]
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        conn.execute("UPDATE ai_fallback_state SET enabled = true")
    mp.setenv("BODY_ASSISTANT_USER_ID", str(ids["O"]))
    yield ids, tok
    mp.undo()


@pytest.fixture(scope="module")
def client(dsn, world):
    mp = pytest.MonkeyPatch()
    mp.setattr(api, "settings", Settings(db_name=TEST_DB))
    with TestClient(api.app, base_url="https://testserver") as c:
        yield c
    mp.undo()


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    for kind in ("anthropic_api", "openai_api", "gemini_api", "openai_compatible", "system_fallback"):
        monkeypatch.setitem(adapters.ADAPTERS, kind, lambda: FakeAdapter)
    FakeAdapter.script, FakeAdapter.calls = [], []
    monkeypatch.setattr(fallback, "fetch_balance", lambda key, timeout=8: 50.0)


def H(token, en=False):
    return {"Authorization": f"Bearer {token}", **(EN if en else {})}


def sse(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def db(dsn):
    return psycopg.connect(dsn, row_factory=dict_row)


# ---------------------------------------------------------------- AI settings
def test_catalog_mcp_and_fallback_view(client, world):
    ids, tok = world
    en = client.get("/v1/users/me/ai-providers", headers=H(tok["F"], True)).json()
    zh = client.get("/v1/users/me/ai-providers", headers=H(tok["F"])).json()
    for k in en["kinds"]:
        assert not cjk({f: k.get(f) for f in ("label", "note", "key_hint")}), k
    assert {k["kind"]: k["label"] for k in zh["kinds"]}["claude_plan_mcp"] == catalog.KINDS["claude_plan_mcp"]["label"]
    assert not cjk(en["mcp"]["prompt"]) and not cjk(en["mcp"]["clients"]) and not cjk(en["mcp"]["keychain"])
    assert cjk(zh["mcp"]["prompt"])
    f_en, f_zh = en["fallback"], zh["fallback"]
    assert not cjk(f_en["display_name"]) and not cjk(f_en["points"]) and not cjk(f_en["message"])
    assert f_en["points_lang"] == "en" and f_en["points_translated"] is True
    assert f_zh["points_lang"] == "zh" and f_zh["points_translated"] is False
    assert len(f_en["points"]) == len(f_zh["points"]) == len(fallback.CONSENT_POINTS)
    assert f_zh["points"] == [p.format(quota=f_zh["quota_cny"]) for p in fallback.CONSENT_POINTS]
    assert f_zh["display_name"].startswith("托底 AI") and f_zh["message"] == "第一次用托底 AI 要先同意"
    # The consent version is the Chinese text's, in both languages; agreeing on the English page records that version
    assert f_en["consent_version"] == f_zh["consent_version"] == fallback.CONSENT_VERSION
    assert fallback.CONSENT_VERSION == "2026-10-22"


def test_consent_in_english_records_the_same_version(client, world, dsn):
    ids, tok = world
    bad = client.post("/v1/users/me/ai-fallback/consent", headers=H(tok["F"], True), json={"version": "old", "agree": True})
    assert bad.status_code == 409 and not cjk(bad.json()["detail"])
    v = client.get("/v1/users/me/ai-fallback", headers=H(tok["F"], True)).json()["consent_version"]
    r = client.post("/v1/users/me/ai-fallback/consent", headers=H(tok["F"], True), json={"version": v, "agree": True})
    assert r.status_code == 200 and r.json()["consent"] is True and not cjk(r.json()["points"])
    with db(dsn) as c:
        assert c.execute("SELECT version FROM ai_fallback_consents WHERE user_id = %s", (ids["F"],)).fetchone()["version"] \
            == fallback.CONSENT_VERSION


def test_default_provider_name_follows_language_but_stays_stored(client, world, dsn):
    ids, tok = world
    r = client.post("/v1/users/me/ai-providers", headers={**H(tok["A"], True), "X-Body-Web": "1"},
                    json={"kind": "claude_plan_mcp"})
    assert r.status_code == 201 and r.json()["display_name"] == "Claude subscription (Pro / Max)"
    with db(dsn) as c:  # stored in Chinese (the catalog label), as before
        assert c.execute("SELECT display_name FROM ai_providers WHERE user_id = %s", (ids["A"],)).fetchone()["display_name"] \
            == catalog.KINDS["claude_plan_mcp"]["label"]
    custom = client.post("/v1/users/me/ai-providers", headers=H(tok["A"]),
                         json={"kind": "anthropic_api", "display_name": "我的 Claude", "api_key": "sk-ant-test-0000000000"})
    assert custom.status_code == 201
    en = client.get("/v1/users/me/ai-providers", headers=H(tok["A"], True)).json()["providers"]
    names = {p["kind"]: (p["display_name"], p["label"]) for p in en}
    assert names["claude_plan_mcp"] == ("Claude subscription (Pro / Max)", "Claude subscription (Pro / Max)")
    assert names["anthropic_api"] == ("我的 Claude", "Anthropic API (Claude)")   # the user's own name is data
    zh = client.get("/v1/users/me/ai-providers", headers=H(tok["A"])).json()["providers"]
    assert {p["kind"]: p["display_name"] for p in zh}["claude_plan_mcp"] == catalog.KINDS["claude_plan_mcp"]["label"]
    # Jarvis options: labels and notes (except the user's own name)
    opts = client.get("/v1/users/me/ai-choice", headers=H(tok["A"], True)).json()["options"]
    assert not cjk([(o["label"], o["note"]) for o in opts if o["label"] != "我的 Claude"])
    zh_opts = client.get("/v1/users/me/ai-choice", headers=H(tok["A"])).json()["options"]
    assert any(o["note"] == "订阅" for o in zh_opts)
    owner = client.get("/v1/users/me/ai-choice", headers=H(tok["O"], True)).json()
    assert {o["key"]: o["label"] for o in owner["options"]}["butler"] == "Body butler"
    assert not cjk(owner["ai"].get("display_name"))


def test_provider_validation_and_choice_errors(client, world):
    ids, tok = world
    body = {"kind": "openai_compatible", "model": "x", "base_url": "http://10.0.0.5/v1", "api_key": "k" * 20}
    en = client.post("/v1/users/me/ai-providers", headers=H(tok["A"], True), json=body)
    zh = client.post("/v1/users/me/ai-providers", headers=H(tok["A"]), json=body)
    assert en.status_code == zh.status_code == 422
    assert not cjk(en.json()["detail"]) and cjk(zh.json()["detail"])
    body["base_url"] = "https://10.0.0.5/v1"   # https to a private IP: the private-IP rule still applies in English
    assert "private" in client.post("/v1/users/me/ai-providers", headers=H(tok["A"], True), json=body).json()["detail"]
    r = client.put("/v1/users/me/ai-choice", headers=H(tok["A"], True), json={"key": "butler"})
    assert r.status_code == 422 and not cjk(r.json()["detail"])
    r = client.post("/v1/users/me/ai-tokens", headers=H(tok["A"], True), json={"client": "Bad Name"})
    assert r.status_code == 422 and not cjk(r.json()["detail"])


def test_web_me_ai_state(client, world):
    ids, tok = world
    ai = client.get("/v1/web/me", headers=H(tok["F"], True)).json()["ai"]
    assert ai["mode"] == "fallback" and not cjk([ai["display_name"], ai.get("message")])
    assert client.get("/v1/web/me", headers=H(tok["F"])).json()["ai"]["display_name"].startswith("托底 AI")


# ---------------------------------------------------------------- chat
def test_chat_errors_and_stream_texts(client, world):
    ids, tok = world
    r = client.post("/v1/assistant/chat", headers=H(tok["F"], True), json={"message": ""})
    assert r.status_code == 422 and not cjk(r.json()["detail"]["message"])
    assert cjk(client.post("/v1/assistant/chat", headers=H(tok["F"]), json={"message": ""}).json()["detail"]["message"])
    # Subscription provider as default: the in-page chat explains where to talk to Jarvis
    r = client.put("/v1/users/me/ai-choice", headers=H(tok["A"]), json={"key": "p:" + str(
        next(p["id"] for p in client.get("/v1/users/me/ai-providers", headers=H(tok["A"])).json()["providers"]
             if p["kind"] == "claude_plan_mcp"))})
    assert r.status_code == 200
    r = client.post("/v1/assistant/chat", headers=H(tok["A"], True), json={"message": "hi"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "subscription" and not cjk(r.json()["detail"]["message"])
    # Fallback chat (F consented above): the notice from inside the stream follows the request language too
    FakeAdapter.script = [{"text": "ok", "stop": "max_tokens"}]
    ev = sse(client.post("/v1/assistant/chat", headers=H(tok["F"], True), json={"message": "hi"}).text)
    conv = next(d for e, d in ev if e == "conversation")
    notice = next(d for e, d in ev if e == "notice")
    assert not cjk(conv["display_name"]) and not cjk(notice["message"])
    FakeAdapter.script = [{"text": "ok", "stop": "max_tokens"}]
    ev = sse(client.post("/v1/assistant/chat", headers=H(tok["F"]), json={"message": "hi"}).text)
    assert next(d for e, d in ev if e == "notice")["message"] == "回答太长被截断了"


def test_confirm_card_summary(client, world):
    ids, tok = world
    pid = next(p["id"] for p in client.get("/v1/users/me/ai-providers", headers=H(tok["A"])).json()["providers"]
               if p["kind"] == "anthropic_api")
    assert client.put("/v1/users/me/ai-choice", headers=H(tok["A"]), json={"key": f"p:{pid}"}).status_code == 200
    call = {"id": "d1", "name": "delete_observation", "args": {"id": 1}}
    FakeAdapter.script = [{"text": "", "tool_calls": [call]}]
    ev = sse(client.post("/v1/assistant/chat", headers=H(tok["A"], True), json={"message": "delete it"}).text)
    calls = next(d for e, d in ev if e == "confirm")["calls"]
    assert calls[0]["summary"].startswith("Delete an observation note: ") and not cjk(calls[0]["summary"])
    FakeAdapter.script = [{"text": "", "tool_calls": [{**call, "id": "d2"}]}]
    ev = sse(client.post("/v1/assistant/chat", headers=H(tok["A"]), json={"message": "删掉"}).text)
    assert next(d for e, d in ev if e == "confirm")["calls"][0]["summary"].startswith("删除一条观察笔记")


def test_asr_messages(client, world):
    ids, tok = world
    head = {"Content-Type": "audio/wav"}
    r = client.post("/v1/assistant/transcribe", headers={**H(tok["F"], True), **head}, content=b"RIFF")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "asr_off" and not cjk(r.json()["detail"]["message"])
    r = client.post("/v1/assistant/transcribe", headers={**H(tok["F"]), **head}, content=b"RIFF")
    assert cjk(r.json()["detail"]["message"])


# ---------------------------------------------------------------- history import
def test_history_job_messages(client, world, dsn):
    ids, tok = world
    uid = ids["F"]

    def job(status, message, result=None):
        with db(dsn) as c:
            c.execute("DELETE FROM history_imports WHERE user_id = %s", (uid,))
            c.execute("""INSERT INTO history_imports (user_id, status, filename, size_bytes, message, result)
                         VALUES (%s, %s, 'export.zip', 10, %s, %s)""", (uid, status, message, Jsonb(result or {})))
        en = client.get("/v1/users/me/history-import", headers=H(tok["F"], True)).json()["job"]
        zh = client.get("/v1/users/me/history-import", headers=H(tok["F"])).json()["job"]
        assert zh["message"] == message
        return en["message"]

    assert job("cancelled", "已取消") == "Cancelled"
    assert job("failed", "压缩包损坏（可能没传完整）；请重新导出再上传").startswith("The zip is damaged")
    m = job("failed", "export.xml 解析失败（文件不完整或损坏：not well-formed (invalid token): line 3）；请重新导出再上传")
    assert m.startswith("Could not read export.xml") and "line 3" in m
    res = {"first_day": "2026-01-01", "last_day": "2026-03-01", "days": 60, "nights": 55, "workouts": 12, "ecg": 2,
           "scores_note": "评分今晚自动重算"}
    m = job("done", "导入完成：2026-01-01 到 2026-03-01，60 天每日数据、55 晚睡眠、12 次训练、2 条心电图。评分今晚自动重算", res)
    assert not cjk(m) and "2026-03-01" in m and "60" in m
    assert job("done", "导入完成，但导出里没有可用的每日数据", {"days": 0}) == "Import done, but the export has no usable daily data"
    with db(dsn) as c:
        c.execute("DELETE FROM history_imports WHERE user_id = %s", (uid,))
    r = client.post("/v1/users/me/history-import", headers=H(tok["F"], True), json={"filename": "x.txt", "size": 10})
    assert r.status_code == 400 and not cjk(r.json()["detail"])
    assert cjk(client.post("/v1/users/me/history-import", headers=H(tok["F"]), json={"filename": "x.txt", "size": 10}).json()["detail"])

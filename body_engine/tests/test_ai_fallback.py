"""Fallback AI (ai/fallback.py): users without their own AI chat with the admin's DeepSeek key, opt-in consent, CNY quota per person per month,
worst-case pre-check, rate limits, balance pause, own-data-only tools, admin status.

Separate test database BODY_TEST_DB_NAME_FALLBACK (default body_test_ai_fallback), dropped and recreated every run; skipped without Postgres.
No paid API: system_fallback (and every other kind) is FakeAdapter; DeepSeek's balance endpoint is monkeypatched. The key is a temp file.

Users: F1, F2 (members, consented to the main form, no AI of their own), A (member with their own anthropic_api provider),
N (member who has not accepted the main consent form), O (owner with the body butler link). F2 shares everything with F1.
"""
import json
import os
from datetime import datetime, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from body_engine import api, auth
from body_engine.ai import adapters, crypto, fallback, providers
from body_engine.ai import fallback_params as fp
from body_engine.ai.adapters.fake import FakeAdapter
from body_engine.ai.adapters.openai_api import DeepSeekFallbackAdapter, OpenAICompatAdapter
from body_engine.config import Settings
from body_engine.db import migrate

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_FALLBACK", "body_test_ai_fallback")
DS_KEY = "test-fallback-key-not-real-0000"
ALL_SCOPES = ["profile", "metrics", "sleep", "scores", "events", "memory", "journal", "nutrition", "training",
              "weight", "supplements", "labs", "exercises", "reminders"]


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
    d = tmp_path_factory.mktemp("fb")
    mp = pytest.MonkeyPatch()
    mp.setenv("BODY_AI_MASTER_KEY_FILE", str(d / "ai_master_key"))
    crypto.init_master_key()
    (d / "deepseek_api_key").write_text(DS_KEY + "\n")
    mp.setenv("BODY_DEEPSEEK_API_KEY_FILE", str(d / "deepseek_api_key"))
    mp.setenv("BODY_ASSISTANT_URL", "https://claude.ai/code/session_test")
    yield d
    mp.undo()


@pytest.fixture(scope="module")
def world(dsn, keys):
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        ids = {}
        for n, role, consented in (("O", "owner", False), ("F1", "member", True), ("F2", "member", True),
                                   ("A", "member", True), ("N", "member", False)):
            ids[n] = conn.execute("INSERT INTO users (display_name, role, consent_at, timezone) VALUES (%s,%s,%s,%s) RETURNING id",
                                  (n, role, datetime.now(timezone.utc) if consented else None,
                                   "Australia/Sydney")).fetchone()["id"]
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        conn.execute("INSERT INTO shares (owner_id, viewer_id, scopes) VALUES (%s, %s, %s)", (ids["F2"], ids["F1"], ALL_SCOPES))
        conn.execute("INSERT INTO assistant_observations (user_id, text, source) VALUES (%s, 'F1 喜欢早上练', 'user')", (ids["F1"],))
    os.environ["BODY_ASSISTANT_USER_ID"] = str(ids["O"])
    return ids, tok


@pytest.fixture(scope="module")
def client(dsn, world):
    mp = pytest.MonkeyPatch()
    mp.setattr(api, "settings", Settings(db_name=TEST_DB))
    with TestClient(api.app, base_url="https://testserver") as c:
        yield c
    mp.undo()
    os.environ.pop("BODY_ASSISTANT_USER_ID", None)


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    for kind in ("anthropic_api", "openai_api", "gemini_api", "openai_compatible", "system_fallback"):
        monkeypatch.setitem(adapters.ADAPTERS, kind, lambda: FakeAdapter)
    FakeAdapter.script, FakeAdapter.calls = [], []
    monkeypatch.setattr(fallback, "fetch_balance", lambda key, timeout=8: 50.0)


def H(token):
    return {"Authorization": f"Bearer {token}"}


def sse(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def db(dsn):
    return psycopg.connect(dsn, row_factory=dict_row)


def reset(dsn, uid):
    with db(dsn) as c:
        c.execute("DELETE FROM ai_usage WHERE user_id = %s", (uid,))
        c.execute("DELETE FROM ai_fallback_quota WHERE user_id = %s", (uid,))
        c.execute("UPDATE ai_fallback_state SET enabled = true, balance_cny = NULL, balance_checked_at = NULL, balance_error = NULL")


def consent(client, tok):
    v = client.get("/v1/users/me/ai-fallback", headers=H(tok)).json()["consent_version"]
    r = client.post("/v1/users/me/ai-fallback/consent", headers=H(tok), json={"version": v, "agree": True})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- pure
def test_tools_are_own_data_subset():
    names = [t.name for t in fallback.tools()]
    assert set(names) == set(fallback.TOOL_NAMES), set(fallback.TOOL_NAMES) - set(names)
    assert not {"get_ecg_list", "get_cardio", "create_recipe"} & set(names)
    for t in fallback.tools():
        assert "user" not in t.input_schema.get("properties", {}) and "user" not in t.input_schema.get("required", [])
    size = sum(len(t.description) + len(json.dumps(t.input_schema, ensure_ascii=False)) for t in fallback.tools())
    assert size < 25000, size  # the full set is ~47k chars


def test_cost_and_estimate():
    u = {"input_tokens": 1_000_000, "cache_read_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert fallback.cost_cny(u) == pytest.approx(fp.PRICE_CACHE_MISS_CNY + fp.PRICE_CACHE_HIT_CNY + fp.PRICE_OUTPUT_CNY)
    est = fallback.estimate_call_cny("x" * 2000, [{"content": [{"type": "text", "text": "y" * 2000}]}], [])
    assert est == pytest.approx((2000 + 2000 + 36) / fp.CHARS_PER_TOKEN * 2e-6 + fp.MAX_OUTPUT_TOKENS * 8e-6, rel=0.05)
    # An image counts as IMAGE_TOKENS, however long its base64 is
    big = {"type": "image", "media_type": "image/jpeg", "data": "A" * 2_000_000}
    with_img = fallback.estimate_call_cny("x", [{"content": [{"type": "text", "text": "hi"}, big]}], [])
    no_img = fallback.estimate_call_cny("x", [{"content": [{"type": "text", "text": "hi"}]}], [])
    assert with_img - no_img == pytest.approx(fp.IMAGE_TOKENS * fp.PRICE_CACHE_MISS_CNY / 1e6, rel=0.01)


def test_compat_adapter_passes_reasoning_back_and_deepseek_disables_thinking():
    a = OpenAICompatAdapter("deepseek-flash", "k", "https://api.deepseek.com")
    hist = [{"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "assistant", "content": [{"type": "tool_call", "id": "c1", "name": "get_scores", "args": {}}],
             "native": {"reasoning_content": "想一想"}, "native_key": a.key},
            {"role": "tool", "content": [{"type": "tool_result", "id": "c1", "name": "get_scores", "content": "{}"}]}]
    msgs = a._messages("sys", hist)
    assert msgs[2]["reasoning_content"] == "想一想" and msgs[2]["tool_calls"][0]["id"] == "c1"
    other = OpenAICompatAdapter("qwen-plus", "k", "https://x")  # different kind:model -> not passed back
    assert "reasoning_content" not in other._messages("sys", hist)[2]
    d = DeepSeekFallbackAdapter(fp.MODEL, "k")
    assert d.extra_body == {"thinking": {"type": "disabled"}} and d.max_tokens == fp.MAX_OUTPUT_TOKENS
    assert d.base_url == fp.BASE_URL and d.kind == "system_fallback"


# ---------------------------------------------------------------- status / consent
def test_status_modes(client, world):
    ids, tok = world
    me = client.get("/v1/web/me", headers=H(tok["F1"])).json()["ai"]
    assert me["mode"] == "fallback" and me["consent"] is False and me["reason"] == "fallback_consent"
    assert me["quota_cny"] == fp.MONTHLY_QUOTA_CNY and me["spent_cny"] == 0 and me["reset_on"].endswith("-01")
    assert client.get("/v1/web/me", headers=H(tok["O"])).json()["ai"]["mode"] == "butler"  # owner keeps the butler by default
    assert client.get("/v1/web/me", headers=H(tok["N"])).json()["ai"]["reason"] == "needs_consent"
    view = client.get("/v1/users/me/ai-providers", headers=H(tok["F1"])).json()["fallback"]
    assert view["used"] is True and len(view["points"]) >= 5 and "balance_cny" not in view
    owner_view = client.get("/v1/users/me/ai-providers", headers=H(tok["O"])).json()["fallback"]
    assert "balance_cny" in owner_view and owner_view["used"] is False
    assert client.get(f"/v1/users/{ids['F2']}/ai-fallback", headers=H(tok["F1"])).status_code == 403  # shares do not cover AI


def test_no_key_means_no_fallback(client, world, monkeypatch, keys):
    ids, tok = world
    monkeypatch.setenv("BODY_DEEPSEEK_API_KEY_FILE", str(keys / "missing"))
    assert client.get("/v1/web/me", headers=H(tok["F1"])).json()["ai"]["mode"] == "none"
    assert client.get("/v1/users/me/ai-providers", headers=H(tok["F1"])).json()["fallback"] is None


def test_chat_needs_fallback_consent_then_works(client, world, dsn):
    ids, tok = world
    reset(dsn, ids["F1"])
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "今天怎么样"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "fallback_consent"
    assert not FakeAdapter.calls
    bad = client.post("/v1/users/me/ai-fallback/consent", headers=H(tok["F1"]), json={"version": "old", "agree": True})
    assert bad.status_code == 409
    consent(client, tok["F1"])
    FakeAdapter.script = [{"text": "恢复|不错", "usage": {"input_tokens": 1000, "cache_read_tokens": 9000, "output_tokens": 200}}]
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "今天怎么样"})
    assert r.status_code == 200, r.text
    evs = sse(r.text)
    conv = next(d for e, d in evs if e == "conversation")
    assert conv["provider"] == "system_fallback" and conv["quota"]["quota_cny"] == fp.MONTHLY_QUOTA_CNY
    call = FakeAdapter.calls[0]
    assert call["api_key"] == DS_KEY and call["model"] == fp.MODEL and call["base_url"] == fp.BASE_URL
    assert call["system"].startswith("## 根规则 v1") and "# 身体管家（精简版）" in call["system"]   # root rules first
    assert "F1 喜欢早上练" in call["system"]
    assert set(call["tools"]) == set(fallback.TOOL_NAMES)
    usage = next(d for e, d in evs if e == "usage")
    expect = (1000 * 2 + 9000 * 0.04 + 200 * 8) / 1e6
    assert usage["currency"] == "CNY" and usage["cost_cny"] == pytest.approx(expect, abs=1e-4)
    assert usage["remaining_cny"] == pytest.approx(fp.MONTHLY_QUOTA_CNY - expect, abs=1e-4)
    with db(dsn) as c:
        row = c.execute("SELECT * FROM ai_usage WHERE user_id = %s", (ids["F1"],)).fetchone()
        assert row["kind"] == "system_fallback" and row["provider_id"] is None
        assert float(row["cost_usd"]) == 0 and float(row["cost_cny"]) == pytest.approx(expect, abs=1e-6)
        assert row["cache_read_tokens"] == 9000 and row["input_tokens"] == 1000
        assert c.execute("SELECT count(*) AS n FROM ai_fallback_consents WHERE user_id = %s",
                         (ids["F1"],)).fetchone()["n"] == 1
    assert providers.month_cost(psycopg.connect(dsn, row_factory=dict_row), ids["F1"]) == 0  # not the user's own budget
    me = client.get("/v1/web/me", headers=H(tok["F1"])).json()["ai"]
    assert me["available"] and me["spent_cny"] == pytest.approx(expect, abs=1e-4)


def test_tools_read_only_own_data(client, world, dsn):
    ids, tok = world
    reset(dsn, ids["F1"])
    FakeAdapter.script = [
        {"tool_calls": [{"id": "t1", "name": "get_profile", "args": {"user": str(ids["F2"])}},
                        {"id": "t2", "name": "get_ecg_list", "args": {}}]},
        {"text": "好"}]
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "看看 F2"})
    assert r.status_code == 200, r.text
    results = FakeAdapter.calls[1]["history"][-1]["content"]
    prof = next(b for b in results if b["id"] == "t1")
    assert '"F1"' in prof["content"] and '"F2"' not in prof["content"]  # forced to me even though F2 shares with F1
    assert next(b for b in results if b["id"] == "t2")["is_error"]  # not in the fallback tool set
    with db(dsn) as c:
        a = c.execute("SELECT channel, args, model FROM assistant_audit WHERE user_id = %s AND tool = 'get_profile' "
                      "ORDER BY id DESC LIMIT 1", (ids["F1"],)).fetchone()
        assert a["channel"] == "api:system_fallback" and a["args"]["user"] == "me" and a["model"] == fp.MODEL


def test_write_needs_confirmation(client, world, dsn, monkeypatch):
    ids, tok = world
    from body_engine.ai import chat as chat_mod
    monkeypatch.setattr(chat_mod, "ALWAYS_CONFIRM", chat_mod.ALWAYS_CONFIRM | {"add_event"})   # exercise the confirm flow with a simple tool
    reset(dsn, ids["F1"])
    FakeAdapter.script = [{"tool_calls": [{"id": "w1", "name": "add_event", "args": {
        "date": "2026-10-18", "category": "pain", "title": "膝盖", "severity": 2, "notes": None, "end_date": None}}]}]
    evs = sse(client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "膝盖疼"}).text)
    pending = next(d for e, d in evs if e == "confirm")["calls"]
    assert pending[0]["tool"] == "add_event"
    FakeAdapter.script = [{"text": "没记"}]
    r = client.post("/v1/assistant/chat/confirm", headers=H(tok["F1"]), json={"call_id": pending[0]["call_id"], "approve": False})
    assert r.status_code == 200 and ("tool", {"name": "add_event", "status": "rejected", "call_id": pending[0]["call_id"]}) in sse(r.text)


def test_quota_used_up_and_admin_override(client, world, dsn):
    ids, tok = world
    reset(dsn, ids["F1"])
    with db(dsn) as c:
        c.execute("INSERT INTO ai_usage (user_id, kind, model, cost_cny) VALUES (%s, 'system_fallback', %s, %s)",
                  (ids["F1"], fp.MODEL, fp.MONTHLY_QUOTA_CNY))
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"})
    d = r.json()["detail"]
    assert r.status_code == 402 and d["code"] == "quota" and "本月额度已用完" in d["message"] and d["reset_on"].endswith("-01")
    me = client.get("/v1/web/me", headers=H(tok["F1"])).json()["ai"]
    assert me["available"] is False and me["reason"] == "quota" and me["remaining_cny"] == 0
    with db(dsn) as c:
        fallback.set_quota(c, ids["F1"], 5)
    assert client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"}).status_code == 200
    with db(dsn) as c:
        fallback.set_quota(c, ids["F1"], 0)
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "fallback_off"
    with db(dsn) as c:
        fallback.set_quota(c, ids["F1"], None)
        assert fallback.quota_cny(c, ids["F1"]) == fp.MONTHLY_QUOTA_CNY


def test_worst_case_estimate_blocks_last_call(client, world, dsn):
    ids, tok = world
    reset(dsn, ids["F1"])
    with db(dsn) as c:  # ¥0.005 left: less than one worst-case call (max output alone is ¥0.016)
        c.execute("INSERT INTO ai_usage (user_id, kind, model, cost_cny) VALUES (%s, 'system_fallback', %s, %s)",
                  (ids["F1"], fp.MODEL, fp.MONTHLY_QUOTA_CNY - 0.005))
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"})
    assert r.status_code == 200  # the pre-checks pass (quota not used up), the loop refuses before calling the model
    err = next(d for e, d in sse(r.text) if e == "error")
    assert err["code"] == "quota" and "不够再问一轮" in err["message"] and not FakeAdapter.calls


def test_rate_limits(client, world, dsn):
    ids, tok = world
    reset(dsn, ids["F1"])
    with db(dsn) as c:
        for _ in range(fp.RATE_PER_MINUTE):
            c.execute("INSERT INTO ai_usage (user_id, kind, model, cost_cny) VALUES (%s, 'system_fallback', %s, 0)",
                      (ids["F1"], fp.MODEL))
    err = next(d for e, d in sse(client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"}).text)
               if e == "error")
    assert err["code"] == "rate_limit" and "一分钟" in err["message"]
    with db(dsn) as c:
        c.execute("UPDATE ai_usage SET at = now() - interval '5 minutes' WHERE user_id = %s", (ids["F1"],))
        n = fp.RATE_PER_DAY - fp.RATE_PER_MINUTE
        c.execute("""INSERT INTO ai_usage (user_id, kind, model, cost_cny, at)
                     SELECT %s, 'system_fallback', %s, 0, now() - interval '5 minutes' FROM generate_series(1, %s)""",
                  (ids["F1"], fp.MODEL, n))
    err = next(d for e, d in sse(client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"}).text)
               if e == "error")
    # The day starts at local midnight (Sydney): if that is less than 5 minutes ago the rows fall on yesterday
    assert err["code"] == "rate_limit"


def test_balance_pause_and_admin_switch(client, world, dsn, monkeypatch):
    ids, tok = world
    reset(dsn, ids["F1"])
    monkeypatch.setattr(fallback, "fetch_balance", lambda key, timeout=8: 1.5)
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "fallback_paused" and "余额" in r.json()["detail"]["message"]
    with db(dsn) as c:
        s = fallback.admin_status(c)
        assert s["balance_cny"] == 1.5 and s["available"] is False
        f1 = next(u for u in s["users"] if u["user_id"] == ids["F1"])
        assert f1["consent"] and f1["uses"] == "fallback"
    monkeypatch.setattr(fallback, "fetch_balance", lambda key, timeout=8: 30.0)
    with db(dsn) as c:
        fallback.refresh_balance(c, force=True)
        fallback.set_enabled(c, False)
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"})
    assert r.status_code == 409 and "管理员暂停" in r.json()["detail"]["message"]
    with db(dsn) as c:
        fallback.set_enabled(c, True)
        s = fallback.admin_status(c)
        assert s["available"] and s["balance_cny"] == 30.0 and s["eligible_users"] >= 2  # O excluded? no: O has no own AI
    # Balance endpoint failing (network) does not pause: DeepSeek itself refuses if the balance is really gone
    def boom(key, timeout=8):
        raise OSError("down")
    monkeypatch.setattr(fallback, "fetch_balance", boom)
    with db(dsn) as c:
        st = fallback.refresh_balance(c, force=True)
        assert st["balance_error"] == "OSError" and float(st["balance_cny"]) == 30.0
    assert client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"}).status_code == 200


def test_own_provider_wins(client, world, dsn):
    ids, tok = world
    r = client.post("/v1/users/me/ai-providers", headers=H(tok["A"]),
                    json={"kind": "anthropic_api", "api_key": "sk-ant-test-own-key-AAAA", "monthly_budget_usd": 5})
    assert r.status_code == 201
    assert client.get("/v1/web/me", headers=H(tok["A"])).json()["ai"]["mode"] == "api"
    assert client.post("/v1/assistant/chat", headers=H(tok["A"]), json={"message": "hi"}).status_code == 200
    assert FakeAdapter.calls[0]["api_key"] == "sk-ant-test-own-key-AAAA"
    view = client.get("/v1/users/me/ai-providers", headers=H(tok["A"])).json()["fallback"]
    assert view["used"] is False


def test_images_and_video_frames_then_revoke(client, world, dsn):
    ids, tok = world
    reset(dsn, ids["F1"])
    img = {"media_type": "image/jpeg", "data": "/9j/4AAQSkZJRg=="}
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]),
                    json={"message": "［视频 8 帧，约 20 秒］记一下", "images": [img] * 8})
    assert r.status_code == 200, r.text
    user_msg = FakeAdapter.calls[0]["history"][-1]["content"]
    assert sum(b["type"] == "image" for b in user_msg) == 8 and "［视频 8 帧" in user_msg[0]["text"]
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "太多", "images": [img] * 9})
    assert r.status_code == 422
    assert {"add_lab_results", "log_metric_snapshot", "add_supplement"} <= set(FakeAdapter.calls[0]["tools"])
    assert client.delete("/v1/users/me/ai-fallback/consent", headers=H(tok["F1"])).status_code == 204
    r = client.post("/v1/assistant/chat", headers=H(tok["F1"]), json={"message": "hi"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "fallback_consent"
    consent(client, tok["F1"])  # consenting again adds a new row
    with db(dsn) as c:
        assert c.execute("SELECT count(*) AS n FROM ai_fallback_consents WHERE user_id = %s", (ids["F1"],)).fetchone()["n"] == 2


def test_old_consent_version_must_reconfirm(client, world, dsn):
    """Consents to an older wording (before photos / voice) do not count: the sheet is shown again."""
    ids, tok = world
    with db(dsn) as c:
        c.execute("UPDATE ai_fallback_consents SET version = 'old' WHERE user_id = %s", (ids["F2"],))
        c.execute("INSERT INTO ai_fallback_consents (user_id, version) VALUES (%s, '2026-10-18')", (ids["F2"],))
    me = client.get("/v1/web/me", headers=H(tok["F2"])).json()["ai"]
    assert me["consent"] is False and me["consent_version"] == fallback.CONSENT_VERSION
    points = client.get("/v1/users/me/ai-fallback", headers=H(tok["F2"])).json()["points"]
    assert any("照片" in p for p in points) and any("语音" in p and "删除" in p for p in points)


# ---------------------------------------------------------------- voice -> text (ai/asr.py)
def make_wav(seconds: float, rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    import io
    import math
    import struct
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        n = int(seconds * rate)
        if width == 2:
            w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 10))) * channels for i in range(n)))
        else:
            w.writeframes(bytes(n * channels * width))
    return buf.getvalue()


@pytest.fixture()
def asr_on(monkeypatch):
    from body_engine.ai import asr
    monkeypatch.setenv("BODY_ASR_URL", "http://asr.test:8000")
    sent = []

    def fake_post(data):
        sent.append(len(data))
        return {"text": "今天中午吃了牛肉面", "seconds": 2.0, "ms": 120}
    monkeypatch.setattr(asr, "_post", fake_post)
    return sent


def W(tok):
    return {**H(tok), "Content-Type": "audio/wav"}


def test_asr_off_by_default(client, world):
    ids, tok = world
    assert client.get("/v1/web/me", headers=H(tok["F1"])).json()["ai"]["asr"] == {"enabled": False}
    r = client.post("/v1/assistant/transcribe", headers=W(tok["F1"]), content=make_wav(2))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "asr_off"


def test_asr_transcribe_and_limits(client, world, dsn, asr_on):
    from body_engine.ai import asr
    ids, tok = world
    with db(dsn) as c:
        c.execute("DELETE FROM asr_usage")
    me = client.get("/v1/web/me", headers=H(tok["F2"])).json()["ai"]["asr"]
    assert me["enabled"] and me["max_seconds"] == asr.MAX_SECONDS and me["used_today"] == 0
    r = client.post("/v1/assistant/transcribe", headers=W(tok["F2"]), content=make_wav(2))
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "今天中午吃了牛肉面", "seconds": 2.0, "used_today": 1, "per_day": asr.ASR_PER_DAY}
    for bad, code in ((make_wav(0.1), "too_short"), (make_wav(61.5), "too_long"), (make_wav(2, channels=2), "bad_audio"),
                      (make_wav(2, width=1), "bad_audio"), (b"not a wav file", "bad_audio")):
        r = client.post("/v1/assistant/transcribe", headers=W(tok["F2"]), content=bad)
        assert r.status_code in (413, 422) and r.json()["detail"]["code"] == code, (code, r.text)
    assert len(asr_on) == 1  # invalid audio never reaches the recogniser
    with db(dsn) as c:
        row = c.execute("SELECT * FROM asr_usage WHERE user_id = %s", (ids["F2"],)).fetchone()
        assert row["ok"] and row["audio_ms"] == 2000 and set(row) == {"id", "user_id", "at", "audio_ms", "proc_ms", "ok", "error"}
        c.execute("""INSERT INTO asr_usage (user_id, audio_ms, proc_ms, ok, at)
                     SELECT %s, 1000, 100, true, now() - interval '5 minutes' FROM generate_series(1, %s)""",
                  (ids["F2"], asr.ASR_PER_DAY))
    r = client.post("/v1/assistant/transcribe", headers=W(tok["F2"]), content=make_wav(2))
    assert r.status_code == 429 and "每天" in r.json()["detail"]["message"]
    with db(dsn) as c:
        c.execute("DELETE FROM asr_usage WHERE user_id = %s", (ids["F2"],))
        c.execute("INSERT INTO asr_usage (user_id, audio_ms, proc_ms, ok) SELECT %s, 1000, 100, true FROM generate_series(1, %s)",
                  (ids["F2"], asr.ASR_PER_MINUTE))
    r = client.post("/v1/assistant/transcribe", headers=W(tok["F2"]), content=make_wav(2))
    assert r.status_code == 429 and "太快" in r.json()["detail"]["message"]


def test_asr_service_down_and_cookie_csrf(client, world, dsn, monkeypatch, asr_on):
    from body_engine.ai import asr
    ids, tok = world
    with db(dsn) as c:
        c.execute("DELETE FROM asr_usage")

    def down(data):
        raise asr.AsrError("asr_down", "语音识别暂时不可用，可以用键盘上的听写", 503)
    monkeypatch.setattr(asr, "_post", down)
    r = client.post("/v1/assistant/transcribe", headers=W(tok["F1"]), content=make_wav(2))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "asr_down"
    with db(dsn) as c:  # a failed recognition is still counted (it used the quota), with the error code
        row = c.execute("SELECT ok, error FROM asr_usage WHERE user_id = %s", (ids["F1"],)).fetchone()
        assert row == {"ok": False, "error": "asr_down"}
    r = client.post("/v1/assistant/transcribe", headers={"Content-Type": "audio/wav"}, content=make_wav(2))
    assert r.status_code == 401
    big = b"RIFF" + b"\0" * (asr.MAX_BYTES + 10)
    r = client.post("/v1/assistant/transcribe", headers=W(tok["F1"]), content=big)
    assert r.status_code == 413


def test_main_consent_required_and_owner_try(client, world, dsn):
    ids, tok = world
    r = client.post("/v1/assistant/chat", headers=H(tok["N"]), json={"message": "hi"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "needs_consent"
    r = client.post("/v1/assistant/chat", headers=H(tok["O"]), json={"message": "hi"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "subscription"  # owner keeps the butler link
    from body_engine.ai import chat
    with db(dsn) as c:  # the CLI ai-fallback try path: owner may test, no separate consent (the owner pays)
        viewer = {"id": ids["O"], "token_scopes": ["read"]}
        evs = list(chat.chat_events(c, viewer, "hi", [], None, allow_owner_fallback=True))
        assert evs[0]["data"]["provider"] == "system_fallback" and evs[-1]["event"] == "done"


# ---------------------------------------------------------------- which AI Jarvis uses (ai/choice.py)
def me_ai(client, tok):
    r = client.get("/v1/web/me", headers=H(tok)).json()
    return r["ai"], r["assistant_url"]


def test_choice_disable_delete_and_revoke_fall_back(client, world, dsn):
    """The user's report: with their own (subscription) AI disabled, Jarvis still went there. Now disabling, deleting
    or revoking every AI token drops Jarvis back to the fallback at once, and picking it again works."""
    ids, tok = world
    with db(dsn) as c:
        c.execute("DELETE FROM ai_providers WHERE user_id = %s", (ids["F2"],))
        c.execute("DELETE FROM ai_choice WHERE user_id = %s", (ids["F2"],))
    assert me_ai(client, tok["F2"])[0]["mode"] == "fallback"
    p = client.post("/v1/users/me/ai-providers", headers=H(tok["F2"]), json={"kind": "codex_plan_mcp", "is_default": True}).json()
    ai, url = me_ai(client, tok["F2"])
    assert ai["mode"] == "mcp" and ai["choice"] == f"p:{p['id']}" and url is None
    r = client.post("/v1/assistant/chat", headers=H(tok["F2"]), json={"message": "hi"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "subscription"
    # disable -> fallback immediately; enable -> back
    client.patch(f"/v1/users/me/ai-providers/{p['id']}", headers=H(tok["F2"]), json={"status": "disabled"})
    ai, _ = me_ai(client, tok["F2"])
    assert ai["mode"] == "fallback" and ai["choice"] == "fallback"
    consent(client, tok["F2"])
    assert client.post("/v1/assistant/chat", headers=H(tok["F2"]), json={"message": "hi"}).status_code == 200
    assert FakeAdapter.calls[-1]["api_key"] == DS_KEY
    opts = client.get("/v1/users/me/ai-choice", headers=H(tok["F2"])).json()["options"]
    codex = next(o for o in opts if o["key"] == f"p:{p['id']}")
    assert codex["status"] == "disabled" and not codex["available"] and next(o for o in opts if o["current"])["key"] == "fallback"
    r = client.put("/v1/users/me/ai-choice", headers=H(tok["F2"]), json={"key": f"p:{p['id']}"})
    assert r.status_code == 422 and "停用" in r.json()["detail"]
    client.patch(f"/v1/users/me/ai-providers/{p['id']}", headers=H(tok["F2"]), json={"status": "active"})
    assert me_ai(client, tok["F2"])[0]["mode"] == "mcp"
    # every AI token revoked -> the subscription cannot reach Body Engine any more -> fallback
    t = client.post("/v1/users/me/ai-tokens", headers=H(tok["F2"]), json={"client": "codex"}).json()
    assert me_ai(client, tok["F2"])[0]["mode"] == "mcp"
    client.delete(f"/v1/users/me/ai-tokens/{t['id']}", headers=H(tok["F2"]))
    assert me_ai(client, tok["F2"])[0]["mode"] == "fallback"
    client.post("/v1/users/me/ai-tokens", headers=H(tok["F2"]), json={"client": "codex"})
    assert me_ai(client, tok["F2"])[0]["mode"] == "mcp"
    # delete -> fallback
    assert client.delete(f"/v1/users/me/ai-providers/{p['id']}", headers=H(tok["F2"])).status_code == 204
    assert me_ai(client, tok["F2"])[0]["mode"] == "fallback"


def test_choice_pick_fallback_over_own_ai_and_back(client, world, dsn):
    ids, tok = world
    reset(dsn, ids["F2"])
    p = client.post("/v1/users/me/ai-providers", headers=H(tok["F2"]),
                    json={"kind": "openai_compatible", "base_url": "https://api.example.com/v1", "model": "m1",
                          "api_key": "sk-own-key-F2-123456", "is_default": True}).json()
    assert me_ai(client, tok["F2"])[0]["mode"] == "api"
    r = client.put("/v1/users/me/ai-choice", headers=H(tok["F2"]), json={"key": "fallback"})
    assert r.status_code == 200 and r.json()["ai"]["mode"] == "fallback" and r.json()["assistant_url"] is None
    assert [o["key"] for o in r.json()["options"] if o["current"]] == ["fallback"]
    client.post("/v1/assistant/chat", headers=H(tok["F2"]), json={"message": "hi"})
    assert FakeAdapter.calls[-1]["api_key"] == DS_KEY  # the pick wins over the own provider
    r = client.put("/v1/users/me/ai-choice", headers=H(tok["F2"]), json={"key": f"p:{p['id']}"})
    assert r.json()["ai"]["mode"] == "api" and r.json()["ai"]["choice"] == f"p:{p['id']}"
    client.post("/v1/assistant/chat", headers=H(tok["F2"]), json={"message": "hi"})
    assert FakeAdapter.calls[-1]["api_key"] == "sk-own-key-F2-123456"
    # ticking "use it" on another provider is an explicit pick too
    client.put("/v1/users/me/ai-choice", headers=H(tok["F2"]), json={"key": "fallback"})
    q = client.post("/v1/users/me/ai-providers", headers=H(tok["F2"]),
                    json={"kind": "anthropic_api", "api_key": "sk-ant-F2-xxxxxxxx", "is_default": True}).json()
    assert me_ai(client, tok["F2"])[0]["choice"] == f"p:{q['id']}"
    # someone else's provider / the butler / nonsense
    a_prov = client.get("/v1/users/me/ai-providers", headers=H(tok["A"])).json()["providers"][0]["id"]
    for key in (f"p:{a_prov}", "butler", "nope"):
        assert client.put("/v1/users/me/ai-choice", headers=H(tok["F2"]), json={"key": key}).status_code == 422, key
    assert client.put(f"/v1/users/{ids['A']}/ai-choice", headers=H(tok["F2"]), json={"key": "fallback"}).status_code == 403
    with db(dsn) as c:
        c.execute("DELETE FROM ai_providers WHERE user_id = %s", (ids["F2"],))


def test_choice_owner_can_pick_fallback_and_butler(client, world, dsn):
    ids, tok = world
    reset(dsn, ids["O"])
    ai, url = me_ai(client, tok["O"])
    assert ai["mode"] == "butler" and url == "https://claude.ai/code/session_test"
    opts = client.get("/v1/users/me/ai-choice", headers=H(tok["O"])).json()["options"]
    assert {o["key"] for o in opts} >= {"fallback", "butler"} and next(o for o in opts if o["key"] == "fallback")["consent"]
    r = client.put("/v1/users/me/ai-choice", headers=H(tok["O"]), json={"key": "fallback"}).json()
    assert r["ai"]["mode"] == "fallback" and r["ai"]["consent"] and r["assistant_url"] is None
    assert r["ai"]["quota_cny"] == fp.MONTHLY_QUOTA_CNY  # the deployer gets the same default quota (ai-fallback quota to change)
    assert client.post("/v1/assistant/chat", headers=H(tok["O"]), json={"message": "hi"}).status_code == 200
    r = client.put("/v1/users/me/ai-choice", headers=H(tok["O"]), json={"key": "butler"}).json()
    assert r["ai"]["mode"] == "butler" and r["assistant_url"] == "https://claude.ai/code/session_test"
    # no fallback on this server: a fallback pick falls through to the next source
    with db(dsn) as c:
        c.execute("UPDATE ai_choice SET source = 'fallback' WHERE user_id = %s", (ids["O"],))
    import body_engine.ai.fallback as fbm
    orig = fbm.read_key
    fbm.read_key = lambda: None
    try:
        assert me_ai(client, tok["O"])[0]["mode"] == "butler"
        assert me_ai(client, tok["F1"])[0]["mode"] == "none"
    finally:
        fbm.read_key = orig

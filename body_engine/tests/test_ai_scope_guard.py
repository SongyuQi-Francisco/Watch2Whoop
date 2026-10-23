"""Jarvis scope guard (ai/scope_guard.py, rollout flag jarvis_scope_guard, issue #170): the fallback AI (the admin pays for it)
turns away clearly off-topic, costly requests with a fixed polite text and no model call; everything health-related, about the
app, feedback and small talk goes through; a person's own AI is never judged; with the flag off nothing changes.

Pure tests need nothing. The rest uses a separate test database BODY_TEST_DB_NAME_SCOPE_GUARD (default body_test_ai_scope_guard),
dropped and recreated every run, skipped without Postgres. No paid API (FakeAdapter).
Users: G (member, fallback AI, flag on via the beta cohort), H (member, fallback AI, flag off), A (member with an own AI of
their own, flag on).
"""
import json
import os
import re
from datetime import datetime, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from body_engine import api, auth
from body_engine.ai import adapters, crypto, fallback, fallback_cli, scope_guard
from body_engine.ai import scope_guard_params as sp
from body_engine.ai.adapters.fake import FakeAdapter
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.rollout import flags, registry

TEST_DB = os.environ.get("BODY_TEST_DB_NAME_SCOPE_GUARD", "body_test_ai_scope_guard")
CJK = re.compile(r"[　-〿一-鿿＀-￯]")

# ---------------------------------------------------------------- the examples (no database)
ALLOWED = [
    # the boundary case from the issue: a translation of a training plan is health
    "帮我把训练计划翻译成英文",
    "translate my training plan into English",
    "把这份菜单翻译成英文: " + "鸡胸肉沙拉、燕麦粥、水煮蛋、牛奶。" * 10,
    "Translate this for my doctor: I have been feeling tired and dizzy every morning for the last three weeks, "
    "and my resting heart rate looks higher than usual since I started the new supplement.",
    # health, training, food, sleep, recovery, supplements, labs, injuries
    "今天恢复怎么样", "今天吃了两碗米饭和一份鸡腿", "我肩膀内旋受限，推荐替代动作", "出差去悉尼，时差怎么调整睡眠",
    "请帮我解释一下尿酸偏高的化验结果", "Explain step by step how to do a squat", "帮我记一下体重 80", "肌酸每天吃多少",
    "写一篇关于睡眠和恢复的文章", "写一份减脂饮食计划", "write me a story about my recovery journey", "help me write a message to my doctor",
    "我做完作业了，今天可以练腿吗", "做个贪吃蛇游戏之前先吃点什么比较好",
    # the app, the account, feedback and feature requests (even ones that say "write a script")
    "这个 app 怎么导入 Hevy 的训练", "app 闪退了，怎么办", "要是能写个脚本把我的数据导出来就好了", "希望你们加一个游戏化的排行榜",
    "I wish the app had a dark mode, can you pass that on", "帮我看看这个网站上鸡胸肉多少钱",
    # small talk and short messages
    "你好", "谢谢", "ok", "继续", "你能做什么？",
    # pointing back at the conversation
    "把上面的计划翻译成英文", "translate the above into English", "能把你刚才写的润色一下吗",
    # short translations and rewrites are cheap and not clearly off-topic
    "翻译一下：你好吗", "translate good morning into Spanish", "润色一下这句话：我今天很开心",
    # no task verb + task object: the model decides
    "讲个笑话", "推荐一部电影", "今天天气怎么样", "make a game plan for Saturday's match",
]
BLOCKED = {
    "帮我写一个 Python 脚本，批量重命名文件": "code",
    "写个爬虫抓取豆瓣电影": "code",
    "做个贪吃蛇游戏": "code",
    "write a javascript function that reverses a string": "code",
    "帮我优化一下这段代码": "code",
    "```python\ndef foo(x):\n    return x * 2\nprint(foo(2))\n``` 这段代码有什么问题": "code",
    "```js\nfunction sleep(ms) {\n  return new Promise(r => setTimeout(r, ms));\n}\n``` explain my training loop": "code",
    "帮我做一下这道数学题，求导数 x^2+3x": "homework",
    "帮我写作业：英语完形填空": "homework",
    "解这个方程 2x+3=7": "homework",
    "帮我解答这道物理题": "homework",
    "can you do my homework for me": "homework",
    "帮我写一篇关于气候变化的800字作文": "writing",
    "帮我写一份英文简历": "writing",
    "Write a short story about a dragon who learns to code": "writing",
    "帮我写个小说": "writing",
    "翻译这篇文章：The quick brown fox jumps over the lazy dog. " * 3: "translation",
    "Translate the following paragraph into Chinese: " + "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 3: "translation",
    "总结这篇论文的主要观点，并润色成一段摘要": "rewrite",
    "详细讲讲股票基金的区别和怎么买": "general",
    "介绍一下量子力学和相对论的区别": "general",
    "Who is the president of France and what are the election rules?": "general",
}


def test_allowed_examples_are_not_blocked():
    for msg in ALLOWED:
        assert scope_guard.check(msg) is None, msg


def test_off_topic_examples_are_blocked_with_their_category():
    for msg, category in BLOCKED.items():
        assert scope_guard.check(msg) == category, msg


def test_pasted_code_is_blocked_whatever_words_it_holds():
    code = "```python\nimport time\ndef nap():\n    time.sleep(5)  # weight training sleep\n    return 1\n```"
    assert scope_guard.check(code) == "code"
    assert scope_guard.check("```\n今天训练：深蹲 5x5\n硬拉 3x5\n```") is None   # a fenced workout log is not code


def test_a_picture_may_hold_anything():
    """Translating / summarising with a picture attached (a lab report, a plan) is left to the model; the text of a code or
    homework request is clear on its own."""
    assert scope_guard.check("总结这篇文章") == "rewrite"
    assert scope_guard.check("总结这篇文章", images=1) is None
    assert scope_guard.check("翻译这篇文章", images=1) is None
    assert scope_guard.check("translate this article", images=2) is None
    assert scope_guard.check("帮我解答这道物理题", images=1) == "homework"
    assert scope_guard.check("帮我写个 Python 脚本", images=1) == "code"


def test_normalisation_and_case():
    assert scope_guard.check("ＷＲＩＴＥ　ａ　Ｐｙｔｈｏｎ　ＳＣＲＩＰＴ") == "code"   # full-width, upper case
    assert scope_guard.check("Translate my TRAINING PLAN into English") is None


def test_english_words_are_matched_as_words():
    """'nap' must not hide an essay about Napoleon; 'sleeping' must hide nothing but be understood as sleep."""
    assert scope_guard.check("write an essay about Napoleon and the war") == "writing"
    assert scope_guard.check("write an essay about sleeping habits") is None
    assert scope_guard.check("write an essay about exercising") is None   # prefix stem exercis*
    assert scope_guard.check("write a poem about injuries") is None


def test_lists_are_well_formed():
    import re as _re
    for category, pats in sp.OFF_TOPIC.items():
        assert pats, category
        for p in pats:
            _re.compile(p)
    for p in sp.WHOLE_DOCUMENT:
        _re.compile(p)
    assert all(w == w.lower() for w in sp.ON_TOPIC_ZH + sp.ON_TOPIC_EN)   # messages are lower-cased before the lookup
    assert all(not CJK.search(w) for w in sp.ON_TOPIC_EN)
    assert set(BLOCKED.values()) == set(scope_guard.CATEGORIES)            # every category has a blocked example
    assert sp.MIN_CHARS <= sp.GENERAL_MIN_CHARS <= sp.TRANSLATE_MIN_CHARS and sp.REWRITE_MIN_CHARS <= sp.TRANSLATE_MIN_CHARS


def test_texts_the_user_sees_are_bilingual_and_name_no_back_end():
    assert CJK.search(sp.DECLINE_ZH) and not CJK.search(sp.DECLINE_EN)
    for t in (sp.DECLINE_ZH, sp.DECLINE_EN, sp.PROMPT):
        assert not re.search(r"deepseek|sparky", t, re.I)
    assert sp.DECLINE_EN.count(". ") == 1 and sp.DECLINE_ZH.count("。") == 2   # two sentences
    assert CJK.search(sp.PROMPT)   # the rules for the AI are written in Chinese (the project's convention)
    assert "话题范围" in sp.PROMPT and "不调工具" in sp.PROMPT


def test_flag_is_declared_and_defaults_off():
    assert scope_guard.FLAG == "jarvis_scope_guard" and scope_guard.FLAG in registry.FLAGS


# ---------------------------------------------------------------- database
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
def world(dsn, tmp_path_factory):
    d = tmp_path_factory.mktemp("scope_guard")
    mp = pytest.MonkeyPatch()
    mp.setenv("BODY_AI_MASTER_KEY_FILE", str(d / "ai_master_key"))
    crypto.init_master_key()
    (d / "deepseek_api_key").write_text("test-scope-guard-key-not-real\n")
    mp.setenv("BODY_DEEPSEEK_API_KEY_FILE", str(d / "deepseek_api_key"))
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("INSERT INTO users (display_name, role) VALUES ('O', 'owner')")
        ids = {n: conn.execute("""INSERT INTO users (display_name, role, consent_at, timezone)
                                  VALUES (%s, 'member', %s, 'Australia/Sydney') RETURNING id""",
                               (n, datetime.now(timezone.utc))).fetchone()["id"] for n in ("G", "H", "A")}
        tok = {n: auth.create_token(conn, ids[n], "web", ["read", "write"]) for n in ids}
        for n in ("G", "H"):
            conn.execute("INSERT INTO ai_fallback_consents (user_id, version) VALUES (%s, %s)", (ids[n], fallback.CONSENT_VERSION))
        conn.execute("UPDATE ai_fallback_state SET enabled = true")
        flags.sync(conn)
        flags.set_flag(conn, scope_guard.FLAG, "cohort", cohort="beta", actor="test")
        flags.cohort_add(conn, ids["G"], actor="test")
        flags.cohort_add(conn, ids["A"], actor="test")
    yield ids, tok
    mp.undo()
    flags.invalidate()


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
    flags.invalidate()


def H(token, lang=None):
    return {"Authorization": f"Bearer {token}", **({"X-Body-Lang": lang} if lang else {})}


def sse(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def db(dsn):
    return psycopg.connect(dsn, row_factory=dict_row)


def chat(client, token, message, lang=None, **extra):
    r = client.post("/v1/assistant/chat", headers=H(token, lang), json={"message": message, **extra})
    assert r.status_code == 200, r.text
    return sse(r.text)


def count(dsn, sql, *args):
    with db(dsn) as c:
        return c.execute(sql, args).fetchone()["n"]


OFF_TOPIC_MSG = "帮我写一个 Python 脚本，批量重命名文件"


def test_flag_on_turns_off_topic_away_without_a_model_call(client, world, dsn):
    ids, tok = world
    events = chat(client, tok["G"], OFF_TOPIC_MSG)
    assert FakeAdapter.calls == []                                         # no model call, so no tools either
    names = [e for e, _ in events]
    assert names == ["conversation", "text", "done"], names
    text = "".join(d["delta"] for e, d in events if e == "text")
    assert text == sp.DECLINE_ZH
    assert count(dsn, "SELECT count(*) AS n FROM ai_usage WHERE user_id = %s", ids["G"]) == 0   # nothing billed or counted
    assert count(dsn, "SELECT count(*) AS n FROM ai_tool_calls WHERE user_id = %s", ids["G"]) == 0
    # the conversation reads back as a question and its answer, and is idle
    conv = next(d for e, d in events if e == "conversation")["id"]
    view = client.get(f"/v1/assistant/conversations/{conv}", headers=H(tok["G"])).json()
    assert [(m["role"], m["text"]) for m in view["messages"]] == [("user", OFF_TOPIC_MSG), ("assistant", sp.DECLINE_ZH)]
    assert view["state"] == "idle" and view["pending"] == []
    # the count: the category only, never the message
    with db(dsn) as c:
        row = c.execute("SELECT channel, args, ok FROM assistant_audit WHERE user_id = %s AND tool = %s",
                        (ids["G"], scope_guard.AUDIT_TOOL)).fetchone()
        assert row == {"channel": "api:system_fallback", "args": {"category": "code"}, "ok": True}
        s = fallback.admin_status(c, refresh=False)
        g = next(u for u in s["users"] if u["user_id"] == ids["G"])
        assert g["scope_blocked"] == 1 and g["calls"] == 0 and g["spent_cny"] == 0
        assert "被拦" in fallback_cli._table(s)
    # the quota the person sees is untouched
    ai = client.get("/v1/web/me", headers=H(tok["G"])).json()["ai"]
    assert ai["spent_cny"] == 0 and ai["remaining_pct"] == 100


def test_the_decline_is_in_the_request_language(client, world):
    ids, tok = world
    events = chat(client, tok["G"], "write a python script that renames files", lang="en")
    assert FakeAdapter.calls == []
    assert "".join(d["delta"] for e, d in events if e == "text") == sp.DECLINE_EN


def test_on_topic_goes_to_the_model_and_the_scope_rules_are_in_its_prompt(client, world):
    ids, tok = world
    FakeAdapter.script = [{"text": "Here is the plan in English."}]
    events = chat(client, tok["G"], "帮我把训练计划翻译成英文")
    assert len(FakeAdapter.calls) == 1
    system = FakeAdapter.calls[0]["system"]
    assert sp.PROMPT in system and system.index("## 话题范围") > system.index("## 边界")   # after the persona, once
    assert system.count("## 话题范围") == 1
    assert FakeAdapter.calls[0]["tools"]                                    # the usual tools are still there
    assert "".join(d["delta"] for e, d in events if e == "text") == "Here is the plan in English."


def test_a_conversation_goes_on_after_a_decline(client, world, dsn):
    ids, tok = world
    first = chat(client, tok["G"], "做个贪吃蛇游戏")
    conv = next(d for e, d in first if e == "conversation")["id"]
    assert FakeAdapter.calls == []
    FakeAdapter.script = [{"text": "好的，今天恢复 70。"}]
    chat(client, tok["G"], "那今天恢复怎么样", conversation_id=conv)
    assert len(FakeAdapter.calls) == 1
    roles = [m["role"] for m in FakeAdapter.calls[0]["history"]]
    assert roles == ["user", "assistant", "user"]                          # the decline is part of the history, the model sees it
    assert FakeAdapter.calls[0]["history"][1]["content"][0]["text"] == sp.DECLINE_ZH


def test_flag_off_is_exactly_as_before(client, world, dsn):
    """H has the same fallback AI and the same message, the flag is not on for them: the model is called, no scope rules in
    the prompt, nothing audited."""
    ids, tok = world
    FakeAdapter.script = [{"text": "Sure, here is a script."}]
    events = chat(client, tok["H"], OFF_TOPIC_MSG)
    assert len(FakeAdapter.calls) == 1
    assert "话题范围" not in FakeAdapter.calls[0]["system"]
    assert "".join(d["delta"] for e, d in events if e == "text") == "Sure, here is a script."
    assert count(dsn, "SELECT count(*) AS n FROM ai_usage WHERE user_id = %s", ids["H"]) == 1
    assert count(dsn, "SELECT count(*) AS n FROM assistant_audit WHERE user_id = %s AND tool = %s", ids["H"], scope_guard.AUDIT_TOOL) == 0
    # and the flag really is what switches it: the same person with the flag on
    with db(dsn) as c:
        flags.cohort_add(c, ids["H"], actor="test")
    flags.invalidate()
    FakeAdapter.calls = []
    assert [e for e, _ in chat(client, tok["H"], OFF_TOPIC_MSG)] == ["conversation", "text", "done"]
    assert FakeAdapter.calls == []
    with db(dsn) as c:
        flags.cohort_remove(c, ids["H"], actor="test")
    flags.invalidate()


def test_a_flag_that_is_off_for_everyone_changes_nothing(client, world, dsn):
    ids, tok = world
    with db(dsn) as c:
        flags.set_flag(c, scope_guard.FLAG, "off", actor="test")
    flags.invalidate()
    try:
        FakeAdapter.script = [{"text": "ok"}]
        chat(client, tok["G"], OFF_TOPIC_MSG)
        assert len(FakeAdapter.calls) == 1 and "话题范围" not in FakeAdapter.calls[0]["system"]
    finally:
        with db(dsn) as c:
            flags.set_flag(c, scope_guard.FLAG, "cohort", cohort="beta", actor="test")
        flags.invalidate()


def test_a_persons_own_ai_is_never_judged(client, world, dsn):
    """A has the flag on but their own AI: the message goes to it as it always did, with their own key and no scope rules."""
    ids, tok = world
    r = client.post("/v1/users/me/ai-providers", headers=H(tok["A"]),
                    json={"kind": "anthropic_api", "api_key": "sk-ant-test-own-key-AAAA", "monthly_budget_usd": 5})
    assert r.status_code == 201, r.text
    FakeAdapter.script = [{"text": "Here you go."}]
    events = chat(client, tok["A"], OFF_TOPIC_MSG)
    assert len(FakeAdapter.calls) == 1 and FakeAdapter.calls[0]["api_key"] == "sk-ant-test-own-key-AAAA"
    assert "话题范围" not in FakeAdapter.calls[0]["system"]
    assert "".join(d["delta"] for e, d in events if e == "text") == "Here you go."
    assert count(dsn, "SELECT count(*) AS n FROM assistant_audit WHERE user_id = %s AND tool = %s", ids["A"], scope_guard.AUDIT_TOOL) == 0


def test_confirming_a_write_in_the_fallback_keeps_the_rules(client, world):
    """The scope rules are part of every fallback call, also the ones that continue a turn after a confirmation."""
    ids, tok = world
    FakeAdapter.script = [{"tool_calls": [{"id": "d1", "name": "undo_sparky_entry", "args": {"kind": "food", "id": "x"}}]},
                          {"text": "好"}]
    events = chat(client, tok["G"], "把刚才记的那顿饭删掉")
    confirm = next(d for e, d in events if e == "confirm")
    r = client.post("/v1/assistant/chat/confirm", headers=H(tok["G"]),
                    json={"call_id": confirm["calls"][0]["call_id"], "approve": False})
    assert r.status_code == 200, r.text
    assert len(FakeAdapter.calls) == 2 and all("## 话题范围" in c["system"] for c in FakeAdapter.calls)

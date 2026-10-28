"""English for server texts the Today page shows (issue #42): HTTP error details (errors_en.py), reminders (title_en /
message_en, migration 310), the Me page status card (assistant/status.localize).

Static part (no DB): every Chinese HTTPException detail in the source, and every Chinese message raised in the modules whose
errors routers turn into details, has an English translation (f-strings are tried with sample values); every rule Reminder
writes English. DB part: zh vs en requests for an error, the reminders endpoints and /memory. Test database
body_test_i18n_server (BODY_TEST_DB_NAME_I18N_SERVER), recreated on every run; skipped if Postgres is unreachable."""
import ast
import os
import pathlib
import re
from datetime import date, datetime, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from body_engine import api, auth, errors_en
from body_engine.assistant import status
from body_engine.config import Settings
from body_engine.db import migrate
from body_engine.i18n import use_lang
from body_engine.rules import engine, safety
from body_engine.rules.base import Reminder, RuleResult

SRC = pathlib.Path(errors_en.__file__).parent
CJK = re.compile(r"[一-鿿]")
# Modules whose raised messages (ValueError, LookupError, ImportRefused, ChatError, AsrError …) routers turn into details.
REACHABLE = ("account/api.py", "ai/api.py", "ai/chat.py", "ai/asr.py", "api.py", "assistant/memory_api.py",
             "assistant/memory.py", "assistant/meals.py", "assistant/recipes.py", "assistant/recipes_api.py",
             "assistant/sparky_write.py", "cardio/", "explain/api.py", "friends/", "goals/", "grocery/", "history/", "insights/api.py",
             "insights/reports.py", "nutrition/extras.py", "profile/api.py", "profile/store.py", "rawdata/api.py",
             "records/api.py", "records/store.py", "records/analysis.py", "rules/api.py", "training/api.py",
             "training/history.py", "training/plans.py", "training/catalog.py", "avatars/", "images/", "water/api.py",
             "web/", "sparky_client.py", "ownstore/write.py")
# Modules with Chinese raise messages that stay Chinese because only the AI (tool arguments, tool results), the command line or
# the admin ever read them. A module that is in neither list fails test_every_chinese_raise_is_classified (issue #64): decide
# whether a page user can see its messages (then translate and add it to REACHABLE) or not (then add it here).
AI_OR_ADMIN_ONLY = (
    "account/identities.py", "account/invites.py", "account/open_signup_cli.py", "feedback/cli.py", "feedback/store.py", "ai/cards", "ai/crypto.py", "ai/fallback", "ai/mcp_http.py", "ai/root_rules.py",
    "ai/web/", "assistant/butler_tools.py", "assistant/diet_profile.py", "assistant/memory_review.py", "assistant/snapshots.py", "assistant/sparky_tools.py",
    "assistant/status.py", "assistant/tools.py", "assistant/writelog.py", "cli.py", "config.py", "importers/", "mcp_server.py",
    "ownstore/migrate.py",
    "nutrition/constraints.py", "ow_client.py", "rawdata/compare.py", "rawdata/ecg.py", "rawdata/ow_archive.py", "records/importer.py",
    "weightlog/store.py",   # tags / note validation: the Today page only sends valid ones (chips, maxlength), the AI reads the rest
    "records/lab_catalog.py", "records/tools.py", "rules/context.py", "rules/store.py", "rules/tools.py", "scores/inputs.py",
    "sync/history.py", "training/day_overrides.py", "training/importer.py", "training/media.py", "training/overrides.py",
    "training/plan_start.py", "training/tools.py", "water/store.py")


def _sample(node) -> str | None:
    """The message with every {expression} replaced by an ASCII sample value; None if it is not a string literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        n = iter(range(100))
        return "".join(v.value if isinstance(v, ast.Constant) else f"x{next(n)}" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _sample(node.left), _sample(node.right)
        return None if left is None and right is None else (left or "x") + (right or "x")
    return None


def _messages(reachable_only: bool):
    for p in sorted(SRC.rglob("*.py")):
        rel = p.relative_to(SRC).as_posix()
        if rel.startswith(("migrations/", "tests/")):
            continue
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) == "HTTPException":
                args = n.args[1:2] + [k.value for k in n.keywords if k.arg == "detail"]
            elif reachable_only and isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call) and rel.startswith(REACHABLE) \
                    and getattr(n.exc.func, "id", None) != "HTTPException":   # those are seen as calls above
                args = list(n.exc.args) + [k.value for k in n.exc.keywords]
            else:
                continue
            for a in list(args):
                if isinstance(a, ast.Dict):
                    args += a.values
            for a in args:
                s = _sample(a)
                if s and CJK.search(s):
                    yield f"{rel}:{n.lineno}", s


def test_every_chinese_detail_has_english():
    missing = [f"{where}: {s}" for where, s in _messages(True) if CJK.search(errors_en.translate(s))]
    assert not missing, "add these to errors_en.py (EXACT / _TEMPLATES):\n" + "\n".join(missing)


def test_every_chinese_raise_is_classified():
    """A module that raises a Chinese message must be in REACHABLE (checked above: its messages reach the page as an error
    detail, so they need English) or in AI_OR_ADMIN_ONLY (the AI, the CLI or the admin read them). Several modules once had
    Chinese messages a page user could meet and no test looked at them (issue #64)."""
    seen = set()
    for p in sorted(SRC.rglob("*.py")):
        rel = p.relative_to(SRC).as_posix()
        if rel.startswith(("migrations/", "tests/")) or rel.startswith(REACHABLE) or rel.startswith(AI_OR_ADMIN_ONLY):
            continue
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call) and getattr(n.exc.func, "id", None) != "HTTPException":
                for a in [*n.exc.args, *(k.value for k in n.exc.keywords)]:   # (HTTPException details are all checked above)
                    if (s := _sample(a)) and CJK.search(s):
                        seen.add(rel)
    assert not seen, f"decide for {sorted(seen)}: translate and add to REACHABLE, or add to AI_OR_ADMIN_ONLY (see the comment)"


def test_patterns_keep_values_and_terms():
    t = errors_en.translate
    assert t("没有查看用户 7 的 sleep 的授权") == "No permission to view the sleep of user 7"
    assert t("训练日：protein_g 900 不在 0–400 之间") == "Training day: protein_g 900 is not within 0–400"
    assert t("2026-10-20 的午餐已经记过「燕麦」，没有重复记（确实又吃了一份就用 allow_duplicates）") == \
        "“燕麦” is already logged for lunch on 2026-10-20; not logged twice"   # food name = the user's data, kept
    assert t("没有授权查看评分") == "No permission to view scores"
    assert t("SparkyFitness：连不上 SparkyFitness（timeout）：boom") == "SparkyFitness: Can't reach SparkyFitness (timeout): boom"
    assert t("something already English") == "something already English"
    assert errors_en.translate_detail({"code": "too_long", "message": "一条最长 60 秒"}) == \
        {"code": "too_long", "message": "At most 60 s per recording"}
    with use_lang("zh"):
        assert errors_en.localize("没有授权查看评分") == "没有授权查看评分"
    with use_lang("en"):
        assert errors_en.localize("没有授权查看评分") == "No permission to view scores"


def test_every_rule_reminder_writes_english():
    """Static: each Reminder(...) in rules/ passes title_en and message_en."""
    bad = []
    for p in sorted((SRC / "rules").glob("*.py")):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "Reminder":
                kws = {k.arg for k in n.keywords}
                if not {"title_en", "message_en"} <= kws:
                    bad.append(f"{p.name}:{n.lineno}")
    assert not bad, bad


def test_rule_english_has_the_same_numbers():
    from test_rules import ctx, one, score, FRI
    rem = one(safety.readiness(ctx(scores={FRI: score(debt=240)})))
    assert rem.title == "睡眠债 4.0 小时" and rem.title_en == "Sleep debt 4.0 h"
    assert "240" in rem.message and "240" in rem.message_en and not CJK.search(rem.message_en)


def test_status_localize():
    items = [
        {"key": "weight", "label": "体重", "value": "88.2", "unit": "kg", "group": "身体", "note": "周均 -1.2 kg（偏快）",
         "source": "auto"},
        {"key": "bedtime", "label": "入睡时间", "value": "凌晨 2:40", "unit": None, "group": "作息", "note": "近 7 晚平均",
         "source": "auto"},
        {"key": "training_week", "label": "本周训练", "value": "4", "unit": "次", "group": "训练",
         "note": "近 7 天 · 计划 5 次", "source": "auto"},
        {"key": "to_goal", "label": "距目标", "value": "已达到", "unit": None, "group": "目标", "note": "目标 80 kg",
         "source": "auto"},
        {"key": "uric_acid", "label": "尿酸", "value": "偏高 489", "unit": "μmol/L", "group": "健康",
         "note": "10-18 · 家测 · 上限 420", "source": "auto", "lab_key": "uric_acid"},
        {"key": "ldl", "label": "坏胆固醇", "value": "偏高 3.6", "unit": "mmol/L", "group": "健康",
         "note": "10-12 · 化验 · 上限 3.4 · 管家写的", "source": "assistant", "lab_key": "ldl"},
        {"key": "goal_weight", "label": "目标体重", "value": "80", "unit": "kg", "group": "目标", "note": None,
         "source": "user"},
        {"key": "phase", "label": "当前阶段", "value": "减脂期", "unit": None, "group": "训练", "note": "第 3 周",
         "source": "assistant"},
    ]
    with use_lang("zh"):
        assert status.localize(items) == items
    with use_lang("en"):
        out = {i["key"]: i for i in status.localize(items)}
    assert (out["weight"]["label"], out["weight"]["group"], out["weight"]["note"]) == \
        ("Weight", "Body", "Weekly avg -1.2 kg (fast)")
    assert out["bedtime"]["value"] == "02:40" and out["bedtime"]["note"] == "Last 7 nights avg"
    assert out["training_week"]["unit"] == "workouts" and out["training_week"]["note"] == "Last 7 days · plan 5"
    assert out["to_goal"]["value"] == "Reached" and out["to_goal"]["note"] == "Goal 80 kg"
    assert out["uric_acid"]["value"] == "High 489" and out["uric_acid"]["note"] == "10-18 · home · max 420"
    assert out["ldl"]["label"] == "坏胆固醇" and out["ldl"]["note"] == "10-12 · lab · max 3.4 · 管家写的"  # AI text kept
    assert out["goal_weight"]["label"] == "Goal weight"
    assert (out["phase"]["label"], out["phase"]["value"], out["phase"]["note"]) == ("Current phase", "减脂期", "第 3 周")
    assert items[0]["label"] == "体重"   # input not mutated


# ---------------------------------------------------------------- DB
TEST_DB = os.environ.get("BODY_TEST_DB_NAME_I18N_SERVER", "body_test_i18n_server")
DAY = date(2026, 10, 16)
EN = {"X-Body-Lang": "en"}


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
    return dsn


@pytest.fixture(scope="module")
def world(dsn):
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        uid = conn.execute("INSERT INTO users (display_name) VALUES ('A') RETURNING id").fetchone()["id"]
        tok = auth.create_token(conn, uid, "t", ["read", "write"])
        engine.store(conn, uid, DAY, [RuleResult("readiness", "triggered", reminders=[
            Reminder("readiness", "warning", "睡眠债 4.0 小时", "最近 14 晚加权平均少睡 240 分钟", subject="sleep_debt",
                     title_en="Sleep debt 4.0 h", message_en="Over the last 14 nights you slept 240 min less")])])
        # an old row from before migration 310 (no English yet)
        conn.execute("""INSERT INTO reminders (user_id, date, rule_key, subject, level, title, message, evidence, rules_version)
                        VALUES (%s, %s, 'progression', 'x', 'info', '旧提醒', '旧内容', '{}', 'v0')""", (uid, DAY))
        for key, label, value, unit, group, note in (("weight", "体重", "88.2", "kg", "身体", "周均 -0.4 kg"),
                                                      ("phase", "当前阶段", "减脂期", None, "训练", "管家写的")):
            conn.execute("""INSERT INTO assistant_status (user_id, key, label, value, unit, tone, "group", note, source)
                            VALUES (%s, %s, %s, %s, %s, 'neutral', %s, %s, %s)""",
                         (uid, key, label, value, unit, group, note, "auto" if key == "weight" else "assistant"))
        # fresh auto refresh: list as stored, no recomputation (no SparkyFitness / scores in this database)
        conn.execute("INSERT INTO sync_state (user_id, source, last_synced_at) VALUES (%s, 'status_auto', %s)",
                     (uid, datetime.now(timezone.utc)))
    return uid, {"Authorization": f"Bearer {tok}"}


@pytest.fixture(scope="module")
def client(dsn):
    api.pool = ConnectionPool(dsn, kwargs={"row_factory": dict_row}, min_size=1, max_size=3, open=True)
    yield TestClient(api.app)
    api.pool.close()


def test_error_detail_by_language(client, world):
    _, h = world
    assert client.get("/v1/users/abc/reminders", headers=h).json()["detail"] == "用户写 me 或数字 ID"
    assert client.get("/v1/users/abc/reminders", headers={**h, **EN}).json()["detail"] == "User must be me or a numeric ID"
    r = client.patch("/v1/users/me/reminders/999999", json={"status": "seen"}, headers={**h, **EN})
    assert r.status_code == 404 and r.json()["detail"] == "Reminder not found (or not yours)"
    assert client.get("/v1/users/me/reminders", headers={"Authorization": "Bearer nope", **EN}).json()["detail"] == \
        "Invalid or revoked token"


def test_reminders_by_language(client, world):
    _, h = world
    q = {"start": DAY.isoformat(), "end": DAY.isoformat()}
    zh = {r["rule_key"]: r for r in client.get("/v1/users/me/reminders", params=q, headers=h).json()}
    en = {r["rule_key"]: r for r in client.get("/v1/users/me/reminders", params=q, headers={**h, **EN}).json()}
    assert (zh["readiness"]["title"], en["readiness"]["title"]) == ("睡眠债 4.0 小时", "Sleep debt 4.0 h")
    assert en["readiness"]["message"] == "Over the last 14 nights you slept 240 min less"
    assert en["progression"]["title"] == "旧提醒"            # no English yet -> Chinese until the next evaluate-rules
    assert not {"title_en", "message_en"} & set(en["readiness"])
    rid = en["readiness"]["id"]
    p = client.patch(f"/v1/users/me/reminders/{rid}", json={"status": "seen"}, headers={**h, **EN})
    assert p.json()["title"] == "Sleep debt 4.0 h"
    # the Today page aggregate reads the same store
    d = client.get("/v1/users/me/today", params={"date": DAY.isoformat()}, headers={**h, **EN}).json()
    assert {r["title"] for r in d["reminders"]} >= {"Sleep debt 4.0 h"}


def test_store_rewrites_english_and_ai_sees_chinese(dsn, world):
    from body_engine.assistant.tools import run_tool
    uid, _ = world
    with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
        res = engine.store(conn, uid, DAY, [RuleResult("readiness", "triggered", reminders=[
            Reminder("readiness", "warning", "睡眠债 4.0 小时", "最近 14 晚加权平均少睡 240 分钟", subject="sleep_debt",
                     title_en="Sleep debt 4.0 h", message_en="Over the last 14 nights you slept 240 min less")])])
        assert res["unchanged"] == 1
        with use_lang("en"):   # the AI tool runs in zh even inside an English request
            out = run_tool(conn, uid, "test", "get_reminders", {"end": DAY.isoformat(),
                                                                 "include_dismissed": False})
    titles = str(out)
    assert "睡眠债 4.0 小时" in titles and "Sleep debt" not in titles


def test_memory_status_by_language(client, world):
    _, h = world
    zh = {i["key"]: i for i in client.get("/v1/users/me/memory", headers=h).json()["status"]}
    en = {i["key"]: i for i in client.get("/v1/users/me/memory", headers={**h, **EN}).json()["status"]}
    assert (zh["weight"]["label"], zh["weight"]["group"]) == ("体重", "身体")
    assert (en["weight"]["label"], en["weight"]["group"], en["weight"]["note"]) == ("Weight", "Body", "Weekly avg -0.4 kg")
    assert (en["phase"]["label"], en["phase"]["value"], en["phase"]["note"]) == ("Current phase", "减脂期", "管家写的")


def test_nutrition_validation_errors_in_english():
    """#183: the added-sugar / carbs errors name the field and the excess; the English version keeps the numbers."""
    from body_engine import errors_en
    assert errors_en.translate("蘑菇：添加糖不能多于总糖（added_sugar_g 9 g，sugar_g 5 g，超出 4 g）") == \
        "蘑菇: added sugar cannot be more than total sugar (added_sugar_g 9 g, sugar_g 5 g, 4 g over)"
    assert errors_en.translate("蘑菇：sugar_g 不能多于碳水（sugar_g 30 g，碳水 20 g，超出 10 g）") == \
        "蘑菇: sugar_g cannot be more than carbs (sugar_g 30 g, carbs 20 g, 10 g over)"

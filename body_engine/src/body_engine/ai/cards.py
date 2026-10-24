"""Interactive cards in the in-page chat (issue #53, rollout flag jarvis_rich; docs/ai-assistant-plan.md §9.19).

The model never writes HTML or chart data. A card is a small JSON block the server builds from the user's own data:
- the model asks for one with the show_card tool, naming WHAT to show (type + date / metric / buttons), never the numbers.
  The tool is sent only in the in-page chat while the flag is on (ai/chat.py); it is not in assistant/tools.TOOLS, so remote
  MCP clients (Claude / ChatGPT connectors) never see it and keep their plain-text answers;
- write tools in AUTO get their card without asking (log_food -> that day's meal card, ...), so a weak model (the fallback)
  still shows one.
Cards go to the page as an SSE `card` event and are stored on their ai_tool_calls row (column card, migration 595), so a
reopened conversation draws them again. The model gets one line back (summary()), so it does not repeat the card in words.

Behind the second flag jarvis_cards_v2 (needs jarvis_rich too) the tool takes more types, built in cards_food.py (diet) and
the meal card gets per-entry buttons; with it off, the tool, the prompt and every card are exactly as before.
Behind the third flag product_card (needs jarvis_cards_v2) the tool also takes type=product (cards_product.py, FB-12): one product's
nutrition, the user's own recorded store prices and price history, and what is at home; with it off, nothing changes.

Payload: {"v": 1, "type": ..., "actions": [...]}. Actions are either
  {"kind": "open", "page": one of OPEN_PAGES}                       -> the page goes there (no URL ever comes from the model);
  {"kind": "send", "key": one of SEND_KEYS}                         -> the page sends its own (translated) text as the user's message;
  {"kind": "send", "label": ..., "text": ...}                       -> buttons of an `actions` card, written by the model.
Data of a card is a snapshot from when it was shown; the pages it opens show the live state.
"""
import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal

import psycopg

from .. import queries, sparky_client
from ..assistant.tools import Tool
from ..rollout import flags
from ..toolspec import nullable, schema
from . import cards_body, cards_food, cards_product

NAME = "show_card"
FLAG = "jarvis_rich"
FLAG_V2 = "jarvis_cards_v2"
FLAG_PRODUCT = cards_product.FLAG   # product_card
PRODUCT_TYPE = cards_product.PRODUCT
TYPES = ("training", "meal", "metric", "sleep", "actions", "muscle")
FOOD_TYPES = ("goals", "suggest", "recipe", "recipes", "templates", "shopping")
BODY_TYPES = ("recovery", "strain", "stress", "healthspan", "trend", "weight", "lab", "supplements", "water", "journal",
              "report", "hr_zones", "vo2max", "monitor", "data_status")
TYPES_V2 = (*FOOD_TYPES, *BODY_TYPES)
# metric -> (source, column / metric name, unit, digits)
METRICS = {
    "recovery": ("scores", "recovery", "%", 0),
    "strain": ("scores", "strain", "", 1),
    "sleep": ("scores", "sleep_actual_min", "h", 1),
    "hrv": ("metrics", "heart_rate_variability_sdnn", "ms", 0),
    "rhr": ("metrics", "resting_heart_rate", "bpm", 0),
    "weight": ("weight", "body_mass", "kg", 1),
}
OPEN_PAGES = ("today", "trends", "record.training", "food", "sleep", "profile",   # sleep: that night's sleep detail page; profile: 个人资料
              "food.recipes", "food.shopping", "recipe",                          # v2; recipe: the card's recipe sheet (its id)
              "healthspan", "lab", "trends.report", "supplements", "data_status")   # lab: that test's trend sheet
SEND_KEYS = ("adjust_training", "next_meal", "explain_trend", "analyze_muscles",
             "more_meals", "log_recipe", "recipe_steps", "shopping_plan", "undo_meal",   # v2
             "explain_recovery", "explain_stress", "improve_body_age", "improve_vo2max", "explain_monitor", "explain_lab",
             "log_supplements", "explain_journal", "explain_report", "explain_sleep",
             "log_product", "record_price")   # product card (flag product_card)
# v2 per-row buttons the page draws on a card's rows (their text is built by the page from the row's own fields)
ENTRY_ACTIONS = ("portion", "delete")
MAX_DAYS, DEFAULT_DAYS = 90, 14
MAX_BUTTONS, MAX_LABEL, MAX_TEXT = 4, 24, 200
MAX_EXERCISES, MAX_ENTRIES = 12, 20
EXERCISE_KEYS = ("slug", "name_zh", "name_en", "status", "reason", "cues", "modality", "per_side", "load", "target",
                 "primary_muscles", "secondary_muscles", "thumb")
ENTRY_KEYS = ("id", "time", "meal_type", "food", "quantity", "unit", "calories", "protein_g")
MACROS = ("calories", "protein_g", "carbs_g", "fat_g")

# AI-facing (Chinese). Appended to the system prompt while the flag is on (chat._loop).
PROMPT = (
    "## 卡片（今天页对话）\n"
    "可以用 show_card 在回答里放一张用户能点的卡片：问今天 / 某天练什么 → training；问吃了多少、还差多少 → meal；"
    "讲某个指标的变化 → metric（metric + days）；分析某晚睡眠 → sleep（date=醒来那天）；要用户在几个选项里选 → actions（2–4 个按钮，label 短，用用户的语言）。"
    "问练了哪些肌群、哪块练得少 → muscle（先开了肌肉负荷图才有；range=today/week/last7）。"
    "记饮食、记训练、记体重成功后系统会自动附卡片，不用再调。一次回答最多 2 张卡片；卡片里已有的数字不要再用文字复述，"
    "文字只说结论和下一步。"
)

PROMPT_V2 = (
    "show_card 的参数：date=YYYY-MM-DD（sleep 是醒来那天，null=今天 / 最近），metric+days 给 metric / trend / weight，"
    "range 给 muscle（today/week/last7）和 report（week/month），slot 给 suggest / recipes，id 给 recipe，buttons 给 actions。\n"
    "饮食：吃了多少 / 离目标还差多少 → goals；推荐吃什么 → suggest（slot，all=全天每餐）；讲一道菜 → recipe（id=食谱 id）；"
    "有哪些菜 → recipes；快捷记餐模板 → templates；采购清单 → shopping。\n"
    "身体：恢复 → recovery；负荷和今天的目标 → strain；压力 → stress；身体年龄和各项贡献 → healthspan；"
    "任意指标一段时间的变化（和上一期比）→ trend（metric 用趋势指标 key，如 hrv、resting_hr、sleep_hours、sleep_efficiency、"
    "steps、strain、vo2max、weight，days 7–90）；体重 → weight；化验单项历史 → lab（metric=化验项 key 或名字）；"
    "补剂和今天打卡 → supplements；喝水 → water；日记里的行为对第二天恢复的影响 → journal（metric=日记项 key，null=全部）；"
    "周报 / 月报 → report（range）；心率区间 → hr_zones；VO₂max → vo2max；五项生命体征是否正常 → monitor；"
    "数据有没有同步上来 → data_status；某晚睡眠（阶段、效率、入睡和醒来时间）→ sleep。\n"
    "讲解某个数值或某天的情况时，先发卡片，再用两三句话解释。"
)

PROMPT_PRODUCT = (
    "商品卡：用户保存了商品、问某个商品的价格 / 营养 / 哪家有卖 / 家里还有没有 → 先调 show_card(type=product)，"
    "id=商品库里的商品 id（find_products / save_product 返回的），不知道 id 就用 metric=商品名或条码，再用一两句话说结论。"
    "卡片只显示有数据的块（营养、各超市最近价、价格走势、家里库存），价格都是用户自己记录的小票 / 价签截图，不是实时价，"
    "出这张卡不需要联网，不要为它去 Coles / Woolworths 网站查价（它们拦截自动访问，不要尝试绕过）；卡片上没有价格时，请用户「拍价签或小票发给我，我帮你记价格」，收到后用 record_store_prices 记。"
)

TOOL = Tool(
    name=NAME,
    description="给用户显示一张卡片。training=某天训练；muscle=肌肉图（range）；meal=饮食；"
                "metric=指标小图；sleep=某晚睡眠（date=醒来那天）；actions=快捷按钮（buttons）。",
    input_schema=schema({
        "type": {"type": "string", "enum": list(TYPES)},
        "date": nullable("string", "YYYY-MM-DD，null=今天"),
        "metric": nullable("string", None, enum=[*METRICS, None]),
        "days": nullable("integer", "metric 的天数，7–90，null=14"),
        "range": nullable("string", None, enum=["today", "week", "last7", None]),
        "buttons": nullable("array", "2–4 个", items=schema({"label": {"type": "string"}, "text": {"type": "string"}})),
    }),
    handler=lambda conn, uid, a: None,   # dispatched by ai/chat.py (build), never through run_tool
    scope="", group="ui",
)
TOOL_V2 = Tool(
    name=NAME,
    description="给用户显示一张卡片，数据由系统按用户自己的数据生成；各 type 用法见系统提示「卡片」一节。",
    input_schema=schema({
        "type": {"type": "string", "enum": [*TYPES, *TYPES_V2]},
        "date": nullable("string", None),
        "metric": nullable("string", None),
        "days": nullable("integer", None),
        "range": nullable("string", None, enum=["today", "week", "last7", "month", None]),
        "slot": nullable("string", None, enum=[*cards_food.SLOTS_ALL, None]),
        "id": nullable("integer", None),
        "buttons": nullable("array", "2–4 个", items=schema({"label": {"type": "string"}, "text": {"type": "string"}})),
    }),
    handler=lambda conn, uid, a: None,
    scope="", group="ui",
)

# Same tool with type=product (flag product_card): a separate definition, so with the flag off the definition sent is TOOL_V2 as before
TOOL_PRODUCT = replace(TOOL_V2, input_schema={**TOOL_V2.input_schema, "properties": {
    **TOOL_V2.input_schema["properties"],
    "type": {"type": "string", "enum": [*TYPES, *TYPES_V2, PRODUCT_TYPE]}}})

# write tool -> the card it shows after a successful run: (type, how to find the date in its args)
AUTO = {
    "log_food": ("meal", lambda a: ((a.get("items") or [{}])[0] or {}).get("date")),
    "update_food_entry": ("meal", lambda a: a.get("date")),
    "log_meal_template": ("meal", lambda a: a.get("date")),
    "log_recipe": ("meal", lambda a: a.get("date")),
    "log_workout": ("training", lambda a: a.get("date")),
    "log_weight": ("metric", lambda a: a.get("date")),
}
# v2 only: (type, the show_card arguments from the tool's args). Writes:
AUTO_V2 = {
    "undo_sparky_entry": ("meal", lambda a: {"date": a.get("date")} if a.get("kind") == "food" else None),
    "undo_meal_log": ("meal", lambda a: {}),
    "combine_food_entries": ("meal", lambda a: {"date": a.get("date")}),
    "set_nutrition_goals": ("goals", lambda a: {}),
    "create_meal_template": ("templates", lambda a: {}),
    "update_meal_template": ("templates", lambda a: {}),
    "update_recipe": ("recipe", lambda a: {"id": a.get("recipe_id")}),
    "generate_shopping_list": ("shopping", lambda a: {"date": a.get("week")}),
    "add_shopping_items": ("shopping", lambda a: {"date": a.get("week")}),
    "mark_bought": ("shopping", lambda a: {}),
    "log_water": ("water", lambda a: {}),
    "add_supplement": ("supplements", lambda a: {}),
    "update_supplement": ("supplements", lambda a: {}),
    "add_lab_results": ("lab", lambda a: _first_lab(a)),
    # the product card (flag product_card; build refuses without it): after saving a product, or recording the price of one
    "save_product": ("product", lambda a: _product_spec(a.get("product_id"), a.get("barcode") or a.get("name"))),
    "record_store_prices": ("product", lambda a: _product_spec(None, (a["items"][0] or {}).get("name"))
                            if len(a.get("items") or []) == 1 else None),
}
# ... and reads, for the fallback AI only (a weak model that rarely calls show_card itself; ai/chat.py): the card of what it
# just looked up. A strong model gets the prompt instead, so its reads (often several days, for its own analysis) stay quiet.
READ_AUTO = {
    "get_nutrition": ("meal", lambda a: {"date": a.get("end")} if a.get("end") and a.get("start") in (None, a.get("end")) else None),
    "get_nutrition_goals": ("goals", lambda a: {"date": a.get("date")}),
    "suggest_meals": ("suggest", lambda a: {"date": a.get("date"), "slot": a.get("slot")}),
    "get_recipe": ("recipe", lambda a: {"id": a.get("recipe_id")}),
    "list_recipes": ("recipes", lambda a: {"slot": a.get("slot")}),
    "get_meal_templates": ("templates", lambda a: {}),
    "get_shopping_list": ("shopping", lambda a: {"date": a.get("week")}),
    "get_sleep": ("sleep", lambda a: {"date": a.get("end")}),
    "get_sleep_details": ("sleep", lambda a: {"date": a.get("end")}),
    "get_scores": ("recovery", lambda a: {"date": a.get("end")}),
    "get_strain_coach": ("strain", lambda a: {"date": a.get("date")}),
    "get_stress": ("stress", lambda a: {"date": a.get("end")}),
    "get_healthspan": ("healthspan", lambda a: {}),
    "get_weight": ("weight", lambda a: {"date": a.get("end")}),
    "get_lab_history": ("lab", lambda a: {"metric": a.get("test")} if a.get("test") else None),
    "list_supplements": ("supplements", lambda a: {}),
    "analyze_journal": ("journal", lambda a: {"date": a.get("end"), "metric": a.get("key")}),
    "get_report": ("report", lambda a: {"date": a.get("end"), "range": a.get("period")}),
    "get_hr_zones": ("hr_zones", lambda a: {"date": a.get("end")}),
    "get_vo2max": ("vo2max", lambda a: {"date": a.get("end")}),
    "get_health_monitor": ("monitor", lambda a: {"date": a.get("date")}),
    "find_products": ("product", lambda a: _product_spec(None, a.get("q"))),
    "get_price_history": ("product", lambda a: _product_spec(None, a.get("q"))),
}


def _product_spec(product_id, text) -> dict | None:
    """show_card arguments for a product: its library id, else a name / barcode; None when the call named neither."""
    if product_id is None and not (isinstance(text, str) and text.strip()):
        return None
    return {"id": product_id, "metric": text.strip() if isinstance(text, str) and text.strip() else None}


def _first_lab(a: dict) -> dict | None:
    r = next((x for x in a.get("results") or [] if isinstance(x, dict) and (x.get("test_key") or x.get("test"))), None)
    return {"metric": r.get("test_key") or r.get("test")} if r else None


def enabled(conn: psycopg.Connection, user_id: int) -> bool:
    return flags.enabled(conn, user_id, FLAG)


def v2_enabled(conn: psycopg.Connection, user_id: int) -> bool:
    return enabled(conn, user_id) and flags.enabled(conn, user_id, FLAG_V2)


def product_enabled(conn: psycopg.Connection, user_id: int) -> bool:
    return v2_enabled(conn, user_id) and flags.enabled(conn, user_id, FLAG_PRODUCT)


def tool(v2: bool, product: bool = False) -> Tool:
    return TOOL_PRODUCT if v2 and product else TOOL_V2 if v2 else TOOL


def prompt(v2: bool, product: bool = False) -> str:
    if not v2:
        return PROMPT
    return f"{PROMPT}\n{PROMPT_V2}" + (f"\n{PROMPT_PRODUCT}" if product else "")


def _day(conn, uid: int, value) -> date:
    if not value:
        return queries.local_today(conn, uid)
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError(f"date 要 YYYY-MM-DD：{value}") from None


def _training(conn, uid: int, day: date) -> dict:
    from ..training import session_log
    v = session_log.view(conn, uid, uid, day)
    exercises = [{k: c.get(k) for k in EXERCISE_KEYS} for s in v["sessions"] for c in s["exercises"]][:MAX_EXERCISES]
    log = v.get("log") or {}
    done = sum(1 for e in log.get("exercises") or [] for s in e.get("sets") or [] if s.get("done"))
    actions = [{"kind": "open", "page": "record.training"}] if v["can_log"] and exercises else []
    if exercises and v["is_today"]:
        actions.append({"kind": "send", "key": "adjust_training"})
    return {"type": "training", "date": day.isoformat(), "is_today": v["is_today"],
            "plan": (v.get("plan") or {}).get("name"), "sessions": [s["name"] for s in v["sessions"]],
            "rest_day": bool(v.get("rest_day")), "readiness": v.get("readiness") or None,
            "exercises": exercises, "log": {"status": log.get("status"), "done_sets": done} if log else None,
            "actions": actions}


def _meal(conn, uid: int, day: date, v2: bool = False, auto: bool = False) -> dict:
    from ..nutrition import daily
    n = daily.nutrition(conn, uid, day, day, include_entries=True)
    d = n["days"][0]
    goal = d.get("goal") or {}
    card = {"type": "meal", "date": day.isoformat(), "logged": d["logged"],
            "totals": {k: d.get(k) for k in MACROS},
            "goal": {k: goal.get(k) for k in MACROS} if goal else None,
            "entries": [{k: e.get(k) for k in ENTRY_KEYS} for e in n.get("entries") or []][:MAX_ENTRIES],
            "actions": [{"kind": "open", "page": "food"}, {"kind": "send", "key": "next_meal"}]}
    if v2:   # tap an entry: change its portion / delete it (the page sends a message naming the entry; deleting asks to confirm)
        card["entry_actions"] = list(ENTRY_ACTIONS) if any(e.get("id") for e in card["entries"]) else []
        if auto and d["logged"]:   # the card right after a log: undo what was just logged
            card["actions"].append({"kind": "send", "key": "undo_meal"})
    return card


def _series(conn, uid: int, metric: str, start: date, end: date) -> dict[date, float]:
    src, col, _, _ = METRICS[metric]
    if src == "scores":
        rows = conn.execute(f"SELECT date, {col} AS v FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s"
                            f" AND {col} IS NOT NULL", (uid, start, end)).fetchall()
        return {r["date"]: float(r["v"]) / (60 if col == "sleep_actual_min" else 1) for r in rows}
    if src == "weight":
        from ..trends import series
        try:
            return series.weights(conn, uid, start, end)
        except sparky_client.SparkyError:
            pass   # no SparkyFitness link: Apple Health's body mass only
    return {r["date"]: float(r["value"]) for r in conn.execute(
        "SELECT date, value FROM daily_metrics WHERE user_id = %s AND metric = %s AND date BETWEEN %s AND %s",
        (uid, col, start, end))}


def _metric(conn, uid: int, metric: str | None, end: date, days: int | None) -> dict:
    if metric not in METRICS:
        raise ValueError(f"metric 只能是 {' / '.join(METRICS)}")
    days = max(7, min(MAX_DAYS, int(days or DEFAULT_DAYS)))
    start = end - timedelta(days=days - 1)
    by = _series(conn, uid, metric, start, end)
    dates = [start + timedelta(days=k) for k in range(days)]
    _, _, unit, digits = METRICS[metric]
    values = [None if by.get(d) is None else round(by[d], digits) for d in dates]
    known = [v for v in values if v is not None]
    return {"type": "metric", "metric": metric, "unit": unit, "digits": digits,
            "dates": [d.isoformat() for d in dates], "values": values,
            "latest": known[-1] if known else None,
            "avg": round(sum(known) / len(known), digits) if known else None,
            "actions": [{"kind": "open", "page": "trends"}] + ([{"kind": "send", "key": "explain_trend"}] if known else [])}


def _muscle(conn, uid: int, rng) -> dict:
    from ..training import muscle_load, params as TP
    if not flags.enabled(conn, uid, "muscle_load"):
        raise ValueError("肌肉负荷图还没有对这个用户开放")
    rng = rng if rng in TP.MUSCLE_RANGES else "week"
    m = muscle_load.compute(conn, uid)
    basis = "last7" if rng == "last7" else "week"
    rows = [{"key": x["key"], "name": x["name"], "sets": x["ranges"][rng]["sets"], "kg": x["ranges"][rng]["volume_kg"],
             "status": x["status"][basis], "today": x["trained_today"], "sport_min": x["sport_min"][rng]} for x in m["muscles"]]
    actions = [{"kind": "open", "page": "record.training"}, {"kind": "send", "key": "analyze_muscles"}]
    if not m["sex"]:   # drawn as the male body until the profile says otherwise (#189)
        actions.append({"kind": "open", "page": "profile"})
    return {"type": "muscle", "range": rng, "sex": m["sex"], "week_start": m["week_start"], "muscles": rows,
            "sports": [{k: x[k] for k in ("type", "name", "min")} for x in m["sports"][rng]], "actions": actions}

SLEEP_MAIN = ("start", "end", "in_bed_min", "asleep_min", "efficiency_pct", "composition", "stages")


SLEEP_MAIN_V2 = ("latency_min", "waso_min", "awakenings", "deep_pct", "rem_pct")


def _sleep(conn, uid: int, day: date | None, v2: bool = False) -> dict:
    """One night (wake date; None = the latest) from the sleep detail page's own builder: times, stages, score, notes.
    v2 also: latency, time awake, wake-ups, the score's parts and the 7-night bedtime / wake / efficiency."""
    from ..sleep_night import view
    v = view.build(conn, uid, uid, day)
    m = v["main"] or {}
    extra = {}
    if v2:
        a7 = (v["baseline"].get("avg7") or {})
        extra = {"more": {k: m.get(k) for k in SLEEP_MAIN_V2} if m else None,
                 "parts": [{k: p.get(k) for k in ("key", "score")} for p in (v["score"] or {}).get("parts") or []],
                 "avg7": {k: a7.get(k) for k in ("bedtime", "wake", "efficiency_pct", "asleep_min")} if a7 else None}
    card = {"type": "sleep", "date": v["date"].isoformat(), "main": {k: m.get(k) for k in SLEEP_MAIN} if m else None,
            "score": (v["score"] or {}).get("value"), "need_min": (v["score"] or {}).get("need_min"),
            "avg7_asleep_min": (v["baseline"].get("avg7") or {}).get("asleep_min"),
            "notes": [{k: n[k] for k in ("key", "tone")} for n in v["notes"]],
            "actions": [{"kind": "open", "page": "sleep"}] if m else []}
    if v2 and m:
        card["actions"].append({"kind": "send", "key": "explain_sleep"})
    return {**card, **extra}


def _actions(buttons) -> dict:
    out = []
    for b in (buttons or [])[:MAX_BUTTONS]:
        label, text = str((b or {}).get("label") or "").strip(), str((b or {}).get("text") or "").strip()
        if label and text:
            out.append({"kind": "send", "label": label[:MAX_LABEL], "text": text[:MAX_TEXT]})
    if not out:
        raise ValueError("actions 要 1–4 个按钮，每个有 label 和 text")
    return {"type": "actions", "actions": out}


def _build_v2(conn, uid: int, kind: str, a: dict) -> dict:
    if kind == "goals":
        return cards_food.goals(conn, uid, _day(conn, uid, a.get("date")))
    if kind == "suggest":
        return cards_food.suggest(conn, uid, _day(conn, uid, a.get("date")), a.get("slot"))
    if kind == "recipe":
        return cards_food.recipe(conn, uid, a.get("id"))
    if kind == "recipes":
        return cards_food.recipe_list(conn, uid, a.get("slot"))
    if kind == "templates":
        return cards_food.templates(conn, uid)
    if kind == "shopping":
        return cards_food.shopping(conn, uid, _day(conn, uid, a.get("date")) if a.get("date") else None)
    day = _day(conn, uid, a.get("date"))
    body = {
        "recovery": lambda: cards_body.recovery(conn, uid, day),
        "strain": lambda: cards_body.strain(conn, uid, day),
        "stress": lambda: cards_body.stress(conn, uid, day),
        "healthspan": lambda: cards_body.healthspan(conn, uid),
        "trend": lambda: cards_body.trend(conn, uid, a.get("metric"), day, a.get("days")),
        "weight": lambda: cards_body.weight(conn, uid, day, a.get("days")),
        "lab": lambda: cards_body.lab(conn, uid, a.get("metric")),
        "supplements": lambda: cards_body.supplements(conn, uid, day),
        "water": lambda: cards_body.water(conn, uid, day),
        "journal": lambda: cards_body.journal(conn, uid, a.get("metric"), day, a.get("days")),
        "report": lambda: cards_body.report(conn, uid, a.get("range"), day),
        "hr_zones": lambda: cards_body.hr_zones(conn, uid, day, a.get("days")),
        "vo2max": lambda: cards_body.vo2max(conn, uid, day, a.get("days")),
        "monitor": lambda: cards_body.monitor(conn, uid, day),
        "data_status": lambda: cards_body.data_status(conn, uid),
    }
    return body[kind]()


def _jsonable(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    raise TypeError(f"{type(v).__name__} in a card")


def plain(card: dict) -> dict:
    """The card as plain JSON (dates as ISO strings, decimals as floats): it is stored as jsonb and sent as an SSE event."""
    return json.loads(json.dumps(card, default=_jsonable))


def build(conn: psycopg.Connection, uid: int, a: dict, v2: bool = False, auto: bool = False) -> dict:
    """The card for show_card's arguments (the user's own data only). ValueError / PermissionError / SparkyError on failure.
    v2: the jarvis_cards_v2 types and buttons; auto: the card follows a write (cards.AUTO), not a show_card call."""
    kind = a.get("type")
    types = (*TYPES, *TYPES_V2, PRODUCT_TYPE) if v2 else TYPES
    if kind not in types:
        raise ValueError(f"type 只能是 {' / '.join(types)}")
    if kind == PRODUCT_TYPE:
        if not flags.enabled(conn, uid, FLAG_PRODUCT):
            raise ValueError("商品卡还没有对这个用户开放")
        card = cards_product.product(conn, uid, a.get("id"), a.get("metric"))
    elif kind in TYPES_V2:
        card = _build_v2(conn, uid, kind, a)
    elif kind == "actions":
        card = _actions(a.get("buttons"))
    elif kind == "training":
        card = _training(conn, uid, _day(conn, uid, a.get("date")))
    elif kind == "meal":
        card = _meal(conn, uid, _day(conn, uid, a.get("date")), v2, auto)
    elif kind == "muscle":
        card = _muscle(conn, uid, a.get("range"))
    elif kind == "sleep":
        card = _sleep(conn, uid, _day(conn, uid, a.get("date")) if a.get("date") else None, v2)
    else:
        card = _metric(conn, uid, a.get("metric"), _day(conn, uid, a.get("date")), a.get("days"))
    return plain({"v": 1, **card})


def auto_spec(tool: str, args: dict, v2: bool = False, reads: bool = False) -> dict | None:
    """show_card arguments for the card a successful write tool gets (v2: AUTO_V2 too; reads: READ_AUTO too), or None."""
    if tool in AUTO:
        kind, when = AUTO[tool]
        try:
            day = when(args or {})
        except (AttributeError, TypeError, IndexError):
            day = None
        return {"type": kind, "date": day, **({"metric": "weight", "days": 30} if kind == "metric" else {})}
    table = {**AUTO_V2, **(READ_AUTO if reads else {})} if v2 else {}
    if tool not in table:
        return None
    kind, spec = table[tool]
    try:
        extra = spec(args or {})
    except (AttributeError, TypeError):
        extra = {}
    return None if extra is None else {"type": kind, "date": None, **extra}


def spec_key(a: dict) -> tuple:
    """What makes two cards the same within a turn (show_card arguments or an automatic spec)."""
    return tuple((a or {}).get(k) for k in ("type", "date", "metric", "id", "slot", "range"))


def summary(card: dict) -> str:
    """The tool result the model gets (AI-facing): what is on screen, so it can refer to it without repeating it."""
    t = card["type"]
    if t in FOOD_TYPES:
        return cards_food.summary(card)
    if t in BODY_TYPES:
        return cards_body.summary(card)
    if t == PRODUCT_TYPE:
        return cards_product.summary(card)
    if t == "actions":
        return f"已显示 {len(card['actions'])} 个按钮；用户点了会作为一条新消息发回。"
    if t == "training":
        names = "、".join(e["name_zh"] or "" for e in card["exercises"][:6])
        what = f"{len(card['exercises'])} 个动作：{names}" if card["exercises"] else ("休息日" if card["rest_day"] else "没有计划")
        return f"已显示 {card['date']} 训练卡（{what}）。不要用文字复述卡片内容。"
    if t == "muscle":
        low = "、".join(x["name"] for x in card["muscles"] if x["status"] in ("none", "low"))[:60]
        sports = "、".join(f"{x['name']} {x['min']} 分钟" for x in card.get("sports") or [])[:80]
        return (f"已显示肌肉负荷图（{card['range']}）。偏少 / 没练（按力量组数）：{low or '无'}。"
                f"{f'手表运动（已折成等效组数计入上面）：{sports}。' if sports else ''}不要用文字复述卡片里的数字。")
    if t == "sleep":
        m = card["main"]
        if not m:
            return f"已显示 {card['date']} 睡眠卡：这天没有手表睡眠数据。"
        what = "、".join(n["key"] for n in card["notes"] if n["tone"] != "good") or "无异常"
        return (f"已显示 {card['date']} 睡眠卡：{m['start']}→{m['end']}，睡着 {m['asleep_min']} 分，睡眠分 {card['score']}；"
                f"规则发现：{what}。不要用文字复述卡片里的数字。")
    if t == "meal":
        tot, goal = card["totals"], card["goal"] or {}
        g = f" / 目标 {goal['calories']:g}" if goal.get("calories") else ""
        return (f"已显示 {card['date']} 饮食卡：{tot['calories'] or 0:g} kcal{g}，蛋白 {tot['protein_g'] or 0:g} g，"
                f"{len(card['entries'])} 条。不要用文字复述卡片内容。")
    return (f"已显示 {card['metric']} 近 {len(card['dates'])} 天小图：最新 {card['latest']}，均值 {card['avg']} {card['unit']}。"
            "不要用文字复述卡片里的数字。")

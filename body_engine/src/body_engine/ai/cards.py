"""Interactive cards in the in-page chat (issue #53, rollout flag jarvis_rich; docs/ai-assistant-plan.md §9.19).

The model never writes HTML or chart data. A card is a small JSON block the server builds from the user's own data:
- the model asks for one with the show_card tool, naming WHAT to show (type + date / metric / buttons), never the numbers.
  The tool is sent only in the in-page chat while the flag is on (ai/chat.py); it is not in assistant/tools.TOOLS, so remote
  MCP clients (Claude / ChatGPT connectors) never see it and keep their plain-text answers;
- write tools in AUTO get their card without asking (log_food -> that day's meal card, ...), so a weak model (the fallback)
  still shows one.
Cards go to the page as an SSE `card` event and are stored on their ai_tool_calls row (column card, migration 595), so a
reopened conversation draws them again. The model gets one line back (summary()), so it does not repeat the card in words.

Payload: {"v": 1, "type": ..., "actions": [...]}. Actions are either
  {"kind": "open", "page": one of OPEN_PAGES}                       -> the page goes there (no URL ever comes from the model);
  {"kind": "send", "key": one of SEND_KEYS}                         -> the page sends its own (translated) text as the user's message;
  {"kind": "send", "label": ..., "text": ...}                       -> buttons of an `actions` card, written by the model.
Data of a card is a snapshot from when it was shown; the pages it opens show the live state.
"""
from datetime import date, timedelta

import psycopg

from .. import queries, sparky_client
from ..assistant.tools import Tool
from ..rollout import flags
from ..toolspec import nullable, schema

NAME = "show_card"
FLAG = "jarvis_rich"
TYPES = ("training", "meal", "metric", "sleep", "actions", "muscle")
# metric -> (source, column / metric name, unit, digits)
METRICS = {
    "recovery": ("scores", "recovery", "%", 0),
    "strain": ("scores", "strain", "", 1),
    "sleep": ("scores", "sleep_actual_min", "h", 1),
    "hrv": ("metrics", "heart_rate_variability_sdnn", "ms", 0),
    "rhr": ("metrics", "resting_heart_rate", "bpm", 0),
    "weight": ("weight", "body_mass", "kg", 1),
}
OPEN_PAGES = ("today", "trends", "record.training", "food", "sleep", "profile")   # sleep: that night's sleep detail page; profile: 个人资料
SEND_KEYS = ("adjust_training", "next_meal", "explain_trend", "analyze_muscles")
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

# write tool -> the card it shows after a successful run: (type, how to find the date in its args)
AUTO = {
    "log_food": ("meal", lambda a: ((a.get("items") or [{}])[0] or {}).get("date")),
    "update_food_entry": ("meal", lambda a: a.get("date")),
    "log_meal_template": ("meal", lambda a: a.get("date")),
    "log_recipe": ("meal", lambda a: a.get("date")),
    "log_workout": ("training", lambda a: a.get("date")),
    "log_weight": ("metric", lambda a: a.get("date")),
}


def enabled(conn: psycopg.Connection, user_id: int) -> bool:
    return flags.enabled(conn, user_id, FLAG)


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


def _meal(conn, uid: int, day: date) -> dict:
    from ..nutrition import daily
    n = daily.nutrition(conn, uid, day, day, include_entries=True)
    d = n["days"][0]
    goal = d.get("goal") or {}
    return {"type": "meal", "date": day.isoformat(), "logged": d["logged"],
            "totals": {k: d.get(k) for k in MACROS},
            "goal": {k: goal.get(k) for k in MACROS} if goal else None,
            "entries": [{k: e.get(k) for k in ENTRY_KEYS} for e in n.get("entries") or []][:MAX_ENTRIES],
            "actions": [{"kind": "open", "page": "food"}, {"kind": "send", "key": "next_meal"}]}


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


def _sleep(conn, uid: int, day: date | None) -> dict:
    """One night (wake date; None = the latest) from the sleep detail page's own builder: times, stages, score, notes."""
    from ..sleep_night import view
    v = view.build(conn, uid, uid, day)
    m = v["main"] or {}
    return {"type": "sleep", "date": v["date"].isoformat(), "main": {k: m.get(k) for k in SLEEP_MAIN} if m else None,
            "score": (v["score"] or {}).get("value"), "need_min": (v["score"] or {}).get("need_min"),
            "avg7_asleep_min": (v["baseline"].get("avg7") or {}).get("asleep_min"),
            "notes": [{k: n[k] for k in ("key", "tone")} for n in v["notes"]],
            "actions": [{"kind": "open", "page": "sleep"}] if m else []}


def _actions(buttons) -> dict:
    out = []
    for b in (buttons or [])[:MAX_BUTTONS]:
        label, text = str((b or {}).get("label") or "").strip(), str((b or {}).get("text") or "").strip()
        if label and text:
            out.append({"kind": "send", "label": label[:MAX_LABEL], "text": text[:MAX_TEXT]})
    if not out:
        raise ValueError("actions 要 1–4 个按钮，每个有 label 和 text")
    return {"type": "actions", "actions": out}


def build(conn: psycopg.Connection, uid: int, a: dict) -> dict:
    """The card for show_card's arguments (the user's own data only). ValueError / PermissionError / SparkyError on failure."""
    kind = a.get("type")
    if kind not in TYPES:
        raise ValueError(f"type 只能是 {' / '.join(TYPES)}")
    if kind == "actions":
        card = _actions(a.get("buttons"))
    elif kind == "training":
        card = _training(conn, uid, _day(conn, uid, a.get("date")))
    elif kind == "meal":
        card = _meal(conn, uid, _day(conn, uid, a.get("date")))
    elif kind == "muscle":
        card = _muscle(conn, uid, a.get("range"))
    elif kind == "sleep":
        card = _sleep(conn, uid, _day(conn, uid, a.get("date")) if a.get("date") else None)
    else:
        card = _metric(conn, uid, a.get("metric"), _day(conn, uid, a.get("date")), a.get("days"))
    return {"v": 1, **card}


def auto_spec(tool: str, args: dict) -> dict | None:
    """show_card arguments for the card a successful write tool gets, or None."""
    if tool not in AUTO:
        return None
    kind, when = AUTO[tool]
    try:
        day = when(args or {})
    except (AttributeError, TypeError, IndexError):
        day = None
    return {"type": kind, "date": day, **({"metric": "weight", "days": 30} if kind == "metric" else {})}


def summary(card: dict) -> str:
    """The tool result the model gets (AI-facing): what is on screen, so it can refer to it without repeating it."""
    t = card["type"]
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
                f"{f'运动（单独一层，不算组数）：{sports}。' if sports else ''}不要用文字复述卡片里的数字。")
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

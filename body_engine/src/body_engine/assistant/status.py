"""Status cards of the body profile (table assistant_status): the small cards on the Today page "Me" tab, one short phrase each.

- auto items are computed by the backend from the database / SparkyFitness (refresh_auto): weight and weekly change, distance to goal, 7-day average recovery score, sleep debt,
  average bedtime, sleep duration, latest uric acid / ALT / triglycerides, workouts in the last 7 days, red meat in the last 7 days,
  added sugar today and its 7-day average (against the cap of the goal in effect, nutrition/daily.py). Refreshed by the hourly job
  (body-engine refresh-status) and on read (when older than STALE_MINUTES); an item that cannot be computed (no data) is dropped,
  and a read error keeps the previous value.
- assistant items are written by the body butler after its own judgement (set_status_items): current phase, injury status, goal weight, plan adherence, etc.
- Fields: label <= 6 chars, value <= 12 chars (excluding unit), unit, tone (good / warn / bad / neutral),
  trend (up / down / flat) + good_direction (up / down: which direction is good), group, spark (old -> new values), note <= 20 chars.
- Lab-linked items: any item whose key is one of the owner's lab test_keys (the auto uric_acid / alt / triglycerides, or one the butler
  wrote, e.g. ldl) gets value / unit / tone / trend / good_direction / spark / date from the lab table at read time (list_status,
  records/lab_history.py; latest reading of any source, home meters included) plus lab_key (the Today page opens the lab trend sheet).
  The butler's own note is kept after the lab note. Lab writes / deletes also call sync_labs so the auto lab rows appear / disappear at once.
"""
import json
import re
import statistics
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from .. import sparky_client
from ..records import lab_history
from ..rules import params as rule_params
from ..rules.nutrition import red_meat_estimate

GROUPS = ["身体", "目标", "健康", "训练", "作息", "饮食", "其他"]
TONES = ("good", "warn", "bad", "neutral")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
STALE_MINUTES = 30
SPARK_MAX = 30
GOAL_KEY = "goal_weight"            # Goal weight written by the butler (value is a number, unit kg); auto to_goal uses it
AUTO_KEYS = ("weight", "to_goal", "recovery", "sleep_debt", "bedtime", "sleep_hours", "uric_acid", "alt",
             "triglycerides", "training_week", "red_meat_week", "added_sugar_today", "added_sugar_7d")
LAB_ITEMS = {"uric_acid": ("尿酸", 10), "alt": ("ALT", 20), "triglycerides": ("甘油三酯", 30)}
FIELDS = ("key", "label", "value", "unit", "tone", "trend", "good_direction", "group", "spark", "note",
          "updated_at", "source")


# ---------------------------------------------------------------- read
def list_status(conn: psycopg.Connection, user_id: int) -> list[dict]:
    rows = conn.execute(f"""SELECT key, label, value, unit, tone, trend, good_direction, "group", spark, note,
                                   updated_at, source, sort FROM assistant_status WHERE user_id = %s""",
                        (user_id,)).fetchall()
    order = {g: i for i, g in enumerate(GROUPS)}
    rows.sort(key=lambda r: (order.get(r["group"], len(GROUPS)), r["sort"], r["key"]))
    labs = lab_history.cards(conn, user_id, [r["key"] for r in rows]) if rows else {}
    out = []
    for r in rows:
        item = {k: r[k] for k in FIELDS}
        lab = labs.get(r["key"])
        if lab is None and r["source"] == "auto" and r["key"] in LAB_ITEMS:
            continue  # its readings were deleted: the card goes too (sync_labs removes the row)
        if lab is not None:
            own = r["note"] if r["source"] != "auto" else None
            item.update({k: lab[k] for k in ("value", "unit", "tone", "trend", "good_direction", "spark")},
                        note=f"{lab['note']} · {own}" if own else lab["note"], lab_key=lab["lab_key"])
        out.append(item)
    return out


# ---------------------------------------------------------------- write (validation)
def _check(item: dict) -> dict:
    key = item.get("key") or ""
    if not KEY_RE.match(key):
        raise ValueError(f"key {key!r} 只能是小写字母、数字、下划线")
    for f, n in (("label", 6), ("value", 12), ("note", 20), ("unit", 8)):
        v = item.get(f)
        if f in ("label", "value") and not (v or "").strip():
            raise ValueError(f"{key}：{f} 不能为空")
        if v is not None and len(v.strip()) > n:
            raise ValueError(f"{key}：{f}「{v}」超过 {n} 个字，状态项要很短（细节写进档案卡）")
    if item.get("tone") not in TONES:
        raise ValueError(f"{key}：tone 只能是 {' / '.join(TONES)}")
    if item.get("trend") not in (None, "up", "down", "flat"):
        raise ValueError(f"{key}：trend 只能是 up / down / flat / null")
    if item.get("good_direction") not in (None, "up", "down"):
        raise ValueError(f"{key}：good_direction 只能是 up / down / null")
    if item.get("group") not in GROUPS:
        raise ValueError(f"{key}：group 只能是 {'、'.join(GROUPS)}")
    spark = item.get("spark")
    if spark is not None:
        if not isinstance(spark, list) or len(spark) > SPARK_MAX or \
                any(x is not None and not isinstance(x, (int, float)) for x in spark):
            raise ValueError(f"{key}：spark 是最多 {SPARK_MAX} 个数字（旧 → 新，可以有 null）")
    return {**item, "label": item["label"].strip(), "value": item["value"].strip(),
            "note": (item.get("note") or "").strip() or None, "unit": (item.get("unit") or "").strip() or None}


def _upsert(conn, user_id: int, item: dict, source: str, sort: int) -> None:
    conn.execute(
        """INSERT INTO assistant_status (user_id, key, label, value, unit, tone, trend, good_direction, "group",
                                         spark, note, sort, source)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (user_id, key) DO UPDATE SET label = EXCLUDED.label, value = EXCLUDED.value,
             unit = EXCLUDED.unit, tone = EXCLUDED.tone, trend = EXCLUDED.trend,
             good_direction = EXCLUDED.good_direction, "group" = EXCLUDED."group", spark = EXCLUDED.spark,
             note = EXCLUDED.note, sort = EXCLUDED.sort, source = EXCLUDED.source, updated_at = now()""",
        (user_id, item["key"], item["label"], item["value"], item.get("unit"), item["tone"], item.get("trend"),
         item.get("good_direction"), item["group"], Jsonb(item["spark"]) if item.get("spark") is not None else None,
         item.get("note"), sort, source))


def set_items(conn: psycopg.Connection, user_id: int, items: list[dict], remove_keys: list[str],
              source: str = "assistant") -> dict:
    """Butler writes status items (whole-item replace). auto keys are computed by the backend and cannot be written; remove_keys drops stale items (auto ones cannot be dropped either)."""
    bad = [i.get("key") for i in items if i.get("key") in AUTO_KEYS] + [k for k in remove_keys if k in AUTO_KEYS]
    if bad:
        raise ValueError(f"{', '.join(bad)} 是后端自动算的状态项，不能手动改（数据更新后会自己变）")
    checked = [_check(i) for i in items]
    existing = {r["key"]: r["sort"] for r in conn.execute(
        "SELECT key, sort FROM assistant_status WHERE user_id = %s", (user_id,))}
    for n, item in enumerate(checked):
        _upsert(conn, user_id, item, source, existing.get(item["key"], 200 + n))
    removed = [r["key"] for r in conn.execute(
        "DELETE FROM assistant_status WHERE user_id = %s AND key = ANY(%s) RETURNING key", (user_id, remove_keys))]
    return {"written": [i["key"] for i in checked], "removed": removed}


# ---------------------------------------------------------------- auto computation
def _trend(cur, prev, eps: float) -> str | None:
    if cur is None or prev is None:
        return None
    return "flat" if abs(cur - prev) < eps else ("up" if cur > prev else "down")


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def _r(x, nd=1):
    return None if x is None else round(x, nd)


def _clock(hours_from_midnight: float) -> str:
    """Hours relative to midnight (23:30 = -0.5, 02:40 = 2.67) -> "凌晨 2:40" / "23:30"."""
    m = round(hours_from_midnight * 60) % (24 * 60)
    h, mm = divmod(m, 60)
    if h < 5:
        return f"凌晨 {h}:{mm:02d}"
    if h < 12:
        return f"早上 {h}:{mm:02d}"
    if h < 18:
        return f"下午 {h}:{mm:02d}"
    return f"{h}:{mm:02d}"


def _weights(conn, user_id: int, start: date, end: date) -> dict[date, float]:
    from ..web.views import weights  # Same basis as the Today page and the rules engine (SparkyFitness manual weigh-ins take priority)
    return weights(conn, user_id, start, end)


def calc_weight(conn, user_id: int, today: date) -> list[tuple[dict, int]]:
    w = _weights(conn, user_id, today - timedelta(days=59), today)
    if not w:
        return []
    days = sorted(w)
    last_day, last = days[-1], w[days[-1]]
    cur7 = _mean(w[d] for d in days if d > today - timedelta(days=7))
    prev7 = _mean(w[d] for d in days if today - timedelta(days=14) < d <= today - timedelta(days=7))
    change = None if cur7 is None or prev7 is None else cur7 - prev7
    if (today - last_day).days > 7:
        tone, note = "neutral", f"上次称 {last_day:%m-%d}"
    elif change is None:
        tone, note = "neutral", "周均变化待数据"
    else:
        tone = "good" if -1.0 <= change <= -0.3 else "warn" if change < -1.0 or change > 0.3 else "neutral"
        note = f"周均 {change:+.1f} kg" + ("（偏快）" if change < -1.0 else "")
    items = [({"key": "weight", "label": "体重", "value": f"{last:.1f}", "unit": "kg", "tone": tone,
               "trend": _trend(cur7, prev7, 0.2), "good_direction": "down", "group": "身体",
               "spark": [_r(w[d]) for d in days][-SPARK_MAX:], "note": note}, 10)]
    goal = conn.execute("SELECT value FROM assistant_status WHERE user_id = %s AND key = %s",
                        (user_id, GOAL_KEY)).fetchone()
    m = re.search(r"\d+(\.\d+)?", (goal or {}).get("value") or "")
    if m:
        target = float(m.group())
        diff = last - target
        if diff <= 0:
            items.append(({"key": "to_goal", "label": "距目标", "value": "已达到", "unit": None, "tone": "good",
                           "trend": None, "good_direction": "down", "group": "目标", "spark": None,
                           "note": f"目标 {target:g} kg"}, 20))
        else:
            eta = f"按现速约 {diff / -change:.0f} 周" if change and change < -0.1 else f"目标 {target:g} kg"
            items.append(({"key": "to_goal", "label": "距目标", "value": f"{diff:.1f}", "unit": "kg",
                           "tone": "neutral", "trend": _trend(cur7, prev7, 0.2), "good_direction": "down",
                           "group": "目标", "spark": None, "note": eta}, 20))
    return items


def calc_scores(conn, user_id: int, today: date) -> list[tuple[dict, int]]:
    rows = {r["date"]: r for r in conn.execute(
        """SELECT date, recovery, sleep_debt_min, sleep_actual_min FROM daily_scores
           WHERE user_id = %s AND date BETWEEN %s AND %s""", (user_id, today - timedelta(days=13), today))}
    days = [today - timedelta(days=13 - i) for i in range(14)]
    get = lambda f, d: (rows.get(d) or {}).get(f)
    out = []
    rec = [get("recovery", d) for d in days]
    cur, prev = _mean(rec[7:]), _mean(rec[:7])
    if cur is not None:
        latest = next(((d, v) for d, v in zip(reversed(days), reversed(rec)) if v is not None))
        tone = "good" if cur >= 67 else "neutral" if cur >= 50 else "warn" if cur >= 34 else "bad"
        out.append(({"key": "recovery", "label": "恢复", "value": f"{cur:.0f}", "unit": None, "tone": tone,
                     "trend": _trend(cur, prev, 3), "good_direction": "up", "group": "身体", "spark": rec,
                     "note": f"7天均 · {latest[0]:%m-%d} {latest[1]}"}, 20))
    debt = [get("sleep_debt_min", d) for d in days]
    last_debt = next((v for v in reversed(debt) if v is not None), None)
    if last_debt is not None:
        h = last_debt / 60
        out.append(({"key": "sleep_debt", "label": "睡眠债", "value": f"{h:.1f}", "unit": "h",
                     "tone": "good" if last_debt <= 60 else "warn" if last_debt <= 120 else "bad",
                     "trend": _trend(_mean(debt[7:]), _mean(debt[:7]), 15), "good_direction": "down", "group": "作息",
                     "spark": [_r(v / 60) if v is not None else None for v in debt], "note": None}, 10))
    actual = [get("sleep_actual_min", d) for d in days]
    cur_s = _mean(actual[7:])
    if cur_s is not None:
        h = cur_s / 60
        out.append(({"key": "sleep_hours", "label": "睡眠时长", "value": f"{h:.1f}", "unit": "h",
                     "tone": "good" if h >= 7 else "warn" if h >= 6 else "bad",
                     "trend": _trend(cur_s, _mean(actual[:7]), 20), "good_direction": "up", "group": "作息",
                     "spark": [_r(v / 60) if v is not None else None for v in actual], "note": "近 7 晚平均"}, 30))
    return out


def calc_bedtime(conn, user_id: int, today: date) -> list[tuple[dict, int]]:
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"])
    rows = conn.execute(
        """SELECT DISTINCT ON (wake_date) wake_date, start_at FROM sleep_sessions
           WHERE user_id = %s AND is_main AND wake_date BETWEEN %s AND %s
           ORDER BY wake_date, asleep_min DESC NULLS LAST""", (user_id, today - timedelta(days=13), today)).fetchall()
    if not rows:
        return []

    def rel(ts):  # Hours relative to midnight of the night of falling asleep: after 18:00 counts as negative (23:30 = -0.5)
        t = ts.astimezone(tz)
        h = t.hour + t.minute / 60
        return h - 24 if h >= 18 else h
    by_day = {r["wake_date"]: rel(r["start_at"]) for r in rows}
    days = [today - timedelta(days=13 - i) for i in range(14)]
    vals = [by_day.get(d) for d in days]
    cur, prev = _mean(vals[7:]), _mean(vals[:7])
    if cur is None:
        return []
    tone = "good" if cur <= 0.5 else "warn" if cur <= 2 else "bad"
    return [({"key": "bedtime", "label": "入睡时间", "value": _clock(cur), "unit": None, "tone": tone,
              "trend": _trend(cur, prev, 1 / 3), "good_direction": "down", "group": "作息",
              "spark": [_r(v, 2) for v in vals], "note": "近 7 晚平均"}, 20)]


def calc_labs(conn, user_id: int, today: date | None) -> list[tuple[dict, int]]:
    """Latest reading of any source (lab report or home meter), trend of the last two, spark of past readings (records/lab_history.py)."""
    out = []
    for key, c in lab_history.cards(conn, user_id, list(LAB_ITEMS)).items():
        label, sort = LAB_ITEMS[key]
        out.append(({"key": key, "label": label, "group": "健康",
                     **{k: c[k] for k in ("value", "unit", "tone", "trend", "good_direction", "spark", "note")}}, sort))
    return out


def calc_training(conn, user_id: int, today: date) -> list[tuple[dict, int]]:
    client = sparky_client.client_for_user(conn, user_id)
    sessions = sparky_client.training_log(client, today - timedelta(days=41), today)
    days = {date.fromisoformat(s["date"][:10]) for s in sessions}
    counts = [sum(1 for d in days if today - timedelta(days=7 * (k + 1)) < d <= today - timedelta(days=7 * k))
              for k in reversed(range(6))]  # 6 rolling 7-day windows, old -> new
    n = counts[-1]
    return [({"key": "training_week", "label": "本周训练", "value": str(n), "unit": "次", "tone":
              "good" if n >= 4 else "warn" if n >= 2 else "bad", "trend": _trend(n, counts[-2], 1),
              "good_direction": "up", "group": "训练", "spark": counts, "note": "近 7 天 · 计划 5 次"}, 10)]


def red_meat_grams(conn, user_id: int, today: date) -> float | None:
    """Red meat grams over the last 7 days (including today), same basis as the rules engine (frequency_caps): journal red_meat_g first, otherwise estimated from food log names
    (names containing "红肉 Ng" use the labelled grams, which is how meal-template and recipe entries are written). Returns None if nothing was logged in these 7 days."""
    start = today - timedelta(days=6)
    client = sparky_client.client_for_user(conn, user_id)
    food = sparky_client.food_entries(client, start, today)
    journal = {r["date"]: r["value"] for r in conn.execute(
        "SELECT date, value FROM journal_entries WHERE user_id = %s AND key = 'red_meat_g' AND date BETWEEN %s AND %s",
        (user_id, start, today))}
    logged = {f["date"] for f in food} | {d.isoformat() for d in journal}
    if not logged:
        return None
    grams = 0.0
    for k in range(7):
        d = start + timedelta(days=k)
        if d in journal:
            grams += journal[d]
            continue
        for f in (x for x in food if x["date"] == d.isoformat()):
            if (est := red_meat_estimate(f.get("food") or "", f.get("quantity"), f.get("unit"))) is not None:
                grams += est[1]
    return grams


def calc_red_meat(conn, user_id: int, today: date) -> list[tuple[dict, int]]:
    grams = red_meat_grams(conn, user_id, today)
    if grams is None:
        return []
    start = today - timedelta(days=6)
    days_logged = len({f["date"] for f in sparky_client.food_entries(sparky_client.client_for_user(conn, user_id),
                                                                     start, today)})
    cap = rule_params.RED_MEAT_G_PER_WEEK
    tone = "good" if grams < cap * rule_params.RED_MEAT_WARN_FRACTION else "warn" if grams <= cap else "bad"
    return [({"key": "red_meat_week", "label": "红肉7天", "value": f"{grams:.0f}", "unit": "g", "tone": tone,
              "trend": None, "good_direction": "down", "group": "饮食", "spark": None,
              "note": f"上限 {cap} g · 记了 {days_logged} 天"}, 10)]


def _sugar_tone(g: float, cap: float) -> str:
    return "good" if g <= cap else "warn" if g <= max(rule_params.ADDED_SUGAR_WARN_G, cap) else "bad"


def calc_sugar(conn, user_id: int, today: date) -> list[tuple[dict, int]]:
    """「今日添加糖」 and 「7 天平均」 (added sugar). Only entries that know their added sugar count; a logged day where
    none do shows 未知 (unknown is not 0). The 7-day average is over days with at least one known entry."""
    from ..goals import nutrition as goals
    from ..nutrition import daily, extras
    start = today - timedelta(days=6)
    rows = daily.entries(conn, user_id, start, today)
    if not rows:
        return []
    cap = goals.resolve(conn, user_id, today)["added_sugar_max_g"]
    days = [start + timedelta(days=k) for k in range(7)]
    per_day = [extras.day_totals([r for r in rows if r["date"] == d.isoformat()]) for d in days]
    spark = [t["added_sugar_g"] for t in per_day]
    out = []
    t = per_day[-1]
    if t["known"]["of"]:
        g = t["added_sugar_g"]
        out.append(({"key": "added_sugar_today", "label": "今日添加糖", "value": "未知" if g is None else f"{g:.0f}",
                     "unit": None if g is None else "g", "tone": "neutral" if g is None else _sugar_tone(g, cap),
                     "trend": None, "good_direction": "down", "group": "饮食", "spark": None,
                     "note": f"上限 {cap:g} g · 有数 {t['known']['added_sugar_g']}/{t['known']['of']} 条"}, 20))
    known_days = [v for v in spark if v is not None]
    if known_days:
        avg = sum(known_days) / len(known_days)
        out.append(({"key": "added_sugar_7d", "label": "7 天平均", "value": f"{avg:.0f}", "unit": "g",
                     "tone": _sugar_tone(avg, cap), "trend": None, "good_direction": "down", "group": "饮食",
                     "spark": spark, "note": f"添加糖 · {len(known_days)} 天有数"}, 30))
    return out


CALCULATORS = [  # (keys owned, function)
    (("weight", "to_goal"), calc_weight),
    (("recovery", "sleep_debt", "sleep_hours"), calc_scores),
    (("bedtime",), calc_bedtime),
    (tuple(LAB_ITEMS), calc_labs),
    (("training_week",), calc_training),
    (("red_meat_week",), calc_red_meat),
    (("added_sugar_today", "added_sugar_7d"), calc_sugar),
]


def _apply_auto(conn, user_id: int, keys: tuple, fn, today: date | None) -> tuple[list[str], list[str]]:
    """Run one calculator and write its items (source auto); keys it owns but did not yield are removed. One savepoint."""
    with conn.transaction():
        items = fn(conn, user_id, today)
        for item, sort in items:
            _upsert(conn, user_id, _check(item), "auto", sort)
        got = {i["key"] for i, _ in items}
        removed = [r["key"] for r in conn.execute(
            "DELETE FROM assistant_status WHERE user_id = %s AND source = 'auto' AND key = ANY(%s) RETURNING key",
            (user_id, [k for k in keys if k not in got]))]
    return sorted(got), removed


def sync_labs(conn: psycopg.Connection, user_id: int) -> None:
    """Right after a lab write / delete: recompute the auto lab cards (uric acid / ALT / triglycerides) instead of waiting for the
    30-minute refresh. Never fails the lab write itself."""
    try:
        _apply_auto(conn, user_id, tuple(LAB_ITEMS), calc_labs, None)
    except Exception:  # noqa: BLE001  the card catches up at the next refresh; list_status reads labs live anyway
        pass


def refresh_auto(conn: psycopg.Connection, user_id: int, today: date | None = None) -> dict:
    """Recompute all auto items. If a computation fails (e.g. SparkyFitness unreachable) keep the old value of the keys it owns; if it yields no data, drop them."""
    if today is None:
        tz = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"]
        today = datetime.now(ZoneInfo(tz)).date()
    written, removed, errors = [], [], {}
    for keys, fn in CALCULATORS:
        try:
            got, gone = _apply_auto(conn, user_id, keys, fn, today)
            written += got
            removed += gone
        except Exception as e:  # noqa: BLE001  data read failed: keep the old value, report the reason
            errors[fn.__name__] = f"{type(e).__name__}: {e}"[:200]
    conn.execute("""INSERT INTO sync_state (user_id, source, last_synced_at, cursor) VALUES (%s, 'status_auto', now(), %s)
                    ON CONFLICT (user_id, source) DO UPDATE SET last_synced_at = now(), cursor = EXCLUDED.cursor""",
                 (user_id, json.dumps(errors, ensure_ascii=False) if errors else None))
    return {"written": written, "removed": removed, "errors": errors}


def refresh_if_stale(conn: psycopg.Connection, user_id: int) -> None:
    row = conn.execute("SELECT last_synced_at FROM sync_state WHERE user_id = %s AND source = 'status_auto'",
                       (user_id,)).fetchone()
    if row is None or row["last_synced_at"] < datetime.now(timezone.utc) - timedelta(minutes=STALE_MINUTES):
        refresh_auto(conn, user_id)


# ---------------------------------------------------------------- English for the Today page (issue #42)
# Items stay stored in Chinese (the AI reads them, explain/personal.py matches Chinese keywords); the page's read path
# (memory_api GET /memory) calls localize() in an English request. Only the texts the backend itself writes are translated:
# groups (fixed list), the auto items' label / value / unit / note, the goal weight item (profile/store.py), the lab part of a
# lab-linked card. Labels / values / notes the AI wrote stay as written (a label equal to a fixed one is translated too).
GROUP_EN = {"身体": "Body", "目标": "Goals", "健康": "Health", "训练": "Training", "作息": "Sleep & routine",
            "饮食": "Nutrition", "其他": "Other"}
LABEL_EN = {"体重": "Weight", "距目标": "To goal", "恢复": "Recovery", "睡眠债": "Sleep debt", "睡眠时长": "Sleep duration",
            "入睡时间": "Bedtime", "尿酸": "Uric acid", "ALT": "ALT", "甘油三酯": "Triglycerides", "本周训练": "Workouts",
            "红肉7天": "Red meat 7d", "今日添加糖": "Added sugar today", "7 天平均": "7-day average",
            "目标体重": "Goal weight", "当前阶段": "Current phase"}
VALUE_EN = {"已达到": "Reached", "未知": "Unknown", "减脂增肌": "Recomp"}
UNIT_EN = {"次": "workouts"}
_LAB_WORD_EN = {"偏高": "High", "偏低": "Low", "正常": "Normal"}
_LAB_SOURCE_EN = {"化验": "lab", "家测": "home"}
_CLOCK = re.compile(r"(凌晨|早上|下午) (\d{1,2}):(\d{2})")
_NOTE_EN = [(re.compile(zh), en) for zh, en in (
    (r"上次称 (\d\d-\d\d)", r"Last weigh-in \1"),
    (r"周均变化待数据", "Weekly change: needs more data"),
    (r"周均 ([+-]?[\d.]+) kg（偏快）", r"Weekly avg \1 kg (fast)"),
    (r"周均 ([+-]?[\d.]+) kg", r"Weekly avg \1 kg"),
    (r"目标 ([\d.]+) kg", r"Goal \1 kg"),
    (r"按现速约 (\d+) 周", r"About \1 wk at this pace"),
    (r"7天均 · (\d\d-\d\d) (\S+)", r"7-day avg · \1: \2"),
    (r"近 7 晚平均", "Last 7 nights avg"),
    (r"近 7 天 · 计划 (\d+) 次", r"Last 7 days · plan \1"),
    (r"上限 (\S+) g · 记了 (\d+) 天", r"Cap \1 g · \2 days logged"),
    (r"上限 (\S+) g · 有数 (\d+)/(\d+) 条", r"Cap \1 g · known \2/\3"),
    (r"添加糖 · (\d+) 天有数", r"Added sugar · \1 days known"),
    (r"示例", "Example"),
)]
_LAB_NOTE = re.compile(r"(\d\d-\d\d) · (\S+)(?: · 上限 (\S+))?")


def _note_en(note: str | None) -> str | None:
    if not note:
        return note
    for rx, en in _NOTE_EN:
        if m := rx.fullmatch(note):
            return m.expand(en)
    return note


def _lab_note_en(note: str) -> str:
    """'10-18 · 家测 · 上限 420' (records/lab_history.card) -> '10-18 · home · max 420'; a butler note after it is kept."""
    m = _LAB_NOTE.match(note)
    if not m:
        return note
    out = f"{m[1]} · {_LAB_SOURCE_EN.get(m[2], m[2])}" + (f" · max {m[3]}" if m[3] else "")
    return out + note[m.end():]


def _value_en(v: str | None) -> str | None:
    if not v:
        return v
    if v in VALUE_EN:
        return VALUE_EN[v]
    if m := _CLOCK.fullmatch(v):  # bedtime "凌晨 2:40" -> "02:40" (the hour is already 0–23)
        return f"{int(m[2]):02d}:{m[3]}"
    word, _, num = v.partition(" ")  # lab "偏高 489"
    return f"{_LAB_WORD_EN[word]} {num}" if word in _LAB_WORD_EN and num else v


def localize(items: list[dict]) -> list[dict]:
    """list_status() items in the request language (no-op in Chinese)."""
    from ..i18n import is_en
    if not is_en():
        return items
    out = []
    for it in items:
        it = dict(it, group=GROUP_EN.get(it.get("group"), it.get("group")), label=LABEL_EN.get(it["label"], it["label"]))
        backend = it.get("source") in ("auto", "user", "seed")
        if backend or it.get("lab_key"):
            it["value"] = _value_en(it["value"])
            it["unit"] = UNIT_EN.get(it.get("unit"), it.get("unit"))
        if it.get("lab_key") and it.get("note"):
            it["note"] = _lab_note_en(it["note"])
        elif backend:
            it["note"] = _note_en(it.get("note"))
        out.append(it)
    return out


# ---------------------------------------------------------------- initial version
SEED_ITEMS = [  # Example initial judgement items for `memory-init --from-md` (maintained by the AI coach afterwards)
    {"key": GOAL_KEY, "label": "目标体重", "value": "75", "unit": "kg", "tone": "neutral", "trend": None,
     "good_direction": None, "group": "目标", "spark": None, "note": "示例"},
    {"key": "phase", "label": "当前阶段", "value": "减脂增肌", "unit": None, "tone": "neutral", "trend": None,
     "good_direction": None, "group": "训练", "spark": None, "note": "示例"},
]


def init_status(conn: psycopg.Connection, user_id: int, with_seed: bool) -> dict:
    """Initial status items: judgement items (with_seed: only fill missing ones) + one pass of the auto items."""
    created = []
    if with_seed:
        for n, item in enumerate(SEED_ITEMS):
            if conn.execute("SELECT 1 FROM assistant_status WHERE user_id = %s AND key = %s",
                            (user_id, item["key"])).fetchone() is None:
                _upsert(conn, user_id, _check(item), "seed", 5 + n)
                created.append(item["key"])
    return {"seeded": created, **refresh_auto(conn, user_id)}

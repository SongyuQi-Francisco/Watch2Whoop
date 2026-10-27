"""Long-term memory against the user's own data (issue #113, phase 3): does what the AI wrote down still match the records?

Fixed rules, no AI and no network (database only). Four kinds of claim, as the issue decided:
- training habits (workouts + logged strength sessions): "5 days a week", "basketball every Wednesday";
- supplements (supplements table): "takes creatine";
- body weight (weigh-ins, goal weight): "weighs 72 kg", "goal 65 kg";
- injuries (body events): "right shoulder hurts" while the event is marked better, or the other way round.
A claim is only judged when the text states it as a present habit or fact (no negation, wish or history around it) and the data
is enough to say something (memory_params TRAIN_* / WEIGHT_*); otherwise there is no verdict. The words, windows and tolerances
are in memory_params.py. This module only reads; memory_review.py decides what happens with a verdict (a match confirms a note,
a mismatch is handed to the AI to put to the user, nothing is changed by itself).
Works on the caller's own user_id only; the caller (memory_review) never runs it for someone viewing through a share, because the
verdicts quote the owner's weight, training, supplements and injuries.
"""
import re
from datetime import date, timedelta
from functools import cached_property

import psycopg

from . import memory_params as mp

CLAUSE_SPLIT = re.compile(r"[。；;\n!?！？]+")
NUM = r"(\d+(?:\.\d+)?|[一二两三四五六七])"
CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}
RANGE = NUM + r"(?:\s*(?:-|–|—|~|～|到|至)\s*" + NUM + r")?"
# "每周练 5 天" / "5 天每周" / "5 days a week"; the filler between "每周" and the number may hold no numeral ("每周三打篮球" is a weekday)
FREQ_ZH = re.compile(r"(?:每周|一周|每星期|一星期|每礼拜|每个星期)([^\d一二两三四五六七，,。；;]{0,6}?)" + RANGE + r"\s*(?:天|次|回)")
FREQ_ZH_SUFFIX = re.compile(RANGE + r"\s*(?:天|次|回)\s*(?:/|每|一)\s*(?:个)?(?:周|星期|礼拜)")
FREQ_EN = re.compile(r"(\d+)(?:\s*[-–]\s*(\d+))?\s*(?:days?|times?|sessions?|workouts?)\s*(?:a|per|/|each)\s*week", re.I)
WEEKDAY = re.compile(r"(上|下|这|本)?(?:周|星期|礼拜)([一二三四五六日天末])")
WEEKDAY_NUM = {"一": (0,), "二": (1,), "三": (2,), "四": (3,), "五": (4,), "六": (5,), "日": (6,), "天": (6,), "末": (5, 6)}
WEIGHT_RE = re.compile(r"(\d+(?:\.\d+)?)(?:\s*(?:-|–|—|~|～|到|至)\s*(\d+(?:\.\d+)?))?\s*(kg|公斤|千克|斤)", re.I)
DATE_RE = re.compile(r"\d{4}\s*[-/年.]\s*\d{1,2}|\d{1,2}\s*月")   # "2026-09-07", "10 月中": a statement about a past moment
TARGET_WORDS = ("目标", "减到", "减至", "瘦到", "增到", "增至", "goal", "target", "aim")
CURRENT_WORDS = ("体重", "body weight", "bodyweight", "weigh")
SPORT_ZH = {"basketball": "篮球", "swimming": "游泳", "running": "跑步", "soccer": "足球", "tennis": "网球", "badminton": "羽毛球",
            "cycling": "骑行", "yoga": "瑜伽", "hiking": "爬坡 / 徒步", "strength": "力量训练", None: "训练"}


class Verdict:
    """One judged claim: match / mismatch, what the text says and what the data says (both short, Chinese, for the AI)."""
    __slots__ = ("verdict", "kind", "claim", "data")

    def __init__(self, verdict: str, kind: str, claim: str, data: str):
        self.verdict, self.kind, self.claim, self.data = verdict, kind, claim, data


def _cut(s: str) -> str:
    return s if len(s) <= mp.DATA_CLAIM_CHARS else s[:mp.DATA_CLAIM_CHARS - 1] + "…"


def _range(a: str, b: str | None) -> tuple[float, float]:
    lo = float(CN_NUM.get(a, a))
    hi = float(CN_NUM.get(b, b)) if b else lo
    return (lo, hi) if lo <= hi else (hi, lo)


def _fmt(lo: float, hi: float, unit: str = "") -> str:
    return f"{lo:g}{unit}" if lo == hi else f"{lo:g}–{hi:g}{unit}"


def _has(text: str, words) -> bool:
    return any(w in text for w in words)


def _kw_at(text: str, word: str) -> int:
    """First position of a word in the lower-cased text (ASCII words at a word start), -1 = absent."""
    if word.isascii():
        m = re.search(r"\b" + re.escape(word), text)
        return m.start() if m else -1
    return text.find(word)


def _negated(text: str, start: int, end: int) -> bool:
    """Negation just before or after the matched words ("不吃肌酸", "肌酸停了")."""
    return _has(text[max(0, start - 4):start], mp.NEGATION_BEFORE) or _has(text[end:end + 4], mp.NEGATION_AFTER)


def _plain_claim(clause: str) -> bool:
    """The sentence states something as true now: no wish ("想每周练 5 天"), no history ("以前每周练 5 天")."""
    low = clause.lower()
    return not (_has(low, mp.WISH_WORDS) or _has(low, mp.PAST_WORDS))


class Context:
    """The user's data the claims are compared with, each part read only when a claim needs it."""

    def __init__(self, conn: psycopg.Connection, user_id: int, today: date):
        self.conn, self.uid, self.today = conn, user_id, today

    @cached_property
    def training(self) -> dict[date, set[str]]:
        """date -> workout types of that day (a logged strength session counts as 'strength_training'), last TRAIN_LOOKBACK_DAYS."""
        start = self.today - timedelta(days=mp.TRAIN_LOOKBACK_DAYS - 1)
        out: dict[date, set[str]] = {}
        for r in self.conn.execute(
                """SELECT local_date, type FROM workouts WHERE user_id = %s AND local_date BETWEEN %s AND %s
                   AND dup_of IS NULL AND excluded IS NULL AND duration_min >= %s AND type <> 'stretching'""",
                (self.uid, start, self.today, mp.TRAIN_MIN_MINUTES)):
            out.setdefault(r["local_date"], set()).add(r["type"])
        for r in self.conn.execute("SELECT DISTINCT date FROM logged_workouts WHERE user_id = %s AND date BETWEEN %s AND %s",
                                   (self.uid, start, self.today)):
            out.setdefault(r["date"], set()).add("strength_training")
        return out

    @cached_property
    def enough_training(self) -> bool:
        return len(self.training) >= mp.TRAIN_MIN_DAYS_EVER

    @cached_property
    def window(self) -> list[date]:
        return [self.today - timedelta(days=i) for i in range(mp.TRAIN_WINDOW_DAYS)]

    @cached_property
    def weight_now(self) -> float | None:
        """kg: the mean of the last WEIGHT_AVG_DAYS days, else the latest reading of the last WEIGHT_RECENT_DAYS; None = none."""
        rows = self.conn.execute(
            """SELECT date, kg FROM (
                 SELECT date, value AS kg, 1 AS pri FROM daily_metrics
                   WHERE user_id = %(u)s AND metric = 'body_mass' AND unit = 'kg' AND date BETWEEN %(s)s AND %(e)s
                 UNION ALL
                 SELECT date, weight_kg, 0 FROM body_measurements
                   WHERE user_id = %(u)s AND weight_kg IS NOT NULL AND date BETWEEN %(s)s AND %(e)s) x
               ORDER BY date DESC, pri""",
            {"u": self.uid, "s": self.today - timedelta(days=mp.WEIGHT_RECENT_DAYS - 1), "e": self.today}).fetchall()
        by_day: dict[date, float] = {}
        for r in rows:                 # a manual weigh-in (pri 0) beats the device's reading of the same day
            by_day.setdefault(r["date"], float(r["kg"]))
        if not by_day:
            return None
        week = [v for d, v in by_day.items() if d > self.today - timedelta(days=mp.WEIGHT_AVG_DAYS)]
        return sum(week) / len(week) if week else by_day[max(by_day)]

    @cached_property
    def goal_kg(self) -> float | None:
        r = self.conn.execute("SELECT value FROM assistant_status WHERE user_id = %s AND key = 'goal_weight'", (self.uid,)).fetchone()
        try:
            return float(r["value"]) if r else None
        except (TypeError, ValueError):
            return None

    @cached_property
    def supplements(self) -> list[dict]:
        return self.conn.execute("SELECT name, ingredients, status, stopped_on FROM supplements WHERE user_id = %s ORDER BY id",
                                 (self.uid,)).fetchall()

    @cached_property
    def events(self) -> list[dict]:
        return self.conn.execute(
            """SELECT occurred_at::date AS day, body_part, title, resolved_on FROM events
               WHERE user_id = %s AND occurred_at >= %s AND (category IS NULL OR category = ANY(%s)) ORDER BY occurred_at""",
            (self.uid, self.today - timedelta(days=mp.INJURY_LOOKBACK_DAYS), list(mp.EVENT_CATEGORIES))).fetchall()


# ---------------------------------------------------------------- training
def _sports_in(text: str) -> dict[str, int]:
    low = text.lower()
    return {sid: pos for sid, (words, _) in mp.SPORT_TYPES.items() for w in words if (pos := _kw_at(low, w)) >= 0}


def _days_with(ctx: Context, sport: str | None, days) -> int:
    types = set(mp.SPORT_TYPES[sport][1]) if sport else None
    return sum(1 for d in days if ctx.training.get(d) and (types is None or ctx.training[d] & types))


def _training(ctx: Context, clause: str) -> list[Verdict]:
    if not _plain_claim(clause) or not ctx.enough_training:
        return []
    low, out, blank = clause.lower(), [], clause
    for rx in (FREQ_ZH, FREQ_ZH_SUFFIX, FREQ_EN):
        for m in rx.finditer(clause):
            blank = blank[:m.start()] + " " * (m.end() - m.start()) + blank[m.end():]
            filler = (m.group(1) or "") if rx is FREQ_ZH else clause[max(0, m.start() - 4):m.start()]
            sports = _sports_in(filler + " " + clause[m.end():m.end() + 3])      # "每周游泳 2 次", "每周 2 次游泳"
            if not (_has(low, mp.TRAIN_WORDS) or sports) or len(sports) > 1 or _negated(clause, m.start(), m.end()):
                continue
            sport = next(iter(sports), None)
            a, b = (m.group(2), m.group(3)) if rx is FREQ_ZH else (m.group(1), m.group(2))
            lo, hi = _range(a, b)
            avg = _days_with(ctx, sport, ctx.window) / (mp.TRAIN_WINDOW_DAYS / 7)
            ok = lo - mp.TRAIN_FREQ_TOLERANCE <= avg <= hi + mp.TRAIN_FREQ_TOLERANCE
            out.append(Verdict("match" if ok else "mismatch", "training_frequency", f"每周{SPORT_ZH[sport]} {_fmt(lo, hi)} 天",
                               f"近 {mp.TRAIN_WINDOW_DAYS // 7} 周平均每周 {avg:.1f} 天"))
    # a weekday + one sport as a habit: "every Wednesday basketball"
    sports = _sports_in(blank)
    if len(sports) == 1 and _has(low, mp.HABIT_WORDS):
        sport, pos = next(iter(sports.items()))
        days = {d for m in WEEKDAY.finditer(blank) if not m.group(1) for d in WEEKDAY_NUM[m.group(2)]}
        if days and not _negated(blank, pos, pos + 2):
            dates = [d for d in ctx.window if d.weekday() in days]
            if len(dates) >= mp.WEEKDAY_MIN_OCCURRENCES:
                hits = _days_with(ctx, sport, dates)
                what = "每周" + "".join("一二三四五六日"[i] for i in sorted(days)) + SPORT_ZH[sport]
                data = f"近 {mp.TRAIN_WINDOW_DAYS // 7} 周这几天有 {hits} 次"
                if hits <= mp.WEEKDAY_MISMATCH_MAX_HITS:
                    out.append(Verdict("mismatch", "training_day", what, data))
                elif hits >= mp.WEEKDAY_VERIFY_HITS:
                    out.append(Verdict("match", "training_day", what, data))
    return out


# ---------------------------------------------------------------- weight
def _weight(ctx: Context, clause: str) -> list[Verdict]:
    if not _plain_claim(clause):
        return []
    low = clause.lower().replace("目标体重", "目标").replace("goal weight", "goal").replace("target weight", "target")
    now: list[tuple[float, float]] = []
    goals: list[tuple[float, float]] = []
    for m in WEIGHT_RE.finditer(low):
        lo, hi = _range(m.group(1), m.group(2))
        if m.group(3) == "斤":
            lo, hi = lo / 2, hi / 2
        last = {kind: max((low.rfind(w, 0, m.start()) for w in words), default=-1)   # the nearest word before the number decides
                for kind, words in (("goal", TARGET_WORDS), ("now", CURRENT_WORDS))}
        kind = max(last, key=last.get) if max(last.values()) >= 0 else None
        if kind == "goal":
            goals.append((lo, hi))
        elif kind == "now" and not DATE_RE.search(low):
            now.append((lo, hi))
    out = []
    if now and (kg := ctx.weight_now) is not None:
        tol = mp.WEIGHT_TOLERANCE_KG
        ok = any(lo - tol <= kg <= hi + tol for lo, hi in now)
        out.append(Verdict("match" if ok else "mismatch", "weight", "体重 " + "、".join(_fmt(lo, hi, " kg") for lo, hi in now),
                           f"近期体重 {kg:.1f} kg"))
    if goals and ctx.goal_kg is not None:
        tol = mp.GOAL_TOLERANCE_KG
        ok = any(lo - tol <= ctx.goal_kg <= hi + tol for lo, hi in goals)
        out.append(Verdict("match" if ok else "mismatch", "goal_weight", "目标 " + "、".join(_fmt(lo, hi, " kg") for lo, hi in goals),
                           f"目标体重设置是 {ctx.goal_kg:g} kg"))
    return out


# ---------------------------------------------------------------- supplements
def _supplements(ctx: Context, clause: str, topic: str | None) -> list[Verdict]:
    low = clause.lower()
    if not _plain_claim(clause) or not (topic == "supplements" or _has(low, mp.SUPPLEMENT_CONTEXT)) or not ctx.supplements:
        return []
    out = []
    for name, words in mp.SUPPLEMENT_WORDS.items():
        hits = [(p, w) for w in words if (p := _kw_at(low, w)) >= 0]
        if not hits or _negated(low, hits[0][0], hits[0][0] + len(hits[0][1])):
            continue
        rows = [r for r in ctx.supplements if any(_kw_at(f"{r['name']} {r['ingredients'] or ''}".lower(), w) >= 0 for w in words)]
        taking = [r for r in rows if r["status"] in mp.SUPPLEMENT_ACTIVE and (r["stopped_on"] is None or r["stopped_on"] > ctx.today)]
        if taking:
            out.append(Verdict("match", "supplement", f"在吃{name}", f"补剂表：{taking[0]['name']} {taking[0]['status']}"))
        elif rows:
            r = rows[0]
            out.append(Verdict("mismatch", "supplement", f"在吃{name}",
                               f"补剂表里「{r['name']}」是 {r['status']}" + (f"，{r['stopped_on']} 停" if r["stopped_on"] else "")))
        else:
            out.append(Verdict("mismatch", "supplement", f"在吃{name}", f"补剂表里没有{name}"))
    return out


# ---------------------------------------------------------------- injuries
def _injuries(ctx: Context, clause: str) -> list[Verdict]:
    low = clause.lower()
    if _has(low, mp.RULE_WORDS) or not _plain_claim(clause):
        return []
    recovered = _has(low, mp.RECOVERED_WORDS)
    if not (recovered or _has(low, mp.INJURY_WORDS)):
        return []
    out = []
    for part, words in mp.BODY_PARTS.items():
        if not any(_kw_at(low, w) >= 0 for w in words):
            continue
        rows = [e for e in ctx.events if any(_kw_at(f"{e['body_part'] or ''} {e['title']}".lower(), w) >= 0 for w in words)]
        if not rows:
            continue
        open_ = [e for e in rows if e["resolved_on"] is None]
        if recovered:
            out.append(Verdict("mismatch" if open_ else "match", "injury", f"{part}已经好了",
                               f"事件「{open_[-1]['title']}」还没标好转" if open_ else "相关事件都标了好转"))
        elif open_:
            out.append(Verdict("match", "injury", f"{part}有伤病", f"事件「{open_[-1]['title']}」还在"))
        elif (last := max(e["resolved_on"] for e in rows)) <= ctx.today - timedelta(days=mp.INJURY_RESOLVED_GRACE_DAYS):
            out.append(Verdict("mismatch", "injury", f"{part}有伤病", f"事件「{rows[-1]['title']}」{last} 已标好转"))
    return out


# ---------------------------------------------------------------- one entry
def check_text(ctx: Context, text: str, topic: str | None) -> dict | None:
    """Verdict on one entry: {"verdict": match|mismatch, "kind", "claim", "data"} or None (nothing checkable, or no data to judge
    with). Any mismatch makes the entry a mismatch (the first two are named); a match needs at least one match and no mismatch."""
    found: list[Verdict] = []
    for clause in CLAUSE_SPLIT.split(text or ""):
        if clause.strip():
            found += _training(ctx, clause) + _weight(ctx, clause) + _supplements(ctx, clause, topic) + _injuries(ctx, clause)
    bad = [v for v in found if v.verdict == "mismatch"]
    use = bad[:2] or found[:1]
    if not use:
        return None
    return {"verdict": "mismatch" if bad else "match", "kind": use[0].kind,
            "claim": _cut("；".join(v.claim for v in use)), "data": _cut("；".join(v.data for v in use))}


def check_entries(conn: psycopg.Connection, user_id: int, entries: list[tuple[str, str, str | None]], today: date) -> dict[str, dict]:
    """entries = [(ref, text, topic)] -> {ref: verdict} for the ones that have one."""
    ctx = Context(conn, user_id, today)
    out = {}
    for ref, text, topic in entries:
        if v := check_text(ctx, text, topic):
            out[ref] = v
    return out

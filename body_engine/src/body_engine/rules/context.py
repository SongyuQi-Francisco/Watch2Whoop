"""Data the rules use (Ctx) and its loading (load).

Data sources (system design section 5; each kind of data has exactly one source):
- SparkyFitness (the evaluated user's own read-only key): training logs, exercise library, workout templates, weekly training plan, food logs, weight;
- nutrition goals: goals/nutrition.py (Body Engine versions; SparkyFitness only before the first version), food extras (added sugar);
- Body Engine: exercise_meta (banned / caution), daily_scores, journal_entries, daily_metrics.body_mass (weight supplement).
Sources that cannot be read are recorded in errors, and rules depending on them return error (earlier reminders are not deleted); SparkyFitness not connected is recorded in missing (insufficient data).
"""
from dataclasses import dataclass, field
from typing import Any
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from .. import sparky_client
from ..goals import nutrition as nutrition_goals, store as goal_store
from ..nutrition import daily as nutrition_daily
from . import params as P

TRAINING_DAYS = P.PROGRESSION_LOOKBACK_DAYS
FOOD_DAYS = 14
WEIGHT_DAYS = 21
SCORE_DAYS = 7
JOURNAL_DAYS = 21


@dataclass
class Ctx:
    user_id: int
    day: date
    tz: ZoneInfo = ZoneInfo("Australia/Sydney")
    sessions: list[dict] | None = None             # training logs [day - 120, day], in the sparky_client.training_log format
    exercises: dict[str, dict] | None = None       # SparkyFitness exercise library: id -> exercise
    presets: dict[int, dict] | None = None         # workout templates: id -> template
    plans: list[dict] | None = None                # weekly training plan
    meta: list[dict] = field(default_factory=list)  # all exercise_meta rows
    food: list[dict] | None = None                 # food logs [day - 13, day] with sugar fields (nutrition/daily.entries)
    goals: list[dict] | None = None                # nutrition goals in effect for 7 days from day (goals/nutrition.resolver)
    weights: list[dict] | None = None              # [{date, weight_kg, source}]，[day − 20, day]
    scores: dict[date, dict] = field(default_factory=dict)
    journal: dict[tuple[date, str], float] = field(default_factory=dict)
    sleep_goal: dict | None = None                 # row from goals.store.get_goal (including plan_rows)
    sleep_nights: list[dict] = field(default_factory=list)  # main sleep of each night before tonight (goals.sleep.nights_from_sessions)
    sleep_duration_min: int | None = None          # target sleep duration (from the goal, or the score's sleep need)
    overrides: dict[date, dict] = field(default_factory=dict)  # training_day_overrides rows by date (training/day_overrides.py)
    diet: Any = None                               # this user's diet constraints in effect (nutrition/constraints.Constraints)
    water: dict[date, int] | None = None           # water drunk per day [day - 1, day] in ml (water/store.py); None = feature off
    water_goal: int | None = None                  # the day's water goal in ml
    now: datetime | None = None                    # evaluation time (tests set it); None = the real clock
    errors: dict[str, str] = field(default_factory=dict)    # source -> reason the read failed
    missing: dict[str, str] = field(default_factory=dict)   # source -> reason it is not connected / not available

    def now_local(self) -> datetime:
        return (self.now or datetime.now(self.tz)).astimezone(self.tz)

    # ---------- Exercises ----------
    @property
    def meta_by_app_id(self) -> dict[str, dict]:
        return {m["app_exercise_id"]: m for m in self.meta if m.get("app_exercise_id")}

    @property
    def meta_by_ref(self) -> dict[str, dict]:
        return {m["exercise_ref"]: m for m in self.meta}

    def exercise_name(self, exercise_id: str, fallback: str | None = None) -> str:
        ex = (self.exercises or {}).get(exercise_id)
        return (ex or {}).get("name") or fallback or exercise_id

    def status_of(self, exercise_id: str) -> tuple[str, dict | None, str]:
        """(active / caution / banned, exercise_meta row, basis). exercise_meta takes priority; if there is no match, check the SparkyFitness description marker."""
        m = self.meta_by_app_id.get(exercise_id)
        if m is not None:
            return m["status"], m, "exercise_meta"
        desc = ((self.exercises or {}).get(exercise_id) or {}).get("description") or ""
        for marker, status in P.SPARKY_STATUS_MARKERS.items():
            if desc.startswith(marker):
                return status, None, "SparkyFitness 动作描述"
        return "active", None, "默认"

    def muscles_of(self, exercise_id: str, fallback: list | None = None) -> set[str]:
        ex = (self.exercises or {}).get(exercise_id) or {}
        return {m.lower() for m in (ex.get("primary_muscles") or fallback or [])}

    def is_leg_exercise(self, exercise_id: str, category: str | None = None, fallback_muscles=None) -> bool:
        ex = (self.exercises or {}).get(exercise_id) or {}
        if (ex.get("category") or category) not in (None, "strength"):
            return False
        return bool(self.muscles_of(exercise_id, fallback_muscles) & P.LEG_MUSCLES)

    # ---------- Training plan ----------
    def active_plan(self, day: date | None = None) -> dict | None:
        day = day or self.day
        for p in self.plans or []:
            start = date.fromisoformat(p["start_date"]) if p.get("start_date") else date.min
            end = date.fromisoformat(p["end_date"]) if p.get("end_date") else date.max
            if p.get("is_active") and p.get("schedule_type") in (None, "weekly") and start <= day <= end:
                return p
        return None

    def planned_for(self, day: date | None = None) -> list[dict]:
        """Contents of that day's plan: [{kind: preset, preset} | {kind: exercise, exercise_id, name} | {kind: label, name}] (in order).
        A day change (training_day_overrides) replaces the weekday's content with other weekdays' content / free labels / rest."""
        day = day or self.day
        ov = self.overrides.get(day)
        if ov is None:
            return self.weekday_items(day.isoweekday(), day)
        out = []
        for ref in ov.get("sessions") or []:
            if ref.get("dow"):
                out += self.weekday_items(int(ref["dow"]), day)
            elif ref.get("label"):
                out.append({"kind": "label", "name": ref["label"]})
        return out

    def weekday_items(self, iso_dow: int, day: date | None = None) -> list[dict]:
        """The active plan's content of one weekday (1 = Monday ... 7 = Sunday), ignoring day changes."""
        plan = self.active_plan(day or self.day)
        if plan is None:
            return []
        dow = iso_dow % 7   # SparkyFitness: 0 = Sunday
        out = []
        for a in sorted((a for a in plan["assignments"] if a["day_of_week"] == dow), key=lambda a: a["sort_order"]):
            if a.get("workout_preset_id") is not None:
                preset = (self.presets or {}).get(a["workout_preset_id"]) or {
                    "id": a["workout_preset_id"], "name": a.get("workout_preset_name"), "exercises": []}
                out.append({"kind": "preset", "preset": preset})
            elif a.get("exercise_id"):
                out.append({"kind": "exercise", "exercise_id": a["exercise_id"],
                            "name": self.exercise_name(a["exercise_id"], a.get("exercise_name"))})
        return out

    def planned_exercises(self, day: date | None = None) -> list[dict]:
        """Every exercise in that day's plan: {exercise_id, name, preset}."""
        out = []
        for item in self.planned_for(day):
            if item["kind"] == "preset":
                out += [{"exercise_id": x["exercise_id"], "name": self.exercise_name(x["exercise_id"], x.get("name")),
                         "category": x.get("category"), "preset": item["preset"]["name"]}
                        for x in item["preset"]["exercises"]]
            elif item["kind"] == "exercise":
                out.append({"exercise_id": item["exercise_id"], "name": item["name"], "category": None, "preset": None})
        return out

    def plan_prescriptions(self) -> dict[str, dict]:
        """Exercises of all templates in the current plan -> rep range and increment (if one exercise appears in several templates, take the first)."""
        plan = self.active_plan()
        if plan is None:
            return {}
        out: dict[str, dict] = {}
        preset_ids = [a["workout_preset_id"] for a in sorted(plan["assignments"], key=lambda a: (a["day_of_week"], a["sort_order"]))
                      if a.get("workout_preset_id") is not None]
        for pid in dict.fromkeys(preset_ids):
            preset = (self.presets or {}).get(pid)
            for x in (preset or {}).get("exercises", []):
                if x["exercise_id"] in out:
                    continue
                working = [s for s in x["sets"] if (s.get("type") or "").lower() not in P.WARMUP_SET_TYPES]
                lows = [s["reps"] for s in working if s.get("reps")]
                out[x["exercise_id"]] = {
                    "exercise_id": x["exercise_id"], "name": self.exercise_name(x["exercise_id"], x.get("name")),
                    "preset": preset["name"], "progression_mode": x.get("progression_mode"),
                    "rep_low": min(lows) if lows else None, "rep_high": x.get("rep_goal"),
                    "increment_kg": x.get("increment_value"), "planned_sets": len(working),
                    "planned_weight_kg": next((s["weight_kg"] for s in working if s.get("weight_kg")), None),
                    "category": x.get("category")}
        return out

    def sessions_on(self, day: date) -> list[dict]:
        return [s for s in self.sessions or [] if s["date"][:10] == day.isoformat()]


def _today(tz: ZoneInfo) -> date:
    return datetime.now(tz).date()


def load_overrides(conn: psycopg.Connection, user_id: int, start: date, end: date) -> dict[date, dict]:
    """Day changes of the weekly plan (training_day_overrides) in [start, end], by date."""
    return {r["date"]: r for r in conn.execute(
        "SELECT date, sessions, source, reason, updated_at FROM training_day_overrides WHERE user_id = %s AND date BETWEEN %s AND %s",
        (user_id, start, end))}


def load(conn: psycopg.Connection, user_id: int, day: date | None = None,
         client: sparky_client.SparkyClient | None = None) -> Ctx:
    user = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    if user is None:
        raise ValueError(f"没有用户 {user_id}")
    tz = ZoneInfo(user["timezone"] or "Australia/Sydney")
    day = day or _today(tz)
    ctx = Ctx(user_id=user_id, day=day, tz=tz)

    # ---- Body Engine ----
    ctx.meta = conn.execute("SELECT * FROM exercise_meta WHERE user_id = %s", (user_id,)).fetchall()
    ctx.scores = {r["date"]: r for r in conn.execute(
        """SELECT date, recovery, sleep_score, sleep_debt_min, sleep_actual_min, sleep_need_min, strain, flags
           FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s""",
        (user_id, day - timedelta(days=SCORE_DAYS), day))}
    ctx.journal = {(r["date"], r["key"]): r["value"] for r in conn.execute(
        "SELECT date, key, value FROM journal_entries WHERE user_id = %s AND date BETWEEN %s AND %s",
        (user_id, day - timedelta(days=JOURNAL_DAYS), day))}
    ctx.sleep_goal = goal_store.get_goal(conn, user_id)
    ctx.sleep_nights = goal_store.load_nights(conn, user_id, tz, day)   # night of day = tonight (the evening before going to sleep)
    ctx.sleep_duration_min = goal_store.duration(conn, user_id, ctx.sleep_goal, day)
    ctx.overrides = load_overrides(conn, user_id, day - timedelta(days=7), day + timedelta(days=7))
    from ..nutrition import constraints as diet_constraints
    ctx.diet = diet_constraints.for_user(conn, user_id, day)
    from .. import water
    if water.enabled(conn, user_id):
        from ..water import store as water_store
        ctx.water = water_store.totals(conn, user_id, day - timedelta(days=1), day)
        ctx.water_goal = water_store.goal(conn, user_id, day)["ml"]
    body_mass = conn.execute(
        """SELECT date, value FROM daily_metrics WHERE user_id = %s AND metric = 'body_mass' AND unit = 'kg'
           AND date BETWEEN %s AND %s""", (user_id, day - timedelta(days=WEIGHT_DAYS - 1), day)).fetchall()

    # ---- SparkyFitness ----
    if client is None:
        try:
            client = sparky_client.reader_for_user(conn, user_id)
        except sparky_client.SparkyError as e:
            ctx.missing["sparky"] = str(e)
    if client is not None:
        goal_of = nutrition_goals.resolver(conn, user_id, client)
        fetches = {
            "exercises": lambda: {e["id"]: e for e in sparky_client.exercises(client)},
            "presets": lambda: sparky_client.workout_presets(client),
            "plans": lambda: sparky_client.workout_plans(client),
            "sessions": lambda: sparky_client.training_log(client, day - timedelta(days=TRAINING_DAYS), day),
            "food": lambda: nutrition_daily.entries(conn, user_id, day - timedelta(days=FOOD_DAYS - 1), day, client),
            "goals": lambda: [goal_of(day + timedelta(days=k)) for k in range(7)],
            "weights": lambda: sparky_client.weight(client, day - timedelta(days=WEIGHT_DAYS - 1), day),
        }
        for name, fetch in fetches.items():
            try:
                setattr(ctx, name, fetch())
            except Exception as e:  # network, permissions, format changes: affect only the rules that depend on it
                ctx.errors[name] = f"{type(e).__name__}: {e}"

    # Weight: SparkyFitness manual weigh-ins take priority; for days without one, use Body Engine's body_mass (synced from watch / scale)
    weights = {r["date"]: {"date": r["date"], "weight_kg": r["weight_kg"], "source": "sparkyfitness"}
               for r in ctx.weights or [] if r.get("weight_kg") is not None}
    for r in body_mass:
        weights.setdefault(r["date"].isoformat(), {"date": r["date"].isoformat(), "weight_kg": r["value"],
                                                   "source": "body_engine"})
    if ctx.weights is not None or body_mass:
        ctx.weights = sorted(weights.values(), key=lambda r: r["date"])
    return ctx


# ================================================================ plain data (2.0.0 package M6, docs/ios-native-2.0.md)
# The phone runs the rules itself (packages/openbodyscore-js src/36_rules.js). Its input is plain JSON in two layers:
# - `data`: the Ctx itself (to_data / from_data), what OBS.rules.evaluate reads;
# - `raw`: the sources before the three pure steps at the end of `load` (assemble): goal versions + SparkyFitness
#   fallback goals -> `goals`, SparkyFitness weigh-ins + body_mass -> `weights`, sleep sessions + the newest sleep need
#   -> `sleep_nights`, `sleep_duration_min`. The phone fills `raw` from its own tables (scores, sleep, body mass,
#   journal) and the server's raw inputs document (GET /v1/users/me/rules/inputs = `inputs`);
#   evaluate_data(assemble(load_raw(...))) equals evaluate_ctx(load(...)) (tests/test_native_rules.py).
SCORE_FIELDS = ("recovery", "sleep_score", "sleep_debt_min", "sleep_actual_min", "sleep_need_min", "strain", "flags")
META_FIELDS = ("exercise_ref", "app_exercise_id", "status", "aliases", "reason", "cues")
GOAL_FIELDS = ("status", "start_date", "pace_min_per_step", "step_days", "start_bedtime_off", "target_bedtime",
               "target_wake", "target_duration_min")
DIET_FIELDS = ("kind", "target", "value", "basis", "match")
VERSION_FIELDS = ("id", "effective_from", "name", "training", "rest", "training_weekdays")
# keys of `raw` the server's inputs document carries; LOCAL_KEYS come from the phone's own tables
INPUT_KEYS = ("sessions", "exercises", "presets", "plans", "meta", "food", "nutrition_versions", "sparky_goals",
              "sparky_weights", "sleep_goal", "overrides", "diet", "water", "water_goal", "errors", "missing")
LOCAL_KEYS = ("scores", "journal", "body_mass", "sleep_sessions", "sleep_need")


def _iso(v):
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def _plain(x):
    """JSON-safe copy: dates as ISO strings, tuples as lists; numbers untouched."""
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return _iso(x)


def goal_data(goal: dict | None) -> dict | None:
    """The bedtime goal row as data (without plan_rows: from_data rebuilds them with goals/store.with_plan)."""
    return None if goal is None else {k: _iso(goal.get(k)) for k in GOAL_FIELDS}


def to_data(ctx: Ctx) -> dict:
    """The Ctx as plain JSON data (OBS.rules.evaluate's input); evaluate_ctx(from_data(to_data(ctx))) == evaluate_ctx(ctx)."""
    return {
        "day": ctx.day.isoformat(), "tz": ctx.tz.key, "now": ctx.now.isoformat() if ctx.now else None,
        "sessions": _plain(ctx.sessions), "exercises": _plain(ctx.exercises),
        "presets": None if ctx.presets is None else _plain(list(ctx.presets.values())), "plans": _plain(ctx.plans),
        "meta": [{k: _plain(m.get(k)) for k in META_FIELDS} for m in ctx.meta], "food": _plain(ctx.food),
        "goals": _plain(ctx.goals), "weights": _plain(ctx.weights),
        "scores": {d.isoformat(): {k: _plain(r.get(k)) for k in SCORE_FIELDS} for d, r in sorted(ctx.scores.items())},
        "journal": [[d.isoformat(), k, v] for (d, k), v in sorted(ctx.journal.items())],
        "sleep_goal": goal_data(ctx.sleep_goal), "sleep_nights": _plain(ctx.sleep_nights),
        "sleep_duration_min": ctx.sleep_duration_min,
        "overrides": {d.isoformat(): {"sessions": _plain(ov.get("sessions") or [])} for d, ov in sorted(ctx.overrides.items())},
        "diet": None if ctx.diet is None else [{k: _plain(r.get(k)) for k in DIET_FIELDS} for r in ctx.diet.rows],
        "water": None if ctx.water is None else {d.isoformat(): v for d, v in sorted(ctx.water.items())},
        "water_goal": ctx.water_goal, "errors": dict(ctx.errors), "missing": dict(ctx.missing)}


def from_data(data: dict) -> Ctx:
    """Inverse of to_data (pure)."""
    from ..goals import store as gs
    from ..nutrition.constraints import Constraints
    D = date.fromisoformat
    goal = data.get("sleep_goal")
    if goal is not None:
        goal = gs.with_plan({**goal, "start_date": D(goal["start_date"])})
    presets = data.get("presets")
    return Ctx(
        user_id=0, day=D(data["day"]), tz=ZoneInfo(data["tz"]),
        now=datetime.fromisoformat(data["now"]) if data.get("now") else None,
        sessions=data.get("sessions"), exercises=data.get("exercises"),
        presets=None if presets is None else {p["id"]: p for p in presets}, plans=data.get("plans"),
        meta=list(data.get("meta") or []), food=data.get("food"), goals=data.get("goals"), weights=data.get("weights"),
        scores={D(d): {**r, "date": D(d)} for d, r in (data.get("scores") or {}).items()},
        journal={(D(d), k): v for d, k, v in data.get("journal") or []},
        sleep_goal=goal, sleep_nights=[{**n, "night": D(n["night"])} for n in data.get("sleep_nights") or []],
        sleep_duration_min=data.get("sleep_duration_min"),
        overrides={D(d): ov for d, ov in (data.get("overrides") or {}).items()},
        diet=None if data.get("diet") is None else Constraints(list(data["diet"])),
        water=None if data.get("water") is None else {D(d): v for d, v in data["water"].items()},
        water_goal=data.get("water_goal"), errors=dict(data.get("errors") or {}), missing=dict(data.get("missing") or {}))


def merge_weights(sparky: list[dict] | None, body_mass: list[dict]) -> list[dict] | None:
    """The end of `load`: SparkyFitness weigh-ins first, Body Engine's body_mass ({date, value}) on days without one."""
    weights = {r["date"]: {"date": r["date"], "weight_kg": r["weight_kg"], "source": "sparkyfitness"}
               for r in sparky or [] if r.get("weight_kg") is not None}
    for r in body_mass:
        d = _iso(r["date"])
        weights.setdefault(d, {"date": d, "weight_kg": r["value"], "source": "body_engine"})
    if sparky is not None or body_mass:
        return sorted(weights.values(), key=lambda r: r["date"])
    return None


def assemble(raw: dict) -> dict:
    """raw -> data (pure): the goals of the 7 days from `day`, the merged weights, the nights before `day` and tonight's
    sleep duration, as `load` builds them."""
    from ..goals import nutrition as ng, sleep as S, store as gs
    D = date.fromisoformat
    day, tz = D(raw["day"]), ZoneInfo(raw["tz"])
    goals = None
    if raw.get("nutrition_versions") is not None:
        versions = [{**v, "effective_from": D(v["effective_from"])} for v in raw["nutrition_versions"]]
        fallback = raw.get("sparky_goals") or {}
        goals = _plain([ng.goal_for(d, versions, fallback.get(d.isoformat()), en=False)
                        for d in (day + timedelta(days=k) for k in range(7))])
    lo, hi = (day - timedelta(days=gs.HISTORY_DAYS)).isoformat(), (day + timedelta(days=1)).isoformat()
    sessions = [{**s, "start_at": datetime.fromisoformat(s["start_at"]) if s.get("start_at") else None,
                 "end_at": datetime.fromisoformat(s["end_at"]) if s.get("end_at") else None}
                for s in raw.get("sleep_sessions") or [] if lo <= s["wake_date"] <= hi]
    nights = [n for n in S.nights_from_sessions(sessions, tz) if n["night"] < day]
    goal = raw.get("sleep_goal")
    keep = ("day", "tz", "now", "sessions", "exercises", "presets", "plans", "meta", "food", "sleep_goal", "diet",
            "water", "water_goal", "errors", "missing")
    return {**{k: raw.get(k) for k in keep}, "goals": goals,
            "weights": merge_weights(raw.get("sparky_weights"), raw.get("body_mass") or []),
            "scores": {r["date"]: {k: r.get(k) for k in SCORE_FIELDS} for r in raw.get("scores") or []},
            "journal": sorted([r["date"], r["key"], r["value"]] for r in raw.get("journal") or []),
            "sleep_nights": _plain(nights), "sleep_duration_min": gs.duration_from(goal, raw.get("sleep_need")),
            "overrides": {r["date"]: {"sessions": r.get("sessions") or []} for r in raw.get("overrides") or []}}


def load_raw(conn: psycopg.Connection, user_id: int, day: date | None = None,
             client: sparky_client.SparkyClient | None = None, now: datetime | None = None) -> dict:
    """`load`'s reads as `raw` (see above): the same tables and SparkyFitness calls, nothing assembled."""
    from .. import queries
    user = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    if user is None:
        raise ValueError(f"没有用户 {user_id}")
    tz = ZoneInfo(user["timezone"] or "Australia/Sydney")
    day = day or _today(tz)
    raw = {"day": day.isoformat(), "tz": tz.key, "now": now.isoformat() if now else None,
           **inputs(conn, user_id, day, client)}
    raw["scores"] = [{"date": r["date"].isoformat(), **{k: r[k] for k in SCORE_FIELDS}} for r in conn.execute(
        """SELECT date, recovery, sleep_score, sleep_debt_min, sleep_actual_min, sleep_need_min, strain, flags
           FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s ORDER BY date""",
        (user_id, day - timedelta(days=SCORE_DAYS), day))]
    raw["journal"] = [{"date": r["date"].isoformat(), "key": r["key"], "value": r["value"]} for r in conn.execute(
        "SELECT date, key, value FROM journal_entries WHERE user_id = %s AND date BETWEEN %s AND %s ORDER BY date, key",
        (user_id, day - timedelta(days=JOURNAL_DAYS), day))]
    raw["body_mass"] = [{"date": r["date"].isoformat(), "value": r["value"]} for r in conn.execute(
        """SELECT date, value FROM daily_metrics WHERE user_id = %s AND metric = 'body_mass' AND unit = 'kg'
           AND date BETWEEN %s AND %s ORDER BY date""", (user_id, day - timedelta(days=WEIGHT_DAYS - 1), day))]
    raw["sleep_sessions"] = [
        {"wake_date": s["wake_date"].isoformat(), "start_at": _iso(s["start_at"]), "end_at": _iso(s["end_at"]),
         "asleep_min": s["asleep_min"], "is_main": s["is_main"]}
        for s in queries.sleep_sessions(conn, user_id, day - timedelta(days=goal_store.HISTORY_DAYS), day + timedelta(days=1))]
    raw["sleep_need"] = goal_store.latest_need(conn, user_id, day)
    return raw


def inputs(conn: psycopg.Connection, user_id: int, day: date, client=None) -> dict:
    """The part of `raw` the phone does not hold (INPUT_KEYS): exercise rules, the SparkyFitness library / templates /
    weekly plan / training log / food log / weigh-ins as read, the goal versions and the SparkyFitness goals of the days
    before the first version, the bedtime goal row, plan day changes, diet constraints, water. Raw rows only; read
    failures in `errors`, a missing SparkyFitness link in `missing`, exactly as `load` records them."""
    out: dict = {k: None for k in INPUT_KEYS}
    out.update(errors={}, missing={})
    out["meta"] = [{k: _plain(m.get(k)) for k in META_FIELDS}
                   for m in conn.execute("SELECT * FROM exercise_meta WHERE user_id = %s", (user_id,)).fetchall()]
    out["sleep_goal"] = goal_data(goal_store.get_goal(conn, user_id))
    out["overrides"] = [{"date": d.isoformat(), "sessions": _plain(ov.get("sessions") or [])} for d, ov in
                        sorted(load_overrides(conn, user_id, day - timedelta(days=7), day + timedelta(days=7)).items())]
    from ..nutrition import constraints as diet_constraints
    out["diet"] = [{k: _plain(r.get(k)) for k in DIET_FIELDS} for r in diet_constraints.for_user(conn, user_id, day).rows]
    from .. import water
    if water.enabled(conn, user_id):
        from ..water import store as water_store
        out["water"] = {d.isoformat(): v for d, v in sorted(water_store.totals(conn, user_id, day - timedelta(days=1), day).items())}
        out["water_goal"] = water_store.goal(conn, user_id, day)["ml"]
    if client is None:
        try:
            client = sparky_client.reader_for_user(conn, user_id)
        except sparky_client.SparkyError as e:
            out["missing"]["sparky"] = str(e)
    if client is None:
        return out
    versions = nutrition_goals.versions(conn, user_id)

    def goals():
        fallback, ok = {}, True
        for d in (day + timedelta(days=k) for k in range(7)):
            if nutrition_goals.version_for(versions, d) is not None:
                continue
            g = None
            if ok:
                try:
                    g = sparky_client.goal_for_date(client, d)
                except Exception:  # noqa: BLE001  as goals/nutrition.resolver: no fallback goal, no retry for later dates
                    ok = False
            fallback[d.isoformat()] = _plain(g)
        out["sparky_goals"] = fallback
        return [{k: _plain(r.get(k)) for k in VERSION_FIELDS} for r in versions]

    fetches = {   # load's order; the names in `errors` are the Ctx's (goals, weights)
        "exercises": lambda: _plain({e["id"]: e for e in sparky_client.exercises(client)}),
        "presets": lambda: _plain(list(sparky_client.workout_presets(client).values())),
        "plans": lambda: _plain(sparky_client.workout_plans(client)),
        "sessions": lambda: _plain(sparky_client.training_log(client, day - timedelta(days=TRAINING_DAYS), day)),
        "food": lambda: _plain(nutrition_daily.entries(conn, user_id, day - timedelta(days=FOOD_DAYS - 1), day, client)),
        "goals": goals,
        "weights": lambda: _plain(sparky_client.weight(client, day - timedelta(days=WEIGHT_DAYS - 1), day)),
    }
    key = {"goals": "nutrition_versions", "weights": "sparky_weights"}
    for name, fetch in fetches.items():
        try:
            out[key.get(name, name)] = fetch()
        except Exception as e:  # noqa: BLE001  network, permissions, format changes: only the rules that depend on it
            out["errors"][name] = f"{type(e).__name__}: {e}"
    return out

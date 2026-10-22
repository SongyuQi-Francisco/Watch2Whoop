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
            client = sparky_client.client_for_user(conn, user_id)
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

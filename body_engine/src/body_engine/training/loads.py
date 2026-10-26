"""Recommended working weights for planned exercises (issue #41; parameters and sources in training/params.py).

For each exercise and rep range, in this order:
1. a progression reminder of the rule engine (rules/progression.py: double progression) -> its next weight, unchanged;
2. the user's own SparkyFitness history (working sets, last LOAD_LOOKBACK_DAYS days):
   same rep range as the latest session -> keep that weight ("同上次"); a different range -> convert through an
   Epley e1RM that counts RIR as reps left ("换算");
3. no history -> a conservative start from body weight, movement pattern and equipment ("估算").
Bodyweight / band / timed exercises get no number. Dumbbells and kettlebells are per single implement, unilateral exercises per side,
as the progression rule logs them. The result is advice for the AI and the Today page; nothing is written.
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from statistics import median

import psycopg

from .. import auth, sparky_client
from ..rules import params as RP
from . import catalog, params as P

BASIS_LABEL = {"progression": "按进阶", "history": "同上次", "e1rm": "换算", "estimate": "估算", "bodyweight": "自重"}
_HISTORY_CACHE: dict[tuple, tuple[float, list[dict]]] = {}


# ---------------------------------------------------------------- formulas
def e1rm(weight_kg: float, reps: int, rir: float | None = None) -> float:
    """Epley estimated 1RM, counting reps in reserve as reps the set could still have done."""
    rtf = reps + (P.LOAD_RIR_ASSUMED if rir is None else max(0.0, float(rir)))
    return weight_kg * (1 + rtf / P.EPLEY_DIVISOR)


def weight_for(one_rm: float, reps: int, rir: float = P.LOAD_TARGET_RIR) -> float:
    """Inverse Epley: the weight for `reps` with `rir` reps left."""
    return one_rm / (1 + (reps + rir) / P.EPLEY_DIVISOR)


def round_down(w: float, step: float) -> float:
    step = step or P.LOAD_STEP_KG
    return round(int(w / step + 1e-9) * step, 2)


def target_reps(rep_low: int | None, rep_high: int | None) -> int | None:
    if rep_low and rep_high:
        return round((rep_low + rep_high) / 2)
    return rep_low or rep_high


def _equip(ex: dict) -> str | None:
    eqs = ex.get("equipment") or []
    return next((e for e in eqs if e in P.STARTER_EQUIPMENT_FACTOR), eqs[0] if eqs else None)


def _min_load(ex: dict, w: float) -> float:
    return max(w, P.LOAD_MIN_KG.get(_equip(ex) or "", 0.0))


def _per(ex: dict) -> str | None:
    if ex.get("per_side"):
        return "每侧"
    return "单只" if _equip(ex) in ("dumbbell", "kettlebell") else None


# ---------------------------------------------------------------- inputs
def working_sets(sets: list[dict]) -> list[dict]:
    return [s for s in sets if (s.get("type") or "").lower() not in RP.WARMUP_SET_TYPES
            and s.get("reps") and (s.get("weight_kg") or 0) > 0]


def load_history(conn: psycopg.Connection, owner_id: int, day: date) -> tuple[list[dict], str | None, bool]:
    """The owner's SparkyFitness sessions plus imported ones (Hevy, hevy_import.py) in the lookback window, the reason when
    SparkyFitness is linked but could not be read, and whether there is any history source at all (none = no history, no error).
    Only the SparkyFitness part is cached (muscle_load._sparky shares the cache and adds the imported sets itself, so they
    must never be in it); the imported part is a local query, read every time."""
    from . import hevy_import
    start = day - timedelta(days=P.LOAD_LOOKBACK_DAYS)
    imported = hevy_import.imported_sessions(conn, owner_id, start, day)
    key = (conn.info.dbname, owner_id, start, day)
    hit = _HISTORY_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < P.LOAD_HISTORY_TTL_S:
        return sorted(hit[1] + imported, key=lambda s: s["date"]), None, True
    try:
        client = sparky_client.client_for_user(conn, owner_id)
    except (sparky_client.SparkyError, ValueError):
        return imported, None, bool(imported)
    try:
        sessions = sparky_client.training_log(client, start, day)
    except sparky_client.SparkyError as e:
        return imported, str(e), True
    _HISTORY_CACHE[key] = (time.monotonic(), sessions)
    return sorted(sessions + imported, key=lambda s: s["date"]), None, True


def clear_history_cache(owner_id: int | None = None) -> None:
    for k in [k for k in _HISTORY_CACHE if owner_id is None or k[1] == owner_id]:
        _HISTORY_CACHE.pop(k, None)


_SHARED_MAPS: dict[int, tuple] = {}


def _shared_maps(shared: list[dict]) -> tuple[dict[str, dict], dict[str, str]]:
    """slug -> row and normalized name -> slug of a cached list of shared catalog rows (catalog._shared_rows); curated rows
    first so a shared alias resolves to the curated exercise."""
    hit = _SHARED_MAPS.get(id(shared))
    if hit is None or hit[0] is not shared:
        if len(_SHARED_MAPS) > 8:
            _SHARED_MAPS.clear()
        by_name: dict[str, str] = {}
        for r in shared:
            if r.get("source") == "curated":
                for n in r["_names"]:
                    by_name.setdefault(n, r["slug"])
        for r in shared:
            for n in r["_names"]:
                by_name.setdefault(n, r["slug"])
        hit = _SHARED_MAPS[id(shared)] = (shared, {r["slug"]: r for r in shared}, by_name)
    return hit[1], hit[2]


class Index:
    """Catalog lookups for logged exercises: normalized name / alias -> slug, SparkyFitness id -> slug (exercise_meta).
    The owner's own custom exercises (custom.py) are in it too, after the shared rows (their names never clash: custom.add refuses)."""

    def __init__(self, conn: psycopg.Connection, owner_id: int):
        shared = catalog._shared_rows(conn, owner_id)
        own = catalog._custom_rows(conn, owner_id)
        rows_by_slug, by_name = _shared_maps(shared)   # the ~6,300 shared rows: built once per catalog version, not per request
        if own:   # custom rows come after every shared row, so they only add names no shared row has
            rows_by_slug, by_name = dict(rows_by_slug), dict(by_name)
            for r in own:
                rows_by_slug.setdefault(r["slug"], r)
            for r in own:
                for n in r["_names"]:
                    by_name.setdefault(n, r["slug"])
        self.rows, self.by_name = rows_by_slug, by_name   # shared dicts when there are no custom rows: read-only
        self.by_app: dict[str, str] = {}
        for m in conn.execute("SELECT exercise_ref, app_exercise_id, name_zh, name_en, aliases FROM exercise_meta WHERE user_id = %s",
                              (owner_id,)).fetchall():
            slug = m["exercise_ref"] if m["exercise_ref"] in self.rows else self.slug_for(None, m["name_zh"], m["name_en"], *(m["aliases"] or []))
            if slug and m["app_exercise_id"]:
                self.by_app[m["app_exercise_id"]] = slug

    def slug_for(self, app_id: str | None, *names: str | None) -> str | None:
        if app_id and app_id in self.by_app:
            return self.by_app[app_id]
        for n in names:
            for key in (catalog.norm(n), catalog.norm(catalog.strip_paren(n))):
                if key and key in self.by_name:
                    return self.by_name[key]
        return None


def history_by_slug(index: Index, sessions: list[dict], day: date) -> dict[str, list[dict]]:
    """slug -> [{date, sets}] newest first (exercises not in the catalog under "app:<SparkyFitness id>")."""
    out: dict[str, dict[str, dict]] = {}
    for s in sessions:
        d = s["date"][:10]
        if d > day.isoformat():
            continue
        for e in s["exercises"]:
            ws = working_sets(e.get("sets") or [])
            if not ws:
                continue
            slug = e.get("slug") or index.slug_for(e.get("exercise_id"), e.get("name"))   # imported rows carry their slug
            key = slug or (f"app:{e['exercise_id']}" if e.get("exercise_id") else f"name:{catalog.norm(e.get('name'))}")
            entry = out.setdefault(key, {}).setdefault(d, {"date": d, "sets": [], "exercise_id": e.get("exercise_id")})
            entry["sets"] += ws
    return {k: [v[d] for d in sorted(v, reverse=True)] for k, v in out.items()}


def body_weight(conn: psycopg.Connection, owner_id: int, day: date, use_sparky: bool = True) -> float | None:
    """Latest body weight in the last 60 days (SparkyFitness weigh-ins first, then Apple Health body_mass).
    use_sparky=False: SparkyFitness just failed for this request, do not wait for it a second time."""
    from ..trends.series import weights
    try:
        w = weights(conn, owner_id, day - timedelta(days=60), day) if use_sparky else {}
    except Exception:  # noqa: BLE001  the starting estimate is optional: never fail a page over it
        w = {}
    if not w:
        row = conn.execute("""SELECT value FROM daily_metrics WHERE user_id = %s AND metric = 'body_mass' AND unit = 'kg'
                              AND date <= %s ORDER BY date DESC LIMIT 1""", (owner_id, day)).fetchone()
        return float(row["value"]) if row else None
    return float(w[max(w)])


# ---------------------------------------------------------------- one exercise
def start_fraction(ex: dict, sex: str | None) -> float | None:
    """Fraction of body weight for the starting working set (params.STARTER_*). None when the scale is unknown."""
    slug, pattern, eq = ex.get("slug"), ex.get("movement_pattern"), _equip(ex)
    part = "lower" if pattern in P.LOWER_BODY_PATTERNS else "upper"
    sex_f = (P.STARTER_SEX_FACTOR.get(sex or "") or P.STARTER_SEX_FACTOR["female"])[part]
    if slug in P.STARTER_SLUG_BW:
        frac = P.STARTER_SLUG_BW[slug]
    elif pattern in P.STARTER_1RM_BW and eq in P.STARTER_EQUIPMENT_FACTOR:
        frac = P.STARTER_1RM_BW[pattern] * P.LOAD_START_FRACTION * P.STARTER_EQUIPMENT_FACTOR[eq]
        if ex.get("per_side") and eq not in ("dumbbell", "kettlebell"):
            frac /= 2   # one-arm cable / machine work: half the two-sided stack
    else:
        return None
    return frac * sex_f


def estimate_start(ex: dict, bw: float | None, sex: str | None) -> float | None:
    """Conservative first working weight without history. None without body weight or when the scale is unknown."""
    frac = start_fraction(ex, sex)
    if not bw or frac is None:
        return None
    return _min_load(ex, round_down(bw * frac, ex.get("increment_kg") or ex.get("default_increment_kg") or P.LOAD_STEP_KG))


def recommend(ex: dict, rep_low: int | None, rep_high: int | None, hist: list[dict] | None,
              progression: dict | None = None, bw: float | None = None, sex: str | None = None,
              day: date | None = None, reason: str = "no_history") -> dict | None:
    """Recommended working weight for one exercise item (catalog fields: slug, modality, equipment, movement_pattern,
    per_side, increment_kg / default_increment_kg). Returns None when a weight makes no sense (timed / cardio).
    reason: why an estimate (if any) has no history behind it — "no_history" (nothing logged for this exercise),
    "not_linked" (no training log connected), "history_unreadable" (SparkyFitness linked but not readable now)."""
    modality = ex.get("modality") or "weight_reps"
    if modality == "duration" or (ex.get("category") or "strength") in ("cardio", "stretching", "mobility"):
        return None
    tr = target_reps(rep_low, rep_high) or round(sum(P.LOAD_DEFAULT_REPS) / 2)
    base = {"target_reps": tr, "target_rir": P.LOAD_TARGET_RIR, "per": _per(ex)}
    step = ex.get("increment_kg") or ex.get("default_increment_kg") or P.LOAD_STEP_KG
    last = None
    if hist:
        h = hist[0]
        top = max(s["weight_kg"] for s in h["sets"])
        best = max(h["sets"], key=lambda s: e1rm(s["weight_kg"], s["reps"], s.get("rir")))
        last = {"date": h["date"], "kg": top, "reps": [s["reps"] for s in h["sets"] if s["weight_kg"] == top]}
        base["last"] = last
        base["e1rm_kg"] = round(e1rm(best["weight_kg"], best["reps"], best.get("rir")), 1)
    if progression and progression.get("next_weight_kg") is not None:
        kg = float(progression["next_weight_kg"])
        direction = "up" if last is None or kg > last["kg"] else "down" if kg < last["kg"] else "hold"
        return {**base, "kg": kg, "basis": "progression", "direction": direction, "label": BASIS_LABEL["progression"]}
    if last is not None:
        reps_med = median(last["reps"])
        lo, hi = rep_low or tr, rep_high or tr
        stale = day is not None and (day - date.fromisoformat(last["date"])).days > P.LOAD_STALE_DAYS
        same_range = lo - P.LOAD_SAME_RANGE_SLACK <= reps_med <= hi + P.LOAD_SAME_RANGE_SLACK
        if same_range and not stale:
            return {**base, "kg": last["kg"], "basis": "history", "label": BASIS_LABEL["history"]}
        one_rm = base["e1rm_kg"] * (P.LOAD_STALE_FACTOR if stale else 1.0)
        kg = _min_load(ex, round_down(weight_for(one_rm, tr), step))
        if same_range:   # same range after a break: never above the last weight
            kg = min(kg, last["kg"])
        return {**base, "kg": kg, "basis": "e1rm", "stale": stale or None, "label": BASIS_LABEL["e1rm"]}
    if modality == "bodyweight_reps":
        return {**base, "kg": None, "basis": "bodyweight", "label": BASIS_LABEL["bodyweight"]}
    if modality == "reps_only":
        return None
    kg = estimate_start(ex, bw, sex)
    if kg is None:
        return None
    return {**base, "kg": kg, "basis": "estimate", "label": BASIS_LABEL["estimate"], "reason": reason,
            "body_weight_kg": round(bw, 1), "bw_fraction": round(start_fraction(ex, sex), 3)}


def apply_readiness(load: dict | None, tier: str | None) -> dict | None:
    """Today's recovery tier (plans.readiness_advice): red -> weights down; yellow -> hold a progression increase."""
    if not load or load.get("kg") is None or tier not in ("red", "yellow"):
        return load
    out = dict(load)
    if tier == "red":
        step = P.LOAD_STEP_KG
        out["kg"] = max(round_down(load["kg"] * P.LOAD_READINESS_FACTOR["red"], step), min(load["kg"], step))
        out["readiness"] = "red"
    elif load.get("basis") == "progression" and load.get("direction") == "up" and load.get("last"):
        out["kg"] = load["last"]["kg"]
        out["readiness"] = "yellow"
    else:
        return load
    out["planned_kg"] = load["kg"]
    return out


def short_text(load: dict | None) -> str | None:
    """Short label for UIs and slim tool output, e.g. "20 kg", "≈ 12 kg 估算", "自重"."""
    if not load:
        return None
    if load.get("kg") is None:
        return load.get("label")   # 自重 / 缺体重
    kg = f"{load['kg']:g} kg"
    return f"≈ {kg} 估算" if load["basis"] == "estimate" else kg


# ---------------------------------------------------------------- many exercises
class Context:
    """Everything recommend() needs for one owner and day, loaded once: SparkyFitness history (by slug), body weight, sex."""

    def __init__(self, conn: psycopg.Connection, viewer_id: int, owner_id: int, day: date,
                 sessions: list[dict] | None = None):
        self.day = day
        self.history_error, linked = None, True
        if sessions is None:
            sessions, self.history_error, linked = load_history(conn, owner_id, day)
        # why an estimate has no history behind it (shown in the exercise detail, issue #66 FB-5)
        self.reason = "history_unreadable" if self.history_error else "no_history" if linked else "not_linked"
        self.index = Index(conn, owner_id)
        self.hist = history_by_slug(self.index, sessions or [], day)
        # The estimate reveals body weight indirectly: only for viewers who may see the weight. It is also given while the
        # history cannot be read (labelled 估算 with that reason): a conservative start beats no number at all.
        self.can_estimate = auth.can_view(conn, viewer_id, owner_id, "weight")
        self._conn, self._owner, self._bw = conn, owner_id, None
        row = conn.execute("SELECT sex FROM users WHERE id = %s", (owner_id,)).fetchone()
        self.sex = (row or {}).get("sex")

    @property
    def bw(self) -> float | None:
        """Body weight, looked up only when some exercise has no history (it costs a SparkyFitness request)."""
        if self._bw is None and self.can_estimate:
            self._bw = body_weight(self._conn, self._owner, self.day, use_sparky=self.history_error is None) or 0.0
        return self._bw or None

    def history_for(self, item: dict) -> list[dict] | None:
        return self.hist.get(item.get("slug") or "") or self.hist.get(f"app:{item.get('app_exercise_id')}")

    def for_item(self, item: dict, progression: dict | None = None) -> dict | None:
        hist = self.history_for(item)
        needs_bw = not hist and not (progression or {}).get("next_weight_kg") and item.get("modality", "weight_reps") == "weight_reps"
        load = recommend(item, item.get("rep_low"), item.get("rep_high"), hist, progression,
                         self.bw if needs_bw else None, self.sex, self.day, self.reason)
        if (load is None and needs_bw and self.can_estimate and not self.bw and start_fraction(item, self.sex) is not None
                and (item.get("category") or "strength") == "strength"):
            # estimable, but no body weight logged in the last 60 days: say so instead of showing nothing
            load = {"kg": None, "basis": "estimate", "reason": "no_body_weight", "label": "缺体重", "per": _per(item),
                    "target_reps": target_reps(item.get("rep_low"), item.get("rep_high"))}
        return load


def attach(ctx: Context, item: dict, progression: dict | None = None) -> dict:
    """Add `load` (dict for the AI) and `load_text` (short label) to a plan item in place; an explicit weight_kg in the plan wins."""
    if item.get("weight_kg"):
        load = {"kg": float(item["weight_kg"]), "basis": "plan", "label": "计划", "per": _per(item)}
    else:
        load = ctx.for_item(item, progression)
    item["load"] = load
    item["load_text"] = short_text(load)
    return item


def attach_logged(ctx: Context, view: dict, progression: dict | None = None) -> dict:
    """Same as attach() for a SparkyFitness plan entry ({exercise_id, name, rep_low, rep_high, weight_kg}): the catalog row is
    found by SparkyFitness id or name; exercises outside the catalog still get history-based weights."""
    slug = ctx.index.slug_for(view.get("exercise_id"), view.get("name"))
    row = ctx.index.rows.get(slug or "") or {}
    item = {k: v for k, v in row.items() if not k.startswith("_")}
    item.update(slug=slug, app_exercise_id=view.get("exercise_id"), rep_low=view.get("rep_low"), rep_high=view.get("rep_high"),
                weight_kg=view.get("weight_kg"), increment_kg=row.get("default_increment_kg"))
    attach(ctx, item, progression)
    view["load"], view["load_text"] = item["load"], item["load_text"]
    return view


def for_exercise(conn: psycopg.Connection, viewer_id: int, owner_id: int, ex: dict, day: date) -> dict:
    """Exercise detail: the recommended weight for the owner (rep range from the active plan when the exercise is in it,
    else params.LOAD_DEFAULT_REPS), the latest progression reminder for it, and the last 3 logged sessions."""
    from . import plans
    lo, hi = P.LOAD_DEFAULT_REPS
    plan = plans._active_plan_row(conn, owner_id)
    for d in (plan or {}).get("days") or []:
        hit = next((x for x in d.get("exercises") or [] if x.get("slug") == ex["slug"]), None)
        if hit and (hit.get("rep_low") or hit.get("rep_high")):
            lo, hi = hit.get("rep_low") or hit.get("rep_high"), hit.get("rep_high") or hit.get("rep_low")
            break
    names = {catalog.norm(x) for x in [ex.get("name_zh"), ex.get("name_en"), ex["slug"]] if x}
    prog = next((s for s in plans._progression_suggestions(conn, viewer_id, owner_id, day - timedelta(days=6), day)
                 if (s.get("exercise_id") and s["exercise_id"] == ex.get("app_exercise_id"))
                 or catalog.norm(s.get("exercise")) in names), None)
    ctx = Context(conn, viewer_id, owner_id, day)
    item = {**ex, "rep_low": lo, "rep_high": hi, "increment_kg": ex.get("default_increment_kg")}
    load = ctx.for_item(item, prog)
    hist = ctx.history_for(item) or []
    # Alternatives carry what the preview card shows (issue #67): a thumbnail and the weight for the same rep range,
    # so the card opens without another request (and from the cached detail when offline)
    alts = []
    for a in ex.get("alternatives") or []:
        row = ctx.index.rows.get(a["slug"]) or {}
        a_load = ctx.for_item({**a, "rep_low": lo, "rep_high": hi, "increment_kg": a.get("default_increment_kg")})
        thumb = catalog.image_urls(row.get("images") or [])[:1]
        alts.append({**a, "thumb": thumb[0] if thumb else None, "load": a_load, "load_text": short_text(a_load)})
    return {"load": load, "load_text": short_text(load), "rep_range": [lo, hi], "alternatives": alts,
            "recent": [{"date": h["date"], "sets": [f"{s['weight_kg']:g}×{s['reps']}" for s in h["sets"]]} for h in hist[:3]],
            **({"load_history_error": ctx.history_error} if ctx.history_error else {})}

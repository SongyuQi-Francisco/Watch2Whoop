"""Tools for the body butler to write SparkyFitness (food, workouts, weight). All are write tools: they can only write for the caller, and the MCP client asks the user to confirm before each run.

They use the read-write key (sparky_client.write_client_for_user) and check before writing:
- Food: date not in the future; calories and the three macros (+ alcohol) roughly add up; optional sugar_g / added_sugar_g /
  fiber_g / sodium_mg (null = unknown) and sugary_drink: sugar / fiber / sodium go into the SparkyFitness food, everything
  (added sugar has no SparkyFitness field) into food_entry_extras (nutrition/extras.py); a same-named food already in the same meal of the same day is treated as a duplicate and skipped
  (only written with allow_duplicates=true). Each entry carries source=body_assistant and a source_id computed from its content, which SparkyFitness uses for idempotency
  (retrying the same entry never writes twice). SparkyFitness requires food entries to hang on a food in the food library, so a custom food is created / reused by content
  (provider_external_id = ba:<content hash>, 1 serving = this portion).
- Workouts: each exercise is first matched against the SparkyFitness exercise library (ID / name / Chinese and English names and aliases in exercise_meta); a match that is banned
  (marked banned in exercise_meta or 【禁用】 in the SparkyFitness description) rejects the whole call with the reason; the same exercise already logged that day is treated as a duplicate and skipped.
- Weight: an existing different value on the same day is not overwritten (only with overwrite=true), an identical one is skipped.
Edit / delete (issue #30, any source; deletes ask the user first): update_food_entry, update_set, undo_entry (food entry by id or an exact
date + meal + name (+ time) match, workout session, single set, one day's check-in). Every change is logged with before / after values
(writelog.py) and rollback() puts it back. The user also deletes food entries on the Today page (delete_food_entry, not logged).
"""
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from .. import sparky_client
from ..nutrition import extras
from ..records import store as records_store
from . import writelog

SOURCE = "body_assistant"
MEALS = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snacks": "加餐 / 零食"}
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_BACKDATE_DAYS = 60


def user_today(conn: psycopg.Connection, user_id: int) -> date:
    tz = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()["timezone"]
    return datetime.now(ZoneInfo(tz)).date()


def check_date(conn, user_id: int, s: str) -> date:
    d = date.fromisoformat(s)
    today = user_today(conn, user_id)
    if d > today:
        raise ValueError(f"日期 {d} 在未来（用户今天是 {today}）；日期是行为发生的那天")
    if (today - d).days > MAX_BACKDATE_DAYS:
        raise ValueError(f"日期 {d} 早于 {MAX_BACKDATE_DAYS} 天前，补这么早的记录请在 SparkyFitness 里手动加")
    return d


def _norm(name: str | None) -> str:
    """For name comparison: strip whitespace, unify full-width brackets, lowercase, drop the "（禁用）" (banned) marker."""
    s = (name or "").replace("（禁用）", "").replace("(禁用)", "")
    s = s.replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", s).lower()


def _strip_paren(name: str) -> str:
    return re.sub(r"\([^)]*\)$", "", _norm(name))


# ---------------------------------------------------------------- food
def _check_food(item: dict) -> list[str]:
    """Return warnings; obvious errors raise directly."""
    kcal = item["calories"]
    p, c, f = item["protein_g"], item["carbs_g"], item["fat_g"]
    alcohol = item.get("alcohol_g") or 0
    for k in ("calories", "protein_g", "carbs_g", "fat_g"):
        if item[k] is None or item[k] < 0:
            raise ValueError(f"{item['food']}：{k} 必须是 ≥ 0 的数")
    if kcal > 4000:
        raise ValueError(f"{item['food']}：{kcal} kcal 太大，确认是不是把一整天或别的单位算进来了")
    est = 4 * p + 4 * c + 9 * f + 7 * alcohol
    if abs(est - kcal) > max(80, 0.25 * kcal):
        raise ValueError(f"{item['food']}：热量 {kcal:g} kcal 和三大营养素算出来的 {est:.0f} kcal "
                         f"（4×蛋白 + 4×碳水 + 9×脂肪 + 7×酒精）差太多，先核对")
    warnings = []
    extras.check_optional(item["food"], item, c)
    if item.get("sugary_drink") is not None and not isinstance(item["sugary_drink"], bool):
        raise ValueError(f"{item['food']}：sugary_drink 是 true / false / null")
    if item.get("saturated_fat_g") is not None and item["saturated_fat_g"] > f + 0.5:
        raise ValueError(f"{item['food']}：饱和脂肪不能多于总脂肪")
    if item.get("saturated_fat_g") and item["saturated_fat_g"] >= 10:
        warnings.append(f"{item['food']}：饱和脂肪 {item['saturated_fat_g']:g}g，偏高（脂肪肝 / 甘油三酯）")
    return warnings


def _food_source_id(d: date, item: dict) -> str:
    key = json.dumps([d.isoformat(), item["meal"], item["food"].strip(), item["amount"].strip(), item["calories"],
                      item["protein_g"], item["carbs_g"], item["fat_g"], item.get("time")], ensure_ascii=False)
    return hashlib.sha1(key.encode()).hexdigest()[:20]


def _ensure_food(client, it: dict) -> tuple[str, str]:
    """A SparkyFitness food entry must hang on a food in the food library (row-level security). Find a same custom food the butler created earlier by content (name, portion, nutrition);
    if none, create one: 1 serving = this portion, nutrition is the total for this portion. That way changing the portion in the app converts correctly,
    and the same food can be picked directly next time."""
    name, amount = it["food"].strip(), it["amount"].strip()
    sig_items = [name, amount, it["calories"], it["protein_g"], it["carbs_g"], it["fat_g"],
                 it.get("saturated_fat_g"), it.get("alcohol_g")]
    extra = [it.get(f) for f in ("sugar_g", "fiber_g", "sodium_mg")]
    if any(v is not None for v in extra):   # only then: foods created before sugar tracking keep their id
        sig_items += extra
    sig = json.dumps(sig_items, ensure_ascii=False)
    ext_id = "ba:" + hashlib.sha1(sig.encode()).hexdigest()[:16]
    page = client.get("/foods/foods-paginated", searchTerm=name, foodFilter="mine", currentPage=1, itemsPerPage=100,
                      sortBy="name:asc", providerType="") or {}
    hit = next((f for f in page.get("foods") or [] if f.get("provider_external_id") == ext_id), None)
    if hit is None:
        hit = client.request("POST", "/foods", {k: v for k, v in {
            "name": name, "is_custom": True, "provider_type": "custom", "provider_external_id": ext_id,
            "serving_size": 1, "serving_unit": amount, "calories": it["calories"], "protein": it["protein_g"],
            "carbs": it["carbs_g"], "fat": it["fat_g"], "saturated_fat": it.get("saturated_fat_g"),
            "alcohol_g": it.get("alcohol_g"), "sugars": it.get("sugar_g"), "dietary_fiber": it.get("fiber_g"),
            "sodium": it.get("sodium_mg"), "notes": "身体管家记录饮食时建（营养是估算）"}.items() if v is not None})
    return hit["id"], (hit.get("default_variant") or {})["id"]


def log_food(conn: psycopg.Connection, user_id: int, items: list[dict], allow_duplicates: bool,
             client: sparky_client.SparkyClient | None = None) -> dict:
    if not items:
        raise ValueError("items 不能为空")
    checked = []
    warnings: list[str] = []
    for it in items:
        if it["meal"] not in MEALS:
            raise ValueError(f"meal 只能是 {', '.join(MEALS)}")
        if it.get("time") and not TIME_RE.match(it["time"]):
            raise ValueError("time 要是 24 小时制 HH:MM")
        if not (it.get("food") or "").strip() or not (it.get("amount") or "").strip():
            raise ValueError("每条都要有 food（吃了什么）和 amount（份量）")
        d = check_date(conn, user_id, it["date"])
        warnings += _check_food(it)
        checked.append((d, it))
    client = client or sparky_client.write_client_for_user(conn, user_id)
    meal_ids = {m["name"]: m["id"] for m in client.get("/meal-types")}
    days: dict[date, list] = {}
    created, skipped = [], []
    for d, it in checked:
        if d not in days:
            days[d] = client.get(f"/food-entries/by-date/{d.isoformat()}") or []
        dup = next((e for e in days[d] if e.get("meal_type") == it["meal"] and _norm(e.get("food_name")) == _norm(it["food"])),
                   None)
        if dup and not allow_duplicates:
            skipped.append({"date": d.isoformat(), "meal": it["meal"], "food": it["food"], "reason":
                            f"这天{MEALS[it['meal']]}已经有「{dup.get('food_name')}」（{dup.get('calories')} kcal，"
                            f"id {dup['id']}），当作重复没写；确认是另一份就用 allow_duplicates=true 再写"})
            continue
        row = _create_food(client, meal_ids, d, it)
        days[d].append(row)
        writelog.record(conn, "food", "create", row["id"], None, row)
        opt = {f: it.get(f) for f in extras.OPTIONAL}
        extras.save(conn, user_id, row["id"], d, opt, it.get("sugary_drink"))   # added sugar has no SparkyFitness field
        created.append({"id": row["id"], "date": d.isoformat(), "meal": it["meal"], "food": it["food"].strip(),
                        "amount": it["amount"].strip(), "calories": it["calories"], "protein_g": it["protein_g"],
                        "carbs_g": it["carbs_g"], "fat_g": it["fat_g"],
                        **{k: v for k, v in opt.items() if v is not None}})
    return {"created": created, "skipped_duplicates": skipped, "warnings": warnings,
            "undo": "记错了可以用 undo_sparky_entry(kind=food, date, id) 删掉，改用 update_food_entry"}


def _create_food(client, meal_ids: dict, d: date, it: dict) -> dict:
    amount = it["amount"].strip()
    food_id, variant_id = _ensure_food(client, it)
    body = {"food_id": food_id, "variant_id": variant_id, "quantity": 1, "unit": amount,
            "entry_date": d.isoformat(), "entry_time": it.get("time"),
            "meal_type_id": meal_ids[it["meal"]], "meal_type": it["meal"],
            "notes": it.get("notes"), "source": SOURCE, "source_id": _food_source_id(d, it)}
    return client.request("POST", "/food-entries", {k: v for k, v in body.items() if v is not None})


# ---------------------------------------------------------------- exercise matching
def exercise_index(conn, user_id: int, client) -> dict:
    """SparkyFitness exercise library + exercise_meta, used for matching and the banned check."""
    library = sparky_client.exercises(client)
    meta = records_store.list_exercises(conn, user_id)
    return {"library": library, "by_id": {e["id"]: e for e in library}, "meta": meta,
            "meta_by_app_id": {m["app_exercise_id"]: m for m in meta if m.get("app_exercise_id")}}


def _meta_names(m: dict) -> list[str]:
    return [n for n in (m.get("name_zh"), m.get("name_en"), m.get("exercise_ref"), *(m.get("aliases") or [])) if n]


def banned_reason(idx: dict, name: str, exercise_id: str | None) -> str | None:
    """Whether this exercise is banned: exercise_meta (by ID or name / alias) or 【禁用】 in the SparkyFitness description."""
    metas = [idx["meta_by_app_id"].get(exercise_id)] if exercise_id else []
    metas += [m for m in idx["meta"] if any(_norm(n) == _norm(name) for n in _meta_names(m))]
    for m in metas:
        if m and m["status"] == "banned":
            return f"{m['name_zh'] or m['exercise_ref']} 是禁用动作" + (f"：{m['reason']}" if m.get("reason") else "")
    lib = idx["by_id"].get(exercise_id) if exercise_id else None
    if lib and ((lib.get("description") or "").startswith("【禁用】") or "（禁用）" in lib["name"]):
        return f"{lib['name']} 在 SparkyFitness 里标了禁用：{(lib.get('description') or '')[:120]}"
    return None


def resolve_exercise(idx: dict, name: str, exercise_id: str | None) -> tuple[dict | None, list[str]]:
    """Exercise name -> SparkyFitness exercise. Returns (exercise or None, candidate names). Order: ID -> name -> exercise_meta name / alias -> name without brackets."""
    lib = idx["library"]
    if exercise_id:
        return idx["by_id"].get(exercise_id), []
    n = _norm(name)
    hits = [e for e in lib if _norm(e["name"]) == n]
    if not hits:
        ids = {m["app_exercise_id"] for m in idx["meta"]
               if m.get("app_exercise_id") and any(_norm(x) == n for x in _meta_names(m))}
        hits = [idx["by_id"][i] for i in ids if i in idx["by_id"]]
    if not hits:
        hits = [e for e in lib if _strip_paren(e["name"]) == _strip_paren(name)]
    if len(hits) == 1:
        return hits[0], []
    cands = [e["name"] for e in hits] or [e["name"] for e in lib if n and (n in _norm(e["name"]) or _strip_paren(e["name"]) in n)]
    return None, cands[:8]


def search_exercises(conn, user_id: int, query: str | None = "", client=None,
                     muscle: str | None = None, equipment: str | None = None,
                     pattern: str | None = None, include_banned: bool = True,
                     limit: int = 30) -> list[dict]:
    """Search the bilingual exercise catalog (plus SparkyFitness library when linked) by name / alias / muscle / equipment / movement pattern."""
    from ..training import catalog as tr_catalog
    cat_hits = tr_catalog.search(
        conn, user_id=user_id, query=query, muscle=muscle,
        equipment=equipment, pattern=pattern, include_banned=include_banned, limit=limit,
    )
    sparky_by_norm: dict[str, dict] = {}
    sparky_lib: list[dict] = []
    idx: dict | None = None
    try:
        sp_client = client or sparky_client.client_for_user(conn, user_id)
        idx = exercise_index(conn, user_id, sp_client)
        sparky_lib = idx["library"]
        for e in sparky_lib:
            sparky_by_norm[_norm(e["name"])] = e
            sparky_by_norm[_strip_paren(e["name"])] = e
    except Exception:
        pass

    out: list[dict] = []
    seen_app_ids: set[str] = set()
    for c in cat_hits:
        app_id = c.get("app_exercise_id")
        if not app_id and sparky_by_norm:
            sp = sparky_by_norm.get(_norm(c["name_zh"])) or sparky_by_norm.get(_strip_paren(c["name_zh"]))
            if sp:
                app_id = sp["id"]
        if app_id:
            seen_app_ids.add(app_id)
        out.append({
            "slug": c["slug"],
            "exercise_id": app_id or c["slug"],
            "name": c["name_zh"],
            "name_zh": c["name_zh"],
            "name_en": c["name_en"],
            "category": c["category"],
            "movement_pattern": c["movement_pattern"],
            "movement_pattern_zh": c.get("movement_pattern_zh"),
            "equipment": c["equipment"],
            "equipment_zh": c["equipment_zh"],
            "primary_muscles": c["primary_muscles"],
            "primary_muscles_zh": c["primary_muscles_zh"],
            "secondary_muscles": c["secondary_muscles"],
            "status": c["status"],
            "cues": c["cues"],
            "reason": c["reason"],
            "joint_flags": c["joint_flags"],
            "per_side": c["per_side"],
        })

    # Append any custom SparkyFitness exercises not in the catalog when no structured filter is active
    if idx and not (muscle or equipment or pattern):
        q = _norm(query)
        for e in sparky_lib:
            if e["id"] in seen_app_ids:
                continue
            m = idx["meta_by_app_id"].get(e["id"])
            names = [e["name"], *(_meta_names(m) if m else [])]
            if not q or any(q in _norm(x) for x in names):
                st = (m or {}).get("status") or ("banned" if banned_reason(idx, e["name"], e["id"]) else "active")
                if not include_banned and st == "banned":
                    continue
                out.append({
                    "slug": (m or {}).get("exercise_ref") or e["id"],
                    "exercise_id": e["id"],
                    "name": e["name"],
                    "name_zh": e["name"],
                    "name_en": (m or {}).get("name_en"),
                    "category": e.get("category"),
                    "movement_pattern": None,
                    "movement_pattern_zh": None,
                    "equipment": [],
                    "equipment_zh": [],
                    "primary_muscles": e.get("primary_muscles") or [],
                    "primary_muscles_zh": [],
                    "secondary_muscles": [],
                    "status": st,
                    "cues": (m or {}).get("cues"),
                    "reason": (m or {}).get("reason"),
                    "joint_flags": {},
                    "per_side": False,
                })
    return out[:limit]


# ---------------------------------------------------------------- workouts
def _check_set(ex: str, s: dict) -> None:
    if s.get("reps") is not None and not 0 <= s["reps"] <= 100:
        raise ValueError(f"{ex}：次数 {s['reps']} 不合理")
    if s.get("weight_kg") is not None and not 0 <= s["weight_kg"] <= 400:
        raise ValueError(f"{ex}：重量 {s['weight_kg']}kg 不合理（哑铃写单只、绳索写每侧）")
    if s.get("rir") is not None and not 0 <= s["rir"] <= 10:
        raise ValueError(f"{ex}：RIR 只能是 0–10")
    if s.get("duration_s") is not None and not 0 < s["duration_s"] <= 6 * 3600:
        raise ValueError(f"{ex}：时长 {s['duration_s']} 秒不合理")
    if all(s.get(k) is None for k in ("reps", "weight_kg", "duration_s")):
        raise ValueError(f"{ex}：每组至少要有次数、重量或时长之一")


def _session_exercise_ids(session: dict) -> set:
    if session.get("type") == "preset":
        return {e.get("exercise_id") for e in session.get("exercises") or []}
    return {session.get("exercise_id")}


def _banned_message(reasons: list[str]) -> str:
    return ("拒绝写入：" + "；".join(dict.fromkeys(reasons)) + "。这些动作在禁用名单里，整次训练都没有记录；"
            "请把禁用动作去掉（或换成替代动作，get_exercise_rules 看要点）再记。")


def log_workout(conn: psycopg.Connection, user_id: int, a: dict, client=None) -> dict:
    d = check_date(conn, user_id, a["date"])
    if not a["exercises"]:
        raise ValueError("exercises 不能为空")
    for ex in a["exercises"]:
        if not ex["sets"]:
            raise ValueError(f"{ex['exercise']}：至少要有一组")
        for s in ex["sets"]:
            _check_set(ex["exercise"], s)
    # First check bans by exercise_meta alone (works without SparkyFitness), then do the full check against the exercise library
    meta_only = {"library": [], "by_id": {}, "meta": records_store.list_exercises(conn, user_id), "meta_by_app_id": {}}
    meta_only["meta_by_app_id"] = {m["app_exercise_id"]: m for m in meta_only["meta"] if m.get("app_exercise_id")}
    pre = [r for ex in a["exercises"] if (r := banned_reason(meta_only, ex["exercise"], ex.get("exercise_id")))]
    if pre:
        raise PermissionError(_banned_message(pre))
    client = client or sparky_client.write_client_for_user(conn, user_id)
    idx = exercise_index(conn, user_id, client)
    # Check all exercises (a banned one rejects the whole call), then write
    banned, unknown, resolved = [], [], []
    for ex in a["exercises"]:
        if reason := banned_reason(idx, ex["exercise"], ex.get("exercise_id")):
            banned.append(reason)
            continue
        hit, cands = resolve_exercise(idx, ex["exercise"], ex.get("exercise_id"))
        if hit and (reason := banned_reason(idx, hit["name"], hit["id"])):
            banned.append(reason)
            continue
        if hit is None and not a["create_missing_exercises"]:
            unknown.append({"exercise": ex["exercise"], "candidates": cands})
        resolved.append((ex, hit))
    if banned:
        raise PermissionError(_banned_message(banned))
    if unknown:
        raise ValueError("这些动作在 SparkyFitness 动作库里对不上：" + json.dumps(unknown, ensure_ascii=False)
                         + "。用候选里的名字或 exercise_id 再试；确实是新动作就让用户确认后用 create_missing_exercises=true")
    existing = client.get("/v2/exercise-entries/by-date", selectedDate=d.isoformat()) or []
    done_ids = set().union(*(_session_exercise_ids(s) for s in existing)) if existing else set()
    exercises, created_ex = [], []
    for i, (ex, hit) in enumerate(resolved):
        if hit is None:  # New exercise confirmed by the user: create it in SparkyFitness
            hit = client.request("POST", "/exercises", {
                "name": ex["exercise"].strip(), "category": "strength", "source": "custom", "is_custom": True,
                "modality": "weight_reps", "description": "身体管家记录训练时新建（用户确认过）"},
                multipart_field="exerciseData")
            created_ex.append(hit["name"])
        exercises.append({
            "exercise_id": hit["id"], "sort_order": i, "duration_minutes": 0, "notes": ex.get("notes"),
            "sets": [{k: v for k, v in {
                "set_number": n + 1, "set_type": s.get("set_type") or "Working Set", "reps": s.get("reps"),
                "weight": s.get("weight_kg"), "rir": s.get("rir"), "duration": s.get("duration_s"),
                "notes": s.get("notes")}.items() if v is not None} for n, s in enumerate(ex["sets"])]})
    dups = [ex["exercise"] for (ex, _), e in zip(resolved, exercises) if e["exercise_id"] in done_ids]
    if dups and not a["allow_duplicate"]:
        return {"created": None, "skipped_duplicate": True, "reason":
                f"{d} 已经记过 {'、'.join(dups)}（可能在 SparkyFitness app 里记过了），当作重复没写；"
                "用 get_training_log 核对，确认是另一次训练再用 allow_duplicate=true"}
    body = {"entry_date": d.isoformat(), "name": a["name"].strip() or "训练", "notes": a.get("notes"),
            "source": SOURCE, "exercises": exercises}
    row = client.request("POST", "/exercise-preset-entries", {k: v for k, v in body.items() if v is not None})
    writelog.record(conn, "session", "create", row["id"], None, {"id": row["id"], "date": d.isoformat(), "type": "preset"})
    return {"created": {"id": row["id"], "date": d.isoformat(), "name": row.get("name"),
                        "exercises": [{"name": (x.get("exercise_snapshot") or {}).get("name") or x.get("name"),
                                       "sets": len(x.get("sets") or [])} for x in row.get("exercises") or []]},
            "new_exercises_in_library": created_ex,
            "undo": "记错了可以用 undo_sparky_entry(kind=workout, date, id) 删掉"}


# ---------------------------------------------------------------- weight / body data
CHECKIN_FIELDS = {"weight_kg": "weight", "body_fat_pct": "body_fat_percentage", "waist_cm": "waist",
                  "muscle_mass_kg": "muscle_mass_kg", "body_water_pct": "body_water_percentage",
                  "bone_mass_kg": "bone_mass_kg", "bmr_kcal": "bmr"}
CHECKIN_RANGES = {"weight_kg": (30, 250), "body_fat_pct": (3, 60), "waist_cm": (50, 180), "muscle_mass_kg": (15, 120),
                  "body_water_pct": (30, 75), "bone_mass_kg": (1, 8), "bmr_kcal": (800, 4000)}


def log_weight(conn: psycopg.Connection, user_id: int, a: dict, client=None) -> dict:
    d = check_date(conn, user_id, a["date"])
    want = {}
    for k, col in CHECKIN_FIELDS.items():
        v = a.get(k)
        if v is None:
            continue
        lo, hi = CHECKIN_RANGES[k]
        if not lo <= v <= hi:
            raise ValueError(f"{k} = {v} 不在合理范围 {lo}–{hi}（体重用 kg）")
        want[col] = v
    if not want:
        raise ValueError("至少要有一个数值（weight_kg / body_fat_pct / waist_cm …）")
    client = client or sparky_client.write_client_for_user(conn, user_id)
    cur = client.get(f"/measurements/check-in/{d.isoformat()}") or {}
    if str(cur.get("entry_date") or d.isoformat())[:10] != d.isoformat():
        cur = {}  # SparkyFitness returns the latest check-in on or before the date, not only that day's
    same = {c: v for c, v in want.items() if cur.get(c) is not None and abs(float(cur[c]) - v) < 1e-6}
    conflict = {c: {"existing": float(cur[c]), "new": v} for c, v in want.items()
                if cur.get(c) is not None and c not in same}
    if conflict and not a["overwrite"]:
        return {"written": None, "conflicts": conflict, "reason":
                f"{d} 已经有不同的数值，没覆盖；确认用新数值就用 overwrite=true（例如同一天称了两次，按早上空腹那次）"}
    todo = {c: v for c, v in want.items() if c not in same}
    if not todo:
        return {"written": None, "unchanged": same, "reason": f"{d} 已经记过同样的数值，没重复写"}
    client.request("POST", "/measurements/check-in", {"entry_date": d.isoformat(), **todo})
    writelog.record(conn, "weight", "update" if cur else "create", d.isoformat(),
                    {"date": d.isoformat(), **{c: cur.get(c) for c in todo}} if cur else None,
                    {"date": d.isoformat(), **todo})
    return {"written": {"date": d.isoformat(), **todo}, "unchanged": same, "overwrote": conflict or None}


# ---------------------------------------------------------------- lookups
def _day_rows(client, kind: str, d: date) -> list[dict]:
    if kind == "food":
        return client.get(f"/food-entries/by-date/{d.isoformat()}") or []
    if kind in ("workout", "set"):
        return client.get("/v2/exercise-entries/by-date", selectedDate=d.isoformat()) or []
    raise ValueError("kind 只能是 food / workout / set / weight")


def _entry_path(kind: str, row: dict) -> str:
    if kind == "food":
        return f"/food-entries/{row['id']}"
    # The butler writes whole sessions (preset entries); a standalone exercise entry has its own path
    return f"/exercise-preset-entries/{row['id']}" if row.get("type", "preset") == "preset" else f"/exercise-entries/{row['id']}"


def _match_food(rows: list[dict], d: date, meal: str | None, food: str | None, hhmm: str | None) -> dict:
    """Exactly one food entry by meal + food name (+ time); 0 or several matches refuse with what was found."""
    if meal not in MEALS or not (food or "").strip():
        raise ValueError("没给 id 时要给 meal（breakfast / lunch / dinner / snacks）和 food 精确匹配；"
                         "或用 get_nutrition(include_entries=true) 查 id")
    if hhmm and not TIME_RE.match(hhmm):
        raise ValueError("time 要是 24 小时制 HH:MM")
    hits = [r for r in rows if r.get("meal_type") == meal and _norm(r.get("food_name")) == _norm(food)
            and (not hhmm or (r.get("entry_time") or "")[:5] == hhmm)]
    if not hits:
        raise ValueError(f"{d} {MEALS[meal]}没有「{food}」{f'（{hhmm}）' if hhmm else ''}；"
                         "用 get_nutrition(include_entries=true) 核对名字或拿 id")
    if len(hits) > 1:
        found = "；".join(f"id {r['id']}（{(r.get('entry_time') or '')[:5] or '无时间'}，{r.get('source') or '其他来源'}）"
                         for r in hits)
        raise ValueError(f"{d} {MEALS[meal]}「{food}」有 {len(hits)} 条，不确定删哪条：{found}。给 time 或 id 再试")
    return hits[0]


def _food_row(client, d: date, entry_id) -> dict:
    row = next((r for r in _day_rows(client, "food", d) if str(r.get("id")) == str(entry_id)), None)
    if row is None:
        raise ValueError(f"{d} 没有这条饮食记录：{entry_id}（用 get_nutrition(include_entries=true) 查 id 和日期）")
    return row


def _session_entries(s: dict) -> list[dict]:
    """Exercise entries of one SparkyFitness session: the exercises of a preset session, or the standalone entry itself."""
    return s.get("exercises") or [] if s.get("type", "preset") == "preset" else [s]


def _find_set(client, d: date, set_id) -> tuple[dict, dict, dict]:
    for s in _day_rows(client, "set", d):
        for e in _session_entries(s):
            for x in e.get("sets") or []:
                if str(x.get("id")) == str(set_id):
                    return s, e, x
    raise ValueError(f"{d} 没有这一组：{set_id}（组的 id 在 get_training_log 里）")


SET_KEYS = ("set_number", "set_type", "reps", "weight", "duration", "rest_time", "notes", "rpe", "rir", "is_pr", "distance")


def _clean_sets(sets: list[dict]) -> list[dict]:
    """Sets as SparkyFitness accepts them back (PUT replaces all sets of the exercise entry; ids are not sent)."""
    out = []
    for n, x in enumerate(sorted(sets, key=lambda x: x.get("set_number") or 0)):
        out.append({**{k: x.get(k) for k in SET_KEYS if x.get(k) is not None}, "set_number": n + 1})
    return out


# ---------------------------------------------------------------- edit
def update_food_entry(conn: psycopg.Connection, user_id: int, a: dict, client=None) -> dict:
    """Change one food entry of any source. Portion / meal / date / time: edited in place (same id).
    New name, amount or nutrition: written as a new entry with the full corrected values (old one removed), returns the new id."""
    d = date.fromisoformat(a["date"])
    if a.get("meal") and a["meal"] not in MEALS:
        raise ValueError(f"meal 只能是 {', '.join(MEALS)}")
    if a.get("time") and not TIME_RE.match(a["time"]):
        raise ValueError("time 要是 24 小时制 HH:MM")
    portion = a.get("portion")
    if portion is not None and not 0 < portion <= 20:
        raise ValueError("portion 是相对现在份量的倍数（0.5 = 一半），要 > 0")
    new_d = check_date(conn, user_id, a["new_date"]) if a.get("new_date") else d
    client = client or sparky_client.write_client_for_user(conn, user_id)
    row = _food_row(client, d, a["id"])
    meal = a.get("meal") or row.get("meal_type")
    hhmm = a.get("time") or (row.get("entry_time") or "")[:5] or None
    nutrition = ("food", "amount", "calories", "protein_g", "carbs_g", "fat_g")
    renamed = False
    if all(a.get(k) is None for k in nutrition):
        if portion is None and not a.get("meal") and not a.get("new_date") and not a.get("time"):
            raise ValueError("没有要改的字段")
        meal_ids = {m["name"]: m["id"] for m in client.get("/meal-types")}
        body = {"quantity": round((row.get("quantity") or 1) * (portion or 1), 4), "unit": row.get("unit"),
                "entry_date": new_d.isoformat(), "meal_type": meal, "meal_type_id": meal_ids.get(meal),
                "entry_time": hhmm, "notes": row.get("notes"), "variant_id": row.get("variant_id")}
        new = client.request("PUT", f"/food-entries/{row['id']}", {k: v for k, v in body.items() if v is not None})
        new = {**row, **(new or {}), **{k: v for k, v in body.items() if v is not None}}
    else:
        cur = sparky_client._food_entry(row)
        f = portion or 1
        renamed = bool(a.get("food")) and a["food"].strip() != (row.get("food_name") or "").strip()   # another food: sugar unknown
        item = {"date": new_d.isoformat(), "meal": meal, "time": hhmm, "notes": row.get("notes"),
                "food": a.get("food") or row.get("food_name"),
                "amount": a.get("amount") or (f"{cur['quantity'] * f:g} {cur['unit'] or '份'}"),
                **{k: a[k] if a.get(k) is not None else (None if cur.get(k) is None else round(cur[k] * f, 1))
                   for k in ("calories", "protein_g", "carbs_g", "fat_g")},
                "saturated_fat_g": None if cur.get("saturated_fat_g") is None else round(cur["saturated_fat_g"] * f, 1),
                **{k: None if cur.get(k) is None or renamed else round(cur[k] * f, 1)
                   for k in ("sugar_g", "fiber_g", "sodium_mg")},
                "alcohol_g": None if row.get("alcohol_g") is None else round(row["alcohol_g"] * (row.get("quantity") or 1)
                                                                             / (row.get("serving_size") or 1) * f, 1)}
        for k in ("calories", "protein_g", "carbs_g", "fat_g"):
            item[k] = item[k] or 0
        if item["saturated_fat_g"] is not None and (item["saturated_fat_g"] > item["fat_g"] + 0.5 or a.get("fat_g") is not None):
            item["saturated_fat_g"] = min(item["saturated_fat_g"], item["fat_g"])
        _check_food(item)
        meal_ids = {m["name"]: m["id"] for m in client.get("/meal-types")}
        new = _create_food(client, meal_ids, new_d, item)
        client.request("DELETE", _entry_path("food", row))
    writelog.record(conn, "food", "update", row["id"], row, new)
    if not renamed:   # added sugar etc. follow the entry (scaled by the portion)
        extras.carry_over(conn, user_id, row["id"], new["id"], new_d, portion or 1)
    else:
        conn.execute("DELETE FROM food_entry_extras WHERE user_id = %s AND entry_id = %s", (user_id, str(row["id"])))
    out = sparky_client._food_entry({**new, "entry_date": str(new.get("entry_date") or new_d)})
    return {"updated": {k: v for k, v in out.items() if v is not None}, "previous_id": row["id"],
            "note": None if str(new["id"]) == str(row["id"]) else "改了名称 / 营养：换成了一条新记录（新 id），原来那条已删"}


def update_set(conn: psycopg.Connection, user_id: int, day: str, set_id, reps, weight_kg, rir, client=None) -> dict:
    """Change reps / weight / RIR of one set (any source). Only the given fields change."""
    d = date.fromisoformat(day)
    if reps is None and weight_kg is None and rir is None:
        raise ValueError("reps、weight_kg、rir 至少给一个")
    client = client or sparky_client.write_client_for_user(conn, user_id)
    s, e, x = _find_set(client, d, set_id)
    before = _clean_sets(e.get("sets") or [])
    after = []
    for y in sorted(e.get("sets") or [], key=lambda y: y.get("set_number") or 0):
        if str(y.get("id")) == str(set_id):
            y = {**y, **{k: v for k, v in (("reps", reps), ("weight", weight_kg), ("rir", rir)) if v is not None}}
            _check_set(e.get("name") or "这一组", {"reps": y.get("reps"), "weight_kg": y.get("weight"), "rir": y.get("rir"),
                                                  "duration_s": y.get("duration")})
        after.append(y)
    after = _clean_sets(after)
    client.request("PUT", f"/exercise-entries/{e['id']}", {"sets": after})
    writelog.record(conn, "sets", "update", e["id"], {"date": d.isoformat(), "entry_id": e["id"], "sets": before},
                    {"date": d.isoformat(), "entry_id": e["id"], "sets": after})
    n = next(i for i, y in enumerate(sorted(e.get("sets") or [], key=lambda y: y.get("set_number") or 0))
             if str(y.get("id")) == str(set_id))
    name = (e.get("exercise_snapshot") or {}).get("name") or e.get("name")
    return {"updated": {"date": d.isoformat(), "exercise": name, "set": n + 1, "was": before[n], "now": after[n]},
            "note": "这个动作的各组 id 已经换新，再改要重新 get_training_log"}


# ---------------------------------------------------------------- delete (any source; the tools ask the user first)
def undo_entry(conn: psycopg.Connection, user_id: int, kind: str, day: str, entry_id: str | None = None, client=None,
               *, meal: str | None = None, food: str | None = None, time: str | None = None) -> dict:
    """Delete one record of any source (written by the AI, logged in the app, migrated): a food entry (id, or date + meal + name
    (+ time)), a whole workout session (id), one set (set id) or that day's weight / body check-in. Logged with the full
    old values, so rollback_write can put it back."""
    d = date.fromisoformat(day)
    if kind not in ("food", "workout", "set", "weight"):
        raise ValueError("kind 只能是 food / workout / set / weight")
    if not entry_id and kind in ("workout", "set"):
        raise ValueError("训练 / 组要给 id（get_training_log 里有）")
    client = client or sparky_client.write_client_for_user(conn, user_id)
    if kind == "weight":
        return _delete_weight(conn, client, d)
    if kind == "set":
        return _delete_set(conn, client, d, entry_id)
    rows = _day_rows(client, kind, d)
    if entry_id:
        row = next((r for r in rows if str(r.get("id")) == str(entry_id)), None)
        if row is None:
            raise ValueError(f"{d} 没有这条记录：{entry_id}")
    else:
        row = _match_food(rows, d, meal, food, time)
    client.request("DELETE", _entry_path(kind, row))
    writelog.record(conn, "food" if kind == "food" else "session", "delete", row["id"], row, None)
    return {"deleted": {"kind": kind, "date": d.isoformat(), "id": row["id"],
                        "name": row.get("food_name") or row.get("name"), "source": row.get("source")}}


def _delete_set(conn, client, d: date, set_id) -> dict:
    s, e, x = _find_set(client, d, set_id)
    sets = e.get("sets") or []
    if len(sets) == 1:
        raise ValueError("这是这个动作唯一的一组；删整次训练用 kind=workout")
    before = _clean_sets(sets)
    after = _clean_sets([y for y in sets if str(y.get("id")) != str(set_id)])
    client.request("PUT", f"/exercise-entries/{e['id']}", {"sets": after})
    writelog.record(conn, "sets", "update", e["id"], {"date": d.isoformat(), "entry_id": e["id"], "sets": before},
                    {"date": d.isoformat(), "entry_id": e["id"], "sets": after})
    name = (e.get("exercise_snapshot") or {}).get("name") or e.get("name")
    return {"deleted": {"kind": "set", "date": d.isoformat(), "exercise": name,
                        "set": {k: x.get(k) for k in ("set_number", "reps", "weight", "rir") if x.get(k) is not None}}}


CHECKIN_COLUMNS = ("weight", "neck", "waist", "hips", "steps", "height", "body_fat_percentage", "muscle_mass_kg",
                   "bone_mass_kg", "body_water_percentage", "bmr")


def _delete_weight(conn, client, d: date) -> dict:
    cur = client.get(f"/measurements/check-in/{d.isoformat()}") or {}
    if not cur.get("id") or str(cur.get("entry_date") or "")[:10] != d.isoformat():
        raise ValueError(f"{d} 没有称重 / 体成分记录")
    before = {"date": d.isoformat(), **{c: cur[c] for c in CHECKIN_COLUMNS if cur.get(c) is not None}}
    client.request("DELETE", f"/measurements/check-in/{cur['id']}")
    writelog.record(conn, "weight", "delete", d.isoformat(), before, None)
    return {"deleted": before}


def delete_food_entry(conn: psycopg.Connection, user_id: int, day: date, entry_id: str, client=None) -> dict:
    """The user deletes one of their own food entries from the Today page (any source: logged by the AI, in the app, migrated).
    Uses the user's own read-write key, so only that user's SparkyFitness data can be touched; the entry must be on that day."""
    client = client or sparky_client.write_client_for_user(conn, user_id)
    row = next((r for r in _day_rows(client, "food", day) if str(r.get("id")) == str(entry_id)), None)
    if row is None:
        raise LookupError(f"{day} 没有这条饮食记录（可能已经删了）")
    client.request("DELETE", _entry_path("food", row))
    return {"deleted": {"id": row["id"], "date": day.isoformat(), "meal": row.get("meal_type"),
                        "food": row.get("food_name"), "source": row.get("source")}}


# ---------------------------------------------------------------- rollback (assistant/writelog.py)
def _recreate_food(client, row: dict) -> dict:
    meal_ids = {m["name"]: m["id"] for m in client.get("/meal-types")}
    body = {"food_id": row.get("food_id"), "variant_id": row.get("variant_id"), "quantity": row.get("quantity") or 1,
            "unit": row.get("unit"), "entry_date": str(row["entry_date"])[:10],
            "entry_time": (row.get("entry_time") or "")[:5] or None, "meal_type": row.get("meal_type"),
            "meal_type_id": meal_ids.get(row.get("meal_type")), "notes": row.get("notes"),
            "source": row.get("source"), "source_id": row.get("source_id")}
    return client.request("POST", "/food-entries", {k: v for k, v in body.items() if v is not None})


def _recreate_session(client, s: dict) -> dict:
    def ex_body(e, i):
        return {"exercise_id": e.get("exercise_id"), "sort_order": i, "duration_minutes": e.get("duration_minutes") or 0,
                "notes": e.get("notes"), "sets": _clean_sets(e.get("sets") or [])}
    d = str(s["entry_date"])[:10]
    if s.get("type", "preset") == "preset":
        body = {"entry_date": d, "name": s.get("name") or "训练", "notes": s.get("notes"), "source": s.get("source"),
                "exercises": [{k: v for k, v in ex_body(e, i).items() if v is not None}
                              for i, e in enumerate(s.get("exercises") or [])]}
        return client.request("POST", "/exercise-preset-entries", {k: v for k, v in body.items() if v is not None})
    body = {**ex_body(s, 0), "entry_date": d, "calories_burned": s.get("calories_burned") or 0}
    body.pop("sort_order")
    return client.request("POST", "/exercise-entries", {k: v for k, v in body.items() if v is not None})


def rollback(conn, user_id: int, kind: str, action: str, before, after, client=None) -> dict:
    client = client or sparky_client.write_client_for_user(conn, user_id)
    if kind == "food":
        if action == "create":
            row = _food_row(client, date.fromisoformat(str(after["entry_date"])[:10]), after["id"])
            client.request("DELETE", _entry_path("food", row))
            return {"deleted": row["id"]}
        if action == "delete":
            return {"restored": _recreate_food(client, before)["id"]}
        # update: in place -> put the old portion / meal / date / time back; replaced -> remove the new one, restore the old one
        new = _food_row(client, date.fromisoformat(str(after["entry_date"])[:10]), after["id"])
        if str(after["id"]) == str(before["id"]):
            body = {"quantity": before.get("quantity"), "unit": before.get("unit"),
                    "entry_date": str(before["entry_date"])[:10], "meal_type": before.get("meal_type"),
                    "entry_time": (before.get("entry_time") or "")[:5] or None, "notes": before.get("notes"),
                    "variant_id": before.get("variant_id")}
            client.request("PUT", f"/food-entries/{new['id']}", {k: v for k, v in body.items() if v is not None})
            return {"restored": new["id"]}
        client.request("DELETE", _entry_path("food", new))
        return {"restored": _recreate_food(client, before)["id"], "deleted": new["id"]}
    if kind == "session":
        if action == "create":
            d = date.fromisoformat(after["date"])
            row = next((r for r in _day_rows(client, "workout", d) if str(r.get("id")) == str(after["id"])), None)
            if row is None:
                raise ValueError("这次训练已经不在了")
            client.request("DELETE", _entry_path("workout", row))
            return {"deleted": row["id"]}
        return {"restored": _recreate_session(client, before)["id"]}
    if kind == "sets":
        d = date.fromisoformat(before["date"])
        rows = _day_rows(client, "set", d)
        entry = next((e for s in rows for e in _session_entries(s) if str(e.get("id")) == str(after.get("entry_id")
                                                                                              or before.get("entry_id"))), None)
        if entry is None:
            raise ValueError("这个动作已经不在了")
        client.request("PUT", f"/exercise-entries/{entry['id']}", {"sets": before["sets"]})
        return {"restored_sets": len(before["sets"])}
    if kind == "weight":
        d = (before or after)["date"]
        if before is None:  # the AI created that day's check-in: remove it
            cur = client.get(f"/measurements/check-in/{d}") or {}
            if cur.get("id") and str(cur.get("entry_date") or "")[:10] == d:
                client.request("DELETE", f"/measurements/check-in/{cur['id']}")
            return {"deleted": d}
        vals = {c: before.get(c) for c in (before if action == "delete" else after) if c != "date"}
        client.request("POST", "/measurements/check-in", {"entry_date": d, **vals})
        return {"restored": {"date": d, **vals}}
    raise ValueError(f"不支持回滚 {kind}")

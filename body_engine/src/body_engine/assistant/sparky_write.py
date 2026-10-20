"""Tools for the body butler to write SparkyFitness (food, workouts, weight). All are write tools: they can only write for the caller, and the MCP client asks the user to confirm before each run.

They use the read-write key (sparky_client.write_client_for_user) and check before writing:
- Food: date not in the future; calories and the three macros (+ alcohol) roughly add up; a same-named food already in the same meal of the same day is treated as a duplicate and skipped
  (only written with allow_duplicates=true). Each entry carries source=body_assistant and a source_id computed from its content, which SparkyFitness uses for idempotency
  (retrying the same entry never writes twice). SparkyFitness requires food entries to hang on a food in the food library, so a custom food is created / reused by content
  (provider_external_id = ba:<content hash>, 1 serving = this portion).
- Workouts: each exercise is first matched against the SparkyFitness exercise library (ID / name / Chinese and English names and aliases in exercise_meta); a match that is banned
  (marked banned in exercise_meta or 【禁用】 in the SparkyFitness description) rejects the whole call with the reason; the same exercise already logged that day is treated as a duplicate and skipped.
- Weight: an existing different value on the same day is not overwritten (only with overwrite=true), an identical one is skipped.
Undo: undo_sparky_entry only deletes food / workout entries the butler wrote itself (source=body_assistant), named by id or (food) by an exact
date + meal + name (+ time) match. The user deletes any of their own food entries on the Today page (delete_food_entry).
"""
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from .. import sparky_client
from ..records import store as records_store

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
    sig = json.dumps([name, amount, it["calories"], it["protein_g"], it["carbs_g"], it["fat_g"],
                      it.get("saturated_fat_g"), it.get("alcohol_g")], ensure_ascii=False)
    ext_id = "ba:" + hashlib.sha1(sig.encode()).hexdigest()[:16]
    page = client.get("/foods/foods-paginated", searchTerm=name, foodFilter="mine", currentPage=1, itemsPerPage=100,
                      sortBy="name:asc", providerType="") or {}
    hit = next((f for f in page.get("foods") or [] if f.get("provider_external_id") == ext_id), None)
    if hit is None:
        hit = client.request("POST", "/foods", {k: v for k, v in {
            "name": name, "is_custom": True, "provider_type": "custom", "provider_external_id": ext_id,
            "serving_size": 1, "serving_unit": amount, "calories": it["calories"], "protein": it["protein_g"],
            "carbs": it["carbs_g"], "fat": it["fat_g"], "saturated_fat": it.get("saturated_fat_g"),
            "alcohol_g": it.get("alcohol_g"), "notes": "身体管家记录饮食时建（营养是估算）"}.items() if v is not None})
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
        amount = it["amount"].strip()
        food_id, variant_id = _ensure_food(client, it)
        body = {"food_id": food_id, "variant_id": variant_id, "quantity": 1, "unit": amount,
                "entry_date": d.isoformat(), "entry_time": it.get("time"),
                "meal_type_id": meal_ids[it["meal"]], "meal_type": it["meal"],
                "notes": it.get("notes"), "source": SOURCE, "source_id": _food_source_id(d, it)}
        row = client.request("POST", "/food-entries", {k: v for k, v in body.items() if v is not None})
        days[d].append(row)
        created.append({"id": row["id"], "date": d.isoformat(), "meal": it["meal"], "food": it["food"].strip(),
                        "amount": amount, "calories": it["calories"], "protein_g": it["protein_g"],
                        "carbs_g": it["carbs_g"], "fat_g": it["fat_g"]})
    return {"created": created, "skipped_duplicates": skipped, "warnings": warnings,
            "undo": "记错了可以用 undo_sparky_entry(kind=food, date, id) 删掉"}


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


def search_exercises(conn, user_id: int, query: str, client=None) -> list[dict]:
    """Search the exercise library (name / alias contains query), with the Body Engine status (banned / caution / active)."""
    client = client or sparky_client.client_for_user(conn, user_id)
    idx = exercise_index(conn, user_id, client)
    q = _norm(query)
    out = []
    for e in idx["library"]:
        m = idx["meta_by_app_id"].get(e["id"])
        names = [e["name"], *(_meta_names(m) if m else [])]
        if not q or any(q in _norm(x) for x in names):
            out.append({"exercise_id": e["id"], "name": e["name"], "category": e.get("category"),
                        "primary_muscles": e.get("primary_muscles"),
                        "status": (m or {}).get("status") or ("banned" if banned_reason(idx, e["name"], e["id"]) else None),
                        "cues": (m or {}).get("cues"), "reason": (m or {}).get("reason")})
    return out[:30]


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
    return {"written": {"date": d.isoformat(), **todo}, "unchanged": same, "overwrote": conflict or None}


# ---------------------------------------------------------------- undo
def _day_rows(client, kind: str, d: date) -> list[dict]:
    if kind == "food":
        return client.get(f"/food-entries/by-date/{d.isoformat()}") or []
    if kind == "workout":
        return client.get("/v2/exercise-entries/by-date", selectedDate=d.isoformat()) or []
    raise ValueError("kind 只能是 food / workout")


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


def undo_entry(conn: psycopg.Connection, user_id: int, kind: str, day: str, entry_id: str | None = None, client=None,
               *, meal: str | None = None, food: str | None = None, time: str | None = None) -> dict:
    """Delete one record the butler wrote itself (source=body_assistant); other sources (logged manually in the app, migrated) are not deleted
    (the user deletes those on the Today page, delete_food_entry). The record is named by id, or for food by an exact date + meal + name (+ time)."""
    d = date.fromisoformat(day)
    if kind not in ("food", "workout"):
        raise ValueError("kind 只能是 food / workout")
    if not entry_id and kind == "workout":
        raise ValueError("训练要给 id（get_training_log 里每次训练的 id）")
    client = client or sparky_client.write_client_for_user(conn, user_id)
    rows = _day_rows(client, kind, d)
    if entry_id:
        row = next((r for r in rows if str(r.get("id")) == str(entry_id)), None)
        if row is None:
            raise ValueError(f"{d} 没有这条记录：{entry_id}")
    else:
        row = _match_food(rows, d, meal, food, time)
    if row.get("source") != SOURCE:
        raise PermissionError(f"这条不是身体管家写的（source={row.get('source')}），不删；"
                              "请用户在今天页「饮食」当天的明细里点开这条删除")
    client.request("DELETE", _entry_path(kind, row))
    return {"deleted": {"kind": kind, "date": d.isoformat(), "id": row["id"],
                        "name": row.get("food_name") or row.get("name")}}


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

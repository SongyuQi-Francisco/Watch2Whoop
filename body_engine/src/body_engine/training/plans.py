"""Weekly training plan engine (Training V2):
- Built-in 3 / 4 / 5-day splits and custom split proposals (`propose_plan`)
- Per-muscle-group weekly working set accounting against hypertrophy targets (`volume_summary`)
- Readiness autoregulation from daily recovery score and sleep debt (`readiness_advice`)
- Persistent storage in `training_plans` with banned-exercise safety gate (`save_plan`)
- Single-exercise swap with biomechanical alternative matching (`swap_exercise`)
- Best-effort bidirectional sync with SparkyFitness workout presets and weekly plan templates.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from .. import auth, sparky_client
from ..i18n import L, use_lang
from ..rules import params as RP, store as rules_store
from ..rules.context import load_overrides
from . import catalog, loads, params as P
from .catalog_data import GROUP_ZH, MUSCLE_GROUP, MUSCLE_ZH

WEEKDAY_ZH = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}
WEEKDAY_EN = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
BANNED_REASON_ZH, BANNED_REASON_EN = "该动作在用户的禁用名单中", "This exercise is on the user's banned list"   # a banned exercise without a reason of its own


def _shown_name(item: dict) -> str:
    """The exercise name the page shows: the English one in an English request (when the catalog has it), else the Chinese one."""
    return L(item["name_zh"], item.get("name_en") or item["name_zh"])


def _user_today(conn: psycopg.Connection, user_id: int) -> date:
    row = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    tz = ZoneInfo((row or {}).get("timezone") or "Australia/Sydney")
    return datetime.now(tz).date()


def format_sets_text(sets: int | None, rep_low: int | None, rep_high: int | None,
                     duration_sec: int | None = None, weight_kg: float | None = None,
                     note: str | None = None) -> str:
    """Human-readable prescription string, e.g. '4 × 8–12 · 20 kg' or '3 组 × 30 秒' (English request: '3 sets × 30 s')."""
    n = sets or 1
    en_sets = f"{n} set" if n == 1 else f"{n} sets"
    if duration_sec and not rep_low and not rep_high:
        base = L(f"{n} 组 × {duration_sec:g} 秒", f"{en_sets} × {duration_sec:g} s")
    elif rep_low and rep_high and rep_low != rep_high:
        base = f"{n} × {rep_low}–{rep_high}"
    elif rep_low or rep_high:
        r = rep_low or rep_high
        base = f"{n} × {r}"
    else:
        base = L(f"{n} 组", en_sets)
    if weight_kg:
        base += f" · {weight_kg:g} kg"
    if note:
        base += L(f"（{note}）", f" ({note.replace('每侧', 'per side').replace(' 秒', ' s')})")   # the templates' notes: 每侧 / 每侧 30 秒
    return base


def list_splits() -> list[dict]:
    """List built-in 3 / 4 / 5-day training split templates."""
    out = []
    for key, tpl in P.SPLIT_TEMPLATES.items():
        out.append({
            "split_type": key,
            "name": tpl["name"],
            "days_per_week": tpl["days_per_week"],
            "summary": tpl["summary"],
            "days": [
                {
                    "day_of_week": dow,
                    "weekday_zh": WEEKDAY_ZH.get(dow, f"周{dow}"),
                    "name": sname,
                    "exercise_count": len(exs),
                }
                for dow, sname, exs in tpl["days"]
            ],
        })
    return out


def readiness_advice(conn: psycopg.Connection, user_id: int, day: date | None = None) -> dict:
    """Compute today's training readiness autoregulation advice from daily_scores (recovery + sleep debt)."""
    day = day or _user_today(conn, user_id)
    row = conn.execute(
        """SELECT date, recovery, sleep_score, sleep_debt_min, strain, ctl, atl, tsb
           FROM daily_scores
           WHERE user_id = %s AND date <= %s AND recovery IS NOT NULL
           ORDER BY date DESC LIMIT 1""",
        (user_id, day),
    ).fetchone()
    if row is None or (day - row["date"]).days > 2:
        return {
            "date": day.isoformat(),
            "score_date": None,
            "tier": "unknown",
            "label": L("暂无恢复分", "No recovery score"),
            "recovery": None,
            "sleep_debt_min": None,
            "set_factor": 1.0,
            "rir_delta": 0,
            "advice": L("今日暂无恢复评分：按计划正常热身，根据热身组体感决定正式组重量（目标 RIR 1–2）。",
                        "No recovery score today: warm up as planned and pick the working weight by feel (target RIR 1–2)."),
        }
    rec = float(row["recovery"])
    debt = float(row["sleep_debt_min"]) if row.get("sleep_debt_min") is not None else 0.0
    if rec < P.RECOVERY_RED:
        tier, label, factor, rir_delta = "red", L("恢复红区 · 建议减量或改恢复日", "Red recovery · cut volume or make it a recovery day"), 0.65, 2
        advice = L(f"恢复分 {rec:.0f}（红区）：建议改为肩部康复激活 / 低强度有氧；"
                   "若坚持力量训练，正式组数减 30%–50%、重量下调 10%–15%，全程保留 RIR ≥ 3，不做大重量下肢复合动作。",
                   f"Recovery {rec:.0f} (red): switch to shoulder rehab activation or easy cardio. If you still lift, "
                   "cut working sets by 30%–50% and weights by 10%–15%, keep RIR ≥ 3 throughout, and skip heavy lower-body compounds.")
    elif rec < P.RECOVERY_GREEN or debt >= P.AUTOREG_SLEEP_DEBT_MIN:
        reason_str = L(f"恢复分 {rec:.0f}" + (f"、睡眠债 {debt:.0f} 分钟" if debt >= P.AUTOREG_SLEEP_DEBT_MIN else ""),
                       f"Recovery {rec:.0f}" + (f", sleep debt {debt:.0f} min" if debt >= P.AUTOREG_SLEEP_DEBT_MIN else ""))
        tier, label, factor, rir_delta = "yellow", L("恢复黄区 · 稳态执行", "Yellow recovery · steady work"), 0.85, 1
        advice = L(f"{reason_str}（黄区）：维持计划重量，前两个复合动作可减 1 组或保留 RIR 2–3，"
                   "今天不强行冲击次数上限或加重量。",
                   f"{reason_str} (yellow): keep the planned weights; the first two compounds can drop a set or keep RIR 2–3. "
                   "Don't push rep ceilings or add weight today.")
    else:
        tier, label, factor, rir_delta = "green", L("恢复绿区 · 适合渐进超负荷", "Green recovery · good for progressive overload"), 1.0, 0
        advice = L(f"恢复分 {rec:.0f}（绿区）：状态良好，按计划组数与目标重量执行；"
                   "若所有工作组都达到次数上限且动作不变形，下次可按增量进阶。",
                   f"Recovery {rec:.0f} (green): you're in good shape. Do the planned sets at the target weights; "
                   "if every working set hits the rep ceiling with clean form, add weight next time.")
    return {
        "date": day.isoformat(),
        "score_date": row["date"].isoformat(),
        "tier": tier,
        "label": label,
        "recovery": round(rec, 1),
        "sleep_debt_min": round(debt, 1) if row.get("sleep_debt_min") is not None else None,
        "set_factor": factor,
        "rir_delta": rir_delta,
        "advice": advice,
    }


def volume_summary(days: list[dict]) -> dict[str, dict]:
    """Calculate weekly working sets per major muscle group (direct primary sets + 0.5x secondary sets)."""
    direct: dict[str, int] = {g: 0 for g in P.WEEKLY_SET_TARGETS}
    effective: dict[str, float] = {g: 0.0 for g in P.WEEKLY_SET_TARGETS}
    frequency: dict[str, set[int]] = {g: set() for g in P.WEEKLY_SET_TARGETS}

    for d in days:
        dow = d.get("day_of_week") or 0
        for ex in d.get("exercises") or []:
            if (ex.get("category") or "strength") != "strength" or ex.get("addon"):   # mobility add-ons are warm-ups
                continue
            n_sets = int(ex.get("sets") or 0)
            if n_sets <= 0:
                continue
            prim_groups = {MUSCLE_GROUP.get(m) for m in (ex.get("primary_muscles") or []) if MUSCLE_GROUP.get(m)}
            sec_groups = {MUSCLE_GROUP.get(m) for m in (ex.get("secondary_muscles") or []) if MUSCLE_GROUP.get(m)} - prim_groups
            for g in prim_groups:
                if g in direct:
                    direct[g] += n_sets
                    effective[g] += n_sets
                    frequency[g].add(dow)
            for g in sec_groups:
                if g in effective:
                    effective[g] += 0.5 * n_sets

    out: dict[str, dict] = {}
    for g, (lo, hi) in P.WEEKLY_SET_TARGETS.items():
        d_sets = direct[g]
        e_sets = round(effective[g], 1)
        if d_sets < lo:
            status = "low"
            status_zh = "偏少"
        elif d_sets > hi:
            status = "high"
            status_zh = "偏高"
        else:
            status = "optimal"
            status_zh = "适中"
        out[g] = {
            "group": g,
            "name_zh": GROUP_ZH.get(g, g),
            "direct_sets": d_sets,
            "effective_sets": e_sets,
            "frequency_days": len(frequency[g]),
            "target_range": [lo, hi],
            "status": status,
            "status_zh": status_zh,
        }
    return out


def _normalize_item(conn: psycopg.Connection, user_id: int, raw: dict | tuple,
                    with_meta: bool = True) -> dict:
    """Resolve one exercise prescription from template tuple or dict against exercise_catalog."""
    if isinstance(raw, (tuple, list)):
        slug, sets, rep_low, rep_high, rest_sec, duration_sec, note = raw
        weight_kg = None
    else:
        slug = raw.get("slug") or raw.get("exercise") or raw.get("name") or ""
        sets = int(raw.get("sets") or 3)
        rep_low = raw.get("rep_low") if raw.get("rep_low") is not None else raw.get("reps")
        rep_high = raw.get("rep_high") if raw.get("rep_high") is not None else rep_low
        rest_sec = raw.get("rest_sec") if raw.get("rest_sec") is not None else 90
        duration_sec = raw.get("duration_sec")
        weight_kg = raw.get("weight_kg")
        note = raw.get("note") or raw.get("notes")

    resolved = catalog.resolve(conn, str(slug), user_id=user_id if with_meta else None)
    if resolved is None:
        return {
            "slug": str(slug),
            "name_zh": str(slug),
            "name_en": None,
            "category": "strength",
            "modality": "weight_reps",
            "movement_pattern": None,
            "equipment": [],
            "equipment_zh": [],
            "primary_muscles": [],
            "primary_muscles_zh": [],
            "secondary_muscles": [],
            "sets": sets,
            "rep_low": rep_low,
            "rep_high": rep_high,
            "rest_sec": rest_sec,
            "duration_sec": duration_sec,
            "weight_kg": weight_kg,
            "increment_kg": 2.5,
            "per_side": False,
            "note": note,
            "sets_text": format_sets_text(sets, rep_low, rep_high, duration_sec, weight_kg, note),
            "status": "active",
            "reason": None,
            "cues": None,
            "joint_flags": {},
            "app_exercise_id": None,
            "in_catalog": False,
        }
    return {
        "slug": resolved["slug"],
        "name_zh": resolved["name_zh"],
        "name_en": resolved["name_en"],
        "category": resolved["category"],
        "modality": resolved["modality"],
        "movement_pattern": resolved["movement_pattern"],
        "movement_pattern_zh": resolved.get("movement_pattern_zh"),
        "equipment": resolved["equipment"],
        "equipment_zh": resolved["equipment_zh"],
        "primary_muscles": resolved["primary_muscles"],
        "primary_muscles_zh": resolved["primary_muscles_zh"],
        "secondary_muscles": resolved["secondary_muscles"],
        "sets": sets,
        "rep_low": rep_low,
        "rep_high": rep_high,
        "rest_sec": rest_sec,
        "duration_sec": duration_sec,
        "weight_kg": weight_kg,
        "increment_kg": resolved.get("default_increment_kg"),
        "per_side": resolved.get("per_side", False),
        "note": note,
        "sets_text": format_sets_text(sets, rep_low, rep_high, duration_sec, weight_kg, note),
        "status": resolved["status"] if with_meta else "active",
        "reason": resolved.get("reason") if with_meta else None,
        "cues": resolved.get("cues"),
        "joint_flags": resolved.get("joint_flags") or {},
        "app_exercise_id": resolved.get("app_exercise_id"),
        "in_catalog": True,
    }


def propose_plan(
    conn: psycopg.Connection,
    user_id: int,
    split_type: str = "4_day_upper_lower",
    custom_days: list[dict] | None = None,
    name: str | None = None,
    notes: str | None = None,
    auto_swap_banned: bool = False,
    training_days: list[int] | None = None,
    start_date: date | str | None = None,
    addons: list[str] | None = None,
    log_client: sparky_client.SparkyClient | None = None,
    with_loads: bool = True,
) -> dict:
    """Generate a structured weekly training plan proposal, checking exercise restrictions and weekly set volume.
    training_days: the user's weekdays (sessions are put on them in order); start_date: any day from today (issue #65; the
    start week only gets the sessions still missing after what was already trained this week); addons: optional mobility
    warm-ups (issue #66 FB-3, suggested from the user's own restrictions); with_loads: add each exercise's recommended
    working weight (training/loads.py; not stored by save_plan)."""
    from . import plan_start
    catalog.ensure_seeded(conn)
    today = _user_today(conn, user_id)
    load_ctx = loads.Context(conn, user_id, user_id, today) if with_loads else None
    start_d = plan_start.check_start(date.fromisoformat(start_date) if isinstance(start_date, str) else start_date, today)
    if custom_days is not None:
        tpl_name = name or "自定义周训练计划"
        tpl_summary = notes or f"自定义 {len(custom_days)} 天分化计划"
        raw_days = [
            (int(d["day_of_week"]), d["name"], d.get("exercises") or [])
            for d in custom_days
        ]
        days_per_week = len(raw_days)
    else:
        if split_type not in P.SPLIT_TEMPLATES:
            valid = "、".join(P.SPLIT_TEMPLATES)
            raise ValueError(L(f"不支持的 split_type={split_type!r}；可选：{valid}，或传入 custom_days 自定义",
                               f"Unsupported split_type={split_type!r}; choose one of {valid.replace('、', ', ')}, or pass custom_days"))
        tpl = P.SPLIT_TEMPLATES[split_type]
        tpl_name = name or tpl["name"]
        tpl_summary = tpl["summary"]
        raw_days = tpl["days"]
        days_per_week = tpl["days_per_week"]
    raw_days = plan_start.remap_days(raw_days, training_days)

    days_out: list[dict] = []
    cautions: list[dict] = []
    banned_conflicts: list[dict] = []
    auto_swapped: list[dict] = []
    seen_caution: set[str] = set()

    for dow, sname, ex_list in raw_days:
        if not 1 <= int(dow) <= 7:
            raise ValueError(L(f"day_of_week 必须在 1（周一）到 7（周日）之间，收到 {dow}",
                               f"day_of_week must be between 1 (Mon) and 7 (Sun), got {dow}"))
        ex_items: list[dict] = []
        for raw_ex in ex_list:
            item = _normalize_item(conn, user_id, raw_ex, with_meta=True)
            if item["status"] == "banned":
                alts = catalog.recommend_alternatives(conn, item["slug"], user_id=user_id, limit=3)
                if auto_swap_banned and alts:
                    chosen = alts[0]
                    swapped = _normalize_item(conn, user_id, {
                        "slug": chosen["slug"],
                        "sets": item["sets"],
                        "rep_low": item["rep_low"],
                        "rep_high": item["rep_high"],
                        "rest_sec": item["rest_sec"],
                        "duration_sec": item["duration_sec"],
                        "weight_kg": item["weight_kg"],
                        "note": item["note"],
                    }, with_meta=True)
                    auto_swapped.append({
                        "day_of_week": dow,
                        "session_name": sname,
                        "from_slug": item["slug"],
                        "from_name_zh": item["name_zh"],
                        "banned_reason": item["reason"],
                        "to_slug": swapped["slug"],
                        "to_name_zh": swapped["name_zh"],
                    })
                    item = swapped
                else:
                    banned_conflicts.append({
                        "day_of_week": dow,
                        "weekday_zh": WEEKDAY_ZH.get(dow),
                        "session_name": sname,
                        "slug": item["slug"],
                        "name_zh": item["name_zh"],
                        "name_en": item.get("name_en"),
                        "reason": item["reason"] or BANNED_REASON_ZH,
                        "alternatives": [
                            {"slug": a["slug"], "name_zh": a["name_zh"], "name_en": a.get("name_en"), "cues": a["cues"],
                             "equipment_zh": a["equipment_zh"], "status": a["status"]}
                            for a in alts
                        ],
                    })
            if item["status"] == "caution" and item["slug"] not in seen_caution:
                seen_caution.add(item["slug"])
                cautions.append({
                    "slug": item["slug"],
                    "name_zh": item["name_zh"],
                    "status": "caution",
                    "reason": item["reason"],
                    "cues": item["cues"],
                })
            if load_ctx is not None:
                loads.attach(load_ctx, item)
            ex_items.append(item)

        days_out.append({
            "day_of_week": int(dow),
            "weekday_zh": WEEKDAY_ZH.get(int(dow), f"周{dow}"),
            "name": sname,
            "exercises": ex_items,
        })

    days_out.sort(key=lambda d: d["day_of_week"])
    addons_applied = plan_start.apply_addons(conn, user_id, days_out, addons, _normalize_item)
    vol = volume_summary(days_out)
    return {
        "start_date": start_d.isoformat(),
        "first_week": plan_start.first_week(conn, user_id, days_out, start_d, today, log_client),
        "addons": addons_applied,
        "addon_suggestions": [a for a in plan_start.suggested_addons(conn, user_id) if a["key"] not in addons_applied],
        "ask_user_first": P.PLAN_QUESTIONS,
        "split_type": split_type,
        "name": tpl_name,
        "days_per_week": days_per_week,
        "summary": tpl_summary,
        "notes": notes,
        "days": days_out,
        "volume_by_group": vol,
        "cautions": cautions,
        "banned_conflicts": banned_conflicts,
        "auto_swapped": auto_swapped,
        "can_save": len(banned_conflicts) == 0,
        "readiness": readiness_advice(conn, user_id),
    }


def ensure_sparky_exercise(conn: psycopg.Connection, user_id: int, ex_item: dict,
                           idx_by_source: dict[str, dict], idx_by_name: dict[str, dict],
                           idx_by_id: dict[str, dict], client: sparky_client.SparkyClient) -> str:
    """Find or create a matching exercise in SparkyFitness and return its SparkyFitness UUID."""
    app_id = ex_item.get("app_exercise_id")
    if app_id and app_id in idx_by_id:
        return app_id
    slug = ex_item["slug"]
    for prefix in ("w2w:", "bm:"):
        if prefix + slug in idx_by_source:
            return idx_by_source[prefix + slug]["id"]
    norm_zh = catalog.norm(ex_item["name_zh"])
    if norm_zh in idx_by_name:
        return idx_by_name[norm_zh]["id"]
    if ex_item.get("name_en") and catalog.norm(ex_item["name_en"]) in idx_by_name:
        return idx_by_name[catalog.norm(ex_item["name_en"])]["id"]

    status_prefix = {"banned": "【禁用】", "caution": "【注意】"}.get(ex_item.get("status") or "active", "")
    desc = f"{status_prefix}{ex_item.get('cues') or ''}".strip() or "Watch2Whoop 动作库同步"
    payload = {
        "name": ex_item["name_zh"],
        "category": ex_item.get("category") or "strength",
        "modality": ex_item.get("modality") or "weight_reps",
        "source": "custom",
        "source_id": f"w2w:{slug}",
        "is_custom": True,
        "description": desc,
        "equipment": ex_item.get("equipment") or None,
        "primary_muscles": ex_item.get("primary_muscles") or None,
        "secondary_muscles": ex_item.get("secondary_muscles") or None,
    }
    created = client.request("POST", "/exercises", {k: v for k, v in payload.items() if v is not None},
                             multipart_field="exerciseData")
    ex_id = created["id"]
    idx_by_id[ex_id] = created
    idx_by_source[f"w2w:{slug}"] = created
    idx_by_name[norm_zh] = created
    conn.execute(
        "UPDATE exercise_meta SET app_exercise_id = %s WHERE user_id = %s AND exercise_ref = %s AND app_exercise_id IS NULL",
        (ex_id, user_id, slug),
    )
    return ex_id


def _sync_plan_to_sparky(conn: psycopg.Connection, user_id: int, plan_row: dict,
                         client: sparky_client.SparkyClient | None = None) -> dict:
    """Best-effort sync of a saved training_plans row to SparkyFitness presets and weekly plan template."""
    try:
        client = client or sparky_client.write_client_for_user(conn, user_id)
    except (sparky_client.SparkyError, ValueError) as e:
        return {"synced": False, "reason": str(e)}

    try:
        lib = sparky_client.exercises(client)
        idx_by_id = {e["id"]: e for e in lib}
        idx_by_source = {e["source_id"]: e for e in lib if e.get("source_id")}
        idx_by_name = {catalog.norm(e["name"]): e for e in lib if e.get("name")}

        existing_presets = {
            p["name"]: p
            for p in (client.get("/workout-presets", page=1, limit=200) or {}).get("presets") or []
        }
        assignments = []
        preset_ids: dict[str, int] = {}

        for day_obj in plan_row["days"]:
            dow_iso = int(day_obj["day_of_week"])
            sparky_dow = dow_iso % 7  # SparkyFitness: 0 = Sunday, 1 = Monday ... 6 = Saturday
            sname = day_obj["name"]
            ex_payloads = []
            for idx, ex in enumerate(day_obj.get("exercises") or []):
                ex_id = ensure_sparky_exercise(conn, user_id, ex, idx_by_source, idx_by_name, idx_by_id, client)
                ex["app_exercise_id"] = ex_id
                n_sets = int(ex.get("sets") or 3)
                lo = ex.get("rep_low")
                hi = ex.get("rep_high") or lo
                inc = ex.get("increment_kg")
                dur = ex.get("duration_sec")
                rest = ex.get("rest_sec")
                w = ex.get("weight_kg")
                rep_note = f"{lo}–{hi} 次" if lo and hi and lo != hi else None
                set_note = "；".join(x for x in (rep_note, ex.get("note")) if x) or None
                ex_payloads.append({
                    "exercise_id": ex_id,
                    "sort_order": idx,
                    "progression_mode": "rep_goal" if inc and hi else "manual",
                    "rep_goal": hi if inc and hi else None,
                    "increment_type": "weight" if inc and hi else None,
                    "increment_value": inc if inc and hi else None,
                    "sets": [
                        {
                            "set_number": s + 1,
                            "set_type": "Working Set",
                            "reps": lo,
                            "weight": w,
                            "duration": dur,
                            "rest_time": rest,
                            "notes": set_note,
                        }
                        for s in range(n_sets)
                    ],
                })
            preset_body = {
                "name": sname,
                "description": f"{plan_row['name']} · {WEEKDAY_ZH.get(dow_iso, '')}（Watch2Whoop 同步）",
                "exercises": ex_payloads,
            }
            cur_preset = existing_presets.get(sname)
            if cur_preset is None:
                created_preset = client.request("POST", "/workout-presets", preset_body)
                pid = created_preset["id"]
            else:
                pid = cur_preset["id"]
                client.request("PUT", f"/workout-presets/{pid}", preset_body)
            preset_ids[sname] = pid
            assignments.append({
                "day_of_week": sparky_dow,
                "workout_preset_id": pid,
                "sort_order": 0,
            })

        start_str = (plan_row["start_date"].isoformat()
                     if isinstance(plan_row["start_date"], date)
                     else str(plan_row["start_date"])[:10])
        plan_payload = {
            "plan_name": plan_row["name"],
            "description": (plan_row.get("notes") or "") + "（Watch2Whoop 训练模块 v2）",
            "start_date": start_str,
            "end_date": None,
            "is_active": True,
            "schedule_type": "weekly",
            "entry_mode": "prompt",
            "assignments": assignments,
        }
        existing_plans = client.get("/workout-plan-templates") or []
        cur_plan = next((p for p in existing_plans if p.get("plan_name") == plan_row["name"]), None)
        if cur_plan is None:
            res_plan = client.request("POST", "/workout-plan-templates", plan_payload)
            sparky_plan_id = str((res_plan or {}).get("id") or "")
        else:
            sparky_plan_id = str(cur_plan["id"])
            client.request("PUT", f"/workout-plan-templates/{sparky_plan_id}", plan_payload)

        conn.execute(
            "UPDATE training_plans SET sparky_plan_id = %s, days = %s::jsonb, updated_at = now() WHERE id = %s",
            (sparky_plan_id or None, json.dumps(plan_row["days"], ensure_ascii=False), plan_row["id"]),
        )
        return {"synced": True, "sparky_plan_id": sparky_plan_id, "presets_synced": len(preset_ids)}
    except Exception as e:
        return {"synced": False, "reason": str(e)}


def save_plan(
    conn: psycopg.Connection,
    user_id: int,
    split_type: str = "4_day_upper_lower",
    days: list[dict] | None = None,
    name: str | None = None,
    start_date: date | str | None = None,
    notes: str | None = None,
    auto_swap_banned: bool = False,
    sync_sparky: bool = True,
    client: sparky_client.SparkyClient | None = None,
    training_days: list[int] | None = None,
    addons: list[str] | None = None,
    log_client: sparky_client.SparkyClient | None = None,
) -> dict:
    """Validate and persist an active weekly training plan in `training_plans`, blocking any banned exercises.
    The AI calls this only after the user confirmed the propose_plan draft (start day, rest of this week, weekdays)."""
    from . import plan_start
    with use_lang("zh"):   # what is stored is the Chinese catalog / template text; names are shown in the request's language later
        proposal = propose_plan(
            conn,
            user_id=user_id,
            split_type=split_type,
            custom_days=days,
            name=name,
            notes=notes,
            auto_swap_banned=auto_swap_banned,
            training_days=training_days,
            start_date=start_date,
            addons=addons,
            log_client=log_client,
            with_loads=False,
        )
    if proposal["banned_conflicts"]:
        reasons = [
            L(f"{c['weekday_zh']}「{c['name_zh']}」（{c['reason']}；可换："
              + ("、".join(a["name_zh"] for a in c["alternatives"]) or "无") + "）",
              f"{WEEKDAY_EN.get(c['day_of_week'], c['weekday_zh'])} “{c.get('name_en') or c['name_zh']}” "
              f"({BANNED_REASON_EN if c['reason'] == BANNED_REASON_ZH else c['reason']}; alternatives: "
              + (", ".join(a.get("name_en") or a["name_zh"] for a in c["alternatives"]) or "none") + ")")
            for c in proposal["banned_conflicts"]
        ]
        raise PermissionError(
            L("拒绝保存周训练计划：计划中包含禁用动作 —— " + "；".join(reasons)
              + "。请先替换禁用动作，或传 auto_swap_banned=true 自动替换为安全动作后再保存。",
              "The plan was not saved because it contains banned exercises: " + "; ".join(reasons)
              + ". Replace them first, or switch on automatic safe swaps, then save again.")
        )

    start_d = date.fromisoformat(proposal["start_date"])

    conn.execute(
        "UPDATE training_plans SET active = FALSE, updated_at = now() WHERE user_id = %s AND active = TRUE",
        (user_id,),
    )
    row = conn.execute(
        """INSERT INTO training_plans (user_id, name, split_type, days_per_week, start_date, active, days, notes)
           VALUES (%s, %s, %s, %s, %s, TRUE, %s::jsonb, %s)
           RETURNING *""",
        (
            user_id,
            proposal["name"],
            proposal["split_type"],
            proposal["days_per_week"],
            start_d,
            json.dumps(proposal["days"], ensure_ascii=False),
            proposal["notes"],
        ),
    ).fetchone()

    # Day changes of the old plan (training/day_overrides.py) no longer apply from the new plan's start
    cleared = conn.execute("DELETE FROM training_day_overrides WHERE user_id = %s AND date >= %s",
                           (user_id, max(start_d, _user_today(conn, user_id)))).rowcount

    # The start week: only the sessions still missing after what was already trained (day changes, source replan)
    first_week_changes = plan_start.apply_first_week(conn, user_id, row, proposal["first_week"])

    sparky_sync = {"synced": False, "reason": "未请求同步"}
    if sync_sparky:
        sparky_sync = _sync_plan_to_sparky(conn, user_id, row, client=client)
        if not sparky_sync.get("synced"):   # issue #66 FB-4: the plan is saved here all the same; say what to do
            link = sparky_client.link_state(conn, user_id)
            sparky_sync = {**sparky_sync, "link": link["state"],
                           "tell_user": "计划已保存在 Body Engine（今天页和 AI 都能用）；没同步到训练记录（SparkyFitness）。"
                                        + (link.get("hint") or "")}

    return {
        "first_week": proposal["first_week"],
        "first_week_changes": first_week_changes,
        "addons": proposal["addons"],
        **({"day_changes_cleared": cleared} if cleared else {}),
        "saved": {
            "id": row["id"],
            "name": row["name"],
            "split_type": row["split_type"],
            "days_per_week": row["days_per_week"],
            "start_date": row["start_date"].isoformat(),
            "active": row["active"],
            "notes": row["notes"],
            "days": proposal["days"],
        },
        "volume_by_group": proposal["volume_by_group"],
        "cautions": proposal["cautions"],
        "auto_swapped": proposal["auto_swapped"],
        "sparky_sync": sparky_sync,
    }


def _active_plan_row(conn: psycopg.Connection, user_id: int) -> dict | None:
    return conn.execute(
        "SELECT * FROM training_plans WHERE user_id = %s AND active = TRUE ORDER BY updated_at DESC, id DESC LIMIT 1",
        (user_id,),
    ).fetchone()


def _progression_suggestions(conn: psycopg.Connection, viewer_id: int, owner_id: int,
                             start: date, end: date) -> list[dict]:
    """Load recent progression reminders from the rule engine."""
    if not auth.can_view(conn, viewer_id, owner_id, "reminders"):
        return []
    by_ex: dict[str, list[dict]] = {}
    for r in rules_store.list_reminders(conn, owner_id, start, end, ["new", "seen"], ["progression"]):
        ev = r.get("evidence") or {}
        key = ev.get("exercise_id") or ev.get("exercise") or str(r["id"])
        by_ex.setdefault(key, []).append({
            "id": r["id"],
            "level": r["level"],
            "title": r["title"],
            "date": r.get("date"),
            "exercise_id": ev.get("exercise_id"),
            "exercise": ev.get("exercise"),
            "next_weight_kg": ev.get("next_weight_kg"),
        })
    out = []
    for key, items in by_ex.items():
        latest = max(items, key=lambda x: (str(x.get("date") or ""), x["id"]))
        out.append({"name": latest.get("exercise") or key, **latest})
    out.sort(key=lambda x: (str(x.get("date") or ""), x["id"]), reverse=True)
    return out


def get_week_plan(
    conn: psycopg.Connection,
    viewer_id: int,
    owner_id: int,
    day: date | None = None,
    detail: bool = True,
) -> dict:
    """Return the active weekly training plan for [Monday..Sunday] around `day`, enriched with exercise metadata,
    recommended working weights (training/loads.py), weekly muscle-group volume, progression suggestions, and readiness autoregulation.
    Read-only: every planned exercise is resolved inside one catalog.lookup_scope (the catalog, exercise_meta and custom
    exercises read once instead of once per exercise)."""
    with catalog.lookup_scope():
        return _get_week_plan(conn, viewer_id, owner_id, day, detail)


def _get_week_plan(conn: psycopg.Connection, viewer_id: int, owner_id: int, day: date | None, detail: bool) -> dict:
    if not auth.can_view(conn, viewer_id, owner_id, "training"):
        raise PermissionError(L("没有授权查看该用户的训练计划（需要 training 权限）",
                                "No permission to view this user's training plan (needs the training scope)"))
    from . import plan_add, plan_remove
    catalog.ensure_seeded(conn)
    day = day or _user_today(conn, owner_id)
    start = day - timedelta(days=day.weekday())
    end = start + timedelta(days=6)
    with_meta = auth.can_view(conn, viewer_id, owner_id, "exercises")
    with_scores = auth.can_view(conn, viewer_id, owner_id, "scores")

    sugg_list = _progression_suggestions(conn, viewer_id, owner_id, day - timedelta(days=6), day)
    sugg_by_name = {}
    for s in sugg_list:
        if s.get("exercise"):
            sugg_by_name[catalog.norm(s["exercise"])] = s
            hit = catalog.resolve(conn, s["exercise"], user_id=owner_id if with_meta else None)
            if hit:   # SparkyFitness names ("高位下拉（正手，略宽于肩）") -> the catalog slug the plan uses
                s["slug"] = hit["slug"]
                sugg_by_name.setdefault(hit["slug"], s)
        if s.get("exercise_id"):
            sugg_by_name[s["exercise_id"]] = s
    used_sugg: dict[int, dict] = {}   # suggestion id -> the plan exercise it belongs to

    plan_row = _active_plan_row(conn, owner_id)
    if plan_row is not None:
        load_ctx = loads.Context(conn, viewer_id, owner_id, day)
        raw_days_by_dow: dict[int, list[dict]] = {}
        normalized_days_for_vol: list[dict] = []
        cautions: list[dict] = []
        seen_caution: set[str] = set()

        for d_obj in plan_row.get("days") or []:
            dow = int(d_obj["day_of_week"])
            exs, vol_items = [], []
            for raw_ex in d_obj.get("exercises") or []:
                item = _normalize_item(conn, owner_id, raw_ex, with_meta=with_meta)
                vol_items.append(item)
                matched_sugg = []
                for key in (item.get("app_exercise_id"), item["slug"], catalog.norm(item["name_zh"])):
                    sg = sugg_by_name.get(key) if key else None
                    if sg and not (item.get("weight_kg") and sg.get("next_weight_kg")
                                   and float(item["weight_kg"]) >= float(sg["next_weight_kg"])):
                        matched_sugg.append(sg)   # a step the plan already prescribes is not suggested again
                        used_sugg[sg["id"]] = item
                    if sg:
                        break
                item["suggestions"] = matched_sugg
                loads.attach(load_ctx, item, matched_sugg[0] if matched_sugg else None)
                item["name"] = _shown_name(item)
                item["exercise_id"] = item.get("app_exercise_id") or item["slug"]
                if item["status"] != "active" and item["slug"] not in seen_caution:
                    seen_caution.add(item["slug"])
                    cautions.append({
                        "exercise_id": item["exercise_id"],
                        "slug": item["slug"],
                        "name": _shown_name(item),
                        "status": item["status"],
                        "reason": item["reason"],
                        "cues": item["cues"],
                    })
                if not detail:
                    exs.append({
                        "slug": item["slug"],
                        "name": _shown_name(item),
                        "sets_text": item["sets_text"],
                        "load": item["load_text"],
                        "status": item["status"],
                        "cues": item["cues"] if item["status"] != "active" else None,
                    })
                else:
                    exs.append(item)
            raw_days_by_dow.setdefault(dow, []).append({"name": d_obj["name"], "day_of_week": dow, "exercises": exs})
            normalized_days_for_vol.append({"day_of_week": dow, "name": d_obj["name"], "exercises": vol_items})

        # Day changes (training/day_overrides.py): a date may hold other weekdays' sessions, a free activity or rest
        ovs = load_overrides(conn, owner_id, start, end)
        extras = plan_add.extras_for(conn, owner_id, start, end)   # exercises added to one date only (issue #154)
        removals = plan_remove.removals_for(conn, owner_id, start, end)   # planned exercises the user removed for one date (issue #172)
        vol_by_dow: dict[int, list[dict]] = {}
        for nd in normalized_days_for_vol:
            vol_by_dow.setdefault(nd["day_of_week"], []).extend(nd["exercises"])
        week_days, vol_days = [], []
        for k in range(7):
            d = start + timedelta(days=k)
            dow = d.isoweekday()
            ov = ovs.get(d)
            before_start = d < plan_row["start_date"]   # issue #65: the plan starts on any day; nothing planned before it
            refs = [] if before_start else ov["sessions"] if ov is not None else [{"dow": dow}]
            planned, vol_items = [], []
            for ref in refs:
                if ref.get("dow"):
                    planned += raw_days_by_dow.get(int(ref["dow"]), [])
                    vol_items += vol_by_dow.get(int(ref["dow"]), [])
                elif ref.get("label"):
                    planned.append({"name": ref["label"], "exercises": []})
            if extras.get(d) and not before_start:
                extra_exs = []
                for raw_ex in extras[d]:
                    item = _normalize_item(conn, owner_id, raw_ex, with_meta=with_meta)
                    vol_items.append(item)
                    item["suggestions"] = []
                    loads.attach(load_ctx, item, None)
                    item["name"] = _shown_name(item)
                    item["exercise_id"] = item.get("app_exercise_id") or item["slug"]
                    item["added_for_day"] = True
                    if item["status"] != "active" and item["slug"] not in seen_caution:
                        seen_caution.add(item["slug"])
                        cautions.append({"exercise_id": item["exercise_id"], "slug": item["slug"], "name": _shown_name(item),
                                         "status": item["status"], "reason": item["reason"], "cues": item["cues"]})
                    extra_exs.append(item if detail else {
                        "slug": item["slug"], "name": _shown_name(item), "sets_text": item["sets_text"], "load": item["load_text"],
                        "status": item["status"], "cues": item["cues"] if item["status"] != "active" else None})
                planned.append({"name": plan_add.extra_session_name(), "exercises": extra_exs, "added_for_day": True})
            gone = {r["slug"] for r in removals.get(d, [])}
            if gone and not before_start:   # the user removed these for this date only; sessions are shared between days: copy
                planned = [{**p, "exercises": [e for e in p["exercises"] if e.get("slug") not in gone]} for p in planned]
                vol_items = [x for x in vol_items if x.get("slug") not in gone]
            vol_days.append({"day_of_week": dow, "exercises": vol_items})
            week_days.append({
                "date": d,
                "day_of_week": dow,
                "weekday_zh": WEEKDAY_ZH[dow],
                "planned": planned,
                "rest_day": len(planned) == 0 and not before_start,
                **({"before_start": True} if before_start else {}),
                **({"removed": removals[d]} if removals.get(d) and not before_start else {}),
                "change": None if before_start else ov and {"from": [p["name"] for p in raw_days_by_dow.get(dow, [])],
                                  "source": ov["source"], "reason": ov.get("reason")},
            })

        return {
            "start": start,
            "end": end,
            "date": day,
            "plan": {
                "id": plan_row["id"],
                "name": plan_row["name"],
                "split_type": plan_row["split_type"],
                "days_per_week": plan_row["days_per_week"],
                "start_date": plan_row["start_date"].isoformat() if isinstance(plan_row["start_date"], date) else str(plan_row["start_date"]),
                "notes": plan_row.get("notes"),
                "source": "body_engine",
            },
            "days": week_days,
            "volume_by_group": volume_summary(vol_days),
            # FB-5: only suggestions about exercises in this plan (the rule engine also writes them for old SparkyFitness plans)
            "suggestions": [{**sg, "slug": used_sugg[sg["id"]]["slug"]} for sg in sugg_list if sg["id"] in used_sugg],
            "cautions": cautions,
            "readiness": readiness_advice(conn, owner_id, day) if with_scores else None,
            "available_splits": list_splits() if detail else None,
            **({"load_history_error": load_ctx.history_error} if load_ctx.history_error and detail else {}),
        }

    # No active plan in training_plans yet: return empty plan with available splits so the UI or AI can propose one
    week_days = []
    for k in range(7):
        d = start + timedelta(days=k)
        dow = d.isoweekday()
        week_days.append({
            "date": d,
            "day_of_week": dow,
            "weekday_zh": WEEKDAY_ZH[dow],
            "planned": [],
            "rest_day": False,
        })
    return {
        "start": start,
        "end": end,
        "date": day,
        "plan": None,
        "days": week_days,
        "volume_by_group": {},
        "suggestions": sugg_list,
        "cautions": [],
        "readiness": readiness_advice(conn, owner_id, day) if with_scores else None,
        "available_splits": list_splits(),
    }


def _pattern_of(conn: psycopg.Connection, ex: dict) -> str | None:
    """Movement pattern of a stored plan exercise (plans saved before it was stored: looked up in the catalog)."""
    return ex.get("movement_pattern") or (catalog.resolve(conn, ex.get("slug") or "") or {}).get("movement_pattern")


def _not_in_plan_message(days: list[dict], from_exercise: str, from_slug: str,
                         day_of_week: int | None, session_name: str | None) -> str:
    """An error the AI can act on (FB-5): that day's exercises by slug, and the days that do have the exercise."""
    def label(d_obj: dict) -> str:
        return f"{WEEKDAY_ZH.get(int(d_obj.get('day_of_week') or 0), '')} {d_obj.get('name') or ''}".strip()

    chosen = [d for d in days
              if (day_of_week is None or int(d.get("day_of_week") or 0) == int(day_of_week))
              and (not session_name or catalog.norm(session_name) in catalog.norm(d.get("name") or ""))]
    msg = f"当前周训练计划里没有找到动作「{from_exercise}」" + (f"（day_of_week={day_of_week}）" if day_of_week else "")
    if day_of_week is not None or session_name:
        if not chosen:
            msg += "；计划里没有这一天 / 这个训练，计划的训练日：" + "、".join(label(d) for d in days)
        for d in chosen:
            msg += f"；{label(d)} 的动作：" + "、".join(
                f"{e.get('slug')}（{e.get('name_zh') or e.get('name') or ''}）" for e in d.get("exercises") or [])
    elsewhere = [label(d) for d in days if d not in chosen
                 and any(e.get("slug") == from_slug for e in d.get("exercises") or [])]
    if elsewhere:
        msg += f"；这个动作在：{'、'.join(elsewhere)}"
    return msg + "。from_exercise 用上面的 slug 再试"


def swap_exercise(
    conn: psycopg.Connection,
    user_id: int,
    from_exercise: str,
    to_exercise: str | None = None,
    day_of_week: int | None = None,
    session_name: str | None = None,
    sync_sparky: bool = True,
    client: sparky_client.SparkyClient | None = None,
) -> dict:
    """Swap an exercise in the user's active weekly plan (or recommend safe alternatives when `to_exercise` is None)."""
    catalog.ensure_seeded(conn)
    plan_row = _active_plan_row(conn, user_id)
    if plan_row is None:
        raise ValueError(L("当前没有生效的周训练计划；请先用 propose_plan / save_plan 创建周计划",
                           "There is no active weekly plan yet; create one first"))

    from_resolved = catalog.resolve(conn, from_exercise, user_id=user_id)
    from_slug = from_resolved["slug"] if from_resolved else catalog.norm(from_exercise)

    # Locate matching exercise entries in the active plan
    days = plan_row.get("days") or []
    matches = []
    for d_idx, d_obj in enumerate(days):
        if day_of_week is not None and int(d_obj.get("day_of_week") or 0) != int(day_of_week):
            continue
        if session_name and catalog.norm(session_name) not in catalog.norm(d_obj.get("name") or ""):
            continue
        for e_idx, ex in enumerate(d_obj.get("exercises") or []):
            ex_slug = ex.get("slug") or ""
            ex_zh = ex.get("name_zh") or ex.get("name") or ""
            if (ex_slug == from_slug
                    or catalog.norm(ex_zh) == catalog.norm(from_exercise)
                    or (from_resolved and ex_slug == from_resolved["slug"])):
                matches.append((d_idx, e_idx, d_obj, ex))

    matched_by = "exact"
    if not matches and from_resolved and from_resolved.get("movement_pattern"):
        # FB-5: the AI named a sibling of the planned exercise (cable_face_pull for the plan's band_face_pull_er warm-up).
        # Within the chosen day(s), one exercise of the same movement pattern is unambiguous; several are not.
        same = [(d_idx, e_idx, d_obj, ex) for d_idx, d_obj in enumerate(days)
                if (day_of_week is None or int(d_obj.get("day_of_week") or 0) == int(day_of_week))
                and (not session_name or catalog.norm(session_name) in catalog.norm(d_obj.get("name") or ""))
                for e_idx, ex in enumerate(d_obj.get("exercises") or [])
                if _pattern_of(conn, ex) == from_resolved["movement_pattern"]]
        if len(same) == 1 or (same and day_of_week is None and len({m[3].get("slug") for m in same}) == 1):
            matches, matched_by = same, "movement_pattern"

    if not matches:
        raise ValueError(_not_in_plan_message(days, from_exercise, from_slug, day_of_week, session_name))

    target_slug = matches[0][3].get("slug") or from_slug
    alternatives = catalog.recommend_alternatives(conn, target_slug, user_id=user_id, limit=5)

    if not to_exercise:
        return {
            "swapped": False,
            "from_exercise": {"slug": target_slug, "name_zh": matches[0][3].get("name_zh") or from_exercise},
            "matches_in_plan": [
                {"day_of_week": m[2]["day_of_week"], "weekday_zh": WEEKDAY_ZH.get(int(m[2]["day_of_week"])),
                 "session_name": m[2]["name"]}
                for m in matches
            ],
            "recommended_alternatives": alternatives,
        }

    to_resolved = catalog.resolve(conn, to_exercise, user_id=user_id)
    if to_resolved is None:
        cands = catalog.search(conn, user_id=user_id, query=to_exercise, limit=5)
        cand_names = [c["name_zh"] for c in cands]
        cand_en = [c.get("name_en") or c["name_zh"] for c in cands]
        raise ValueError(L(f"动作库里找不到替代动作「{to_exercise}」" + (f"；相近动作：{'、'.join(cand_names)}" if cand_names else ""),
                           f"The exercise library has no replacement “{to_exercise}”" + (f"; similar: {', '.join(cand_en)}" if cand_en else "")))

    if to_resolved["status"] == "banned":
        safe_alts = catalog.recommend_alternatives(conn, to_resolved["slug"], user_id=user_id, limit=3)
        raise PermissionError(L(
            f"拒绝替换：「{to_resolved['name_zh']}」是禁用动作"
            + (f"（{to_resolved['reason']}）" if to_resolved.get("reason") else "")
            + (f"；建议改用：{'、'.join(a['name_zh'] for a in safe_alts)}" if safe_alts else ""),
            f"Swap refused: “{to_resolved.get('name_en') or to_resolved['name_zh']}” is a banned exercise"
            + (f" ({to_resolved['reason']})" if to_resolved.get("reason") else "")
            + (f"; use one of these instead: {', '.join(a.get('name_en') or a['name_zh'] for a in safe_alts)}" if safe_alts else "")))

    replaced_in = []
    for d_idx, e_idx, d_obj, old_ex in matches:
        new_item = _normalize_item(conn, user_id, {
            "slug": to_resolved["slug"],
            "sets": old_ex.get("sets") or 3,
            "rep_low": old_ex.get("rep_low"),
            "rep_high": old_ex.get("rep_high"),
            "rest_sec": old_ex.get("rest_sec") or 90,
            "duration_sec": old_ex.get("duration_sec"),
            "weight_kg": None,  # Reset weight when swapping to a different exercise
            "note": "每侧" if to_resolved.get("per_side") else None,
        }, with_meta=True)
        days[d_idx]["exercises"][e_idx] = new_item
        replaced_in.append({
            "day_of_week": d_obj["day_of_week"],
            "weekday_zh": WEEKDAY_ZH.get(int(d_obj["day_of_week"])),
            "session_name": d_obj["name"],
            "from_slug": old_ex.get("slug"),
            "from_name_zh": old_ex.get("name_zh"),
            "to_slug": new_item["slug"],
            "to_name_zh": new_item["name_zh"],
            "sets_text": new_item["sets_text"],
        })

    conn.execute(
        "UPDATE training_plans SET days = %s::jsonb, updated_at = now() WHERE id = %s",
        (json.dumps(days, ensure_ascii=False), plan_row["id"]),
    )
    plan_row["days"] = days
    sparky_sync = {"synced": False, "reason": "未请求同步"}
    if sync_sparky:
        sparky_sync = _sync_plan_to_sparky(conn, user_id, plan_row, client=client)

    return {
        "swapped": True,
        "plan_id": plan_row["id"],
        **({"matched_by": matched_by} if matched_by != "exact" else {}),
        "replaced": replaced_in,
        "to_exercise": {
            "slug": to_resolved["slug"],
            "name_zh": to_resolved["name_zh"],
            "name_en": to_resolved["name_en"],
            "status": to_resolved["status"],
            "cues": to_resolved["cues"],
            "equipment_zh": to_resolved["equipment_zh"],
            "primary_muscles_zh": to_resolved["primary_muscles_zh"],
        },
        "volume_by_group": volume_summary(days),
        "sparky_sync": sparky_sync,
    }

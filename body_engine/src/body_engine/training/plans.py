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
from ..rules import params as RP, store as rules_store
from . import catalog, params as P
from .catalog_data import GROUP_ZH, MUSCLE_GROUP, MUSCLE_ZH

WEEKDAY_ZH = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


def _user_today(conn: psycopg.Connection, user_id: int) -> date:
    row = conn.execute("SELECT timezone FROM users WHERE id = %s", (user_id,)).fetchone()
    tz = ZoneInfo((row or {}).get("timezone") or "Australia/Sydney")
    return datetime.now(tz).date()


def format_sets_text(sets: int | None, rep_low: int | None, rep_high: int | None,
                     duration_sec: int | None = None, weight_kg: float | None = None,
                     note: str | None = None) -> str:
    """Human-readable Chinese prescription string, e.g. '4 × 8–12 · 20 kg' or '3 组 × 30 秒'."""
    n = sets or 1
    if duration_sec and not rep_low and not rep_high:
        base = f"{n} 组 × {duration_sec:g} 秒"
    elif rep_low and rep_high and rep_low != rep_high:
        base = f"{n} × {rep_low}–{rep_high}"
    elif rep_low or rep_high:
        r = rep_low or rep_high
        base = f"{n} × {r}"
    else:
        base = f"{n} 组"
    if weight_kg:
        base += f" · {weight_kg:g} kg"
    if note:
        base += f"（{note}）"
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
            "label": "暂无恢复分",
            "recovery": None,
            "sleep_debt_min": None,
            "set_factor": 1.0,
            "rir_delta": 0,
            "advice": "今日暂无恢复评分：按计划正常热身，根据热身组体感决定正式组重量（目标 RIR 1–2）。",
        }
    rec = float(row["recovery"])
    debt = float(row["sleep_debt_min"]) if row.get("sleep_debt_min") is not None else 0.0
    if rec < P.RECOVERY_RED:
        tier, label, factor, rir_delta = "red", "恢复红区 · 建议减量或改恢复日", 0.65, 2
        advice = (f"恢复分 {rec:.0f}（红区）：建议改为肩部康复激活 / 低强度有氧；"
                  "若坚持力量训练，正式组数减 30%–50%、重量下调 10%–15%，全程保留 RIR ≥ 3，不做大重量下肢复合动作。")
    elif rec < P.RECOVERY_GREEN or debt >= P.AUTOREG_SLEEP_DEBT_MIN:
        reason_str = f"恢复分 {rec:.0f}" + (f"、睡眠债 {debt:.0f} 分钟" if debt >= P.AUTOREG_SLEEP_DEBT_MIN else "")
        tier, label, factor, rir_delta = "yellow", "恢复黄区 · 稳态执行", 0.85, 1
        advice = (f"{reason_str}（黄区）：维持计划重量，前两个复合动作可减 1 组或保留 RIR 2–3，"
                  "今天不强行冲击次数上限或加重量。")
    else:
        tier, label, factor, rir_delta = "green", "恢复绿区 · 适合渐进超负荷", 1.0, 0
        advice = (f"恢复分 {rec:.0f}（绿区）：状态良好，按计划组数与目标重量执行；"
                  "若所有工作组都达到次数上限且动作不变形，下次可按增量进阶。")
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
            if (ex.get("category") or "strength") != "strength":
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
) -> dict:
    """Generate a structured weekly training plan proposal, checking exercise restrictions and weekly set volume."""
    catalog.ensure_seeded(conn)
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
            raise ValueError(f"不支持的 split_type={split_type!r}；可选：{valid}，或传入 custom_days 自定义")
        tpl = P.SPLIT_TEMPLATES[split_type]
        tpl_name = name or tpl["name"]
        tpl_summary = tpl["summary"]
        raw_days = tpl["days"]
        days_per_week = tpl["days_per_week"]

    days_out: list[dict] = []
    cautions: list[dict] = []
    banned_conflicts: list[dict] = []
    auto_swapped: list[dict] = []
    seen_caution: set[str] = set()

    for dow, sname, ex_list in raw_days:
        if not 1 <= int(dow) <= 7:
            raise ValueError(f"day_of_week 必须在 1（周一）到 7（周日）之间，收到 {dow}")
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
                        "reason": item["reason"] or "该动作在用户的禁用名单中",
                        "alternatives": [
                            {"slug": a["slug"], "name_zh": a["name_zh"], "cues": a["cues"],
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
            ex_items.append(item)

        days_out.append({
            "day_of_week": int(dow),
            "weekday_zh": WEEKDAY_ZH.get(int(dow), f"周{dow}"),
            "name": sname,
            "exercises": ex_items,
        })

    vol = volume_summary(days_out)
    return {
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
) -> dict:
    """Validate and persist an active weekly training plan in `training_plans`, blocking any banned exercises."""
    proposal = propose_plan(
        conn,
        user_id=user_id,
        split_type=split_type,
        custom_days=days,
        name=name,
        notes=notes,
        auto_swap_banned=auto_swap_banned,
    )
    if proposal["banned_conflicts"]:
        reasons = [
            f"{c['weekday_zh']}「{c['name_zh']}」（{c['reason']}；可换："
            + ("、".join(a["name_zh"] for a in c["alternatives"]) or "无") + "）"
            for c in proposal["banned_conflicts"]
        ]
        raise PermissionError(
            "拒绝保存周训练计划：计划中包含禁用动作 —— " + "；".join(reasons)
            + "。请先替换禁用动作，或传 auto_swap_banned=true 自动替换为安全动作后再保存。"
        )

    start_d = (date.fromisoformat(start_date) if isinstance(start_date, str)
               else (start_date or _user_today(conn, user_id)))

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

    sparky_sync = {"synced": False, "reason": "未请求同步"}
    if sync_sparky:
        sparky_sync = _sync_plan_to_sparky(conn, user_id, row, client=client)

    return {
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
    weekly muscle-group volume, progression suggestions, and readiness autoregulation."""
    if not auth.can_view(conn, viewer_id, owner_id, "training"):
        raise PermissionError("没有授权查看该用户的训练计划（需要 training 权限）")
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
        if s.get("exercise_id"):
            sugg_by_name[s["exercise_id"]] = s

    plan_row = _active_plan_row(conn, owner_id)
    if plan_row is not None:
        raw_days_by_dow: dict[int, list[dict]] = {}
        normalized_days_for_vol: list[dict] = []
        cautions: list[dict] = []
        seen_caution: set[str] = set()

        for d_obj in plan_row.get("days") or []:
            dow = int(d_obj["day_of_week"])
            exs = []
            for raw_ex in d_obj.get("exercises") or []:
                item = _normalize_item(conn, owner_id, raw_ex, with_meta=with_meta)
                matched_sugg = []
                if item.get("app_exercise_id") and item["app_exercise_id"] in sugg_by_name:
                    matched_sugg.append(sugg_by_name[item["app_exercise_id"]])
                elif catalog.norm(item["name_zh"]) in sugg_by_name:
                    matched_sugg.append(sugg_by_name[catalog.norm(item["name_zh"])])
                item["suggestions"] = matched_sugg
                item["name"] = item["name_zh"]
                item["exercise_id"] = item.get("app_exercise_id") or item["slug"]
                if item["status"] != "active" and item["slug"] not in seen_caution:
                    seen_caution.add(item["slug"])
                    cautions.append({
                        "exercise_id": item["exercise_id"],
                        "slug": item["slug"],
                        "name": item["name_zh"],
                        "status": item["status"],
                        "reason": item["reason"],
                        "cues": item["cues"],
                    })
                if not detail:
                    exs.append({
                        "slug": item["slug"],
                        "name": item["name_zh"],
                        "sets_text": item["sets_text"],
                        "status": item["status"],
                        "cues": item["cues"] if item["status"] != "active" else None,
                    })
                else:
                    exs.append(item)
            raw_days_by_dow.setdefault(dow, []).append({"name": d_obj["name"], "exercises": exs})
            normalized_days_for_vol.append({
                "day_of_week": dow,
                "name": d_obj["name"],
                "exercises": [_normalize_item(conn, owner_id, x, with_meta=with_meta) for x in d_obj.get("exercises") or []],
            })

        week_days = []
        for k in range(7):
            d = start + timedelta(days=k)
            dow = d.isoweekday()
            planned = raw_days_by_dow.get(dow, [])
            week_days.append({
                "date": d,
                "day_of_week": dow,
                "weekday_zh": WEEKDAY_ZH[dow],
                "planned": planned,
                "rest_day": len(planned) == 0,
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
            "volume_by_group": volume_summary(normalized_days_for_vol),
            "suggestions": sugg_list,
            "cautions": cautions,
            "readiness": readiness_advice(conn, owner_id, day) if with_scores else None,
            "available_splits": list_splits() if detail else None,
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
        raise ValueError("当前没有生效的周训练计划；请先用 propose_plan / save_plan 创建周计划")

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

    if not matches:
        raise ValueError(f"当前周训练计划里没有找到动作「{from_exercise}」"
                         + (f"（day_of_week={day_of_week}）" if day_of_week else ""))

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
        raise ValueError(f"动作库里找不到替代动作「{to_exercise}」"
                         + (f"；相近动作：{'、'.join(cand_names)}" if cand_names else ""))

    if to_resolved["status"] == "banned":
        safe_alts = catalog.recommend_alternatives(conn, to_resolved["slug"], user_id=user_id, limit=3)
        raise PermissionError(
            f"拒绝替换：「{to_resolved['name_zh']}」是禁用动作"
            + (f"（{to_resolved['reason']}）" if to_resolved.get("reason") else "")
            + (f"；建议改用：{'、'.join(a['name_zh'] for a in safe_alts)}" if safe_alts else "")
        )

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

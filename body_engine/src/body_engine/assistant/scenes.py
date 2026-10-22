"""Scene context tools (issue #100, docs/ai-assistant-plan.md §9.18): one read returns everything a task needs, plus
`rules` (guardrails the AI must follow) and `needs` (what is missing and should be asked or fixed first). Same convention as
grocery/prices.shopping_context; the matching playbooks are ai/playbooks/*.md.

- get_training_context: this week's plan, readiness, banned / caution exercises, the last 14 days of training and load,
  the training days and equipment we know of;
- get_week_review_context: one week's recovery, sleep, load, stress, training, food adherence, weight, journal and events,
  compared with the week before (insights/reports.report, the same numbers as the Today page's weekly report).
Authorization per section, like get_briefing: viewing someone else only shows the sections their shares allow (`hidden`);
one failing section (e.g. SparkyFitness down) is reported in place and does not spoil the rest. Output is compact-friendly
(short rows, no ids the AI does not need).
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from .. import auth, queries
from ..ai import params as P
from ..insights import reports, views
from ..records import store as records_store
from ..training import history, plans
from ..training.tools import slim_calendar


def _today(conn, uid: int) -> date:
    tz = (conn.execute("SELECT timezone FROM users WHERE id = %s", (uid,)).fetchone() or {}).get("timezone") or "UTC"
    return datetime.now(ZoneInfo(tz)).date()


def _sections(conn, viewer_id: int, owner_id: int, sections: tuple, fns: dict, out: dict) -> list[str]:
    """Fill out[key] for every (key, scope) the viewer may see; returns the hidden keys."""
    hidden = []
    for key, scope in sections:
        if not auth.can_view(conn, viewer_id, owner_id, scope):
            hidden.append(key)
            continue
        try:
            with conn.transaction():
                out[key] = fns[key]()
        except Exception as e:
            out[key] = {"error": f"{type(e).__name__}: {e}"}
    if hidden:
        out["hidden"] = hidden
    return hidden


def _profile_card(conn, uid: int, key: str) -> str | None:
    row = conn.execute("SELECT value FROM assistant_profile WHERE user_id = %s AND key = %s", (uid, key)).fetchone()
    return row["value"] if row else None


# ---------------------------------------------------------------- training
TRAINING_RULES = (
    "禁用动作（exercises.banned）一律不排、不推荐，给同模式的替代动作（get_exercise 的 alternatives）；注意动作按 cues 做。",
    "按 readiness 调整强度：红区减量或改恢复日，黄区不加重量、不冲次数；连续练同一肌群前看 recent 里最近几天练了什么。",
    "新排或重排整周计划：先用 propose_plan 出草案给用户看，用户确认后才 save_plan；只改某一天用 reschedule_day / swap_days。",
    "只改这个用户自己的计划；看别人的数据时只给建议，不写。",
)


def _plan_section(conn, viewer_id: int, owner_id: int, day: date) -> dict:
    wk = plans.get_week_plan(conn, viewer_id, owner_id, day, detail=False)
    days = []
    for d in wk["days"]:
        row = {"date": d["date"], "wd": d["weekday_zh"]}
        if d.get("before_start"):
            row["before_start"] = True
        elif d["rest_day"]:
            row["rest"] = True
        else:
            row["sessions"] = [{"name": s["name"],
                                "exercises": [f"{e['name']} {e['sets_text'] or ''} {e['load'] or ''}".strip()
                                              for e in s["exercises"]]} for s in d["planned"]]
        if d.get("change"):
            row["changed_from"] = d["change"]["from"]
        days.append(row)
    plan = wk["plan"]
    row = plans._active_plan_row(conn, owner_id) if plan else None
    return {"plan": plan and {k: plan[k] for k in ("name", "split_type", "days_per_week", "start_date", "notes")},
            "week": {"start": wk["start"], "end": wk["end"]}, "days": days,
            # weekly sets per muscle group: "effective (target low–high) status"
            "volume_by_group": {v["name_zh"]: f"{v['effective_sets']:g} 组（{v['target_range'][0]}–{v['target_range'][1]}）"
                                              f"{v['status_zh']}" for v in wk["volume_by_group"].values()},
            "progression": [{"exercise": s["name"], "next_weight_kg": s.get("next_weight_kg"), "title": s["title"]}
                            for s in wk["suggestions"]],
            "training_days": sorted({int(d["day_of_week"]) for d in (row or {}).get("days") or []}) or None,
            "equipment_in_plan": sorted({x for d in (row or {}).get("days") or [] for e in d.get("exercises") or []
                                         for x in plans._normalize_item(conn, owner_id, e, with_meta=False)["equipment_zh"]})
                                 or None}


def _readiness_section(conn, uid: int, day: date) -> dict:
    r = plans.readiness_advice(conn, uid, day)
    out = {k: r[k] for k in ("score_date", "tier", "recovery", "sleep_debt_min", "set_factor", "rir_delta", "advice")}
    last = conn.execute("""SELECT date, strain, ctl, atl, tsb FROM daily_scores WHERE user_id = %s AND date <= %s
                           AND ctl IS NOT NULL ORDER BY date DESC LIMIT 1""", (uid, day)).fetchone()
    if last:
        out["load"] = {"date": last["date"], "ctl": last["ctl"], "atl": last["atl"], "tsb": last["tsb"]}
    try:
        coach = views.strain_coach(conn, uid, day)
        out["strain_target"] = {k: coach.get(k) for k in ("zone", "target_low", "target_high", "current_strain")
                                if coach.get(k) is not None}
    except Exception:  # the target is extra; readiness stands without it
        pass
    return out


def _exercise_section(conn, uid: int) -> dict:
    rules = records_store.exercise_rules(conn, uid)
    return {"banned": [{"name": r["name_zh"], "reason": r["reason"]} for r in rules["banned"]],
            "caution": [{"name": r["name_zh"], "reason": r["reason"], "cues": r["cues"]} for r in rules["caution"]]}


def _recent_section(conn, viewer_id: int, uid: int, day: date) -> dict:
    cal = slim_calendar(history.calendar(conn, viewer_id, uid, start=day - timedelta(days=P.TRAINING_RECENT_DAYS - 1),
                                         end=day))
    cal["days"] = [d for d in cal["days"] if d["sessions"] or d["strain"] is not None]
    return cal


TRAINING_SECTIONS = (("plan", "training"), ("readiness", "scores"), ("exercises", "exercises"), ("recent", "training"))


def training_context(conn: psycopg.Connection, viewer_id: int, owner_id: int, day: date | None = None) -> dict:
    day = day or _today(conn, owner_id)
    out: dict = {"date": day}
    fns = {"plan": lambda: _plan_section(conn, viewer_id, owner_id, day),
           "readiness": lambda: _readiness_section(conn, owner_id, day),
           "exercises": lambda: _exercise_section(conn, owner_id),
           "recent": lambda: _recent_section(conn, viewer_id, owner_id, day)}
    _sections(conn, viewer_id, owner_id, TRAINING_SECTIONS, fns, out)
    if auth.can_view(conn, viewer_id, owner_id, "memory"):
        out["equipment"] = _profile_card(conn, owner_id, P.EQUIPMENT_CARD_KEY)
    out["rules"] = list(TRAINING_RULES)
    out["needs"] = _training_needs(out)
    return out


def _training_needs(out: dict) -> list[str]:
    needs = []
    plan = out.get("plan") or {}
    if "error" in plan:
        needs.append("训练计划读不到：" + plan["error"])
    elif "plan" in out and not plan.get("plan"):
        needs.append("还没有周计划：先问用户每周哪几天能练、从哪天开始、练几分化，再 propose_plan")
    elif "plan" in out and not plan.get("training_days"):
        needs.append("不知道每周哪几天能练：问用户")
    if "equipment" in out and not out["equipment"]:
        needs.append(f"不知道有哪些器械（家里 / 健身房）：问用户，记进档案卡 {P.EQUIPMENT_CARD_KEY}（update_profile_card）")
    r = out.get("readiness") or {}
    if "readiness" in out and r.get("tier") == "unknown":
        needs.append("最近 2 天没有恢复分：按计划热身后看体感，或请用户同步手表数据")
    rec = out.get("recent") or {}
    if rec.get("sparky_error"):
        needs.append("力量训练记录（SparkyFitness）没连上：推荐重量和最近的组数看不到，告诉用户去「我 → 训练记录」连接")
    return needs


# ---------------------------------------------------------------- week review
REVIEW_RULES = (
    "只说有数据的；没记录 ≠ 没做（needs 里列了缺什么）。和上一周比用 vs_previous 里的差值，不要自己重新算。",
    "先给结论（这周整体怎么样、最好和最需要注意的各一条），再给依据，最后给下周 1–3 条能做到的建议。",
    "要改计划、营养目标或饮食约束：先说理由、问用户，同意后再调对应的写工具。",
    "看别人的数据时只看 hidden 以外的部分，只给建议、不写。",
)
REVIEW_SECTIONS = (("scores", "scores"), ("training", "training"), ("nutrition", "nutrition"), ("weight", "weight"),
                   ("journal", "journal"), ("events", "events"))


def _journal_section(conn, uid: int, start: date, end: date, report: dict | None) -> dict:
    rows = conn.execute("""SELECT i.label_zh AS label, i.kind, count(*) AS days, sum((e.value > 0)::int) AS yes,
                                  round(avg(e.value)::numeric, 1) AS avg,
                                  array_remove(array_agg(e.note ORDER BY e.date), NULL) AS notes
                           FROM journal_entries e JOIN journal_items i ON i.user_id = e.user_id AND i.key = e.key
                           WHERE e.user_id = %s AND e.date BETWEEN %s AND %s
                           GROUP BY i.label_zh, i.kind ORDER BY count(*) DESC, i.label_zh""", (uid, start, end)).fetchall()
    items = []
    for r in rows:
        row = {"item": r["label"], "logged_days": r["days"]}
        row.update({"yes_days": r["yes"]} if r["kind"] == "bool" else {"avg": float(r["avg"]) if r["avg"] is not None else None})
        if r["notes"]:
            row["notes"] = [n[:P.REVIEW_NOTE_CHARS] for n in r["notes"][-P.REVIEW_NOTES_PER_ITEM:]]
        items.append(row)
    out = {"items": items}
    if report is not None:
        out["findings_90d"] = [{k: f[k] for k in ("label", "direction", "diff_recovery", "reliable")}
                               for f in report.get("journal_findings") or []]
    return out


def _events_section(conn, uid: int, start: date, end: date) -> list[dict]:
    return [{"date": f"{e['occurred_at']:%m-%d}", "title": e["title"], "body_part": e["body_part"], "severity": e["severity"]}
            for e in queries.events(conn, uid, start, end)]


def week_review_context(conn: psycopg.Connection, viewer_id: int, owner_id: int, end: date | None = None) -> dict:
    end = end or _today(conn, owner_id)
    start = end - timedelta(days=6)
    out: dict = {"start": start, "end": end, "previous": {"start": start - timedelta(days=7), "end": start - timedelta(days=1)}}
    rep: dict = {}

    def report() -> dict:   # one report for the score / training / food / weight sections (it reads SparkyFitness once)
        if not rep:
            rep.update(reports.report(conn, owner_id, "week", end, viewer_id=viewer_id))   # blocks by shares (#111)
        return rep

    def block(key: str, change_key: str | None = None):
        def fn():
            r = report()
            cur = r["current"][key]
            ch = (r["change"] or {}).get(change_key or key)
            return {"week": cur, "vs_previous": ch} if ch else {"week": cur}
        return fn

    def scores():
        r = report()
        return {k: {"week": r["current"][k], "vs_previous": (r["change"] or {}).get(k)}
                for k in ("recovery", "sleep", "load", "stress")}

    fns = {"scores": scores, "training": block("training"), "nutrition": block("nutrition"),
           "weight": lambda: report()["current"]["weight"],
           "journal": lambda: _journal_section(conn, owner_id, start, end,
                                               report() if auth.can_view(conn, viewer_id, owner_id, "scores") else None),
           "events": lambda: _events_section(conn, owner_id, start, end)}
    _sections(conn, viewer_id, owner_id, REVIEW_SECTIONS, fns, out)
    out["rules"] = list(REVIEW_RULES)
    out["needs"] = _review_needs(out)
    return out


def _review_needs(out: dict) -> list[str]:
    needs = []
    rec = ((out.get("scores") or {}).get("recovery") or {}).get("week") or {}
    if "scores" in out and (rec.get("days") or 0) < P.REVIEW_MIN_DAYS:
        needs.append(f"这周只有 {rec.get('days') or 0} 天恢复分：手表可能没戴 / 没同步，结论要打折扣")
    nut = (out.get("nutrition") or {}).get("week") or {}
    if nut.get("error"):
        needs.append("饮食记录读不到（SparkyFitness 没连上）：达标率这周没法算")
    elif "nutrition" in out and (nut.get("days_logged") or 0) < P.REVIEW_MIN_DAYS:
        needs.append(f"这周只记了 {nut.get('days_logged') or 0} 天饮食：达标率只供参考，可以问用户其他几天大概吃了什么")
    w = out.get("weight") or {}
    if w.get("error"):
        needs.append("体重读不到（SparkyFitness 没连上）")
    elif "weight" in out and not w.get("weigh_ins"):
        needs.append("这周没有称重")
    tr = (out.get("training") or {}).get("week") or {}
    if tr.get("error"):
        needs.append("力量训练记录读不到（SparkyFitness 没连上）：只看得到手表记录的运动")
    return needs

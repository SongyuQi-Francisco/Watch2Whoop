"""Body cards of the in-page chat (rollout flag jarvis_cards_v2 on top of jarvis_rich; ai/cards.py dispatches here).

Every builder calls the function behind the Today page's own endpoint for that module and keeps the fields the page's
component draws, so ai.js can hand the card to the same component (app.js window.BodyCards) and it looks the same:
  recovery / strain (/today scores + /trends 14 days), stress (/stress), healthspan (/healthspan), trend / weight / hr_zones
  (/trends), lab (/labs/{key}/history), supplements (/supplements + today's journal check-ins), water (/water), journal
  (/journal/analysis), report (/reports), vo2max (/vo2max), monitor (/health-monitor), data_status (/data-status).
Only the user's own data (the chat's viewer is the owner). No card carries an ECG or beat-to-beat series: data_status
leaves those two rows out, so nothing about them reaches any AI through a card.
"""
from datetime import date, timedelta

import psycopg

from .. import queries

MAX_DAYS, DEFAULT_DAYS = 90, 30
SHORT_DAYS = 14                # recovery / strain cards: the last two weeks under today's value
TREND_KEEP = ("label", "unit", "group", "values", "avg7", "n", "min", "max", "mean", "prev_mean", "prev_n", "baseline",
              "zones", "target")
LAB_ROWS = 6
NO_CARD_KINDS = {"ecg", "heartbeats"}   # data_status rows never put in a card
SUPP_STATUSES = ("active",)


def _days(days, default: int = DEFAULT_DAYS) -> int:
    try:
        return max(7, min(MAX_DAYS, int(days or default)))
    except (TypeError, ValueError):
        return default


def _trends(conn, uid: int, end: date, days: int, keys: tuple[str, ...]) -> dict:
    """The /trends payload cut to some series (what app.js cardHtml / recoveryTrendHtml read)."""
    from ..web import views
    t = views.trends(conn, {"id": uid}, uid, end, days)
    series = {k: {f: v for f, v in t["series"][k].items() if f in TREND_KEEP} for k in keys if k in t["series"]}
    return {"start": t["start"], "end": t["end"], "days": t["days"], "dates": t["dates"], "series": series,
            "unavailable": {k: v for k, v in (t.get("unavailable") or {}).items() if k in keys}, "hr_zones": t.get("hr_zones")}


def trend_keys() -> tuple[str, ...]:
    from ..trends import series
    return tuple(k[0] for k in series.SERIES)


def recovery(conn: psycopg.Connection, uid: int, day: date) -> dict:
    from ..web import views
    sc = views.scores_today(conn, uid, day)
    t = sc.get("today") or {}
    tr = _trends(conn, uid, day, SHORT_DAYS, ("recovery",))
    return {"type": "recovery", "date": day.isoformat(), "recovery": t.get("recovery"), "zone": sc.get("recovery_zone"),
            "word": sc.get("recovery_word"), "hrv_ms": t.get("hrv_night_ms"), "rhr": t.get("rhr_night"),
            "sleep_performance": t.get("sleep_performance"), "last": sc.get("last_recovery"), "trend": tr,
            "actions": [{"kind": "open", "page": "today"}, {"kind": "send", "key": "explain_recovery"}]}


def strain(conn: psycopg.Connection, uid: int, day: date) -> dict:
    from ..insights import views as iv
    coach = iv.strain_coach(conn, uid, day)
    tr = _trends(conn, uid, day, SHORT_DAYS, ("strain",))
    return {"type": "strain", "date": day.isoformat(), "coach": coach, "trend": tr,
            "actions": [{"kind": "open", "page": "trends"}, {"kind": "send", "key": "adjust_training"}]}


def stress(conn: psycopg.Connection, uid: int, day: date) -> dict:
    from ..insights import views as iv
    st = iv.stress_days(conn, uid, day, day, hours=True)
    return {"type": "stress", "date": day.isoformat(), "days": st["days"],
            "actions": [{"kind": "open", "page": "today"}, {"kind": "send", "key": "explain_stress"}]}


def healthspan(conn: psycopg.Connection, uid: int) -> dict:
    from ..insights import views as iv
    h = iv.healthspan(conn, uid, 26, uid)
    keep = ("body_age", "chrono_age", "delta_years", "pace_of_aging", "window_weeks", "as_of", "algo_version",
            "history", "contributors", "insufficient", "missing_reason")
    return {"type": "healthspan", **{k: h.get(k) for k in keep},
            "actions": [{"kind": "open", "page": "healthspan"}, {"kind": "send", "key": "improve_body_age"}]}


def trend(conn: psycopg.Connection, uid: int, metric: str | None, end: date, days) -> dict:
    keys = trend_keys()
    if metric not in keys:
        raise ValueError(f"trend 的 metric 是趋势指标 key：{' / '.join(keys)}")
    t = _trends(conn, uid, end, _days(days), (metric,))
    return {"type": "trend", "metric": metric, "trend": t,
            "actions": [{"kind": "open", "page": "trends"}, {"kind": "send", "key": "explain_trend"}]}


def weight(conn: psycopg.Connection, uid: int, end: date, days) -> dict:
    card = trend(conn, uid, "weight", end, _days(days, 60))
    vals = [v for v in card["trend"]["series"].get("weight", {}).get("values") or [] if v is not None]
    return {**card, "type": "weight", "change_kg": round(vals[-1] - vals[0], 1) if len(vals) >= 2 else None}


def hr_zones(conn: psycopg.Connection, uid: int, end: date, days) -> dict:
    t = _trends(conn, uid, end, _days(days, 28), ())
    return {"type": "hr_zones", "date": end.isoformat(), "days": t["days"], "zones": t["hr_zones"],
            "actions": [{"kind": "open", "page": "trends"}]}


def vo2max(conn: psycopg.Connection, uid: int, end: date, days) -> dict:
    from ..insights import views as iv
    v = iv.vo2max(conn, uid, end, 365)
    keep = ("current", "latest", "trend_per_year", "monthly", "norm_median", "percentile", "level", "level_zh", "level_label")
    t = _trends(conn, uid, end, _days(days, 90), ("vo2max",))
    return {"type": "vo2max", **{k: v.get(k) for k in keep}, "trend": t,
            "actions": [{"kind": "open", "page": "trends"}, {"kind": "send", "key": "improve_vo2max"}]}


def monitor(conn: psycopg.Connection, uid: int, day: date) -> dict:
    from ..insights import views as iv
    m = iv.health_monitor(conn, uid, day)
    return {"type": "monitor", "date": day.isoformat(), "metrics": m.get("metrics") or [],
            "out_of_range": m.get("out_of_range"), "all_in_range": m.get("all_in_range"),
            "actions": [{"kind": "open", "page": "today"}, {"kind": "send", "key": "explain_monitor"}]}


def lab(conn: psycopg.Connection, uid: int, test) -> dict:
    from ..records import lab_catalog, lab_history
    name = str(test or "").strip()
    if not name:
        raise ValueError("lab 要给 metric = 化验项 key 或名字（get_lab_results 里的）")
    key = lab_catalog.resolve_key(name, None, set()) or name
    h = lab_history.history(conn, uid, key)
    if h is None:
        raise ValueError(f"没有「{name}」的化验记录")
    keep = ("collected_on", "value", "value_text", "unit", "source", "fasting", "status", "delta", "pct")
    return {"type": "lab", **{k: h[k] for k in ("test_key", "test_name", "category", "unit", "ref_low", "ref_high",
                                                "good_direction")},
            "rows": [{k: r.get(k) for k in keep} for r in h["rows"]][-24:],
            "actions": [{"kind": "open", "page": "lab"}, {"kind": "send", "key": "explain_lab"}]}


def supplements(conn: psycopg.Connection, uid: int, day: date) -> dict:
    from ..records import store
    rows = store.list_supplements(conn, uid, list(SUPP_STATUSES))
    keys = [r["journal_key"] for r in rows if r.get("journal_key")]
    taken = {e["key"]: e["value"] for e in store.journal_entries(conn, uid, day, day, keys)} if keys else {}
    items = [{"id": r["id"], "name": r["name"], "dose": r.get("dose"), "timing": r.get("timing"),
              "journal_key": r.get("journal_key"),
              "taken": None if not r.get("journal_key") or r["journal_key"] not in taken else bool(taken[r["journal_key"]])}
             for r in rows]
    return {"type": "supplements", "date": day.isoformat(), "items": items,
            "actions": [{"kind": "send", "key": "log_supplements"}, {"kind": "open", "page": "supplements"}]}


def water(conn: psycopg.Connection, uid: int, day: date) -> dict:
    from .. import water as water_mod
    from ..water import store
    if not water_mod.enabled(conn, uid):
        raise ValueError("饮水记录还没有对这个用户开放")
    d = store.day_view(conn, uid, day)
    g = d.get("goal") or {}
    return {"type": "water", "date": day.isoformat(), "total_ml": d["total_ml"], "goal_ml": g.get("ml"),
            "percent": d.get("percent"), "entries": [{k: e.get(k) for k in ("at", "ml", "source")} for e in d["entries"][:8]],
            "quick_ml": d.get("quick_ml") or [],
            "actions": [{"kind": "open", "page": "food"}]}


def journal(conn: psycopg.Connection, uid: int, key: str | None, end: date, days) -> dict:
    """The /journal/analysis results (each item, its next-day recovery comparison) as app.js behaviorHtml reads them."""
    from ..records import analysis, defaults
    n = max(30, min(180, int(days or 90)))
    results = [analysis.analyze(conn, uid, key, end, n)] if key else analysis.analyze_all(conn, uid, end, n)
    results = [{**r, "item": defaults.with_label([r["item"]])[0]} for r in results]   # `label` in the page's language
    keep_o = ("status", "diff_yes_minus_no", "direction", "reliable")
    rows = [{"item": {k: r["item"].get(k) for k in ("key", "label_zh", "label", "kind", "unit")},
             "days_yes": r.get("days_yes"), "days_no": r.get("days_no"),
             "outcomes": {"next_recovery": {k: ((r.get("outcomes") or {}).get("next_recovery") or {}).get(k) for k in keep_o}}}
            for r in results]
    return {"type": "journal", "end": end.isoformat(), "days": n, "key": key, "results": rows,
            "actions": [{"kind": "open", "page": "trends"}, {"kind": "send", "key": "explain_journal"}]}


def report(conn: psycopg.Connection, uid: int, period: str | None, end: date) -> dict:
    from ..insights import views as iv
    period = period if period in ("week", "month") else "week"
    r = iv.report(conn, uid, period, end, uid)
    return {"type": "report", "period": period, "start": r["start"], "end": r["end"], "previous": r.get("previous"),
            "previous_values": {"sleep": {"avg_bedtime": ((r.get("previous_values") or {}).get("sleep") or {}).get("avg_bedtime")}},
            "current": r.get("current") or {}, "change": r.get("change") or {},
            "journal_findings": (r.get("journal_findings") or [])[:3],
            "actions": [{"kind": "open", "page": "trends.report"}, {"kind": "send", "key": "explain_report"}]}


def data_status(conn: psycopg.Connection, uid: int) -> dict:
    from ..rollout import flags
    from ..sync import data_status as ds
    if not flags.enabled(conn, uid, "data_status"):
        raise ValueError("「数据状态」还没有对这个用户开放")
    d = ds.collect(conn, uid)
    types = [{k: t.get(k) for k in ("key", "label", "status", "last_date", "days_since", "days")}
             for t in d["types"] if t["key"] not in NO_CARD_KINDS]
    summary = {s: sum(1 for t in types if t["status"] == s) for s in (d.get("summary") or {})}
    return {"type": "data_status", "today": d["today"], "window_days": d.get("window_days"), "types": types,
            "summary": summary, "actions": [{"kind": "open", "page": "data_status"}]}


def summary(card: dict) -> str | None:
    """The model's one line for a body card (AI-facing, Chinese); None for other types."""
    t = card["type"]
    no_repeat = "不要用文字复述卡片里的数字。"
    if t == "recovery":
        return f"已显示 {card['date']} 恢复卡：恢复 {card['recovery']}（{card['zone'] or '无'}），HRV {card['hrv_ms']}，静息心率 {card['rhr']}，带近 14 天柱状图。{no_repeat}"
    if t == "strain":
        c = card["coach"] or {}
        return f"已显示 {card['date']} 负荷卡：现在 {c.get('current_strain')}，目标 {c.get('target_low')}–{c.get('target_high')}，带近 14 天。{no_repeat}"
    if t == "stress":
        d = (card["days"] or [{}])[0]
        return f"已显示 {card['date']} 压力卡：{d.get('score')}（{d.get('level') or '无'}），高 {d.get('high_h')} 小时。{no_repeat}"
    if t == "healthspan":
        top = "、".join(f"{c['label']} {c['years']:+.1f} 年" for c in sorted(card["contributors"] or [], key=lambda c: -abs(c.get('years') or 0))[:3]
                       if c.get("years") is not None)
        return f"已显示身体年龄卡：{card['body_age']}（实际 {card['chrono_age']}），影响最大：{top or '无'}。{no_repeat}"
    if t in ("trend", "weight"):
        s = card["trend"]["series"].get(card["metric"]) or {}
        extra = f"，期间变化 {card['change_kg']} kg" if t == "weight" and card.get("change_kg") is not None else ""
        return (f"已显示 {s.get('label') or card['metric']} 近 {card['trend']['days']} 天趋势卡：均值 {s.get('mean')}，"
                f"上一期均值 {s.get('prev_mean')}{extra}。{no_repeat}")
    if t == "hr_zones":
        z = card["zones"] or {}
        return f"已显示近 {card['days']} 天心率区间卡（各区间分钟：{z.get('total_min')}）。{no_repeat}"
    if t == "vo2max":
        return f"已显示 VO₂max 卡：现在 {card['current']}，{card.get('level_zh') or ''}，同龄百分位 {card.get('percentile')}。{no_repeat}"
    if t == "monitor":
        off = "、".join(m["label"] for m in card["metrics"] if m.get("status") in ("high", "low")) or "无"
        return f"已显示 {card['date']} 健康监测卡：超出个人正常范围的：{off}。{no_repeat}"
    if t == "lab":
        last = (card["rows"] or [{}])[-1]
        return f"已显示化验卡「{card['test_name']}」：{len(card['rows'])} 次，最近 {last.get('collected_on')} {last.get('value')} {card['unit'] or ''}。{no_repeat}"
    if t == "supplements":
        names = "、".join(f"{i['name']}{'✓' if i['taken'] else ''}" for i in card["items"])
        return f"已显示 {card['date']} 补剂卡：{names or '没有在吃的补剂'}（✓ = 今天打卡了）。"
    if t == "water":
        return f"已显示 {card['date']} 饮水卡：{card['total_ml']} / {card['goal_ml']} ml。{no_repeat}"
    if t == "journal":
        oks = [(x["item"].get("label") or x["item"].get("label_zh"), x["outcomes"]["next_recovery"]["diff_yes_minus_no"])
               for x in card["results"] if x["outcomes"]["next_recovery"]["status"] == "ok"]
        names = "、".join(f"{lab}（{d:+.0f}）" for lab, d in oks if d is not None)
        return f"已显示日记分析卡（近 {card['days']} 天，做了 vs 没做时第二天恢复分的差）：{names or '天数还不够'}。{no_repeat}"
    if t == "report":
        return f"已显示{'周' if card['period'] == 'week' else '月'}报卡（{card['start']}–{card['end']}）。{no_repeat}"
    if t == "data_status":
        s = card["summary"]
        return f"已显示数据状态卡：正常 {s.get('ok', 0)}、过期 {s.get('stale', 0)}、从没收到 {s.get('never', 0)} 类。"
    return None

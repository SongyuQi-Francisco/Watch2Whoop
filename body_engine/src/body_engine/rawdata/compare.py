"""SDNN vs RMSSD recovery report (`body-engine hrv-compare`): the evidence the user looks at before their HRV kind switches
(per user since #117: scores/hrv_kind.py, `body-engine hrv-kind`).

Loads the scoring inputs once, scores every day twice (HRV = Apple's SDNN / RMSSD from heartbeat_series) without writing anything,
and compares coverage, nightly noise, recovery distributions, agreement and the nights that differ most. Output is Chinese Markdown;
it contains personal health data, so it is written outside git (health_data/reports/ or stdout).
"""
import dataclasses
import math
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta

import psycopg

from .. import queries
from ..scores import engine as SE
from ..scores import hrv_kind as SK
from ..scores import inputs as SI
from ..scores import params as P

GREEN, RED = 67, 34


def _corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 5:
        return None
    try:
        return round(statistics.correlation(xs, ys), 2)
    except statistics.StatisticsError:
        return None


def _pair_corr(pairs: list[tuple[float, float]]) -> float | None:
    return _corr([a for a, _ in pairs], [b for _, b in pairs])


def _zone(r: int) -> str:
    return "绿" if r >= GREEN else "黄" if r >= RED else "红"


def _lag1(xs: list[float]) -> float | None:
    """Lag-1 autocorrelation of consecutive nights (higher = the nightly value carries more signal than single-reading noise)."""
    return _corr(xs[:-1], xs[1:]) if len(xs) > 6 else None


def _within_between(inp: SI.Inputs, rows: list[dict]) -> tuple[float | None, int]:
    """Median within-night SD of ln HRV readings (nights with ≥ 2) / SD of the nightly values. Lower = readings in one night agree more."""
    nights = {n.wake_date: n for n in inp.nights}
    within, nightly = [], []
    for r in rows:
        if r["hrv_night_ln"] is None or r["date"] not in nights:
            continue
        n = nights[r["date"]]
        vals = [math.log(v) for t, v in inp.hrv if n.start <= t <= n.end and v > 0]
        nightly.append(r["hrv_night_ln"])
        if len(vals) >= 2:
            within.append(statistics.pstdev(vals))
    if len(within) < 5 or len(nightly) < 5:
        return None, len(within)
    return round(statistics.median(within) / statistics.pstdev(nightly), 2), len(within)


def _summary(inp: SI.Inputs, rows: list[dict]) -> dict:
    rec = [r["recovery"] for r in rows if r["recovery"] is not None]
    ln = [r["hrv_night_ln"] for r in rows if r["hrv_night_ln"] is not None]
    readings = [r["details"].get("recovery", {}).get("hrv_readings") for r in rows if r["hrv_night_ln"] is not None]
    wb, wb_n = _within_between(inp, rows)
    return {"nights_hrv": len(ln), "median_hrv_ms": round(math.exp(statistics.median(ln)), 1) if ln else None,
            "readings_per_night": round(statistics.mean(readings), 2) if readings else None,
            "lag1": _lag1(ln), "within_between": wb, "within_nights": wb_n,
            "recovery_days": len(rec), "recovery_mean": round(statistics.mean(rec), 1) if rec else None,
            "recovery_sd": round(statistics.pstdev(rec), 1) if len(rec) > 1 else None,
            "green": sum(r >= GREEN for r in rec), "yellow": sum(RED <= r < GREEN for r in rec), "red": sum(r < RED for r in rec),
            "corr_sleep_score": _pair_corr([(r["recovery"], r["sleep_score"]) for r in rows
                                            if r["recovery"] is not None and r["sleep_score"] is not None]),
            "corr_rhr": _pair_corr([(r["recovery"], r["rhr_night"]) for r in rows
                                    if r["recovery"] is not None and r["rhr_night"] is not None])}


def reading_check(conn: psycopg.Connection, user_id: int, start: datetime, end: datetime) -> dict:
    """Per-reading check of our beat parsing: our SDNN vs Apple's SDNN on the same readings."""
    rows = conn.execute("""SELECT apple_sdnn, sdnn, rmssd, n_beats, n_valid FROM heartbeat_series
                           WHERE user_id = %s AND start_at >= %s AND start_at < %s""", (user_id, start, end)).fetchall()
    pairs = [(r["apple_sdnn"], r["sdnn"]) for r in rows if r["apple_sdnn"] and r["sdnn"]]
    return {"readings": len(rows), "with_rmssd": sum(r["rmssd"] is not None for r in rows),
            "median_beats": statistics.median([r["n_beats"] for r in rows]) if rows else None,
            "sdnn_corr": _pair_corr(pairs),
            "median_rmssd": statistics.median([r["rmssd"] for r in rows if r["rmssd"]]) if rows else None}


def compare(conn: psycopg.Connection, client, user_id: int, start: date | None = None, end: date | None = None) -> dict:
    origin = SE.scoring_origin(conn, user_id)
    if origin is None:
        raise ValueError("这个用户还没有手表数据")
    end = end or queries.local_today(conn, user_id)
    inp = SI.load(conn, client, user_id, origin - timedelta(days=P.HRMAX_WINDOW_DAYS), end, None, "sdnn")
    start = max(start or origin, origin)
    inp_r = dataclasses.replace(inp, hrv_kind="rmssd",
                                hrv=SI.rmssd_hrv(conn, user_id, inp.tz, origin - timedelta(days=P.HRMAX_WINDOW_DAYS), end))
    today = datetime.now(inp.tz).date()
    rows_s = [r for r in SE.score_days(inp, origin, end, today) if start <= r["date"] <= end]
    rows_r = [r for r in SE.score_days(inp_r, origin, end, today) if start <= r["date"] <= end]
    both = [(a, b) for a, b in zip(rows_s, rows_r) if a["recovery"] is not None and b["recovery"] is not None]
    diffs = sorted(both, key=lambda ab: -abs(ab[0]["recovery"] - ab[1]["recovery"]))
    tz = inp.tz
    stored = SK.stored_kinds(conn, user_id)   # the kind this user's stored scores use (scores/hrv_kind.py)
    lo, hi = datetime.combine(start, datetime.min.time(), tz), datetime.combine(end + timedelta(days=1), datetime.min.time(), tz)
    return {
        "user_id": user_id, "start": start, "end": end, "algo_version": P.ALGO_VERSION,
        "current_kind": stored.pop() if len(stored) == 1 else P.HRV_KIND,
        "sdnn": _summary(inp, rows_s), "rmssd": _summary(inp_r, rows_r), "check": reading_check(conn, user_id, lo, hi),
        "both_days": len(both),
        "recovery_corr": _corr([a["recovery"] for a, _ in both], [b["recovery"] for _, b in both]),
        "zone_agree": round(sum(_zone(a["recovery"]) == _zone(b["recovery"]) for a, b in both) / len(both), 3) if both else None,
        "mean_abs_diff": round(statistics.mean(abs(a["recovery"] - b["recovery"]) for a, b in both), 1) if both else None,
        "top": [{"date": a["date"], "sdnn": a["recovery"], "rmssd": b["recovery"], "sleep_score": a["sleep_score"],
                 "hrv_sdnn": round(math.exp(a["hrv_night_ln"]), 1), "hrv_rmssd": round(math.exp(b["hrv_night_ln"]), 1),
                 "readings": a["details"].get("recovery", {}).get("hrv_readings")} for a, b in diffs[:12]],
        "monthly": _monthly(both),
    }


def _monthly(both) -> list[dict]:
    by = defaultdict(list)
    for a, b in both:
        by[a["date"].strftime("%Y-%m")].append((a["recovery"], b["recovery"]))
    return [{"month": m, "days": len(v), "sdnn": round(statistics.mean(x for x, _ in v)), "rmssd": round(statistics.mean(y for _, y in v))}
            for m, v in sorted(by.items())]


def _fmt(x, suffix="") -> str:
    return "—" if x is None else f"{x}{suffix}"


def markdown(res: dict) -> str:
    s, r, c = res["sdnn"], res["rmssd"], res["check"]
    L = [f"# HRV 口径对比：SDNN vs RMSSD（用户 {res['user_id']}，{res['start']} 到 {res['end']}）", "",
         f"现在评分用 **{res['current_kind'].upper()}**（算法 {res['algo_version']}）。本报告只计算、不写库；同一套睡眠、静息心率、呼吸数据，只换 HRV 一项。",
         "", "## 1. 逐拍数据自检",
         f"- 心跳序列 {c['readings']} 条，能算 RMSSD 的 {c['with_rmssd']} 条，每条中位 {_fmt(c['median_beats'])} 拍；"
         f"RMSSD 中位数 {_fmt(c['median_rmssd'] and round(c['median_rmssd'], 1), ' ms')}。",
         f"- 用逐拍时间自己算的 SDNN 和 Apple 的 SDNN 相关 r = {_fmt(c['sdnn_corr'])}（越接近 1 说明解析和去伪迹越可信）。",
         "", "## 2. 两种口径并排", "",
         "| | SDNN（现在） | RMSSD |", "|---|---|---|",
         f"| 有 HRV 的晚数 | {s['nights_hrv']} | {r['nights_hrv']} |",
         f"| 当晚 HRV 中位数 | {_fmt(s['median_hrv_ms'], ' ms')} | {_fmt(r['median_hrv_ms'], ' ms')} |",
         f"| 每晚读数（平均） | {_fmt(s['readings_per_night'])} | {_fmt(r['readings_per_night'])} |",
         f"| 相邻两晚的相关（越高越稳） | {_fmt(s['lag1'])} | {_fmt(r['lag1'])} |",
         f"| 同一晚几次读数的离散 / 晚与晚的离散（越低越一致） | {_fmt(s['within_between'])}（{s['within_nights']} 晚） | {_fmt(r['within_between'])}（{r['within_nights']} 晚） |",
         f"| 有恢复分的天数 | {s['recovery_days']} | {r['recovery_days']} |",
         f"| 恢复分 均值 ± SD | {_fmt(s['recovery_mean'])} ± {_fmt(s['recovery_sd'])} | {_fmt(r['recovery_mean'])} ± {_fmt(r['recovery_sd'])} |",
         f"| 绿 / 黄 / 红 天数 | {s['green']} / {s['yellow']} / {s['red']} | {r['green']} / {r['yellow']} / {r['red']} |",
         f"| 恢复分和当晚睡眠分的相关 | {_fmt(s['corr_sleep_score'])} | {_fmt(r['corr_sleep_score'])} |",
         f"| 恢复分和当晚静息心率的相关（应为负） | {_fmt(s['corr_rhr'])} | {_fmt(r['corr_rhr'])} |",
         "", "## 3. 两种恢复分一致吗",
         f"- 两边都有分的 {res['both_days']} 天：相关 r = {_fmt(res['recovery_corr'])}，绿黄红一致 {_fmt(res['zone_agree'] and round(res['zone_agree'] * 100), '%')}，"
         f"平均相差 {_fmt(res['mean_abs_diff'])} 分。", "", "| 月份 | 天数 | SDNN 均值 | RMSSD 均值 |", "|---|---|---|---|"]
    L += [f"| {m['month']} | {m['days']} | {m['sdnn']} | {m['rmssd']} |" for m in res["monthly"]]
    L += ["", "## 4. 差得最多的 12 天", "", "| 日期 | SDNN 恢复 | RMSSD 恢复 | 睡眠分 | 当晚 SDNN | 当晚 RMSSD | 读数 |", "|---|---|---|---|---|---|---|"]
    L += [f"| {t['date']} | {t['sdnn']} | {t['rmssd']} | {_fmt(t['sleep_score'])} | {t['hrv_sdnn']} | {t['hrv_rmssd']} | {_fmt(t['readings'])} |"
          for t in res["top"]]
    L += ["", "## 5. 怎么看",
          "- RMSSD 是 WHOOP / Oura 的口径，主要反映副交感（迷走）活动；SDNN 还混着呼吸和慢波，单条 1 分钟读数更吵。",
          "- 每晚读数次数由 Apple 决定，换口径不会变多；看第 2 节「相邻两晚的相关」「同一晚离散」哪边更稳，以及恢复分和睡眠分 / 静息心率的关系是否更合理。",
          "- 确认切换后：`body-engine hrv-kind --user-id N --enable-rmssd`（只对这个人；还要有足够的逐拍数据，阈值见 `scores/params.py` 的 `RMSSD_*`），"
          "下一次完整的 `compute-scores`（夜间任务）整段重算；基线随之重建（前 14 晚没有恢复分）。"]
    return "\n".join(L) + "\n"

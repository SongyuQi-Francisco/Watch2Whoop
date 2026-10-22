"""Explanation + rule-based advice for one metric or status card item, computed from the owner's own data.

Every block of personal data is checked against shares (auth.can_view) for the viewer, so a family member sees only what they were shared;
a block they may not see is left out and named in `hidden`. Nothing here calls an AI: `ai_prompt` is a question draft the page hands to
the viewer's own AI. Thresholds come from scores/params.py, rules/params.py and status.py so the numbers match the rest of the page.

Language (issue #42): every text the sheet shows is built with L(zh, en) / the glossary twins and follows the request language;
`ai_prompt` too (the viewer pastes it into their own AI). `missing` stays Chinese: the page only pattern-matches it (app.js SHORT).
Matching on the owner's own notes and profile card (Chinese data) is unchanged.
"""
import re
import statistics
from datetime import date, timedelta

import psycopg

from .. import auth, queries
from ..i18n import L, is_en
from ..assistant import status as status_mod
from ..insights import fitness
from ..records import store as records_store
from ..rules import params as RP
from ..scores import params as SP, store as scores_store
from ..web import views
from . import glossary as G

METRIC_SCOPE = {"recovery": "scores", "sleep_score": "scores", "sleep_hours": "scores", "sleep_debt": "scores",
                "sleep_consistency": "sleep", "strain": "scores", "hrv": "metrics", "resting_hr": "metrics",
                "vo2max": "metrics", "sleep_efficiency": "sleep", "bedtime": "sleep", "weight": "weight"}
TONE = {"good": "good", "warn": "mid", "bad": "bad", "neutral": "plain", "mid": "mid", "plain": "plain"}
# (zh, en) pairs, picked with L(*pair)
RECOVERY_PART = {"hrv": ("HRV", "HRV"), "rhr": ("静息心率", "Resting HR"), "sleep_performance": ("睡眠表现", "Sleep performance"),
                 "resp_rate": ("呼吸频率", "Respiratory rate")}
SLEEP_PART = {"duration": ("时长", "Duration"), "stages": ("睡眠阶段", "Sleep stages"), "consistency": ("规律性", "Consistency"),
              "interruptions": ("中途醒来", "Interruptions")}
SLEEP_PART_TIP = {
    "duration": ("最拉分的是时长：先保证 7 小时，固定起床时间、入睡往前挪。",
                 "Duration pulls the score down most: secure 7 hours first; fix your wake time and move bedtime earlier."),
    "stages": ("最拉分的是睡眠阶段（深睡 / REM 少）：睡前别喝酒、别吃太撑，晚睡也会丢掉前半夜的深睡。",
               "Sleep stages pull the score down most (little deep / REM sleep): no alcohol or heavy meals before bed; "
               "a late bedtime also loses the deep sleep of the first half of the night."),
    "consistency": ("最拉分的是规律性：入睡时间和前两周差太多，先固定起床时间。",
                    "Consistency pulls the score down most: your bedtime differs too much from the last two weeks; fix your wake time first."),
    "interruptions": ("最拉分的是中途醒来：睡前少喝水和酒，卧室凉一点、遮光。",
                      "Interruptions pull the score down most: less water and alcohol before bed, a cooler, darker bedroom."),
}
VO2_LEVEL_EN = {"excellent": "Excellent", "good": "Good", "average": "Average", "fair": "Fair", "poor": "Poor"}
_MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _md(d: date) -> str:
    """Short day label: 10-21 / 21 Oct."""
    return L(f"{d:%m-%d}", f"{d.day} {_MON[d.month - 1]}")


def _avg7() -> str:
    return L("7 天均", "7-day avg")


def _avg7n() -> str:
    return L("7 晚均", "7-night avg")


def _hours() -> str:
    return L("小时", "h")


def _fmt(v, nd=0) -> str:
    if v is None:
        return "—"
    s = f"{v:.{nd}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def _fact(label: str, value: str) -> dict:
    return {"label": label, "value": value}


def _adv(text: str, tone: str = "plain") -> dict:
    return {"text": text, "tone": tone}


class Out:
    """Collects the personal part of an explanation."""

    def __init__(self):
        self.current: dict | None = None     # {value, unit, basis, tone}
        self.facts: list[dict] = []
        self.advice: list[dict] = []
        self.missing: str | None = None

    def merge(self, other: "Out") -> None:
        self.facts += other.facts
        self.advice += other.advice


# ---------------------------------------------------------------- metric calculators: (conn, owner, day) -> Out
def _daily_values(conn, owner: int, metric: str, start: date, end: date) -> dict[date, float]:
    return {r["date"]: r["value"] for r in queries.daily_metrics(conn, owner, start, end, [metric]) if r["value"] is not None}


def _vs_baseline(by_date: dict[date, float], day: date) -> tuple:
    """(7-day mean up to day, baseline mean, baseline sd, latest (date, value)); baseline = the 60 days before the 7-day window, >= 14 values."""
    recent = [v for d, v in by_date.items() if day - timedelta(days=6) <= d <= day]
    base = [v for d, v in by_date.items() if day - timedelta(days=6 + SP.BASELINE_DAYS) <= d < day - timedelta(days=6)]
    past = sorted((d, v) for d, v in by_date.items() if d <= day)
    m = sd = None
    if len(base) >= SP.MIN_BASELINE_NIGHTS:
        m, sd = statistics.fmean(base), statistics.stdev(base)
    return _mean(recent), m, sd, (past[-1] if past else None)


def calc_hrv(conn, owner: int, day: date) -> Out:
    o = Out()
    vals = _daily_values(conn, owner, "heart_rate_variability_sdnn", day - timedelta(days=6 + SP.BASELINE_DAYS), day)
    cur, m, sd, last = _vs_baseline(vals, day)
    if cur is None:
        o.missing = "近 7 天没有 HRV 数据"
        o.advice.append(_adv(L("睡觉时戴着手表，HRV 主要在睡眠中测。", "Wear the watch to bed: HRV is measured mainly during sleep.")))
        return o
    o.facts.append(_fact(_avg7(), f"{_fmt(cur)} ms"))
    if last:
        o.facts.append(_fact(L(f"最近 {_md(last[0])}", f"Latest {_md(last[0])}"), f"{_fmt(last[1])} ms"))
    if m is None:
        o.current = {"value": round(cur), "unit": "ms", "basis": _avg7(), "tone": "plain"}
        o.advice.append(_adv(L("基线还不够（要 14 天以上的数据），先看趋势，过两周再和自己比。",
                               "Not enough data for a baseline yet (needs 14+ days): watch the trend for now and compare with yourself in two weeks.")))
        return o
    o.facts.append(_fact(L("你的基线", "Your baseline"), f"{_fmt(m)} ± {_fmt(sd)} ms"))
    z, pct = (cur - m) / max(sd, 1e-6), (cur / m - 1) * 100
    if z >= 0.5:
        tone, text = "good", L(f"比你的基线高 {pct:.0f}%：恢复状态好，可以按计划上强度。",
                               f"{pct:.0f}% above your baseline: you are recovering well and can train hard as planned.")
    elif z > -0.5:
        tone, text = "plain", L("在你的基线范围内：保持现在的作息和训练节奏。",
                                "Within your baseline range: keep your current sleep and training rhythm.")
    elif z > -1:
        tone, text = "mid", L(f"比基线低 {-pct:.0f}%：留意睡眠、饮酒和训练量，今天别冲极限。",
                              f"{-pct:.0f}% below baseline: watch sleep, alcohol and training volume; don't push to your limit today.")
    else:
        tone, text = "bad", L(f"明显低于基线（低 {-pct:.0f}%）：今天降强度或休息，早点睡、多喝水；静息心率也升高的话，可能要生病。",
                              f"Clearly below baseline ({-pct:.0f}% lower): train easier or rest today, sleep early and drink more water; "
                              "if your resting HR is up too, you may be getting sick.")
    o.current = {"value": round(cur), "unit": "ms", "basis": _avg7(), "tone": tone}
    o.advice.append(_adv(text, tone))
    return o


def calc_resting_hr(conn, owner: int, day: date) -> Out:
    o = Out()
    vals = _daily_values(conn, owner, "resting_heart_rate", day - timedelta(days=6 + SP.BASELINE_DAYS), day)
    cur, m, sd, last = _vs_baseline(vals, day)
    if cur is None:
        o.missing = "近 7 天没有静息心率数据"
        return o
    o.facts.append(_fact(_avg7(), f"{_fmt(cur)} bpm"))
    if last:
        o.facts.append(_fact(L(f"最近 {_md(last[0])}", f"Latest {_md(last[0])}"), f"{_fmt(last[1])} bpm"))
    if m is None:
        o.current = {"value": round(cur), "unit": "bpm", "basis": _avg7(), "tone": "plain"}
        o.advice.append(_adv(L("基线还不够（要 14 天以上的数据），先看趋势。",
                               "Not enough data for a baseline yet (needs 14+ days): watch the trend for now.")))
        return o
    o.facts.append(_fact(L("你的基线", "Your baseline"), f"{_fmt(m)} ± {_fmt(sd)} bpm"))
    delta = cur - m
    if delta >= SP.ILLNESS_RHR_DELTA:
        tone, text = "bad", L(f"比基线高 {delta:.0f} bpm：可能是疲劳、脱水、喝酒或生病早期，今天降强度、多喝水、早点睡。",
                              f"{delta:.0f} bpm above baseline: possibly fatigue, dehydration, alcohol or early illness; "
                              "train easier today, drink more water and sleep early.")
    elif delta >= 2:
        tone, text = "mid", L(f"比基线高 {delta:.0f} bpm：留意睡眠和补水，训练量别再加。",
                              f"{delta:.0f} bpm above baseline: watch sleep and hydration, and don't add training volume.")
    elif delta > -2:
        tone, text = "plain", L("和你的基线持平。", "In line with your baseline.")
    else:
        tone, text = "good", L(f"比基线低 {-delta:.0f} bpm：心肺状态好。", f"{-delta:.0f} bpm below baseline: your cardiovascular system is in good shape.")
    o.current = {"value": round(cur), "unit": "bpm", "basis": _avg7(), "tone": tone}
    o.advice.append(_adv(text, tone))
    return o


def _score_rows(conn, owner: int, day: date, days: int = 7) -> list[dict]:
    return scores_store.scores(conn, owner, day - timedelta(days=days - 1), day, details=True)


def calc_sleep_hours(conn, owner: int, day: date) -> Out:
    o = Out()
    rows = _score_rows(conn, owner, day)
    mins = [r["sleep_actual_min"] for r in rows if r["sleep_actual_min"] is not None]
    if not mins:
        o.missing = "近 7 晚没有睡眠数据"
        return o
    h = statistics.fmean(mins) / 60
    last = next(r for r in reversed(rows) if r["sleep_actual_min"] is not None)
    need = next((r["sleep_need_min"] for r in reversed(rows) if r["sleep_need_min"] is not None), None)
    o.facts += [_fact(_avg7n(), f"{_fmt(h, 1)} {_hours()}"),
                _fact(_md(last["date"]), f"{_fmt(last['sleep_actual_min'] / 60, 1)} {_hours()}")]
    if need:
        o.facts.append(_fact(L("今晚需要", "Need tonight"), f"{_fmt(need / 60, 1)} {_hours()}"))
    if h < 6:
        tone, text = "bad", L(f"近 7 晚平均 {_fmt(h, 1)} 小时，明显不够：先固定起床时间，入睡每周往前挪 15–30 分钟，直到 7 小时以上。",
                              f"{_fmt(h, 1)} h on average over the last 7 nights, clearly not enough: fix your wake time first, "
                              "then move bedtime 15–30 minutes earlier each week until you reach 7+ hours.")
    elif h < 7:
        tone, text = "mid", L(f"近 7 晚平均 {_fmt(h, 1)} 小时，略少：每晚再多睡 {_fmt((7 - h) * 60)} 分钟左右就到 7 小时。",
                              f"{_fmt(h, 1)} h on average over the last 7 nights, a bit short: about {_fmt((7 - h) * 60)} more "
                              "minutes a night gets you to 7 hours.")
    else:
        tone, text = "good", L(f"近 7 晚平均 {_fmt(h, 1)} 小时，够了，保持。",
                               f"{_fmt(h, 1)} h on average over the last 7 nights: enough, keep it up.")
    o.current = {"value": round(h, 1), "unit": _hours(), "basis": _avg7n(), "tone": tone}
    o.advice.append(_adv(text, tone))
    if need and need / 60 - h >= 0.25:
        o.advice.append(_adv(L(f"按你昨天的负荷和睡眠债，今晚最好睡 {_fmt(need / 60, 1)} 小时。",
                               f"Given yesterday's strain and your sleep debt, aim for {_fmt(need / 60, 1)} h tonight."), "mid"))
    return o


def calc_sleep_score(conn, owner: int, day: date) -> Out:
    o = Out()
    rows = [r for r in _score_rows(conn, owner, day) if r["sleep_score"] is not None]
    if not rows:
        o.missing = "近 7 晚没有睡眠分"
        return o
    last = rows[-1]
    avg = statistics.fmean(r["sleep_score"] for r in rows)
    tone = "good" if last["sleep_score"] >= 80 else "mid" if last["sleep_score"] >= 60 else "bad"
    o.current = {"value": last["sleep_score"], "unit": "", "basis": _md(last["date"]), "tone": tone}
    o.facts.append(_fact(_avg7n(), _fmt(avg)))
    parts = {k: v for k, v in ((last.get("details") or {}).get("sleep") or {}).items() if k in SLEEP_PART and v is not None}
    for k, v in parts.items():
        o.facts.append(_fact(L(*SLEEP_PART[k]), _fmt(v)))
    if parts:
        worst = min(parts, key=parts.get)
        if parts[worst] < 80:
            o.advice.append(_adv(L(*SLEEP_PART_TIP[worst]), "mid" if parts[worst] >= 50 else "bad"))
    if not o.advice:
        o.advice.append(_adv(L("各项都不错，保持现在的作息。", "Every part looks good: keep your current schedule."), "good"))
    return o


def calc_sleep_debt(conn, owner: int, day: date) -> Out:
    o = Out()
    rows = [r for r in _score_rows(conn, owner, day) if r["sleep_debt_min"] is not None]
    if not rows:
        o.missing = "还没有睡眠债数据"
        return o
    debt = rows[-1]["sleep_debt_min"] / 60
    tone = "good" if debt <= 1 else "mid" if debt <= 2 else "bad"
    o.current = {"value": round(debt, 1), "unit": _hours(), "basis": _md(rows[-1]["date"]), "tone": tone}
    o.advice.append(_adv({
        "good": L("睡眠债很少，保持。", "Very little sleep debt: keep it up."),
        "mid": L(f"欠了 {_fmt(debt, 1)} 小时：接下来几晚每晚提前 30 分钟上床就能慢慢还上。",
                 f"{_fmt(debt, 1)} h of sleep debt: going to bed 30 minutes earlier for the next few nights pays it back gradually."),
        "bad": L(f"欠了 {_fmt(debt, 1)} 小时：这几天把训练强度降一点，优先睡觉，每晚提前 30–60 分钟上床。",
                 f"{_fmt(debt, 1)} h of sleep debt: ease off training intensity for a few days, put sleep first and "
                 "go to bed 30–60 minutes earlier each night."),
    }[tone], tone))
    return o


def calc_sleep_consistency(conn, owner: int, day: date) -> Out:
    o = Out()
    vals = [((r.get("details") or {}).get("sleep") or {}).get("consistency") for r in _score_rows(conn, owner, day)]
    cur = _mean(vals)
    if cur is None:
        o.missing = "近 7 晚没有规律性数据"
        return o
    tone = "good" if cur >= 80 else "mid" if cur >= 60 else "bad"
    o.current = {"value": round(cur), "unit": "", "basis": _avg7n(), "tone": tone}
    o.advice.append(_adv({
        "good": L("作息很规律，保持。", "Very regular schedule: keep it up."),
        "mid": L("入睡时间有些摇摆：先把起床时间固定在同一个点（差不超过 30 分钟）。",
                 "Your bedtime wobbles a bit: first fix your wake time (within 30 minutes every day)."),
        "bad": L("作息很不规律：每天同一时间起床最重要，入睡时间会慢慢跟上。",
                 "Very irregular schedule: getting up at the same time every day matters most; bedtime will follow."),
    }[tone], tone))
    return o


def calc_sleep_efficiency(conn, owner: int, day: date) -> Out:
    o = Out()
    rows = conn.execute(
        """SELECT DISTINCT ON (wake_date) wake_date, asleep_min, extract(epoch FROM end_at - start_at) / 60 AS in_bed
           FROM sleep_sessions WHERE user_id = %s AND is_main AND wake_date BETWEEN %s AND %s
           ORDER BY wake_date, asleep_min DESC NULLS LAST""", (owner, day - timedelta(days=6), day)).fetchall()
    effs = [min(100.0, 100 * r["asleep_min"] / float(r["in_bed"])) for r in rows if r["in_bed"] and r["asleep_min"]]
    if not effs:
        o.missing = "近 7 晚没有睡眠记录"
        return o
    cur = round(statistics.fmean(effs))   # classify the number the page shows
    tone = "good" if cur >= 85 else "mid" if cur >= 75 else "bad"
    o.current = {"value": round(cur), "unit": "%", "basis": _avg7n(), "tone": tone}
    o.advice.append(_adv({
        "good": L("躺下基本就睡着，保持。", "You fall asleep soon after lying down: keep it up."),
        "mid": L("躺床上醒着的时间偏多：困了再上床，睡不着 20 分钟就起来做点安静的事。",
                 "Quite a lot of time awake in bed: go to bed when sleepy; if you can't sleep for 20 minutes, get up and do something quiet."),
        "bad": L("躺床上醒着的时间很多：别在床上刷手机，困了再上床；长期这样可以了解一下 CBT-I（失眠的认知行为治疗）。",
                 "A lot of time awake in bed: don't scroll in bed and go to bed only when sleepy; if this lasts, look into "
                 "CBT-I (cognitive behavioural therapy for insomnia)."),
    }[tone], tone))
    return o


def calc_bedtime(conn, owner: int, day: date) -> Out:
    o = Out()
    items = status_mod.calc_bedtime(conn, owner, day)
    if not items:
        o.missing = "近 2 周没有睡眠记录"
        return o
    item = items[0][0]
    tone = TONE[item["tone"]]
    o.current = {"value": item["value"], "unit": "", "basis": _avg7n(), "tone": tone}
    o.advice.append(_adv({
        "good": L("入睡时间合适，保持。", "Good bedtime: keep it up."),
        "mid": L(f"平均 {item['value']} 才睡：每周往前挪 15–30 分钟，目标 24 点前。",
                 f"Asleep at {item['value']} on average: move it 15–30 minutes earlier each week, aiming for before midnight."),
        "bad": L(f"平均 {item['value']} 才睡，生物钟往后推了很多：先固定起床时间、起床就晒太阳，入睡每周只提前 15–30 分钟，别一次提前太多。",
                 f"Asleep at {item['value']} on average, your body clock has shifted a lot later: fix your wake time first and get "
                 "sunlight right after waking; move bedtime only 15–30 minutes earlier per week, not too much at once."),
    }[tone], tone))
    return o


def calc_recovery(conn, owner: int, day: date) -> Out:
    o = Out()
    rows = _score_rows(conn, owner, day)
    today = next((r for r in rows if r["date"] == day), None)
    rec = [r["recovery"] for r in rows if r["recovery"] is not None]
    if rec:
        o.facts.append(_fact(_avg7(), _fmt(statistics.fmean(rec))))
    if not today or today["recovery"] is None:
        o.missing = "这天还没有恢复分"
        if rec:
            last = next(r for r in reversed(rows) if r["recovery"] is not None)
            o.facts.append(_fact(L(f"上次 {_md(last['date'])}", f"Last {_md(last['date'])}"), str(last["recovery"])))
        return o
    zone = views.recovery_zone(today["recovery"])
    tone = {"green": "good", "yellow": "mid", "red": "bad"}[zone]
    o.current = {"value": today["recovery"], "unit": "", "basis": _md(day), "tone": tone}
    o.advice.append(_adv({
        "green": L("今天适合大重量或高强度，按计划练满。", "A good day for heavy weights or high intensity: do your full plan."),
        "yellow": L("按计划训练，控制强度（每组留 2 次余力），不冲新纪录。",
                    "Train as planned but control intensity (2 reps in reserve per set); no PR attempts."),
        "red": L("今天以恢复为主：轻松有氧、拉伸或休息，早点睡、多喝水。",
                 "Focus on recovery today: easy cardio, stretching or rest; sleep early and drink more water."),
    }[zone], tone))
    z = ((today.get("details") or {}).get("recovery") or {}).get("z") or {}
    neg = {k: v for k, v in z.items() if k in RECOVERY_PART and v is not None and v < -0.3}
    if neg:
        worst = min(neg, key=neg.get)
        o.advice.append(_adv(L(f"拉低最多的是{RECOVERY_PART[worst][0]}（比你的基线差）。",
                               f"{RECOVERY_PART[worst][1]} pulls it down most (worse than your baseline)."), "mid"))
    for k, v in z.items():
        if k in RECOVERY_PART and v is not None:
            o.facts.append(_fact(L(f"{RECOVERY_PART[k][0]} 对比基线", f"{RECOVERY_PART[k][1]} vs baseline"),
                                 L("高", "Higher") if v >= 0.3 else L("低", "Lower") if v <= -0.3 else L("持平", "Same")))
    return o


def calc_strain(conn, owner: int, day: date) -> Out:
    o = Out()
    rows = {r["date"]: r for r in _score_rows(conn, owner, day, days=8)}
    y = rows.get(day - timedelta(days=1)) or {}
    vals = [r["strain"] for d, r in rows.items() if d < day and r["strain"] is not None]
    if y.get("strain") is None and not vals:
        o.missing = "近 7 天没有负荷数据"
        return o
    if y.get("strain") is not None:
        s = y["strain"]
        tone = "plain" if s < 10 else "good" if s < 14 else "mid" if s < 18 else "bad"
        o.current = {"value": round(s, 1), "unit": "", "basis": L("前一天", "Previous day"), "tone": tone}
    if vals:
        o.facts.append(_fact(_avg7(), _fmt(statistics.fmean(vals), 1)))
    rec = (rows.get(day) or {}).get("recovery")
    zone = views.recovery_zone(rec)
    if zone:
        o.facts.append(_fact(L("今天恢复", "Recovery today"), str(rec)))
        o.advice.append(_adv({
            "green": L("今天恢复绿，可以安排高负荷（14–17）。", "Recovery is green today: you can plan high strain (14–17)."),
            "yellow": L("今天恢复黄，负荷控制在 10–14。", "Recovery is yellow today: keep strain at 10–14."),
            "red": L("今天恢复红，负荷控制在 10 以下，以轻松活动为主。",
                     "Recovery is red today: keep strain under 10, mostly light activity."),
        }[zone], {"green": "good", "yellow": "mid", "red": "bad"}[zone]))
    if y.get("tsb") is not None and y["tsb"] < -30:
        o.advice.append(_adv(L("最近累积疲劳很高（TSB 很低），安排一两天低负荷日。",
                               "Accumulated fatigue is very high (very low TSB): plan one or two low-strain days."), "bad"))
    return o


def calc_vo2max(conn, owner: int, day: date) -> Out:
    o = Out()
    v = fitness.vo2max(conn, owner, day)
    if v["current"] is None:
        o.missing = "近一年没有 VO₂max 读数"
        o.advice.append(_adv(L("户外步行、跑步或徒步时开体能训练记录，手表才会估算。",
                               "Start a workout on the watch for outdoor walks, runs or hikes, so it can estimate VO₂max.")))
        return o
    tone = {"excellent": "good", "good": "good", "average": "plain", "fair": "mid", "poor": "bad"}.get(v["level"], "plain")
    o.current = {"value": v["current"], "unit": "mL/kg·min", "basis": L("近 90 天均", "90-day avg"), "tone": tone}
    if v["level_zh"]:
        lvl = L(v["level_zh"], VO2_LEVEL_EN.get(v["level"], v["level_zh"]))
        o.facts.append(_fact(L("同龄同性别", "Same age and sex"),
                             L(f"{lvl} · 第 {v['percentile']:.0f} 百分位", f"{lvl} · {v['percentile']:.0f}th percentile")
                             if v["percentile"] is not None else lvl))
    if v["norm_median"] is not None:
        o.facts.append(_fact(L("同龄中位数", "Median for your age"), _fmt(v["norm_median"], 1)))
    if v["trend_per_year"] is not None:
        o.facts.append(_fact(L("每年变化", "Change per year"), f"{v['trend_per_year']:+.1f}"))
    if v["missing_reason"]:
        o.advice.append(_adv(L("在「我 → 个人资料」里填性别和生日，才能和同龄人比。",
                               "Add your sex and birthday under Me → Profile to compare with people your age.")))
    if v["level"] in ("excellent", "good"):
        o.advice.append(_adv(L("心肺能力不错：每周 2–3 次有氧保持住。", "Good cardiorespiratory fitness: keep it with 2–3 cardio sessions a week."), "good"))
    else:
        o.advice.append(_adv(L("提升空间大：每周 2–3 次 Zone 2 有氧（如爬坡快走）30–60 分钟，加 1 次间歇，几个月能看到上升。",
                               "Plenty of room to improve: 2–3 Zone 2 cardio sessions a week (e.g. incline walking) of 30–60 minutes, "
                               "plus one interval session; you should see it rise within a few months."), "mid"))
    if v["trend_per_year"] is not None and v["trend_per_year"] < -1:
        o.advice.append(_adv(L("过去一年在下降，有氧训练要加回来。", "It has been falling over the past year: bring cardio back in."), "mid"))
    return o


def _goal_weight(conn, owner: int) -> float | None:
    row = conn.execute("SELECT value FROM assistant_status WHERE user_id = %s AND key = %s",
                       (owner, status_mod.GOAL_KEY)).fetchone()
    m = re.search(r"\d+(\.\d+)?", (row or {}).get("value") or "")
    return float(m.group()) if m else None


def calc_weight(conn, owner: int, day: date) -> Out:
    o = Out()
    start = day - timedelta(days=20)
    try:
        w = views.weights(conn, owner, start, day)
    except Exception:  # noqa: BLE001  SparkyFitness unreachable / not linked: fall back to the watch's body mass
        w = {r["date"]: r["value"] for r in queries.daily_metrics(conn, owner, start, day, ["body_mass"])}
    if not w:
        o.missing = "近 3 周没有体重记录"
        return o
    last_day = max(w)
    cur7 = _mean(v for d, v in w.items() if d > day - timedelta(days=7))
    prev7 = _mean(v for d, v in w.items() if day - timedelta(days=14) < d <= day - timedelta(days=7))
    o.current = {"value": round(w[last_day], 1), "unit": "kg", "basis": _md(last_day), "tone": "plain"}
    if cur7 is not None:
        o.facts.append(_fact(_avg7(), f"{_fmt(cur7, 1)} kg"))
    goal = _goal_weight(conn, owner)
    if goal is not None:
        o.facts.append(_fact(L("目标", "Goal"), f"{_fmt(goal, 1)} kg"))
    if cur7 is None or prev7 is None:
        o.advice.append(_adv(L("每周至少称 3–4 次，才能算出周均变化。", "Weigh in at least 3–4 times a week so the weekly change can be computed.")))
        return o
    change = cur7 - prev7
    o.facts.append(_fact(L("周均变化", "Weekly change"), f"{change:+.1f} kg"))
    losing = goal is not None and w[last_day] > goal
    if not losing:
        o.advice.append(_adv(L("看周均变化，单日波动 1–2 kg 很正常。", "Watch the weekly change; day-to-day swings of 1–2 kg are normal.")))
    elif change < -1.0:
        o.current["tone"] = "mid"
        o.advice.append(_adv(L("掉得偏快：适当加点碳水，避免疲劳、早醒和掉肌肉。",
                               "Losing a bit fast: add some carbs to avoid fatigue, early waking and muscle loss."), "mid"))
    elif change <= -0.3:
        o.current["tone"] = "good"
        o.advice.append(_adv(L("速度合适，保持现在的热量和训练。", "Good pace: keep your current calories and training."), "good"))
    else:
        o.advice.append(_adv(L("周均没怎么降：每天少 200–300 kcal，或每天多走 2000–3000 步。",
                               "The weekly average has barely dropped: eat 200–300 kcal less a day, or walk 2,000–3,000 more steps a day."), "mid"))
    return o


CALC = {"hrv": calc_hrv, "resting_hr": calc_resting_hr, "sleep_hours": calc_sleep_hours, "sleep_score": calc_sleep_score,
        "sleep_debt": calc_sleep_debt, "sleep_consistency": calc_sleep_consistency,
        "sleep_efficiency": calc_sleep_efficiency, "bedtime": calc_bedtime, "recovery": calc_recovery,
        "strain": calc_strain, "vo2max": calc_vo2max, "weight": calc_weight}


# Trends tab metrics: the personal part comes from the same /trends data the tab shows (30 days ending on the day)
def _status_text(key: str, st: dict) -> tuple[str, str]:
    """(value, tone) of a status card for the explanation header."""
    if key == "illness":
        return f"{st['label']} · {st['score']}", {"low": "good", "watch": "mid", "high": "bad"}[st["level"]]
    if key == "body_battery":
        return str(st["current"]), "good" if st["current"] >= 60 else "mid" if st["current"] >= 30 else "bad"
    if key == "recovery_time":
        return f"{st['hours']} {_hours()}", "good" if not st["hours"] else "plain" if st["hours"] <= 24 else "mid"
    if key == "hr_recovery":
        return f"{_fmt(st['bpm'])} bpm", {"good": "good", "fair": "plain", "low": "bad"}[st["level"]]
    if key in ("strain_combined", "sleep_perf_whoop"):
        return _fmt(st["value"], 1), "plain"
    label = st.get("label", "—")
    tone = {"balanced": "good", "productive": "good", "exceptional": "good", "strong": "good", "good": "good",
            "low": "mid", "strained": "mid", "adequate": "mid", "fair": "mid", "poor": "bad", "overreaching": "bad",
            "limited": "bad", "late": "bad"}.get(st.get("status") or st.get("level"), "plain")
    return label, tone


def calc_trend(conn, owner: int, day: date, key: str) -> Out:
    o = Out()
    e = G.metric(key)
    d = views.trend_series.build(conn, {"id": owner}, owner, day, 30, views.local_today(conn, owner), views.user_tz(conn, owner))
    st = d["status"].get(e.get("status")) if e.get("status") else None
    s = d["series"].get(e.get("series")) if e.get("series") else None
    nd, unit = e.get("digits", 0), e.get("unit", "")
    if st:
        v, tone = _status_text(key, st)
        o.current = {"value": v, "unit": "", "basis": _md(day), "tone": tone}
    elif s and s["n"]:
        last = next((x for x in reversed(s["values"]) if x is not None), None)
        o.current = {"value": round(last, nd) if nd else round(last), "unit": unit, "basis": L("最近", "Latest"), "tone": "plain"}
    if s and s["n"]:
        o.facts.append(_fact(L("30 天均", "30-day avg"), f"{_fmt(s['mean'], nd)} {unit}".strip()))
        if s.get("prev_mean") is not None:
            o.facts.append(_fact(L("前 30 天", "Previous 30 days"), f"{_fmt(s['prev_mean'], nd)} {unit}".strip()))
    if o.current is None and not o.facts:
        o.missing = "还没有数据"
    return o


for _k, _e in G.TREND_METRICS.items():
    METRIC_SCOPE.setdefault(_k, _e["scope"])
    CALC.setdefault(_k, lambda conn, owner, day, _k=_k: calc_trend(conn, owner, day, _k))


# ---------------------------------------------------------------- status-item calculators
def calc_lab(conn, owner: int, key: str, day: date) -> Out:
    o = Out()
    rep = next(iter(records_store.lab_report(conn, owner, [key])), None)
    if rep is None or rep["latest"]["value"] is None:
        o.missing = "还没有化验记录"
        return o
    last = rep["latest"]
    unit = last["unit"] or ""
    tone = {"normal": "good", "high": "bad" if (rep.get("above_upper_pct") or 0) > 20 else "mid", "low": "mid"}.get(rep["status"], "plain")
    o.current = {"value": last["value"], "unit": unit, "basis": f"{last['collected_on']:%Y-%m-%d}", "tone": tone}
    how = [L(*LAB_SOURCE.get(last.get("source"), ("化验", "Lab test"))),
           {True: L("空腹", "Fasting"), False: L("饭后", "After a meal")}.get(last.get("fasting"))]
    o.facts.append(_fact(L("来源", "Source"), " · ".join(x for x in how if x)))
    lo, hi = last.get("ref_low"), last.get("ref_high")
    if lo is not None or hi is not None:
        rng = f"{_fmt(lo, 2)}–{_fmt(hi, 2)}" if lo is not None and hi is not None else \
            f"≤ {_fmt(hi, 2)}" if hi is not None else f"≥ {_fmt(lo, 2)}"
        o.facts.append(_fact(L("参考范围", "Reference range"), f"{rng} {unit}".strip()))
    hist = [h for h in rep["history"] if h["value"] is not None]
    if len(hist) >= 2:
        prev = hist[-2]
        o.facts.append(_fact(L(f"上次 {prev['collected_on']:%Y-%m-%d}", f"Previous {prev['collected_on']:%Y-%m-%d}"),
                             f"{_fmt(prev['value'], 2)} {unit}".strip()))
    if rep["status"] == "high":
        pct = rep.get("above_upper_pct")
        p = f" {pct:.0f}%" if pct is not None else ""
        o.advice.append(_adv(L(f"最近一次高于上限{p}：按下面的建议调整饮食和生活方式，按时复查。",
                               f"The latest result is{p} above the upper limit: adjust diet and lifestyle as suggested below and "
                               "get retested on time."), tone))
    elif rep["status"] == "low":
        o.advice.append(_adv(L("最近一次低于参考范围，复查时问医生。",
                               "The latest result is below the reference range: ask your doctor at the follow-up."), "mid"))
    else:
        o.advice.append(_adv(L("在参考范围内，保持。", "Within the reference range: keep it up."), "good"))
    if last.get("source") == "home_device":
        o.advice.append(_adv(L("最近一次是家用仪器测的，误差比化验所大，看趋势时和同样条件（空腹 / 饭后）的家测比，"
                               "下结论以化验所的结果为准。",
                               "The latest reading is from a home device, which is less accurate than a lab: for trends, compare "
                               "with home readings taken under the same conditions (fasting / after a meal), and rely on lab results "
                               "for conclusions."), "plain"))
        if key == "uric_acid" and last.get("fasting") is False:
            o.advice.append(_adv(L("这次是饭后测的，尿酸饭后会偏高一些，下次尽量早上空腹测，才好和以前比。",
                                   "This reading was taken after a meal, when uric acid reads a little higher; next time test fasting "
                                   "in the morning so it compares with earlier readings."), "plain"))
    weeks = (day - last["collected_on"]).days // 7
    if rep["status"] != "normal" and weeks >= 12:
        o.advice.append(_adv(L(f"已经 {weeks} 周没复查了，约 GP 复查。", f"No retest for {weeks} weeks: book a follow-up with your GP."), "mid"))
    return o


LAB_SOURCE = {"lab": ("化验所", "Lab"), "home_device": ("家用仪器", "Home device")}


def _lab_entry(conn, owner: int, key: str, label: str | None = None) -> dict:
    if key in G.LABS:
        return G.lab(key)
    name = label or next((r["test_name"] for r in records_store.lab_report(conn, owner, [key])), key)
    return {"title": name, "what": L("化验 / 体检指标。数值和参考范围以化验单为准；看趋势时比较同一来源、同样条件（是否空腹）下的历次结果。",
                                     "A lab / check-up result. Values and reference ranges follow your lab report; for trends, compare "
                                     "results from the same source under the same conditions (fasting or not)."),
            "keywords": [name]}


def explain_lab(conn: psycopg.Connection, viewer_id: int, owner: int, key: str, day: date) -> dict:
    """One lab test (the trend sheet). Caller has checked the labs scope."""
    e = _lab_entry(conn, owner, key)
    view = _entry_view("lab", key, e)
    _apply(view, calc_lab(conn, owner, key, day))
    if auth.can_view(conn, viewer_id, owner, "memory"):  # profile card lines live in memory
        view["profile"] = profile_sentences(conn, owner, [e["title"]], e.get("keywords", []))
    view["ai_prompt"] = _prompt(e["title"], view)
    return view


def calc_training_week(item: dict) -> Out:
    o = Out()
    n = int(re.search(r"\d+", item["value"]).group()) if re.search(r"\d+", item["value"] or "") else None
    m = re.search(r"计划\s*(\d+)", item.get("note") or "")
    plan = int(m.group(1)) if m else None
    if n is None:
        return o
    tone = TONE.get(item["tone"], "plain")
    o.current = {"value": n, "unit": L("次", "sessions"), "basis": L("近 7 天", "Last 7 days"), "tone": tone}
    if plan:
        o.facts.append(_fact(L("计划", "Plan"), L(f"{plan} 次 / 周", f"{plan} / week")))
        o.advice.append(_adv(L("本周计划完成了，注意安排休息日。", "This week's plan is done: remember to schedule rest days.") if n >= plan else
                             L(f"比计划少 {plan - n} 次：恢复允许的话补上，不要一天练两次补。",
                               f"Sessions behind plan: {plan - n}. Make them up if Recovery allows, but don't train twice a day to catch up."),
                             "good" if n >= plan else tone))
    return o


def calc_red_meat(item: dict) -> Out:
    o = Out()
    m = re.search(r"\d+(\.\d+)?", item["value"] or "")
    if not m:
        return o
    grams = float(m.group())
    tone = TONE.get(item["tone"], "plain")
    o.current = {"value": round(grams), "unit": "g", "basis": L("近 7 天", "Last 7 days"), "tone": tone}
    # The cap is the user's own diet constraint (nutrition/constraints.py, issue #76), written into the card's note
    c = re.search(r"上限 (\d+(?:\.\d+)?) g", item.get("note") or "")
    if not c:
        return o
    cap = float(c.group(1))
    o.facts.append(_fact(L("你设的每周上限", "Your weekly limit"), f"{cap:g} g"))
    o.advice.append(_adv(L(f"还有 {cap - grams:.0f} g 余量。", f"{cap - grams:.0f} g left this week.") if grams <= cap else
                         L(f"超了 {grams - cap:.0f} g：接下来几天的蛋白质换成禽肉、鱼、蛋、奶或豆制品。",
                           f"{grams - cap:.0f} g over: get your protein from poultry, fish, eggs, dairy or soy for the next "
                           "few days."), tone))
    return o


# ---------------------------------------------------------------- profile card sentences and exercise rules
_REF = re.compile(r"（[\d.、，,\s]+）")


def profile_sentences(conn, owner: int, primary: list[str], related: list[str], limit: int = 4) -> list[str]:
    """Sentences from the owner's profile card that mention the item (section references like "（2.3、4.3）" stripped).
    Ranked by hits: a primary word (the item's own label / value) counts 3, a related keyword 1; ties keep card order."""
    primary, related = [k for k in primary if k], [k for k in related if k and k not in primary]
    if not primary and not related:
        return []
    found: list[tuple[int, int, str]] = []
    for r in conn.execute("SELECT value FROM assistant_profile WHERE user_id = %s ORDER BY sort, key", (owner,)):
        for s in re.split(r"(?<=[。；;！？])", r["value"] or ""):
            s = _REF.sub("", s).strip(" 。；;")
            low = s.lower()
            score = 3 * sum(k.lower() in low for k in primary) + sum(k.lower() in low for k in related)
            if s and score and all(s != x for _, _, x in found):
                found.append((-score, len(found), s if len(s) <= 140 else s[:139] + "…"))
    return [s for _, _, s in sorted(found)[:limit]]


def exercise_limits(conn, owner: int) -> dict:
    rules = records_store.exercise_rules(conn, owner)
    pick = lambda r: {"name": r["name_zh"] or r["name_en"] or r["exercise_ref"], "reason": r["reason"], "cues": r["cues"]}
    return {"banned": [pick(r) for r in rules["banned"]], "caution": [pick(r) for r in rules["caution"]]}


# ---------------------------------------------------------------- assemble
def _entry_view(kind: str, key: str, e: dict) -> dict:
    return {"kind": kind, "key": key, "title": e["title"], "what": e["what"],
            "read": [{"range": r, "label": l, "tone": t} for r, l, t in e.get("read", [])],
            "recovery": e.get("recovery") or None, "tips": list(e.get("tips", [])), "avoid": list(e.get("avoid", [])),
            "current": None, "facts": [], "advice": [], "profile": [], "exercises": None,
            "missing": None, "hidden": [], "source": "rules"}


def _apply(view: dict, o: Out) -> None:
    view["current"] = view["current"] or o.current
    view["facts"] += o.facts
    view["advice"] += o.advice
    view["missing"] = view["missing"] or (o.missing if o.current is None else None)


def _prompt(title: str, view: dict, subject: str | None = None) -> str:
    cur = view.get("current")
    bits = [f"{cur['basis']} {cur['value']}{(' ' + cur['unit']) if cur.get('unit') else ''}"] if cur else []
    bits += [f"{f['label']} {f['value']}" for f in view["facts"][:4]]
    if is_en():
        what = subject or f"my \"{title}\""
        return (f"Please explain {what}" + (f" ({'; '.join(bits)})" if bits else "") +
                ": what does it mean, how am I doing right now, what exactly should I do next, and what should I avoid?")
    what = subject or f"我的「{title}」"
    return (f"帮我解读一下{what}" + (f"（{'；'.join(bits)}）" if bits else "") +
            "：这是什么意思、我现在的情况怎么样、接下来具体该怎么做、要避免什么？")


def explain_metric(conn: psycopg.Connection, viewer_id: int, owner: int, key: str, day: date) -> dict:
    e = G.metric(key)
    view = _entry_view("metric", key, e)
    scope = METRIC_SCOPE[key]
    if auth.can_view(conn, viewer_id, owner, scope):
        _apply(view, CALC[key](conn, owner, day))
    else:
        view["hidden"].append(scope)
        view["missing"] = views.forbidden(scope)["error"]
    view["ai_prompt"] = _prompt(e["title"], view)
    return view


def _status_item(conn, owner: int, key: str, viewer_id: int | None = None) -> dict | None:
    """A card the viewer may not see (status.list_status: its data needs its own scope) counts as missing."""
    return next((i for i in status_mod.list_status(conn, owner, viewer_id) if i["key"] == key), None)


def explain_status(conn: psycopg.Connection, viewer_id: int, owner: int, key: str, day: date) -> dict | None:
    """Caller has checked the memory scope (status items live there). None = no such status item, or one built from
    data the viewer may not see (a weight / lab / sleep card needs that scope too, issue #111)."""
    item = _status_item(conn, owner, key, viewer_id)
    if item is None:
        return None
    text = " ".join(x for x in (item["label"], item["value"], item.get("note")) if x)
    cond = G.condition_for(text)
    if key in G.STATUS_AS_METRIC:
        view = explain_metric(conn, viewer_id, owner, G.STATUS_AS_METRIC[key], day)
        keywords = G.METRICS[G.STATUS_AS_METRIC[key]].get("keywords", [])
    elif key in G.LABS or item.get("lab_key"):
        e = _lab_entry(conn, owner, item.get("lab_key") or key, item["label"])
        view = _entry_view("status", key, e)
        if auth.can_view(conn, viewer_id, owner, "labs"):
            _apply(view, calc_lab(conn, owner, item.get("lab_key") or key, day))
        else:
            view["hidden"].append("labs")
        keywords = e.get("keywords", [])
    elif key in G.STATUS:
        e = dict(G.status(key))
        if cond:  # e.g. phase "减脂增肌": the status entry's title, the condition's explanation
            e = {**cond, "title": e["title"], "what": L(f"{e['what']}「{item['value']}」：{cond['what']}",
                                                         f"{e['what']} \u201c{item['value']}\u201d: {cond['what']}")}
        view = _entry_view("status", key, e)
        keywords = e.get("keywords", [])
        if key in ("to_goal", "goal_weight") and auth.can_view(conn, viewer_id, owner, "weight"):
            _apply(view, calc_weight(conn, owner, day))
        elif key == "training_week":
            _apply(view, calc_training_week(item))
        elif key == "red_meat_week":
            _apply(view, calc_red_meat(item))
    elif cond:
        view = _entry_view("status", key, cond)
        keywords = cond["keywords"]
    else:
        view = _entry_view("status", key, {"title": item["label"], "what": item.get("note") or L(
            "管家根据你的记录写下的判断，相关记录见下面。", "A note Jarvis wrote from your records; related records are below.")})
        keywords = []
    view["kind"] = "status"
    view["item"] = {k: item[k] for k in ("key", "label", "value", "unit", "tone", "note", "group", "source", "updated_at")}
    if cond and key not in G.STATUS_AS_METRIC:
        for m in cond.get("metrics", []):  # e.g. sleep conditions: attach the owner's own sleep numbers (one fact + advice each)
            if not auth.can_view(conn, viewer_id, owner, METRIC_SCOPE[m]):
                continue
            o = CALC[m](conn, owner, day)
            if o.current:
                c = o.current
                view["facts"].append(_fact(G.metric(m)["title"], f"{c['value']}{(' ' + c['unit']) if c.get('unit') else ''}"
                                                                 f" · {c['basis']}"))
                view["advice"] += o.advice
        if "肩" in cond["keywords"] and auth.can_view(conn, viewer_id, owner, "exercises"):
            ex = view["exercises"] = exercise_limits(conn, owner)
            if ex["banned"] or ex["caution"]:
                view["advice"].append(_adv(L(f"你的动作库里有 {len(ex['banned'])} 个禁用、{len(ex['caution'])} 个要小心的动作（见下面），"
                                             "练之前对一下；肩前侧疼了立刻换动作。",
                                             f"Your exercise library lists banned: {len(ex['banned'])}, caution: {len(ex['caution'])} "
                                             "(see below). Check them before training; if the front of the shoulder hurts, switch "
                                             "exercises right away."), "mid"))
    if view["current"] is None and key not in G.STATUS_AS_METRIC:
        view["current"] = {"value": item["value"], "unit": item.get("unit") or "", "basis": item["label"],
                           "tone": TONE.get(item["tone"], "plain")}
    own = [item["label"], *re.findall(r"[\u4e00-\u9fffA-Za-z]{2,}", item["value"] or "")]
    view["profile"] = profile_sentences(conn, owner, own, keywords)
    view["ai_prompt"] = _prompt(view["title"], view, L(f"我的「{item['label']}：{item['value']}」",
                                                       f"my \"{item['label']}: {item['value']}\""))
    return view


"""One night on the sleep detail page (by wake date): times, stage composition and timeline, the sleep score and its parts,
the owner's own 7 / 30-night averages, overnight vitals and rule-based notes. Nothing here calls an AI: `ai_prompt` is the
question the page hands to the viewer's own AI when they tap "ask Jarvis".

Authorisation: the endpoint needs the `sleep` share (the night itself, its stages and averages). The sleep score, sleep need /
debt and the overnight vitals come from the scores row, so they need `scores` too; without it they are null and `hidden`
lists "scores". Averages compare the night with the nights before it (the night itself is not in its own average).
Texts the page shows (notes, prompt) follow the request language (L); the codes (`key`, `tone`) are for the page and the AI.
"""
import math
import statistics
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg

from .. import auth, queries
from ..i18n import L
from ..insights import sleep_detail
from ..scores import params as SP
from ..web import views
from . import params as P

STAGES = ("deep", "core", "rem", "unspecified", "awake")
SCORE_PARTS = ("duration", "stages", "consistency", "interruptions")
SCORE_WEIGHTS = {"duration": SP.DURATION_IMPACT, "stages": SP.STAGES_IMPACT, "consistency": SP.CONSISTENCY_IMPACT,
                 "interruptions": SP.INTERRUPTIONS_IMPACT}
TONE_ORDER = {"bad": 0, "mid": 1, "good": 2}
_MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# ---------------------------------------------------------------- small helpers
def _r(v, nd=0):
    return None if v is None else (round(v) if nd == 0 else round(v, nd))


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if len(xs) >= P.MIN_BASELINE_NIGHTS else None


def _clock_min(dt: datetime, tz: ZoneInfo) -> int:
    t = dt.astimezone(tz)
    return t.hour * 60 + t.minute


def _circ_mean(minutes: list[int]) -> int | None:
    """Mean clock time on the 24-hour circle (bedtimes cross midnight)."""
    if len(minutes) < P.MIN_BASELINE_NIGHTS:
        return None
    x = sum(math.cos(m / 1440 * 2 * math.pi) for m in minutes)
    y = sum(math.sin(m / 1440 * 2 * math.pi) for m in minutes)
    a = math.atan2(y, x) % (2 * math.pi)
    return round(a / (2 * math.pi) * 1440) % 1440


def _circ_diff(a: int, b: int) -> int:
    """a − b on the clock, in −720…720 (positive = a is later)."""
    return (a - b + 720) % 1440 - 720


def _hm(minutes: int | None) -> str | None:
    return None if minutes is None else f"{minutes // 60 % 24:02d}:{minutes % 60:02d}"


def _dur(minutes: float) -> str:
    m = int(round(abs(minutes)))
    return L(f"{m // 60} 小时 {m % 60} 分" if m >= 60 else f"{m} 分钟", f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m} min")


def _md(d: date) -> str:
    return L(f"{d.month} 月 {d.day} 日", f"{d.day} {_MON[d.month - 1]}")


def _efficiency(row: dict, in_bed: float) -> float | None:
    return round(min(100.0, 100 * row["asleep_min"] / in_bed), 1) if in_bed and row["asleep_min"] else None


# ---------------------------------------------------------------- database reads
MAIN_SQL = """SELECT DISTINCT ON (wake_date) wake_date, start_at, end_at, asleep_min, deep_min, core_min, rem_min, awake_min,
                     unspecified_min, source, device
              FROM sleep_sessions WHERE user_id = %s AND is_main AND wake_date BETWEEN %s AND %s
              ORDER BY wake_date, asleep_min DESC NULLS LAST"""


def _mains(conn, owner_id: int, start: date, end: date) -> dict[date, dict]:
    return {r["wake_date"]: r for r in conn.execute(MAIN_SQL, (owner_id, start, end))}


def _latest(conn, owner_id: int, today: date) -> date | None:
    r = conn.execute("SELECT max(wake_date) AS d FROM sleep_sessions WHERE user_id = %s AND is_main AND wake_date <= %s",
                     (owner_id, today)).fetchone()
    return r["d"]


def _neighbours(conn, owner_id: int, day: date, today: date) -> tuple[date | None, date | None]:
    r = conn.execute(
        """SELECT (SELECT max(wake_date) FROM sleep_sessions WHERE user_id = %(u)s AND is_main AND wake_date < %(d)s) AS prev,
                  (SELECT min(wake_date) FROM sleep_sessions WHERE user_id = %(u)s AND is_main AND wake_date > %(d)s
                     AND wake_date <= %(t)s) AS next""", {"u": owner_id, "d": day, "t": today}).fetchone()
    return r["prev"], r["next"]


def _scores(conn, owner_id: int, start: date, end: date) -> dict[date, dict]:
    return {r["date"]: r for r in conn.execute(
        """SELECT date, sleep_score, sleep_performance, sleep_actual_min, sleep_need_min, sleep_debt_min,
                  round(exp(hrv_night_ln)::numeric, 1)::float AS hrv_ms, rhr_night, resp_rate_night,
                  details->'sleep' AS sleep, details->'recovery' AS recovery, flags
           FROM daily_scores WHERE user_id = %s AND date BETWEEN %s AND %s""", (owner_id, start, end))}


# ---------------------------------------------------------------- blocks
def _night(conn, owner_id: int, tz: ZoneInfo, row: dict, sc: dict | None) -> dict:
    start, end = row["start_at"], row["end_at"]
    in_bed = (end - start).total_seconds() / 60
    s = (sc or {}).get("sleep") or {}
    eff = s.get("efficiency")
    stages, source, reason = views.night_stages(conn, owner_id, tz, start, end)
    minutes = {k: row[f"{k}_min"] for k in STAGES}
    staged = sum(minutes[k] or 0 for k in ("deep", "core", "rem")) > 0
    total = sum(v or 0 for v in minutes.values())
    comp = [{"stage": k, "min": _r(minutes[k]), "pct": _r(100 * (minutes[k] or 0) / total)}
            for k in STAGES if minutes[k] or (staged and k != "unspecified")] if total else []
    asleep = row["asleep_min"] or 0
    return {"start": start.astimezone(tz).strftime("%H:%M"), "end": end.astimezone(tz).strftime("%H:%M"),
            "start_at": start.astimezone(tz).isoformat(), "end_at": end.astimezone(tz).isoformat(),
            "in_bed_min": round(in_bed), "asleep_min": row["asleep_min"],
            **{f"{k}_min": minutes[k] for k in STAGES},
            "efficiency_pct": round(100 * eff, 1) if eff is not None else _efficiency(row, in_bed),
            "latency_min": _r(s.get("latency_min")), "waso_min": _r(s.get("waso_min")), "awakenings": s.get("awakenings"),
            "deep_pct": _r(100 * (minutes["deep"] or 0) / asleep) if staged and asleep else None,   # of time asleep (notes)
            "rem_pct": _r(100 * (minutes["rem"] or 0) / asleep) if staged and asleep else None,
            "source": row["source"], "device": row["device"],
            "staged": staged, "composition": comp,
            "stages": stages, "stages_source": source, "stages_reason": reason}


def _score(sc: dict | None) -> dict | None:
    if not sc or sc["sleep_score"] is None:
        return None
    s = sc.get("sleep") or {}
    return {"value": sc["sleep_score"],
            "parts": [{"key": k, "score": s.get(k), "weight": SCORE_WEIGHTS[k]} for k in SCORE_PARTS],
            "performance_pct": _r(100 * sc["sleep_performance"]) if sc["sleep_performance"] is not None else None,
            "actual_min": sc["sleep_actual_min"], "need_min": sc["sleep_need_min"], "debt_min": sc["sleep_debt_min"],
            "need_from_debt": s.get("need_from_debt"), "need_from_strain": s.get("need_from_strain"),
            "nap_min": s.get("nap_min")}


def _avg_block(mains: list[dict], scores: list[dict] | None, tz: ZoneInfo) -> dict:
    in_bed = [(r["end_at"] - r["start_at"]).total_seconds() / 60 for r in mains]
    staged = [r for r in mains if r["asleep_min"] and (r["deep_min"] or 0) + (r["core_min"] or 0) + (r["rem_min"] or 0) > 0]
    deep = [100 * (r["deep_min"] or 0) / r["asleep_min"] for r in staged]
    rem = [100 * (r["rem_min"] or 0) / r["asleep_min"] for r in staged]
    out = {"nights": len(mains),
           "asleep_min": _r(_mean([r["asleep_min"] for r in mains])),
           "in_bed_min": _r(_mean(in_bed)),
           "efficiency_pct": _r(_mean([_efficiency(r, b) for r, b in zip(mains, in_bed)]), 1),
           "deep_min": _r(_mean([r["deep_min"] or 0 for r in staged])), "rem_min": _r(_mean([r["rem_min"] or 0 for r in staged])),
           "deep_pct": _r(_mean(deep)), "rem_pct": _r(_mean(rem)),
           "bedtime": _hm(_circ_mean([_clock_min(r["start_at"], tz) for r in mains])),
           "wake": _hm(_circ_mean([_clock_min(r["end_at"], tz) for r in mains]))}
    if scores is not None:
        out["sleep_score"] = _r(_mean([s["sleep_score"] for s in scores]))
    return out


def _baseline(conn, owner_id: int, tz: ZoneInfo, day: date, mains: dict, scores: dict | None) -> dict:
    out = {}
    for n in (P.BASELINE_SHORT, P.BASELINE_LONG):
        days = [day - timedelta(days=k) for k in range(1, n + 1)]
        out[f"avg{n}"] = _avg_block([mains[d] for d in days if d in mains],
                                    None if scores is None else [scores[d] for d in days if d in scores], tz)
    r7 = sleep_detail.sri_for(conn, owner_id, tz, day - timedelta(days=6), day)
    r30 = sleep_detail.sri_for(conn, owner_id, tz, day - timedelta(days=29), day)
    out["regularity_pct_7d"] = sleep_detail.consistency_pct(r7["sri"])
    out["regularity_pct_30d"] = sleep_detail.consistency_pct(r30["sri"])
    return out


VITALS = (  # key, unit, digits, how to read it from a scores row
    ("hrv", "ms", 0, lambda s: s["hrv_ms"]),
    ("rhr", "bpm", 0, lambda s: s["rhr_night"]),
    ("resp_rate", "rpm", 1, lambda s: s["resp_rate_night"]),
    ("spo2", "%", 1, lambda s: (s.get("recovery") or {}).get("spo2_night")),
    ("wrist_temp", "°C", 2, lambda s: (s.get("recovery") or {}).get("wrist_temp")),
)


def _vitals(day: date, scores: dict[date, dict]) -> list[dict]:
    sc = scores.get(day)
    if sc is None:
        return []
    past = [scores[d] for d in (day - timedelta(days=k) for k in range(1, P.BASELINE_LONG + 1)) if d in scores]
    out = []
    for key, unit, nd, get in VITALS:
        v = get(sc)
        if v is None:
            continue
        avg = _mean([get(s) for s in past])
        out.append({"key": key, "value": _r(v, nd), "unit": unit, "avg30": _r(avg, nd),
                    "delta": None if avg is None else _r(v - avg, nd)})
    return out


# ---------------------------------------------------------------- rule-based notes
def _note(key: str, tone: str, text: str) -> dict:
    return {"key": key, "tone": tone, "text": text}


def notes(night: dict, score: dict | None, base: dict, vitals: list[dict], flags: dict | None) -> list[dict]:
    """Short findings about this night, worst first (at most MAX_NOTES). Pure: tests feed it plain dicts."""
    out: list[dict] = []
    avg30 = base.get(f"avg{P.BASELINE_LONG}") or {}
    if score and score.get("need_min") and score.get("actual_min") is not None:
        short = score["need_min"] - score["actual_min"]
        if short >= P.SHORT_OF_NEED_MIN:
            out.append(_note("short_of_need", "bad" if short >= P.SHORT_OF_NEED_BAD_MIN else "mid",
                             L(f"比睡眠需求少 {_dur(short)}", f"{_dur(short)} short of your sleep need")))
        else:
            out.append(_note("need_met", "good", L("睡够了需求", "Sleep need met")))
    if score:
        weak = [p for p in score["parts"] if p["score"] is not None and p["score"] < P.PART_WEAK]
        if weak:
            w = min(weak, key=lambda p: p["score"])
            name = {"duration": ("时长", "duration"), "stages": ("睡眠阶段", "sleep stages"),
                    "consistency": ("规律性", "consistency"), "interruptions": ("中途醒来", "interruptions")}[w["key"]]
            out.append(_note(f"weak_{w['key']}", "mid" if w["score"] >= 50 else "bad",
                             L(f"睡眠分里最拉分的是{name[0]}（{w['score']}）", f"Weakest part of the score: {name[1]} ({w['score']})")))
    if night.get("deep_pct") is not None and night["deep_pct"] < P.DEEP_LOW_PCT:
        out.append(_note("deep_low", "mid", L(f"深睡只占睡着时间的 {night['deep_pct']}%", f"Deep sleep only {night['deep_pct']}% of time asleep")))
    if night.get("rem_pct") is not None and night["rem_pct"] < P.REM_LOW_PCT:
        out.append(_note("rem_low", "mid", L(f"REM 只占睡着时间的 {night['rem_pct']}%", f"REM only {night['rem_pct']}% of time asleep")))
    eff = night.get("efficiency_pct")
    if eff is not None and eff < P.EFFICIENCY_GOOD:
        out.append(_note("efficiency_low", "bad" if eff < P.EFFICIENCY_POOR else "mid",
                         L(f"睡眠效率 {eff:g}%，躺着醒着的时间偏多", f"Sleep efficiency {eff:g}%: a lot of time awake in bed")))
    lat = night.get("latency_min")
    if lat is not None and lat >= P.LATENCY_LONG_MIN:
        out.append(_note("latency_long", "bad" if lat >= P.LATENCY_POOR_MIN else "mid",
                         L(f"入睡用了 {lat} 分钟", f"Took {lat} min to fall asleep")))
    waso = night.get("waso_min")
    if waso is not None and waso >= P.WASO_LONG_MIN:
        n = night.get("awakenings")
        out.append(_note("waso_long", "bad" if waso >= P.WASO_POOR_MIN else "mid",
                         L(f"睡着后醒了 {waso} 分钟" + (f"（{n} 次）" if n else ""),
                           f"Awake {waso} min after falling asleep" + (f" ({n}×)" if n else ""))))
    if avg30.get("bedtime") and night.get("start"):
        h, m = (int(x) for x in night["start"].split(":"))
        bh, bm = (int(x) for x in avg30["bedtime"].split(":"))
        shift = _circ_diff(h * 60 + m, bh * 60 + bm)
        if abs(shift) >= P.BEDTIME_SHIFT_MIN:
            out.append(_note("bedtime_late" if shift > 0 else "bedtime_early", "mid" if shift > 0 else "good",
                             L(f"入睡比 30 晚平均{'晚' if shift > 0 else '早'} {_dur(shift)}",
                               f"Asleep {_dur(shift)} {'later' if shift > 0 else 'earlier'} than your 30-night average")))
    by = {v["key"]: v for v in vitals}
    hrv = by.get("hrv")
    if hrv and hrv["avg30"] and hrv["value"] < hrv["avg30"] * (1 - P.HRV_LOW_PCT / 100):
        out.append(_note("hrv_low", "mid", L(f"HRV 比 30 晚平均低 {round(100 * (1 - hrv['value'] / hrv['avg30']))}%",
                                             f"HRV {round(100 * (1 - hrv['value'] / hrv['avg30']))}% below your 30-night average")))
    rhr = by.get("rhr")
    if rhr and rhr["delta"] is not None and rhr["delta"] >= P.RHR_HIGH_BPM:
        out.append(_note("rhr_high", "mid", L(f"睡眠心率比平时高 {rhr['delta']:g} bpm", f"Sleeping HR {rhr['delta']:g} bpm above usual")))
    resp = by.get("resp_rate")
    if resp and resp["delta"] is not None and resp["delta"] >= P.RESP_HIGH:
        out.append(_note("resp_high", "mid", L(f"呼吸频率比平时高 {resp['delta']:g}", f"Respiratory rate {resp['delta']:g} above usual")))
    temp = by.get("wrist_temp")
    if temp and temp["delta"] is not None and abs(temp["delta"]) >= P.TEMP_DELTA_C:
        out.append(_note("temp_off", "mid", L(f"手腕温度比平时{'高' if temp['delta'] > 0 else '低'} {abs(temp['delta']):g}°C",
                                              f"Wrist temperature {abs(temp['delta']):g}°C {'above' if temp['delta'] > 0 else 'below'} usual")))
    spo2 = by.get("spo2")
    if spo2 and spo2["value"] < P.SPO2_LOW:
        out.append(_note("spo2_low", "bad", L(f"血氧中位数 {spo2['value']:g}%", f"Median SpO₂ {spo2['value']:g}%")))
    if (flags or {}).get("illness_warning"):
        out.append(_note("illness", "bad", L("静息心率高、HRV 低：可能在生病或过度疲劳", "High resting HR and low HRV: possibly ill or overreached")))
    if not any(n["tone"] != "good" for n in out):
        out.append(_note("all_good", "good", L("这晚各项都在正常范围", "Everything within your normal range")))
    out.sort(key=lambda n: TONE_ORDER[n["tone"]])
    return out[:P.MAX_NOTES]


def ai_prompt(day: date, night: dict | None, score: dict | None) -> str:
    if night is None:
        return L(f"{_md(day)}早上醒来的那晚我没有睡眠数据，可能是什么原因？", f"I have no sleep data for the night ending {_md(day)}. Why might that be?")
    asleep = _dur(night["asleep_min"] or 0)
    sc = f"，睡眠分 {score['value']}" if score else ""
    sc_en = f", sleep score {score['value']}" if score else ""
    return L(f"详细分析一下我 {_md(day)} 早上醒来的那晚睡眠（{night['start']} → {night['end']}，睡着 {asleep}{sc}）："
             "阶段组成、和我平时比怎么样、哪里拉分，今晚怎么调整。",
             f"Analyse my sleep in detail for the night ending {_md(day)} ({night['start']} → {night['end']}, asleep {asleep}{sc_en}): "
             "stage composition, how it compares with my usual nights, what pulled it down, and what to change tonight.")


# ---------------------------------------------------------------- the page
def build(conn: psycopg.Connection, viewer_id: int, owner_id: int, day: date | None = None) -> dict:
    tz = queries.user_tz(conn, owner_id)
    today = queries.local_today(conn, owner_id)
    day = day or _latest(conn, owner_id, today) or today
    can_scores = auth.can_view(conn, viewer_id, owner_id, "scores")
    mains = _mains(conn, owner_id, day - timedelta(days=P.BASELINE_LONG), day)
    scores = _scores(conn, owner_id, day - timedelta(days=P.BASELINE_LONG), day) if can_scores else None
    prev, nxt = _neighbours(conn, owner_id, day, today)
    row, sc = mains.get(day), (scores or {}).get(day)
    night = _night(conn, owner_id, tz, row, sc) if row else None
    if night is not None and not can_scores:   # latency / wake-ups come from the scores row: without the share they stay empty
        night.update(latency_min=None, waso_min=None, awakenings=None)
    naps = [{"start": r["start_at"].astimezone(tz).strftime("%H:%M"), "end": r["end_at"].astimezone(tz).strftime("%H:%M"),
             "asleep_min": r["asleep_min"]} for r in conn.execute(
        """SELECT start_at, end_at, asleep_min FROM sleep_sessions WHERE user_id = %s AND wake_date = %s AND NOT is_main
           ORDER BY start_at""", (owner_id, day))]
    score = _score(sc)
    base = _baseline(conn, owner_id, tz, day, mains, scores)
    if scores is not None and sc is not None:
        base["need_min"] = sc["sleep_need_min"]
    vitals = _vitals(day, scores) if scores is not None else []
    out = {"date": day, "today": today, "prev": prev, "next": nxt, "is_latest": nxt is None,
           "main": night, "naps": naps, "score": score, "baseline": base, "vitals": vitals,
           "notes": notes(night, score, base, vitals, (sc or {}).get("flags")) if night else [],
           "reason": None if night else "这天没有手表睡眠数据",
           "ai_prompt": ai_prompt(day, night, score), "hidden": [] if can_scores else ["scores"]}
    return out

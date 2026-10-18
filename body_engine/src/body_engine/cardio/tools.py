"""Butler tools for cardio metrics and ECG summaries (read-only; wrapped into Tool by assistant/tools.py).
The ECG waveform is never given to the AI: only Apple's classification, heart rate and symptoms (docs/raw-data-plan.md §7.1)."""
from datetime import date
from zoneinfo import ZoneInfo

from ..rawdata import store as rx_store
from ..toolspec import DETAIL, USER, nullable, schema
from . import views


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


_schema = schema
RECENT_VALID = 10
RECENT_NIGHTS = 7


def slim_cardio(res: dict) -> dict:
    """Summary + the last valid HRR60 readings + the last nights' recovery index (the full lists: detail=true)."""
    hrr = res.get("hrr") or {}
    workouts = hrr.get("workouts") or []
    valid = [{k: w.get(k) for k in ("date", "name_zh", "peak_hr", "hrr60")} for w in workouts if w.get("valid")]
    ri = res.get("recovery_index") or {}
    return {"start": res.get("start"), "end": res.get("end"),
            "hrr60": {**{k: v for k, v in hrr.items() if k != "workouts"}, "workouts": len(workouts),
                      "valid": len(valid), "recent_valid": valid[-RECENT_VALID:]},
            "recovery_index": {"median_pct": ri.get("median_pct"), "recent": (ri.get("nights") or [])[-RECENT_NIGHTS:]}}


def _ecg_list(conn, uid: int, limit: int) -> dict:
    tz = ZoneInfo(conn.execute("SELECT timezone FROM users WHERE id = %s", (uid,)).fetchone()["timezone"])
    return {"recordings": rx_store.list_ecg(conn, uid, limit, tz),
            "note": "分类是 Apple 心电图 app 的结果，不是诊断；有症状或房颤迹象请看 GP"}


def _cardio(conn, uid, a):
    res = views.cardio(conn, uid, _d(a["start"]), _d(a["end"]))
    return res if a.get("detail") else slim_cardio(res)


TOOL_SPECS: list[dict] = [
    dict(name="get_cardio",
         description="心肺恢复：训练后 60 秒心率下降 HRR60（结束前最高心率 − 60 秒后心率；结束前不到 75% 最大心率的不算），"
                     "近 30 天均值和前 30 天对比（越大越好）；recovery_index = 夜间最低 5 分钟心率出现在整晚的百分之几（越早越好）。",
         input_schema=_schema({"user": USER, "start": nullable("string", "YYYY-MM-DD，null = 近 90 天"),
                               "end": nullable("string", "YYYY-MM-DD，null = 今天"),
                               "detail": {**DETAIL, "description": "true=每次训练和每晚的明细、无效原因"}}),
         handler=_cardio, scope="scores"),
    dict(name="get_ecg_list",
         description="Apple Watch 心电图：时间、Apple 的分类（窦性心律 / 房颤迹象 / 心率过高或过低 / 记录不佳）、心率、症状。"
                     "没有波形；只转述 Apple 的结果，不诊断，房颤迹象或有症状建议看 GP。",
         input_schema=_schema({"user": USER, "limit": {"type": "integer", "description": "常用 10–50"}}),
         handler=lambda conn, uid, a: _ecg_list(conn, uid, a["limit"]), scope="ecg"),
]

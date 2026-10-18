"""Analytics matching WHOOP: butler tool specs (wrapped into Tool and registered by assistant/tools.py). All read-only."""
from datetime import date

from ..toolspec import OPT_DAY, OPT_END, OPT_START, USER, schema
from . import views


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


_schema = schema
DAY, START, END = OPT_DAY, OPT_START, OPT_END

TOOL_SPECS: list[dict] = [
    dict(name="get_healthspan",
         description="身体年龄（对标 WHOOP Age）：body_age、实际年龄、差值、衰老速度、每周历史；contributors = 8 项指标各让身体年龄 ± 几年"
                     "和建议；insufficient = 数据不够的项。趋势参考，不是诊断。",
         input_schema=_schema({"user": USER, "weeks": {"type": "integer", "description": "历史几周，常用 12–26"}}),
         handler=lambda conn, uid, a: views.healthspan(conn, uid, a["weeks"]), scope="scores"),
    dict(name="get_stress",
         description="压力（对标 WHOOP Stress Monitor）：醒着不动时心率 / HRV 偏离平静基线，0–3（< 1 低、1–2 中、≥ 2 高），"
                     "每天平均、低 / 中 / 高各几小时、最高的小时。",
         input_schema=_schema({"user": USER, "start": START, "end": END,
                               "hours": {"type": "boolean", "description": "附每小时"}}),
         handler=lambda conn, uid, a: views.stress_days(conn, uid, _d(a["start"]), _d(a["end"]), a["hours"]), scope="scores"),
    dict(name="get_strain_coach",
         description="今日负荷目标（对标 WHOOP Strain Coach）：按恢复分和 TSB 给的 strain 目标区间、已完成 strain、进度。",
         input_schema=_schema({"user": USER, "date": DAY}),
         handler=lambda conn, uid, a: views.strain_coach(conn, uid, _d(a["date"])), scope="scores"),
    dict(name="get_sleep_coach",
         description="睡眠教练（对标 WHOOP Sleep Coach）：今晚睡眠需求（基线 + 睡眠债 + 负荷）、几点起、几点睡；有入睡目标时按目标。",
         input_schema=_schema({"user": USER, "date": DAY}),
         handler=lambda conn, uid, a: views.sleep_coach(conn, uid, _d(a["date"])), scope="scores"),
    dict(name="get_health_monitor",
         description="健康监测（对标 WHOOP Health Monitor）：静息心率、HRV、呼吸频率、血氧、手腕温度的个人正常范围（60 天 ± 2 SD）"
                     "和最近一晚是否在范围内。",
         input_schema=_schema({"user": USER, "date": DAY}),
         handler=lambda conn, uid, a: views.health_monitor(conn, uid, _d(a["date"])), scope="scores"),
    dict(name="get_sleep_details",
         description="睡眠细项：7 / 30 天规律性（SRI 0–100%）、每晚入睡 / 起床、效率、潜伏期、夜醒、深睡 / REM。默认最近 14 晚。",
         input_schema=_schema({"user": USER, "start": START, "end": END}),
         handler=lambda conn, uid, a: views.sleep_details(conn, uid, _d(a["start"]), _d(a["end"])), scope="sleep"),
    dict(name="get_hr_zones",
         description="心率区间：每天和每次训练在 Z1–Z5（50/60/70/80/90% HRmax）的分钟数，默认最近 7 天。",
         input_schema=_schema({"user": USER, "start": START, "end": END}),
         handler=lambda conn, uid, a: views.hr_zones(conn, uid, _d(a["start"]), _d(a["end"])), scope="scores"),
    dict(name="get_vo2max",
         description="VO₂max 趋势（每月平均、每年变化）、同龄同性别百分位和等级（FRIEND 2022）。",
         input_schema=_schema({"user": USER, "end": END, "days": {"type": "integer", "description": "常用 365"}}),
         handler=lambda conn, uid, a: views.vo2max(conn, uid, _d(a["end"]), a["days"]), scope="scores"),
    dict(name="get_report",
         description="周报 / 月报数据：恢复、睡眠、负荷、压力、训练、体重的汇总和比上期的变化，近 90 天影响次日恢复的行为。",
         input_schema=_schema({"user": USER, "period": {"type": "string", "enum": ["week", "month"]}, "end": END}),
         handler=lambda conn, uid, a: views.report(conn, uid, a["period"], _d(a["end"])), scope="scores"),
]

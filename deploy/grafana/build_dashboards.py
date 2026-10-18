#!/usr/bin/env python3
"""Generates deploy/grafana/dashboards/*.json (read by Grafana provisioning).

Dashboards are maintained with this script (SQL and layout in one place, easier than hand-editing thousands of lines of JSON); after editing, run
    python3 deploy/grafana/build_dashboards.py
Grafana reloads automatically within 30 seconds. Standard library only.

Conventions:
- Time zone Australia/Sydney, default range last 90 days; variables $user (Body Engine users.id) and $ow_user (the matching OW user, hidden).
- Date columns are always `date::timestamptz AS time` (the grafana_ro session time zone is Australia/Sydney, i.e. local midnight).
- Time range filters by local date: date BETWEEN {F} AND {T}.
- Baseline band matches the scores: mean ± 1 standard deviation of the previous 60 days (excluding the current day).
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "dashboards"

BODY = {"type": "grafana-postgresql-datasource", "uid": "body-engine"}
OW = {"type": "grafana-postgresql-datasource", "uid": "open-wearables"}
SPARKY = {"type": "yesoreyeram-infinity-datasource", "uid": "sparkyfitness"}

F = "($__timeFrom()::timestamptz)::date"
T = "($__timeTo()::timestamptz)::date"
RANGE = f"date BETWEEN {F} AND {T}"

RECOVERY_STEPS = [{"color": "red", "value": None}, {"color": "yellow", "value": 34}, {"color": "green", "value": 67}]
SLEEP_STEPS = [{"color": "red", "value": None}, {"color": "yellow", "value": 70}, {"color": "green", "value": 85}]
GREY = {"mode": "fixed", "fixedColor": "#8e8e8e"}
HOURS = "suffix: 小时"
KG = "suffix: kg"

RULE_NAMES = ("CASE rule_key WHEN 'progression' THEN '加重建议' WHEN 'calorie_adjust' THEN '调热量' "
              "WHEN 'pre_workout_fuel' THEN '练前进食' WHEN 'frequency_caps' THEN '频率上限（红肉）' "
              "WHEN 'exercise_restrictions' THEN '禁用 / 注意动作' WHEN 'readiness' THEN '恢复和睡眠债' "
              "ELSE rule_key END")


# ---------------------------------------------------------------- common
class Layout:
    """Lay out top to bottom: row(height, [(width, panel), ...])."""

    def __init__(self):
        self.y = 0
        self.panels: list = []

    def row(self, h: int, items: list) -> None:
        x = 0
        for w, p in items:
            p["gridPos"] = {"x": x, "y": self.y, "w": w, "h": h}
            p["id"] = len(self.panels) + 1
            self.panels.append(p)
            x += w
        self.y += h

    def header(self, title: str) -> None:
        self.panels.append({"type": "row", "title": title, "collapsed": False, "panels": [],
                            "id": len(self.panels) + 1, "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}})
        self.y += 1


def sql(raw: str, fmt: str = "time_series", ds: dict = BODY, ref: str = "A") -> dict:
    return {"refId": ref, "datasource": ds, "rawQuery": True, "editorMode": "code", "format": fmt,
            "rawSql": " ".join(raw.split())}


def override(name: str, **props) -> dict:
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": k.replace("__", "."), "value": v} for k, v in props.items()]}


def timeseries(title: str, targets: list, unit: str = "short", desc: str = "", draw: str = "line",
               overrides: list | None = None, steps: list | None = None, color: dict | None = None,
               decimals: int | None = None, minv=None, maxv=None, custom: dict | None = None,
               legend: str = "list") -> dict:
    c = {"drawStyle": draw, "lineWidth": 2 if draw == "line" else 1, "fillOpacity": 70 if draw == "bars" else 0,
         "showPoints": "auto" if draw == "line" else "never", "pointSize": 5, "spanNulls": 3 * 86400000,
         "barAlignment": 0, "axisPlacement": "auto", "gradientMode": "scheme" if steps else "none",
         "thresholdsStyle": {"mode": "off"}}
    c.update(custom or {})
    defaults = {"unit": unit, "custom": c,
                "color": color or ({"mode": "thresholds"} if steps else {"mode": "palette-classic"}),
                "thresholds": {"mode": "absolute", "steps": steps or [{"color": "green", "value": None}]}}
    if decimals is not None:
        defaults["decimals"] = decimals
    if minv is not None:
        defaults["min"] = minv
    if maxv is not None:
        defaults["max"] = maxv
    return {"type": "timeseries", "title": title, "description": desc, "targets": targets,
            "datasource": targets[0]["datasource"],
            "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
            "options": {"legend": {"displayMode": legend, "placement": "bottom", "showLegend": True},
                        "tooltip": {"mode": "multi", "sort": "none"}}}


def stat(title: str, target: dict, unit: str = "short", steps: list | None = None, decimals: int | None = None,
         desc: str = "") -> dict:
    d = {"unit": unit, "color": {"mode": "thresholds"},
         "thresholds": {"mode": "absolute", "steps": steps or [{"color": "blue", "value": None}]}}
    if decimals is not None:
        d["decimals"] = decimals
    return {"type": "stat", "title": title, "description": desc, "targets": [target], "datasource": target["datasource"],
            "fieldConfig": {"defaults": d, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "graphMode": "area", "colorMode": "value", "justifyMode": "center", "textMode": "value",
                        "orientation": "auto", "showPercentChange": False}}


def table(title: str, target: dict, desc: str = "", overrides: list | None = None, sort: list | None = None,
          unit: str = "short", order: list | None = None) -> dict:
    tr = [{"id": "organize", "options": {"indexByName": {n: i for i, n in enumerate(order)}}}] if order else []
    return {"type": "table", "transformations": tr, "title": title, "description": desc, "targets": [target], "datasource": target["datasource"],
            "fieldConfig": {"defaults": {"unit": unit, "custom": {"align": "auto", "cellOptions": {"type": "auto"}},
                                         "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]}},
                            "overrides": overrides or []},
            "options": {"showHeader": True, "cellHeight": "sm", "sortBy": sort or [],
                        "footer": {"show": False, "reducer": ["sum"], "fields": ""}}}


def barchart(title: str, target: dict, desc: str = "", horizontal: bool = True, stacking: str = "normal",
             overrides: list | None = None) -> dict:
    return {"type": "barchart", "title": title, "description": desc, "targets": [target], "datasource": target["datasource"],
            "fieldConfig": {"defaults": {"unit": "short", "color": {"mode": "palette-classic"},
                                         "custom": {"fillOpacity": 80, "lineWidth": 1, "gradientMode": "none"}},
                            "overrides": overrides or []},
            "options": {"orientation": "horizontal" if horizontal else "vertical", "stacking": stacking,
                        "showValue": "auto", "barWidth": 0.8, "groupWidth": 0.7, "xTickLabelRotation": 0,
                        "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                        "tooltip": {"mode": "multi", "sort": "none"}}}


def dashboard(uid: str, title: str, desc: str, layout: Layout, ow: bool = False, tags: list | None = None) -> dict:
    variables = [{
        "name": "user", "label": "用户", "type": "query", "datasource": BODY, "refresh": 1, "hide": 0,
        "query": "SELECT id AS __value, display_name AS __text FROM users ORDER BY id",
        "definition": "SELECT id AS __value, display_name AS __text FROM users ORDER BY id",
        "current": {"text": "Alex", "value": "1"}, "includeAll": False, "multi": False, "sort": 0, "options": []}]
    if ow:
        q = "SELECT ow_user_id FROM users WHERE id = $user AND ow_user_id IS NOT NULL"
        variables.append({"name": "ow_user", "label": "OW 用户", "type": "query", "datasource": BODY, "refresh": 1,
                          "hide": 2, "query": q, "definition": q, "includeAll": False, "multi": False, "options": []})
    links = [{"title": "身体管理", "type": "dashboards", "tags": ["身体管理"], "asDropdown": False,
              "includeVars": True, "keepTime": True, "icon": "external link"}]
    return {"uid": uid, "title": title, "description": desc, "tags": ["身体管理", *(tags or [])],
            "timezone": "Australia/Sydney", "weekStart": "monday", "editable": False, "graphTooltip": 1,
            "time": {"from": "now-90d", "to": "now"}, "refresh": "", "schemaVersion": 41, "version": 1,
            "fiscalYearStartMonth": 0, "liveNow": False,
            "timepicker": {"refresh_intervals": ["5m", "15m", "1h"]},
            "templating": {"list": variables}, "annotations": {"list": []}, "links": links,
            "panels": layout.panels}


def baseline_query(src: str, label: str, min_n: int = 14) -> str:
    """src: a subquery returning (date, v) (no time filter needed). Outputs the day value + mean ± 1 SD of the previous 60 days (excluding the current day)."""
    return f"""
WITH s AS (SELECT date, v FROM ({src}) x WHERE v IS NOT NULL AND date BETWEEN {F} - 61 AND {T}),
b AS (SELECT date, v, avg(v) OVER w AS m, stddev_samp(v) OVER w AS sd, count(v) OVER w AS n FROM s
      WINDOW w AS (ORDER BY date RANGE BETWEEN '60 days'::interval PRECEDING AND '1 day'::interval PRECEDING))
SELECT date::timestamptz AS time, v AS "{label}",
  CASE WHEN n >= {min_n} THEN m - sd END AS "基线下沿", CASE WHEN n >= {min_n} THEN m END AS "60 天基线",
  CASE WHEN n >= {min_n} THEN m + sd END AS "基线上沿"
FROM b WHERE date >= {F} ORDER BY 1"""


def band_overrides(value_name: str) -> list:
    return [
        override("基线上沿", **{"custom__fillBelowTo": "基线下沿", "custom__lineWidth": 0, "custom__fillOpacity": 18,
                              "custom__showPoints": "never", "color": GREY, "custom__hideFrom": {"legend": True, "tooltip": False, "viz": False}}),
        override("基线下沿", **{"custom__lineWidth": 0, "custom__fillOpacity": 0, "custom__showPoints": "never", "color": GREY,
                              "custom__hideFrom": {"legend": True, "tooltip": False, "viz": False}}),
        override("60 天基线", **{"displayName": "60 天基线（灰带 ± 1 SD）", "custom__lineStyle": {"fill": "dash", "dash": [6, 4]}, "custom__lineWidth": 1,
                               "custom__showPoints": "never", "color": GREY}),
        override(value_name, **{"custom__showPoints": "always", "custom__lineWidth": 2}),
    ]


def tiers(expr: str, cuts: tuple, names: tuple) -> str:
    """Split a value into three columns by tier (low / mid / high) so each bar in a stacked bar chart has a single colour. cuts = (mid lower bound, high lower bound)."""
    lo, hi = cuts
    return (f'CASE WHEN {expr} < {lo} THEN {expr} END AS "{names[0]}", '
            f'CASE WHEN {expr} >= {lo} AND {expr} < {hi} THEN {expr} END AS "{names[1]}", '
            f'CASE WHEN {expr} >= {hi} THEN {expr} END AS "{names[2]}"')


def tier_colors(names: tuple, colors: tuple = ("red", "yellow", "green")) -> list:
    return [override(n, color={"mode": "fixed", "fixedColor": c}) for n, c in zip(names, colors)]


STACK = {"stacking": {"mode": "normal", "group": "A"}}
REC_T = ("红（< 34）", "黄（34–66）", "绿（≥ 67）")
SLEEP_T = ("< 70", "70–84", "≥ 85")
DEBT_T = ("< 2 小时", "2–4 小时", "≥ 4 小时")


def daily(cols: str, where: str = "", table_: str = "daily_scores") -> str:
    return (f"SELECT date::timestamptz AS time, {cols} FROM {table_} WHERE user_id = $user AND {RANGE} "
            f"{('AND ' + where) if where else ''} ORDER BY 1")


# ---------------------------------------------------------------- 1. Overview
def overview() -> dict:
    L = Layout()
    L.row(4, [
        (4, stat("恢复分", sql(daily('recovery AS "恢复分"')), steps=RECOVERY_STEPS,
                 desc="最近一个有值的日子。绿 ≥ 67，黄 34–66，红 < 34。")),
        (4, stat("睡眠分", sql(daily('sleep_score AS "睡眠分"')), steps=SLEEP_STEPS)),
        (4, stat("负荷 strain", sql(daily('strain AS "strain"')), decimals=1,
                 steps=[{"color": "blue", "value": None}, {"color": "orange", "value": 14}, {"color": "red", "value": 18}],
                 desc="0–21。今天还没过完时是到目前为止的值。")),
        (4, stat("实际睡眠", sql(daily('sleep_actual_min / 60.0 AS "实际睡眠"')), unit=HOURS, decimals=1,
                 steps=[{"color": "red", "value": None}, {"color": "yellow", "value": 6}, {"color": "green", "value": 7}])),
        (4, stat("睡眠债", sql(daily('sleep_debt_min / 60.0 AS "睡眠债"')), unit=HOURS, decimals=1,
                 steps=[{"color": "green", "value": None}, {"color": "yellow", "value": 2}, {"color": "red", "value": 4}])),
        (4, stat("TSB（状态）", sql(daily('tsb AS "TSB"')), decimals=0,
                 steps=[{"color": "red", "value": None}, {"color": "yellow", "value": -30}, {"color": "green", "value": -10}],
                 desc="CTL − ATL：负数 = 最近练得比长期习惯多（疲劳），太低（< −30）注意恢复。")),
    ])
    L.row(8, [
        (12, timeseries("恢复分", [sql(daily(tiers("recovery", (34, 67), REC_T)))], draw="bars", custom=STACK,
                        overrides=tier_colors(REC_T), minv=0, maxv=100, desc="HRV（SDNN）0.6、睡眠期静息心率 0.2、睡眠表现 0.15、呼吸 0.05，和前 60 天基线比。"
                        "没有手表睡眠的天是空的（看「数据质量」）。")),
        (12, timeseries("睡眠分", [sql(daily(tiers("sleep_score", (70, 85), SLEEP_T)))], draw="bars", custom=STACK,
                        overrides=tier_colors(SLEEP_T), minv=0, maxv=100, desc="OW Sleep Score 规格：时长 40%、阶段 20%、规律性 20%、中断 20%。")),
    ])
    L.row(8, [
        (12, timeseries("负荷：strain 和肌肉负荷",
                        [sql(daily('strain AS "strain", muscle_load AS "肌肉负荷（训练容量 kg）"'))], draw="bars",
                        desc="strain：心率 TRIMP 映射到 0–21（K = 170）。肌肉负荷：SparkyFitness 当天力量动作工作组 Σ 次数 × 重量，"
                        "没有训练记录的天留空（没记录 ≠ 没练）。",
                        overrides=[override("肌肉负荷（训练容量 kg）", **{"custom__axisPlacement": "right", "unit": KG,
                                            "custom__drawStyle": "points", "custom__pointSize": 8, "color": {"mode": "fixed", "fixedColor": "purple"}}),
                                   override("strain", **{"color": {"mode": "fixed", "fixedColor": "blue"}, "max": 21, "min": 0})])),
        (12, timeseries("训练负荷趋势：CTL / ATL / TSB", [sql(daily('ctl AS "CTL（42 天，体能）", atl AS "ATL（7 天，疲劳）", tsb AS "TSB（状态）"'))],
                        decimals=0, desc="用每天的 TRIMP 做指数加权：CTL 42 天、ATL 7 天、TSB = CTL − ATL。前 42 天是热身期。",
                        overrides=[override("TSB（状态）", **{"custom__drawStyle": "bars", "custom__fillOpacity": 35,
                                                         "color": {"mode": "fixed", "fixedColor": "orange"}})],
                        custom={"showPoints": "never"})),
    ])
    L.row(8, [
        (12, timeseries("睡眠时长和睡眠需求", [sql(daily('sleep_actual_min / 60.0 AS "实际睡眠", sleep_need_min / 60.0 AS "睡眠需求"'))],
                        unit=HOURS, decimals=1, desc="实际睡眠 = 主睡眠 + 前一个主睡眠之后的小睡。睡眠需求 = 8h + 睡眠债（最多 +2h）+ 前一天负荷加成。",
                        overrides=[override("实际睡眠", **{"custom__drawStyle": "bars", "custom__fillOpacity": 60,
                                                       "color": {"mode": "fixed", "fixedColor": "blue"}}),
                                   override("睡眠需求", **{"custom__lineStyle": {"fill": "dash", "dash": [6, 4]},
                                                       "color": {"mode": "fixed", "fixedColor": "orange"}})])),
        (12, timeseries("睡眠债", [sql(daily(tiers("sleep_debt_min / 60.0", (2, 4), DEBT_T)))], unit=HOURS, decimals=1,
                        draw="bars", custom=STACK, overrides=tier_colors(DEBT_T, ("green", "yellow", "red")),
                        desc="最近 14 晚「8h − 实际睡眠」的加权平均（近的权重大），进入当晚睡眠前的值。≥ 2h 提醒、≥ 4h 警告（规则 readiness）。")),
    ])
    return dashboard("body-overview", "身体管理 · 总览",
                     "恢复 / 睡眠 / 负荷 / 训练负荷趋势 / 睡眠债。数据来自 Body Engine 的 daily_scores（每小时更新）。", L)


# ---------------------------------------------------------------- 2. Physiology
def physiology() -> dict:
    L = Layout()
    rhr = "SELECT date, rhr_night AS v FROM daily_scores WHERE user_id = $user"
    hrv = "SELECT date, hrv_night_ln AS v FROM daily_scores WHERE user_id = $user"
    resp = "SELECT date, resp_rate_night AS v FROM daily_scores WHERE user_id = $user"
    metric = "SELECT date, value AS v FROM daily_metrics WHERE user_id = $user AND metric = '{m}'"
    L.row(9, [
        (12, timeseries("静息心率（睡眠期最低 5 分钟）", [sql(baseline_query(rhr, "静息心率"))], unit="bpm", decimals=0,
                        overrides=band_overrides("静息心率"),
                        desc="恢复分用的当晚值；没有睡眠期心率的天退回 Apple 全天 resting_heart_rate。灰带 = 前 60 天均值 ± 1 SD（≥ 14 晚才画）。"
                        "高于灰带上沿 = 偏高（疲劳 / 生病 / 喝酒 / 晚睡）。")),
        (12, timeseries("HRV（ln SDNN）", [sql(baseline_query(hrv, "ln HRV"))], decimals=2, overrides=band_overrides("ln HRV"),
                        desc="睡眠窗口内 SDNN 读数取 ln 后的中位数（Apple 夜里只测 1–3 次，单条读数的晚上很吵）。"
                        "低于灰带下沿 = 恢复差。ln 4.0 ≈ 55 ms，3.5 ≈ 33 ms。")),
    ])
    L.row(9, [
        (12, timeseries("呼吸频率（睡眠期）", [sql(baseline_query(resp, "呼吸频率"))], unit="次/分", decimals=1,
                        overrides=band_overrides("呼吸频率"), desc="当晚中位数。明显高于基线常见于生病、喝酒。")),
        (12, timeseries("血氧", [sql(baseline_query(metric.format(m="oxygen_saturation"), "血氧", min_n=5))], unit="percent",
                        decimals=1, overrides=band_overrides("血氧"),
                        desc="全天平均（daily_metrics.oxygen_saturation）。评分只做标记：当晚中位数 < 94% 或 < 90% 的读数 ≥ 2 条 → spo2_low。")),
    ])
    L.row(9, [
        (12, timeseries("手腕温度（睡眠期）", [sql(baseline_query(metric.format(m="apple_sleeping_wrist_temperature"), "手腕温度", min_n=5))],
                        unit="celsius", decimals=2, overrides=band_overrides("手腕温度"),
                        desc="Apple 只在部分晚上记录（一年约 20 晚）。评分只做标记：比 60 天中位数偏 ≥ 0.6°C。")),
        (12, timeseries("最大摄氧量 VO₂max", [sql(baseline_query(metric.format(m="vo2_max"), "VO₂max", min_n=3))],
                        unit="mL/min·kg", decimals=1, overrides=band_overrides("VO₂max"),
                        desc="Apple 在户外走路 / 跑步后估算，几周才一次。长期往上走 = 有氧能力在进步。")),
    ])
    return dashboard("body-physiology", "身体管理 · 生理指标",
                     "静息心率、HRV、呼吸、血氧、手腕温度、VO₂max，带前 60 天基线带（均值 ± 1 SD，和恢复分用的基线一致）。", L)


# ---------------------------------------------------------------- 3. Weight and nutrition
WEIGHT_JSONATA = (
    '( $r := $[weight != null].{"d": entry_date, "ms": $toMillis(entry_date & "T00:00:00+10:00"), "w": weight};'
    ' $r.( $m := ms; $win := $r[ms > $m - 6*86400000 and ms <= $m];'
    ' {"time": d & "T00:00:00+10:00", "weight": w, "avg7": $round($average($win.w), 2), "n7": $count($win)} ) )')


def weight_target(fmt: str = "timeseries") -> dict:
    return {"refId": "A", "datasource": SPARKY, "type": "json", "source": "url", "format": fmt, "parser": "backend",
            "url": "http://host.docker.internal:8300/api/measurements/check-in-measurements-range/2020-01-01/${__to:date:YYYY-MM-DD}",
            "url_options": {"method": "GET", "data": ""}, "root_selector": WEIGHT_JSONATA,
            "columns": [{"selector": "time", "text": "time", "type": "timestamp"},
                        {"selector": "weight", "text": "体重", "type": "number"},
                        {"selector": "avg7", "text": "7 天均值", "type": "number"},
                        {"selector": "n7", "text": "7 天内称重次数", "type": "number"}]}


def weight_activity() -> dict:
    L = Layout()
    wt = weight_target("table")
    L.row(9, [
        (16, timeseries("体重（SparkyFitness）", [wt], unit=KG, decimals=1,
                        desc="来自 SparkyFitness 的称重记录（你手动记 / app 同步的）。7 天均值 = 含当天往前 7 天内所有称重的平均（规则 calorie_adjust "
                        "要求每个 7 天窗口 ≥ 2 次称重；只有 1 次时均值就等于那次）。目标：3 个月内到 80kg。",
                        overrides=[override("体重", **{"custom__drawStyle": "points", "custom__pointSize": 9,
                                                     "color": {"mode": "fixed", "fixedColor": "blue"}}),
                                   override("7 天均值", **{"custom__lineWidth": 2, "custom__showPoints": "never",
                                                        "custom__spanNulls": True, "color": {"mode": "fixed", "fixedColor": "orange"}}),
                                   override("7 天内称重次数", **{"custom__hideFrom": {"legend": True, "tooltip": False, "viz": True}})],
                        custom={"spanNulls": True},
                        steps=None)),
        (8, table("称重记录", weight_target("table"), sort=[{"displayName": "日期", "desc": True}],
                  order=["time", "体重", "7 天均值", "7 天内称重次数"],
                  overrides=[override("time", **{"displayName": "日期", "unit": "time:YYYY-MM-DD"}),
                             override("体重", **{"unit": KG, "decimals": 1}),
                             override("7 天均值", **{"unit": KG, "decimals": 2})])),
    ])

    def rolling(metric: str, label: str) -> str:
        return f"""
WITH s AS (SELECT date, value FROM daily_metrics WHERE user_id = $user AND metric = '{metric}'
           AND date BETWEEN {F} - 6 AND {T}),
b AS (SELECT date, value, avg(value) OVER (ORDER BY date RANGE BETWEEN '6 days'::interval PRECEDING AND CURRENT ROW) AS m FROM s)
SELECT date::timestamptz AS time, value AS "{label}", m AS "7 天均值" FROM b WHERE date >= {F} ORDER BY 1"""

    avg_line = override("7 天均值", **{"custom__drawStyle": "line", "custom__lineWidth": 2, "custom__fillOpacity": 0,
                                    "color": {"mode": "fixed", "fixedColor": "orange"}})
    L.row(8, [
        (12, timeseries("每日步数", [sql(rolling("step_count", "步数"))], draw="bars", decimals=0,
                        overrides=[avg_line, override("步数", **{"color": {"mode": "fixed", "fixedColor": "green"}})],
                        desc="OW 导入器按 HealthKit 规则去重（手表 > 手机），比快捷指令直接相加的数少。今天还没过完。")),
        (12, timeseries("活动能量", [sql(rolling("active_energy_burned", "活动能量"))], draw="bars", unit="kcal", decimals=0,
                        overrides=[avg_line, override("活动能量", **{"color": {"mode": "fixed", "fixedColor": "red"}})],
                        desc="Apple 的 active energy（不含基础代谢），kcal。")),
    ])
    return dashboard("body-weight", "身体管理 · 体重和活动",
                     "体重（SparkyFitness）和 7 天均值、每日步数、活动能量。饮食明细在 SparkyFitness 里看。", L)


# ---------------------------------------------------------------- 4. Journal and reminders
def journal_reminders() -> dict:
    L = Layout()
    L.row(10, [(24, {
        "type": "state-timeline", "title": "日记：每天做了什么（只显示记录过的天）",
        "description": "日期 = 行为发生的那天。没记录 = 缺失（空白），不等于「否」。数量型日记项（红肉 g、喝水 L）不在这里。",
        "datasource": BODY,
        # Add an empty value the next day after each record so the colour block covers only that day (a state timeline otherwise extends until the next record)
        "targets": [sql(f"""WITH e AS (
              SELECT e.date, i.label_zh, i.sort, e.value
              FROM journal_entries e JOIN journal_items i ON i.user_id = e.user_id AND i.key = e.key
              WHERE e.user_id = $user AND i.kind = 'bool' AND e.{RANGE}),
            x AS (SELECT date, label_zh, sort, value FROM e
                  UNION ALL SELECT date + 1, label_zh, sort, NULL FROM e)
            SELECT date::timestamptz AS time, label_zh AS metric, max(value) AS value
            FROM x GROUP BY date, label_zh, sort ORDER BY 1, sort""")],
        "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "custom": {"fillOpacity": 80, "lineWidth": 0, "spanNulls": False},
                                     "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
                                     "mappings": [{"type": "value", "options": {
                                         "1": {"text": "是", "color": "green", "index": 0},
                                         "0": {"text": "否", "color": "#8e8e8e", "index": 1}}}]},
                        "overrides": []},
        "options": {"mergeValues": False, "showValue": "never", "alignValue": "center", "rowHeight": 0.8,
                    "legend": {"showLegend": False, "displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "single", "sort": "none"}}})])
    L.row(6, [(24, timeseries("次日恢复分（画在行为当天）",
                              [sql(f"""SELECT (date - 1)::timestamptz AS time, {tiers("recovery", (34, 67), REC_T)} FROM daily_scores
                                       WHERE user_id = $user AND date - 1 BETWEEN {F} AND {T} ORDER BY 1""")],
                              draw="bars", custom=STACK, overrides=tier_colors(REC_T), minv=0, maxv=100,
                              desc="D 天的柱子是 D+1 的恢复分，和上面同一列的行为对照着看。"))])
    L.row(9, [(24, table("日记行为 → 次日恢复分（所选时间范围）", sql(f"""
WITH j AS (
  SELECT i.label_zh, i.sort, e.date, e.value
  FROM journal_entries e JOIN journal_items i ON i.user_id = e.user_id AND i.key = e.key
  WHERE e.user_id = $user AND i.kind = 'bool' AND e.{RANGE}),
r AS (SELECT date - 1 AS date, recovery FROM daily_scores WHERE user_id = $user AND recovery IS NOT NULL),
allr AS (SELECT round(avg(recovery)) AS m FROM r WHERE {RANGE})
SELECT j.label_zh AS "行为",
  count(r.recovery) FILTER (WHERE j.value = 1) AS "「是」天数",
  round(avg(r.recovery) FILTER (WHERE j.value = 1)) AS "「是」→ 次日恢复",
  count(r.recovery) FILTER (WHERE j.value = 0) AS "「否」天数",
  round(avg(r.recovery) FILTER (WHERE j.value = 0)) AS "「否」→ 次日恢复",
  round(avg(r.recovery) FILTER (WHERE j.value = 1)) - round(avg(r.recovery) FILTER (WHERE j.value = 0)) AS "差（是 − 否）",
  (SELECT m FROM allr) AS "全部天次日恢复（参考）",
  CASE WHEN count(r.recovery) FILTER (WHERE j.value = 1) < 5 OR count(r.recovery) FILTER (WHERE j.value = 0) < 5
       THEN '样本不足（是 / 否各要 ≥ 5 天）'
       WHEN count(r.recovery) FILTER (WHERE j.value = 1) < 15 OR count(r.recovery) FILTER (WHERE j.value = 0) < 15
       THEN '仅供参考（各 ≥ 15 天才可信）' ELSE '可信' END AS "说明"
FROM j LEFT JOIN r ON r.date = j.date
GROUP BY j.label_zh, j.sort ORDER BY j.sort""", fmt="table"),
        desc="只比较明确记了「是」和「否」的天（没记录不算「否」）。只是两组均值的差，没控制睡眠、负荷等其他因素；"
             "带统计检验的版本问 AI 助手（analyze_journal）。"))])
    L.row(8, [
        (12, barchart("提醒次数（按规则，所选时间范围）", sql(f"""
SELECT {RULE_NAMES} AS "规则",
  NULLIF(count(*) FILTER (WHERE level = 'info'), 0) AS "提示",
  NULLIF(count(*) FILTER (WHERE level = 'warning'), 0) AS "警告",
  NULLIF(count(*) FILTER (WHERE level = 'alert'), 0) AS "严重"
FROM reminders WHERE user_id = $user AND {RANGE} GROUP BY 1 ORDER BY 1""", fmt="table"),
                      desc="每天的评估会重新列出当天仍成立的提醒，所以同一件事连续几天都会计数（= 提醒天次）。",
                      overrides=[override("提示", color={"mode": "fixed", "fixedColor": "blue"}),
                                 override("警告", color={"mode": "fixed", "fixedColor": "orange"}),
                                 override("严重", color={"mode": "fixed", "fixedColor": "red"})])),
        (12, barchart("规则评估结果（按规则，所选时间范围）", sql(f"""
SELECT {RULE_NAMES.replace('rule_key', "e->>'rule_key'")} AS "规则",
  NULLIF(count(*) FILTER (WHERE e->>'status' = 'triggered'), 0) AS "触发",
  NULLIF(count(*) FILTER (WHERE e->>'status' = 'not_triggered'), 0) AS "没触发",
  NULLIF(count(*) FILTER (WHERE e->>'status' = 'insufficient_data'), 0) AS "数据不足",
  NULLIF(count(*) FILTER (WHERE e->>'status' = 'error'), 0) AS "出错"
FROM rule_runs, jsonb_array_elements(results) e WHERE user_id = $user AND {RANGE} GROUP BY 1 ORDER BY 1""", fmt="table"),
                      desc="每条规则每天一次评估。「数据不足」多 = 该多记录（例如调热量要每周至少称 2 次体重）。",
                      overrides=[override("触发", color={"mode": "fixed", "fixedColor": "orange"}),
                                 override("没触发", color={"mode": "fixed", "fixedColor": "green"}),
                                 override("数据不足", color={"mode": "fixed", "fixedColor": "#8e8e8e"}),
                                 override("出错", color={"mode": "fixed", "fixedColor": "red"})])),
    ])
    L.row(9, [(24, table("最近的提醒", sql(f"""
SELECT date::text AS "日期", {RULE_NAMES} AS "规则",
  CASE level WHEN 'info' THEN '提示' WHEN 'warning' THEN '警告' WHEN 'alert' THEN '严重' END AS "级别",
  title AS "内容", CASE status WHEN 'new' THEN '新' WHEN 'seen' THEN '已看' WHEN 'dismissed' THEN '不再提醒' END AS "状态"
FROM reminders WHERE user_id = $user AND {RANGE}
ORDER BY date DESC, CASE level WHEN 'alert' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, rule_key LIMIT 200""", fmt="table"),
        overrides=[override("日期", custom__width=110), override("规则", custom__width=150),
                   override("级别", **{"custom__width": 70, "custom__cellOptions": {"type": "color-text"},
                                     "mappings": [{"type": "value", "options": {
                                         "提示": {"color": "blue", "index": 0}, "警告": {"color": "orange", "index": 1},
                                         "严重": {"color": "red", "index": 2}}}]}),
                   override("状态", custom__width=90)]))])
    return dashboard("body-journal", "身体管理 · 日记和提醒",
                     "日记行为和次日恢复分的对照；规则提醒按规则统计。", L)


# ---------------------------------------------------------------- 5. Data quality
def data_quality() -> dict:
    L = Layout()
    src_colors = [override("ow", color={"mode": "fixed", "fixedColor": "green"}),
                  override("apple_export", color={"mode": "fixed", "fixedColor": "blue"}),
                  override("shortcut", color={"mode": "fixed", "fixedColor": "orange"})]
    stacked = STACK
    L.row(8, [
        (12, timeseries("每日指标条数（按来源）", [sql(f"""SELECT date::timestamptz AS time, source AS metric, count(*) AS value
            FROM daily_metrics WHERE user_id = $user AND {RANGE} GROUP BY 1, 2 ORDER BY 1""")],
                        draw="bars", custom=stacked, overrides=src_colors, decimals=0,
                        desc="daily_metrics 每天有多少个指标、最后用的是哪个来源（优先级 ow > apple_export > shortcut，高的覆盖低的）。"
                        "OW 的 iPhone app 稳定同步后应该全是 ow。")),
        (12, timeseries("睡眠段数（按来源，归到醒来那天）", [sql(f"""SELECT wake_date::timestamptz AS time,
            source || CASE WHEN is_main THEN '（主睡眠）' ELSE '（小睡 / 其他）' END AS metric, count(*) AS value
            FROM sleep_sessions WHERE user_id = $user AND wake_date BETWEEN {F} AND {T} GROUP BY 1, 2 ORDER BY 1""")],
                        draw="bars", custom=stacked, decimals=0,
                        desc="没有主睡眠的天 → 睡眠分、恢复分都空。")),
    ])
    ow_src = ("CASE WHEN ds.source = 'apple_health_xml' THEN 'export.xml' "
              "WHEN ds.device_type = 'watch' THEN 'OW app · 手表' WHEN ds.device_type = 'phone' THEN 'OW app · 手机' "
              "ELSE 'OW app · 其他' END")
    ow_range = ("p.recorded_at >= $__timeFrom()::timestamptz - interval '1 day' AND p.recorded_at < $__timeTo()::timestamptz")
    L.row(8, [
        (12, timeseries("OW 原始样本数（按来源）", [sql(f"""SELECT (p.recorded_at AT TIME ZONE 'Australia/Sydney')::date::timestamptz AS time,
            {ow_src} AS metric, count(*) AS value
            FROM data_point_series p JOIN data_source ds ON ds.id = p.data_source_id
            WHERE ds.user_id = '$ow_user' AND {ow_range} GROUP BY 1, 2 ORDER BY 1""", ds=OW)],
                        draw="bars", custom=stacked, decimals=0,
                        overrides=[override("export.xml", color={"mode": "fixed", "fixedColor": "blue"}),
                                   override("OW app · 手表", color={"mode": "fixed", "fixedColor": "green"}),
                                   override("OW app · 手机", color={"mode": "fixed", "fixedColor": "yellow"})],
                        desc="Open Wearables 库里每天的原始样本（心率、HRV、步数……）条数。export.xml 是 2026-10-14 导入的全量历史，"
                        "之后靠 iPhone 上的 OW app 同步。")),
        (12, timeseries("有心率的小时数", [sql(f"""WITH h AS (
            SELECT (p.recorded_at AT TIME ZONE 'Australia/Sydney')::date AS d, count(DISTINCT date_trunc('hour', p.recorded_at)) AS n
            FROM data_point_series p JOIN data_source ds ON ds.id = p.data_source_id
            JOIN series_type_definition t ON t.id = p.series_type_definition_id
            WHERE ds.user_id = '$ow_user' AND t.code = 'heart_rate' AND {ow_range} GROUP BY 1)
            SELECT d::timestamptz AS time, CASE WHEN n < 12 THEN n END AS "不够（< 12 小时）",
              CASE WHEN n >= 12 THEN n END AS "够（≥ 12 小时）" FROM h ORDER BY 1""", ds=OW)],
                        draw="bars", decimals=0, minv=0, maxv=24, custom=STACK,
                        overrides=tier_colors(("不够（< 12 小时）", "够（≥ 12 小时）"), ("red", "green")),
                        desc="strain 要一天里 ≥ 12 个小时有心率（否则 no_raw_hr / hr_coverage_low）。红 = 不够。")),
    ])
    L.row(9, [
        (12, barchart("评分 flags（所选时间范围的天数）", sql(f"""SELECT f AS "flag", count(*) AS "天数"
            FROM daily_scores, jsonb_object_keys(flags) f WHERE user_id = $user AND {RANGE} GROUP BY 1 ORDER BY 2 DESC""", fmt="table"),
                      stacking="none", desc="每个 flag 的中文说明见右边的表；no_sleep / no_raw_hr 多 = 手表没戴或没同步。")),
        (12, table("每天的 flags", sql(f"""SELECT date::text AS "日期", string_agg(f.key || '：' || f.value, '；' ORDER BY f.key) AS "flags"
            FROM daily_scores, jsonb_each_text(flags) f WHERE user_id = $user AND {RANGE} GROUP BY date ORDER BY date DESC""", fmt="table"),
                   overrides=[override("日期", custom__width=110)])),
    ])
    L.row(7, [
        (12, table("Body Engine：最近同步 / 计算", sql("""
SELECT * FROM (
  SELECT CASE source WHEN 'ow' THEN '从 OW 导入（import-ow）' WHEN 'shortcut' THEN '快捷指令导入'
         WHEN 'apple_export' THEN '全量导出导入' ELSE source END AS "任务",
         last_synced_at AS "时间", last_synced_at AS "多久前", cursor AS "游标 / 数据到"
  FROM sync_state WHERE user_id = $user
  UNION ALL SELECT '评分计算（compute-scores）', max(computed_at), max(computed_at), max(date)::text FROM daily_scores WHERE user_id = $user
  UNION ALL SELECT '规则评估（evaluate-rules）', max(ran_at), max(ran_at), max(date)::text FROM rule_runs WHERE user_id = $user
) x ORDER BY "时间" DESC""", fmt="table"),
                   overrides=[override("时间", unit="dateTimeAsLocalNoDateIfToday"),
                              override("多久前", **{"unit": "dateTimeFromNow", "custom__cellOptions": {"type": "color-text"},
                                                   "color": {"mode": "thresholds"}})],
                   desc="每小时 :10 的定时任务会更新这几行。超过 2 小时没动 → 看 deploy/ops/doctor.sh。")),
        (12, table("Open Wearables：最近收到的数据", sql(f"""
SELECT {ow_src} AS "来源", max(p.created_at) AS "最后写入", max(p.created_at) AS "多久前", max(p.recorded_at) AS "最新样本时间"
FROM data_point_series p JOIN data_source ds ON ds.id = p.data_source_id
WHERE ds.user_id = '$ow_user' AND p.recorded_at > now() - interval '30 days'
GROUP BY 1 ORDER BY 2 DESC""", ds=OW, fmt="table"),
                   overrides=[override("最后写入", unit="dateTimeAsLocalNoDateIfToday"),
                              override("多久前", unit="dateTimeFromNow"),
                              override("最新样本时间", unit="dateTimeAsLocalNoDateIfToday")],
                   desc="最近 30 天的样本里，各来源最后一次写进 OW 的时间。OW app 只在手机解锁、app 在后台被唤醒时同步。")),
    ])
    return dashboard("body-data-quality", "身体管理 · 数据质量",
                     "各来源每天的数据量、心率覆盖、评分 flags、最近同步时间。", L, ow=True)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, build in [("01-overview", overview), ("02-physiology", physiology), ("03-weight", weight_activity),
                        ("04-journal", journal_reminders), ("05-data-quality", data_quality)]:
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(build(), ensure_ascii=False, indent=1) + "\n")
        print(f"写入 {path.relative_to(OUT.parent)}")


if __name__ == "__main__":
    main()

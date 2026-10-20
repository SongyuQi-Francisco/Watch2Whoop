"""AI assistant tools for health records. Only tool specs (dicts) are given; assistant/tools.py wraps them into registered Tools (avoiding circular imports).
Format follows Claude API strict tools: all properties are in required, and optional ones use null for "not provided / unchanged".
Write tools (writes=True) have no user parameter and can only write for oneself."""
from datetime import date, timedelta

from ..toolspec import DATE, DETAIL, OPT_END, USER, nullable, schema
from . import analysis, lab_catalog, lab_history, store


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


_schema = schema
_nullable = nullable


def _changes(args: dict, *skip: str) -> dict:
    """null = unchanged; date strings are converted to date."""
    out = {}
    for k, v in args.items():
        if k in skip or v is None:
            continue
        out[k] = _d(v) if k in ("started_on", "stopped_on", "resolved_on", "collected_on") else v
    return out


def _require(row, what: str):
    if row is None:
        raise ValueError(f"没有这条{what}（或不是你的）")
    return row


SUPP_STATUS = ["active", "as_needed", "paused", "stopped", "not_needed"]
SUPP_STATUS_DESC = "active 在吃 / as_needed 按需 / paused 暂停 / stopped 已停 / not_needed 不需要"


def _deleted_journal(ok: bool, a: dict) -> dict:
    if not ok:
        raise ValueError(f"{a['date']} 没有记 {a['key']}")
    return {"deleted": {"date": a["date"], "key": a["key"]}}


def _journal(conn, uid, a):
    end = _d(a["end"]) or date.today()
    start = _d(a["start"]) or end - timedelta(days=29)
    return store.journal_entries(conn, uid, start, end, a["keys"] or None)


OUTCOME_NAMES = "next_recovery 次日恢复分 / next_rhr 次日静息心率 / next_ln_hrv 次日 ln HRV"


def summarize_analysis(results: list[dict]) -> dict:
    """Only the comparisons that have a result; items without enough days become one short line each."""
    ok, short, meta = [], [], {}
    for r in results:
        meta = meta or {"window": f"{r['start']}–{r['end']}", "method": r["method"], "outcomes": OUTCOME_NAMES}
        outs = {k: {"yes": o["yes"]["mean"], "no": o["no"]["mean"], "diff": o["diff_yes_minus_no"],
                    "ci95": o["ci95_approx"], "direction": o["direction"], "reliable": o["reliable"]}
                for k, o in r["outcomes"].items() if o.get("status") == "ok"}
        label = f"{r['item']['key']}（{r['item']['label_zh']}）"
        if outs:
            ok.append({"item": label, "days_yes": r["days_yes"], "days_no": r["days_no"], "outcomes": outs})
        else:
            short.append(f"{label} 是 {r['days_yes']} / 否 {r['days_no']} 天")
    return {**meta, "results": ok, "insufficient": short}


def _lab_history(conn, uid, a):
    key = lab_catalog.resolve_key(a["test"], None, set()) or a["test"]
    h = lab_history.history(conn, uid, key)
    if h is None:
        raise ValueError(f"没有「{a['test']}」的化验记录（用 get_lab_results 看有哪些项目）")
    return lab_history.compact(h, a.get("limit"))


def _analyze(conn, uid, a):
    days = a["days"] or 90
    res = (analysis.analyze(conn, uid, a["key"], _d(a["end"]), days) if a["key"]
           else analysis.analyze_all(conn, uid, _d(a["end"]), days))
    if a.get("detail"):
        return res
    return summarize_analysis([res] if a["key"] else res)


def _exercise_rules(conn, uid, a):
    rules = store.exercise_rules(conn, uid)
    if a.get("detail"):
        return rules
    keep = lambda r: {"ref": r["exercise_ref"], "name": r["name_zh"], "reason": r["reason"], "cues": r["cues"]}
    return {"banned": [keep(r) for r in rules["banned"]], "caution": [keep(r) for r in rules["caution"]],
            "active": [r["name_zh"] or r["exercise_ref"] for r in rules["active"]]}


TOOL_SPECS: list[dict] = [
    # ---------- Supplements ----------
    dict(
        name="list_supplements",
        description="补剂清单：名称、剂量、时间、成分、状态、开始 / 停止日期、为什么吃、当前建议、对应日记项。",
        input_schema=_schema({"user": USER, "statuses": {"type": "array", "items": {"type": "string", "enum": SUPP_STATUS},
                                                         "description": "空 = 全部"}}),
        handler=lambda conn, uid, a: store.list_supplements(conn, uid, a["statuses"] or None),
        scope="supplements",
    ),
    dict(
        name="add_supplement",
        description="新加一种补剂。journal_key 填了会自动建对应的日记项（例 supp_magnesium）。",
        input_schema=_schema({
            "name": {"type": "string", "description": "名称（带品牌）"},
            "status": {"type": "string", "enum": SUPP_STATUS, "description": SUPP_STATUS_DESC},
            "dose": _nullable("string", "每天剂量，例 5g"),
            "timing": _nullable("string", "例 每天随餐"),
            "ingredients": _nullable("string"),
            "started_on": _nullable("string", "YYYY-MM-DD"),
            "rationale": _nullable("string", "为什么吃"),
            "advice": _nullable("string", "当前建议"),
            "journal_key": _nullable("string", "小写字母下划线"),
        }),
        handler=lambda conn, uid, a: store.add_supplement(conn, uid, _changes(a)),
        scope="supplements", writes=True,
    ),
    dict(
        name="update_supplement",
        description="修改一种补剂（id 从 list_supplements）。null = 不改。不再吃：status=stopped + stopped_on。",
        input_schema=_schema({
            "supplement_id": {"type": "integer"},
            "name": _nullable("string"),
            "status": {"type": ["string", "null"], "enum": [*SUPP_STATUS, None]},
            "dose": _nullable("string"),
            "timing": _nullable("string"),
            "ingredients": _nullable("string", "规格"),
            "started_on": _nullable("string", "YYYY-MM-DD"),
            "stopped_on": _nullable("string", "YYYY-MM-DD"),
            "advice": _nullable("string", "整段替换"),
        }),
        handler=lambda conn, uid, a: _require(store.update_supplement(conn, uid, a["supplement_id"],
                                                                      _changes(a, "supplement_id")), "补剂"),
        scope="supplements", writes=True,
    ),
    dict(
        name="delete_supplement",
        description="删除录错 / 重复的补剂及它的日记项，不可恢复。不再吃用 update_supplement status=stopped。",
        input_schema=_schema({"supplement_id": {"type": "integer"}}),
        handler=lambda conn, uid, a: _require(store.delete_supplement(conn, uid, a["supplement_id"]), "补剂"),
        scope="supplements", writes=True,
    ),
    # ---------- Lab results ----------
    dict(
        name="get_lab_results",
        description="化验 / 体检：每项最近一次的值、单位、参考范围、status（high / low / normal / abnormal / unknown）、"
                    "历次结果和变化。异常在前；参考范围来源在 note。",
        input_schema=_schema({"user": USER, "tests": {"type": "array", "items": {"type": "string"},
                                                      "description": "test_key（uric_acid、alt…）或中文名；空 = 全部"}}),
        handler=lambda conn, uid, a: store.lab_report(conn, uid, a["tests"] or None),
        scope="labs",
    ),
    dict(
        name="add_lab_results",
        description="把一张化验单的各项写进化验记录。按名称对上 test_key（uric_acid、triglycerides、alt、ast、ggt、ldl、hdl、"
                    "glucose_fasting、hba1c、creatinine、egfr、crp…）；非标准单位自动换算（参考范围一起）；离谱数值会被拒；"
                    "flag 不填按参考范围算；同天同项已有不同结果不覆盖（overwrite=true 才改）。",
        input_schema=_schema({
            "collected_on": {**DATE, "description": "采样日期（不是出报告日期）"},
            "lab": _nullable("string", "化验机构"),
            "results": {"type": "array", "items": _schema({
                "test": {"type": "string", "description": "化验单上的项目名原文"},
                "test_key": _nullable("string", "不知道就 null；新项目给英文小写键"),
                "value": _nullable("number", "定性结果填 null，写 value_text"),
                "value_text": _nullable("string", "非数值结果或原文，例 阴性、<0.5"),
                "unit": _nullable("string", "单位原文"),
                "ref_low": _nullable("number"),
                "ref_high": _nullable("number"),
                "flag": {"type": ["string", "null"], "enum": ["H", "L", "abnormal", "normal", None]},
                "note": _nullable("string"),
            })},
            "overwrite": {"type": "boolean"},
            "source": {"type": "string", "enum": ["lab", "home_device"],
                       "description": "lab 化验单 / home_device 家用仪器（尿酸仪等，误差大，lab 填仪器名）"},
            "fasting": _nullable("boolean", "空腹 true / 饭后 false / 不知道 null；家测尿酸要问清"),
        }),
        handler=lambda conn, uid, a: lab_catalog.add_results(conn, uid, date.fromisoformat(a["collected_on"]), a["lab"],
                                                             a["results"], a["overwrite"],
                                                             a.get("source") or "lab", a.get("fasting")),
        scope="labs", writes=True,
    ),
    dict(
        name="get_lab_history",
        description="某一项化验的历次结果（旧 → 新）：日期、数值、来源（lab 化验所 / home_device 家用仪器）、是否空腹、"
                    "和上一次比的变化（delta、pct %），以及参考范围 ref 和哪个方向算好（better：down 越低越好 / up 越高越好）。"
                    "看趋势用它；备注和其他项目用 get_lab_results。",
        input_schema=_schema({"user": USER, "test": {"type": "string", "description": "test_key（如 uric_acid）或中文名"},
                              "limit": _nullable("integer", "只要最近几次；null = 全部")}),
        handler=_lab_history,
        scope="labs",
    ),
    dict(
        name="delete_lab_results",
        description="删除某采样日期的化验结果（按 test_key，任何来源）。",
        input_schema=_schema({
            "collected_on": DATE,
            "test_keys": {"type": "array", "items": {"type": "string"}},
        }),
        handler=lambda conn, uid, a: lab_catalog.delete_results(conn, uid, date.fromisoformat(a["collected_on"]),
                                                                a["test_keys"]),
        scope="labs", writes=True,
    ),
    # ---------- Journal ----------
    dict(
        name="list_journal_items",
        description="日记项（每天记的行为：咖啡、喝酒、桑拿、晚饭太晚、各补剂…）：key、中文名、kind（bool / number）、单位。"
                    "记日记前先确认 key。",
        input_schema=_schema({"user": USER}),
        handler=lambda conn, uid, a: store.list_journal_items(conn, uid, active_only=True),
        scope="journal",
    ),
    dict(
        name="log_journal",
        description="记某天某项日记（日期 = 行为发生那天），已记过会覆盖。bool 项 1 / 0，number 项填数值。"
                    "没记 ≠ 否：用户明确说没做才记 0。",
        input_schema=_schema({
            "date": DATE,
            "key": {"type": "string"},
            "value": {"type": "number"},
            "note": _nullable("string"),
        }),
        handler=lambda conn, uid, a: store.log_journal(conn, uid, date.fromisoformat(a["date"]), a["key"],
                                                       a["value"], a["note"], source="assistant"),
        scope="journal", writes=True,
    ),
    dict(
        name="delete_journal_entry",
        description="撤销某天某项日记（变回「没记录」，不是记成「否」）。",
        input_schema=_schema({"date": DATE, "key": {"type": "string"}}),
        handler=lambda conn, uid, a: _deleted_journal(store.delete_journal_entry(conn, uid, date.fromisoformat(a["date"]),
                                                                                a["key"]), a),
        scope="journal", writes=True,
    ),
    dict(
        name="get_journal",
        description="日记记录（没出现的日子 = 没记录，不是「否」）。",
        input_schema=_schema({
            "user": USER,
            "start": _nullable("string", "YYYY-MM-DD，null = end 前 30 天"),
            "end": OPT_END,
            "keys": {"type": "array", "items": {"type": "string"}, "description": "空 = 全部"},
        }),
        handler=_journal,
        scope="journal",
    ),
    dict(
        name="analyze_journal",
        description="日记分析：某个行为做 vs 没做，第二天恢复分、静息心率、ln HRV 的均值差。是 / 否各 ≥ 5 天才出结果，"
                    "≥ 15 天 reliable；没控制混杂，只当线索。默认只返回有结果的项。",
        input_schema=_schema({
            "user": USER,
            "key": _nullable("string", "null = 全部日记项"),
            "end": OPT_END,
            "days": _nullable("integer", "窗口天数，null = 90"),
            "detail": DETAIL,
        }),
        handler=_analyze,
        scope="journal",
    ),
    # ---------- Body events (list_events / add_event are in assistant/tools.py) ----------
    dict(
        name="update_event",
        description="修改一条身体事件（id 从 list_events），例如补好转日期 resolved_on。null = 不改。",
        input_schema=_schema({
            "event_id": {"type": "integer"},
            "title": _nullable("string"),
            "occurred_at": _nullable("string", "ISO 8601"),
            "time_precision": {"type": ["string", "null"], "enum": ["minute", "day", "approx", "month", None]},
            "category": _nullable("string", "injury / pain / illness / symptom / medical / posture / other"),
            "body_part": _nullable("string"),
            "severity": _nullable("integer", "1–5"),
            "note": _nullable("string", "整段替换"),
            "resolved_on": _nullable("string", "YYYY-MM-DD"),
        }),
        handler=lambda conn, uid, a: _require(store.update_event(conn, uid, a["event_id"], _changes(a, "event_id")), "事件"),
        scope="events", writes=True,
    ),
    # ---------- Exercises ----------
    dict(
        name="get_exercise_rules",
        description="动作限制：banned（永久不做）、caution（要小心，附原因和做法）、active（正常动作名）。"
                    "给训练建议前先查，不推荐 banned。",
        input_schema=_schema({"user": USER, "detail": {**DETAIL, "description": "true=带别名、英文名、app 动作 ID、正常动作要点"}}),
        handler=_exercise_rules,
        scope="exercises",
    ),
    dict(
        name="set_exercise_rule",
        description="新建或修改一个动作的补充信息（状态、要点、原因）。null = 不改。",
        input_schema=_schema({
            "exercise_ref": {"type": "string", "description": "英文 slug，例 upright_row"},
            "name_zh": _nullable("string"),
            "status": {"type": ["string", "null"], "enum": ["active", "caution", "banned", None]},
            "per_side": _nullable("boolean", "单侧（组数按每侧）"),
            "cues": _nullable("string", "动作要点"),
            "reason": _nullable("string"),
            "app_exercise_id": _nullable("string", "SparkyFitness 动作 ID"),
        }),
        handler=lambda conn, uid, a: store.upsert_exercise(conn, uid, a["exercise_ref"], _changes(a, "exercise_ref")),
        scope="exercises", writes=True,
    ),
]

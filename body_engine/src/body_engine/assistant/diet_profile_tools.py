"""AI tools of 「我的饮食档案」 (assistant/diet_profile.py). Registered in assistant/tools.py. Writing is not a delete, so it
runs without confirmation (not in ALWAYS_CONFIRM)."""
from datetime import date

from ..nutrition import constraint_params as CP, constraints
from ..toolspec import USER, nullable, schema
from . import diet_profile

_n = nullable


def _obj(props: dict, desc: str) -> dict:
    return {**schema(props), "type": ["object", "null"], "description": desc}


def _arr(item: dict, desc: str) -> dict:
    return {"type": ["array", "null"], "items": item, "description": desc}


S = {"type": "string"}
INPUT = schema({
    "targets": _obj({"steps": _n("integer"), "water_ml": _n("integer"), "note": _n("string")},
                    "目标本身用饮食目标工具改；这里只放步数、喝水、备注"),
    "rationale": _obj({"goal_weight_kg": _n("number"), "note": _n("string"), "checkpoints": {"type": "array", "items": schema(
        {"date": S, "weight_kg": _n("number"), "note": _n("string")})}}, "目标体重、检查点；消耗和缺口后端算"),
    "constraints": _arr(schema({"rule": S, "basis": {"type": "string", "description": "依据，例 某项化验偏高（日期）"}}),
                        "饮食限制的说明 + 依据；要提醒 / 打分真正执行的用 set_diet_constraint"),
    "day_plan": _obj({"meals": {"type": "array", "items": schema(
        {"meal": S, "foods": S, "kcal": _n("number"), "protein_g": _n("number")})},
        "staples": {"type": "array", "items": S}}, "每餐吃什么、多少；常备食材"),
    "adjust_rules": _arr(schema({"when": S, "action": S}), "例 周均体重 2 周不降 → 每天 −150 kcal"),
    "changes": _n("string", "改了什么（记一条版本）"),
    "reason": _n("string", "为什么改"),
    "version": _n("string", "null = 自动 +0.1；大改给 V3.0"),
})

TOOL_SPECS: list[dict] = [
    dict(
        name="get_diet_profile",
        description="饮食档案：当前目标、近 4 周平均基础代谢 / 活动 / 总消耗、每天缺口和预计每周减重、约束及依据、一天怎么吃、"
                    "调整规则、版本记录。",
        input_schema=schema({"user": USER}),
        handler=lambda conn, uid, a: diet_profile.view(conn, a.get("_viewer_id", uid), uid, versions=5),
        scope="memory",
    ),
    dict(
        name="update_diet_profile",
        description="改饮食档案（今天页饮食页显示）：给的块整块替换，null 不动。计划或目标变了要写 changes + reason（自动记版本）。",
        input_schema=INPUT,
        handler=lambda conn, uid, a: diet_profile.update(
            conn, uid, {s: a.get(s) for s in diet_profile.SECTIONS}, a.get("changes"), a.get("reason"), a.get("version")),
        scope="memory", writes=True,
    ),
]


def _d(v):
    return date.fromisoformat(v) if v else None


CONSTRAINT_TOOL_SPECS: list[dict] = [
    dict(
        name="get_diet_constraints",
        description="用户自己的饮食约束（提醒、菜谱、采购按它执行；没有 = 不限制）、可设的种类和参考值；history=true 含历次版本。",
        input_schema=schema({"user": USER, "history": {"type": "boolean"}}),
        handler=lambda conn, uid, a: constraints.listing(conn, uid, bool(a.get("history"))),
        scope="nutrition",
    ),
    dict(
        name="set_diet_constraint",
        description="新建 / 修改一条饮食约束（旧的保留成历史）。先和用户确认数值和依据。",
        input_schema=schema({
            "kind": {"type": "string", "enum": list(CP.KINDS)},
            "value": _n("number", "上限数值；开关类 null"),
            "target": _n("string", "food_times_per_week 的食物名"),
            "match": {"type": ["array", "null"], "items": S, "description": "别名"},
            "basis": _n("string", "依据，例 医生建议 / 某项化验（日期）"),
            "effective_from": _n("string", "YYYY-MM-DD，默认今天"),
        }),
        handler=lambda conn, uid, a: constraints.set_constraint(
            conn, uid, a["kind"], a.get("value"), a.get("target"), a.get("match"), a.get("basis"), _d(a.get("effective_from"))),
        scope="nutrition", writes=True,
    ),
    dict(
        name="end_diet_constraint",
        description="停用一条饮食约束（从某天起不再执行，历史保留）。",
        input_schema=schema({
            "kind": {"type": "string", "enum": list(CP.KINDS)},
            "target": _n("string"),
            "effective_from": _n("string", "YYYY-MM-DD，默认今天"),
        }),
        handler=lambda conn, uid, a: constraints.end_constraint(conn, uid, a["kind"], a.get("target"), _d(a.get("effective_from"))),
        scope="nutrition", writes=True,
    ),
]

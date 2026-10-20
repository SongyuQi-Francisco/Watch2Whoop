"""AI tools of 「我的饮食档案」 (assistant/diet_profile.py). Registered in assistant/tools.py. Writing is not a delete, so it
runs without confirmation (not in ALWAYS_CONFIRM)."""
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
                        "饮食限制 + 依据"),
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

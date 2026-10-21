"""Scene tools (issue #100): the training / week-review context tools (assistant/scenes.py) and get_playbook (ai/playbooks/).
Wrapped into Tool and registered by assistant/tools.py. All read-only; the context tools authorize each section against the
viewer's shares like get_briefing, so the tool itself only needs `profile`."""
from datetime import date

from ..ai import playbooks
from ..toolspec import OPT_DAY, OPT_END, USER, schema
from . import scenes


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


TOOL_SPECS: list[dict] = [
    dict(name="get_training_context",
         description="排 / 调整一周训练先调它：本周计划、恢复和准备度、禁用 / 注意动作、最近 14 天训练和负荷、能练的日子和器械；"
                     "rules（必须遵守）、needs（缺什么，先问用户）。",
         input_schema=schema({"user": USER, "date": OPT_DAY}),
         handler=lambda conn, uid, a: scenes.training_context(conn, a.get("_viewer_id", uid), uid, _d(a["date"])),
         group="training", scope="profile"),
    dict(name="get_week_review_context",
         description="复盘一周先调它：7 天（到 end）的恢复、睡眠、负荷、压力、训练、饮食达标、体重、日记和身体事件，"
                     "和上一周比（vs_previous）；rules、needs。",
         input_schema=schema({"user": USER, "end": OPT_END}),
         handler=lambda conn, uid, a: scenes.week_review_context(conn, a.get("_viewer_id", uid), uid, _d(a["end"])),
         group="body", scope="profile"),
    dict(name="get_playbook",
         description="做这几件事之前先读操作说明（调哪些工具、顺序、什么时候先问用户、护栏）：plan_shopping 规划采购、"
                     "plan_training_week 排一周训练、weekly_review 复盘一周、log_meal_from_photo 照片记餐。",
         input_schema=schema({"name": {"type": "string", "enum": list(playbooks.NAMES)}}),
         handler=lambda conn, uid, a: playbooks.get(a["name"]),
         group="help", scope="profile"),
]

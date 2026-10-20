"""AI tools of the feedback queue (root rules, ai/root_rules.md). Registered in assistant/tools.py and in the fallback AI's
tool set. report_feedback writes only the feedback queue (not health data), runs without confirmation."""
from ..toolspec import nullable, schema
from . import store

_s = nullable("string")

TOOL_SPECS: list[dict] = [
    dict(
        name="report_feedback",
        description="把系统 bug 或用户的新需求报告给开发者（根规则）。只写产品行为：不写健康数值、名字、邮箱、电话、token、链接；"
                    "服务端会再去敏、查重、限流。返回 tell_user：照它用一句话告诉用户。",
        input_schema=schema({
            "kind": {"type": "string", "enum": ["bug", "feature"]},
            "title": {"type": "string", "description": "≤ 60 字"},
            "what": {"type": "string", "description": "现象 / 想要什么"},
            "expected": _s, "steps": nullable("string", "复现步骤"),
            "area": nullable("string", "工具名或页面"), "error_text": nullable("string", "错误原文"),
        }),
        handler=lambda conn, uid, a: store.report(conn, uid, a["kind"], a["title"], a["what"], a["expected"], a["steps"],
                                                  a["area"], a["error_text"], channel=a.get("_channel")),
        scope="memory",
        writes=True,
    ),
    dict(
        name="list_feedback",
        description="用户自己报过（或 +1 过）的反馈和进展。",
        input_schema=schema({}),
        handler=lambda conn, uid, a: store.list_for_user(conn, uid),
        scope="memory",
    ),
]

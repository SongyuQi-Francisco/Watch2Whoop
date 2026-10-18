from body_engine.assistant.tools import TOOLS


def test_tool_schemas_are_strict_compatible():
    # Claude API strict tools require: every property is in required, and no additional properties are allowed
    for t in TOOLS:
        s = t.input_schema
        assert s["additionalProperties"] is False, t.name
        assert set(s["required"]) == set(s["properties"]), t.name


def test_write_tools_cannot_target_other_users():
    for t in TOOLS:
        if t.writes:
            assert "user" not in t.input_schema["properties"], t.name

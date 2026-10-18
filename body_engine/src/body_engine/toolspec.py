"""Shared JSON-schema pieces for the AI tools (assistant/tools.py and the */tools.py spec modules).

Kept short on purpose: every tool definition is sent to the model on every call (MCP tools/list, in-page chat), docs/ai-assistant-plan.md §9.13.
Format follows Claude API strict tools: every property is in `required`, optional ones are nullable."""


def schema(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def nullable(t: str, desc: str | None = None, **extra) -> dict:
    return {"type": [t, "null"], **({"description": desc} if desc else {}), **extra}


USER = {"type": "string", "description": "me 或授权给你的家人 ID"}
DATE = {"type": "string", "description": "YYYY-MM-DD"}
START = {"type": "string", "description": "YYYY-MM-DD 含"}
END = {"type": "string", "description": "YYYY-MM-DD 含"}
OPT_START = nullable("string", "YYYY-MM-DD，null=默认")
OPT_END = nullable("string", "YYYY-MM-DD，null=今天")
OPT_DAY = nullable("string", "YYYY-MM-DD，null=今天")
DETAIL = {"type": "boolean", "description": "true=完整明细（大），默认 false 摘要"}


# ---------------------------------------------------------------- relaxed schema for MCP clients
# The strict form above (everything required, nullable = "not given") is what the Claude API's strict tools need (in-page chat).
# MCP clients get a relaxed copy: nullable properties and OPTIONAL_DEFAULTS are optional, so the model does not have to write
# `"x": null` for every unused field (fewer output tokens) and tools/list is shorter. fill_defaults puts the omitted values back
# before the handler runs (assistant/tools.run_tool), so handlers always see the full strict shape.
OPTIONAL_DEFAULTS = {"user": "me", "detail": False, "details": False}


def _is_nullable(prop: dict) -> bool:
    t = prop.get("type")
    return isinstance(t, list) and "null" in t


def relaxed(s: dict) -> dict:
    if not isinstance(s, dict):
        return s
    out = {k: v for k, v in s.items() if k != "additionalProperties"}
    if s.get("type") == "object" and "properties" in s:
        out["properties"] = {k: relaxed(v) for k, v in s["properties"].items()}
        req = [k for k in s.get("required", []) if not _is_nullable(s["properties"][k]) and k not in OPTIONAL_DEFAULTS]
        if req:
            out["required"] = req
        else:
            out.pop("required", None)
    if isinstance(s.get("items"), dict):
        out["items"] = relaxed(s["items"])
    return out


def fill_defaults(s: dict, value):
    """Fill what a relaxed caller left out: nullable -> None, OPTIONAL_DEFAULTS -> their default; recursing into objects / arrays."""
    if not isinstance(s, dict):
        return value
    if s.get("type") == "object" and isinstance(value, dict) and "properties" in s:
        out = dict(value)
        for k, prop in s["properties"].items():
            if k not in out:
                if k in OPTIONAL_DEFAULTS:
                    out[k] = OPTIONAL_DEFAULTS[k]
                elif _is_nullable(prop):
                    out[k] = None
            else:
                out[k] = fill_defaults(prop, out[k])
        return out
    if isinstance(value, list) and isinstance(s.get("items"), dict):
        return [fill_defaults(s["items"], v) for v in value]
    return value

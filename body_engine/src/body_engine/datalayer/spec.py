"""Tool definitions and their JSON schemas (moved from toolspec.py and assistant/tools.Tool, issue #116).

Kept short on purpose: every tool definition is sent to the model on every call (MCP tools/list, in-page chat).
The strict form follows Claude API strict tools: every property is in `required`, optional ones are nullable.
MCP clients get a relaxed copy (`relaxed`): nullable properties and the caller's `optional_defaults` are optional, so the
model does not have to write `"x": null` for every unused field (fewer output tokens) and tools/list is shorter.
`fill_defaults` puts the omitted values back before the handler runs, so handlers always see the full strict shape.
Domain wording (what `user` means, date formats) belongs to the application, not here."""
from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[Any, int, dict], Any]  # (conn, owner_id, args) -> result
    scope: str            # data scope needed when viewing someone else's data ("" = no other person's data)
    writes: bool = False  # write operations: read-only callers do not see them, MCP clients ask before running them
    group: str = ""       # domain group: which tools are sent on each call, the rest via find_tools (datalayer/toolsets.py)
    strict: bool = True   # Claude strict tools; only the meta tools with a free-form `args` object are not strict

    def api_definition(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema, "strict": self.strict}


def schema(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def nullable(t: str, desc: str | None = None, **extra) -> dict:
    return {"type": [t, "null"], **({"description": desc} if desc else {}), **extra}


def _is_nullable(prop: dict) -> bool:
    t = prop.get("type")
    return isinstance(t, list) and "null" in t


def relaxed(s: dict, optional_defaults: Mapping[str, Any] | None = None) -> dict:
    """The schema MCP clients see: `optional_defaults` (property name -> value used when left out) and nullable
    properties are not required, `additionalProperties` is dropped; recursing into objects / arrays."""
    if not isinstance(s, dict):
        return s
    opt = optional_defaults or {}
    out = {k: v for k, v in s.items() if k != "additionalProperties"}
    if s.get("type") == "object" and "properties" in s:
        out["properties"] = {k: relaxed(v, opt) for k, v in s["properties"].items()}
        req = [k for k in s.get("required", []) if not _is_nullable(s["properties"][k]) and k not in opt]
        if req:
            out["required"] = req
        else:
            out.pop("required", None)
    if isinstance(s.get("items"), dict):
        out["items"] = relaxed(s["items"], opt)
    return out


def fill_defaults(s: dict, value, optional_defaults: Mapping[str, Any] | None = None):
    """Fill what a relaxed caller left out: nullable -> None, `optional_defaults` -> their default; recursing into objects / arrays."""
    if not isinstance(s, dict):
        return value
    opt = optional_defaults or {}
    if s.get("type") == "object" and isinstance(value, dict) and "properties" in s:
        out = dict(value)
        for k, prop in s["properties"].items():
            if k not in out:
                if k in opt:
                    out[k] = opt[k]
                elif _is_nullable(prop):
                    out[k] = None
            else:
                out[k] = fill_defaults(prop, out[k], opt)
        return out
    if isinstance(value, list) and isinstance(s.get("items"), dict):
        return [fill_defaults(s["items"], v, opt) for v in value]
    return value

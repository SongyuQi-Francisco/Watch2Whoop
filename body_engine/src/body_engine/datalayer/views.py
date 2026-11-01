"""Views: what a restricted caller may reach (moved from the application's own whitelist, issue #116,
docs/datalayer-design.md section 3.7). A view is a set of tools with the owner argument taken out of their schemas, so
the model only ever talks about the caller's own data. What is in a view (and why) stays with the application."""
import dataclasses
from dataclasses import dataclass, field
from typing import Collection, Mapping

from .spec import Tool


def without_owner(schema: dict, owner_arg: str = "user") -> dict:
    """A tool's schema without the owner argument (it could only be "me"; the executor fills that in)."""
    props = {k: v for k, v in (schema.get("properties") or {}).items() if k != owner_arg}
    return {**schema, "properties": props, "required": [r for r in schema.get("required", []) if r != owner_arg]}


@dataclass(frozen=True)
class View:
    """tools: the names in the view, in the order it sends them (None = every tool, in registration order);
    exclude: names kept out; force_self: a call that still names an owner is about the caller all the same, so sharing is
    never involved (False: the argument is only hidden, a call that names someone goes through Policy.can_view as usual)."""
    tools: tuple[str, ...] | None = None
    exclude: Collection[str] = field(default_factory=frozenset)
    force_self: bool = True
    owner_arg: str = "user"

    def select(self, by_name: Mapping[str, Tool], extra: tuple[str, ...] = ()) -> tuple[Tool, ...]:
        """The view's tools, as this caller sees them. extra: names added to a whitelist for this call (behind a feature
        switch of the application); a name that is not a tool is skipped."""
        names = by_name if self.tools is None else (*self.tools, *(n for n in extra if n not in self.tools))
        return tuple(dataclasses.replace(by_name[n], input_schema=without_owner(by_name[n].input_schema, self.owner_arg))
                     for n in names if n in by_name and n not in self.exclude)

    def call_args(self, tool: Tool, args: dict) -> dict:
        """The arguments to run `tool` with for this caller."""
        if self.force_self and self.owner_arg in tool.input_schema.get("properties", {}):
            return {**args, self.owner_arg: "me"}
        return args

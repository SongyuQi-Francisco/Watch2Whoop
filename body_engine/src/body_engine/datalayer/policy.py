"""Who is calling and what they may do (issue #116, docs/datalayer-design.md section 3.3).

Caller: the identity of one call, taken only from what the server trusts (a token, the stdio process configuration),
never from what the client says about itself. Policy: the application's answer to "may this viewer see that owner's
data of this scope"; the framework decides the rest (writes only for oneself, read-only callers never write).
SelfOnly is the default answer: nobody sees anybody else's data."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Caller:
    viewer_id: int
    channel: str          # the channel in the audit, e.g. mcp / butler / mcp:<client>
    can_write: bool = True


class Policy(Protocol):
    """Asked on every tool call, also when viewer_id == owner_id (answer True there; scope "" = a tool without a data scope)."""

    def can_view(self, conn, viewer_id: int, owner_id: int, scope: str) -> bool: ...


class SelfOnly:
    """The default Policy, for an application without sharing."""

    def can_view(self, conn, viewer_id: int, owner_id: int, scope: str) -> bool:
        return viewer_id == owner_id


def resolve_owner(viewer_id: int, args: dict, owner_arg: str = "user") -> int:
    """Whose data a call is about: "me" (or no owner argument) = the viewer, a number = that user."""
    who = args.get(owner_arg, "me")
    return viewer_id if who == "me" else int(who)

"""Who is calling and what they may do (issue #116, docs/datalayer-design.md section 3.3).

Caller: the identity of one call, taken only from what the server trusts (a token, the stdio process configuration),
never from what the client says about itself. Policy: the application's answer to "may this viewer see that owner's
data of this scope"; the framework decides the rest (writes only for oneself, read-only callers never write)."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Caller:
    viewer_id: int
    channel: str          # the channel in the audit, e.g. mcp / butler / mcp:<client>
    can_write: bool = True


class Policy(Protocol):
    def can_view(self, conn, viewer_id: int, owner_id: int, scope: str) -> bool: ...


def resolve_owner(viewer_id: int, args: dict, owner_arg: str = "user") -> int:
    """Whose data a call is about: "me" (or no owner argument) = the viewer, a number = that user."""
    who = args.get(owner_arg, "me")
    return viewer_id if who == "me" else int(who)
